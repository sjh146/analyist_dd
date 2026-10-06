#!/usr/bin/env python3
"""자체점검: 전방(forward) 성적표 틱 갱신 배선 — `refresh_forward_scorecard_if_stale`.

WHY (2026-10-06 실측): CG75 의 사전등록 판정은 `n_dates ≥ 10` 을 요구하는데 전방 성적표를
주기 실행하는 배선이 없어(크론 추가 = 승인 대상) ml_predictions 가 매일 쌓여도 판정이 영원히
'표본부족' 에 머물렀다 → 틱이 하루 1회 갱신하도록 배선했다. 이 테스트는 그 낡음 판정·요약
파싱·실패 격리가 틱을 막지 않는지 본다(도커/DB 불필요 — 순수 파이썬).

실행: python3 scripts/_forward_refresh_test.py   (호스트)
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import model_engineer_cycle as m  # noqa: E402

RESULTS = []


def check(name, cond, detail=""):
    RESULTS.append((name, bool(cond), detail))
    print(f"{'PASS' if cond else 'FAIL'}  {name}" + (f"  — {detail}" if detail else ""))


def _synth(path, h1_dates=6, h5_dates=2):
    payload = {
        "generated_at": "2026-10-07T01:04:00+09:00",
        "predictions_rows": 50407,
        "model_versions": {"v1.0": 50407},
        "result": {
            "h1": {"n_pairs": 21779, "n_dates": h1_dates, "pooled_auc": 0.5051,
                   "daily_auc_mean": 0.5226, "top10_ret_mean": 0.00045,
                   "all_ret_mean": 0.00816},
            "h5": {"n_pairs": 6513, "n_dates": h5_dates, "pooled_auc": 0.5496,
                   "daily_auc_mean": 0.5453, "top10_ret_mean": 0.02234,
                   "all_ret_mean": 0.02810},
        },
    }
    with open(path, "w", encoding="utf-8") as f:
        json.dump(payload, f)
    return payload


def main():
    now = time.time()

    # ── 1) 낡음 판정 ────────────────────────────────────────────────────────────
    check("파일 부재(mtime=0) → 갱신 필요", m._forward_needs_refresh(0, now) is True)
    check("age 5h → 갱신 불필요", m._forward_needs_refresh(now - 5 * 3600, now) is False)
    check("age 25h → 갱신 필요", m._forward_needs_refresh(now - 25 * 3600, now) is True)
    check("경계 age==20h → 불필요(> 비교)", m._forward_needs_refresh(now - 20 * 3600, now) is False)
    check("경계 age==20.01h → 필요", m._forward_needs_refresh(now - 20.01 * 3600, now) is True)

    tmpdir = tempfile.mkdtemp(prefix="fwd_test_")

    # ── 2) 요약 한 줄 ───────────────────────────────────────────────────────────
    good = os.path.join(tmpdir, "good.json")
    _synth(good)
    line = m._forward_summary_line(good)
    check("요약: h1 n_dates 포함", "h1 n_dates 6" in line, line)
    check("요약: h5 pooled 반올림", "h5 n_dates 2" in line and "0.5496" in line, line)
    check("요약: top10 vs 전체 둘 다", "top10" in line and "vs 전체" in line)
    check("요약: 파일 부재 → 파싱 실패(예외 없음)",
          "파싱 실패" in m._forward_summary_line(os.path.join(tmpdir, "nope.json")))
    bad = os.path.join(tmpdir, "bad.json")
    with open(bad, "w", encoding="utf-8") as f:
        f.write("{not json")
    check("요약: 손상 JSON → 파싱 실패", "파싱 실패" in m._forward_summary_line(bad))

    # ── 3) 최신 파일이면 subprocess 를 아예 부르지 않는다 ───────────────────────
    fresh = os.path.join(tmpdir, "fresh.json")
    _synth(fresh)
    os.utime(fresh, (now, now))
    orig_run = subprocess.run

    def _explode(*a, **k):  # noqa: ANN001
        raise AssertionError("최신 파일인데 계측기를 호출했다")

    subprocess.run = _explode
    try:
        out = m.refresh_forward_scorecard_if_stale(path=fresh)
        check("최신 → '최신' 반환·계측기 미호출", out.startswith("전방 성적표: 최신"), out)
    except AssertionError as e:
        check("최신 → '최신' 반환·계측기 미호출", False, str(e))

    # ── 4) 낡은 파일 → 계측기 호출(가짜 실행) 후 요약을 돌려준다 ─────────────────
    stale = os.path.join(tmpdir, "stale.json")
    _synth(stale, h1_dates=1, h5_dates=1)
    os.utime(stale, (now - 30 * 3600, now - 30 * 3600))
    called = {}

    def _fake_ok(cmd, **k):  # noqa: ANN001
        called["cmd"] = cmd
        _synth(stale, h1_dates=7, h5_dates=3)
        os.utime(stale, (now, now))
        return subprocess.CompletedProcess(cmd, 0, "", "")

    subprocess.run = _fake_ok
    try:
        out = m.refresh_forward_scorecard_if_stale(path=stale)
        check("낡음 → 재측정 후 새 요약 반영", "h1 n_dates 7" in out, out)
        check("낡음 → docker exec 명령 형태",
              isinstance(called.get("cmd"), list) and called["cmd"][:2] == ["docker", "exec"]
              and any("forward_scorecard.py" in str(c) for c in called["cmd"]),
              str(called.get("cmd"))[:120])
    except Exception as e:  # noqa: BLE001
        check("낡음 → 재측정 후 새 요약 반영", False, f"{type(e).__name__}: {e}")

    # ── 5) rc!=0 / 예외 → 틱을 막지 않고 문자열 반환 ────────────────────────────
    os.utime(stale, (now - 30 * 3600, now - 30 * 3600))   # 다시 낡게(위 가짜 실행이 mtime 을 새로 찍었다)

    def _fake_fail(cmd, **k):  # noqa: ANN001
        return subprocess.CompletedProcess(cmd, 1, "", "boom")

    subprocess.run = _fake_fail
    out = m.refresh_forward_scorecard_if_stale(path=stale)
    check("rc!=0 → 'rc=1' 문자열(예외 없음)", "rc=1" in out, out)

    def _fake_raise(cmd, **k):  # noqa: ANN001
        raise TimeoutError("timeout")

    subprocess.run = _fake_raise
    out = m.refresh_forward_scorecard_if_stale(path=stale)
    check("예외 → '재측정 실패' 문자열(틱 비차단)", "재측정 실패" in out, out)

    subprocess.run = orig_run

    # ── 6) 틱에 배선됐는가(문자열 계약) ────────────────────────────────────────
    src = open(os.path.join(os.path.dirname(os.path.abspath(__file__)),
                            "model_engineer_cycle.py"), encoding="utf-8").read()
    check("tick() 이 전방 성적표 갱신을 호출",
          "refresh_forward_scorecard_if_stale()" in src)

    n_fail = sum(1 for _, ok, _ in RESULTS if not ok)
    print(f"\n{len(RESULTS) - n_fail}/{len(RESULTS)} PASS")
    sys.exit(1 if n_fail else 0)


if __name__ == "__main__":
    main()

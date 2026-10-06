#!/usr/bin/env python3
"""자체점검 — 배포 챔피언 성적표 자동 갱신(refresh_scorecard_if_stale, 2026-10-06).

무엇을 보증하나(트레이더 환류 '계약2 거짓 통과' 2026-10-06 실측):
  ① 성적표가 최신이면 **재생성하지 않는다**(매 틱 서브프로세스 낭비 방지)
  ② mtime 이 낡았으면 재생성한다
  ③ mtime 이 6h 이내여도 **원장에 더 새 기록이 있으면** 내용이 낡은 것으로 보고 재생성한다
     (자정 직후 promote_dryrun 이 옛 기록을 가리키는 경우)
  ④ 재생성 실패(예외·비정상 종료)는 **fail-open** — 예외를 올리지 않고 옛 값을 유지한다
  ⑤ 실제 champion_scorecard.py 가 rc=0 으로 계약 필드(folds·mean·purge·promote_dryrun)를 쓴다
  ⑥ 틱이 이 함수를 실제로 호출한다(배선 확인 — 함수만 있고 미배선이면 아무 효과가 없다)

실행(호스트 python3 — 도커 불필요):
    python3 scripts/_scorecard_refresh_test.py
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import time
import types
from datetime import datetime

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import model_engineer_cycle as me                   # noqa: E402

FAILS: list[str] = []


def check(name, cond, detail=""):
    print(("PASS " if cond else "FAIL ") + name + (f" — {detail}" if detail else ""))
    if not cond:
        FAILS.append(name)


def _with_stubs(scard_path, ledger_rows, run_impl):
    """me 의 SCORECARD/load_ledger/subprocess 를 임시로 갈아끼우는 컨텍스트."""
    class _Ctx:
        def __enter__(self):
            self._sc, self._ll, self._sp = me.SCORECARD, me.load_ledger, me.subprocess
            me.SCORECARD = scard_path
            me.load_ledger = lambda limit=None: list(ledger_rows)
            me.subprocess = types.SimpleNamespace(run=run_impl)
            return self

        def __exit__(self, *a):
            me.SCORECARD, me.load_ledger, me.subprocess = self._sc, self._ll, self._sp
            return False
    return _Ctx()


def main() -> int:
    tmp = tempfile.mkdtemp(prefix="sc_refresh_")
    scard = os.path.join(tmp, "scorecard.json")
    json.dump({"promote_dryrun": {"record_id": "OLD", "record_ts": "2026-10-02T00:00:00+09:00",
                                  "status": "would_promote"}, "fold_stats": {"n_folds": 3}},
              open(scard, "w", encoding="utf-8"))
    now_dt = datetime.now().astimezone()
    old_ts = now_dt.replace(year=now_dt.year - 1).isoformat()
    fresh_ts = datetime.fromtimestamp(os.path.getmtime(scard) - 3600).astimezone().isoformat()

    calls = {"n": 0}

    def stub_ok(*args, **kw):
        calls["n"] += 1
        json.dump({"promote_dryrun": {"record_id": "NEW", "record_ts": now_dt.isoformat(),
                                      "status": "kept_incumbent"}, "fold_stats": {"n_folds": 5}},
                  open(scard, "w", encoding="utf-8"))
        return subprocess.CompletedProcess(args, 0, "ok", "")

    # ① 최신이면 재생성 안 함
    os.utime(scard, None)
    calls["n"] = 0
    with _with_stubs(scard, [{"ts": fresh_ts}], stub_ok):
        out = me.refresh_scorecard_if_stale()
    check("① 최신 → 재생성 안 함", calls["n"] == 0 and out.startswith("성적표: 최신"), out)

    # ② mtime 낡음 → 재생성
    old_mtime = time.time() - 48 * 3600
    os.utime(scard, (old_mtime, old_mtime))
    calls["n"] = 0
    with _with_stubs(scard, [{"ts": old_ts}], stub_ok):
        out = me.refresh_scorecard_if_stale()
    check("② mtime 48h → 재생성", calls["n"] == 1 and "재생성" in out and "NEW" in out, out)

    # ③ mtime 은 최신이어도 원장에 더 새 기록이 있으면 재생성
    os.utime(scard, None)
    calls["n"] = 0
    later = datetime.fromtimestamp(time.time() + 120).astimezone().isoformat()
    with _with_stubs(scard, [{"ts": later}], stub_ok):
        me.refresh_scorecard_if_stale()
    # stub 이 파일 mtime 을 now 로 갱신하므로 '호출됐는가'로 판정
    check("③ 내용 낡음(원장 최신 > mtime) → 재생성", calls["n"] == 1, f"calls={calls['n']}")

    # ④ 서브프로세스 예외 → fail-open
    os.utime(scard, (old_mtime, old_mtime))

    def stub_raise(*a, **kw):
        raise subprocess.TimeoutExpired(cmd="x", timeout=1)
    with _with_stubs(scard, [{"ts": old_ts}], stub_raise):
        out = me.refresh_scorecard_if_stale()
    check("④ 예외 → fail-open(옛 값 유지)", out.startswith("성적표 재생성 실패"), out)

    def stub_fail(*a, **kw):
        return subprocess.CompletedProcess(a, 1, "", "boom")
    os.utime(scard, (old_mtime, old_mtime))
    with _with_stubs(scard, [{"ts": old_ts}], stub_fail):
        out = me.refresh_scorecard_if_stale()
    check("④b rc≠0 → fail-open", "rc=1" in out, out)

    # ⑤ 실제 스크립트 e2e (레포 정본 파일에 씀)
    real_out = me.SCORECARD
    try:
        before = os.path.getmtime(real_out) if os.path.exists(real_out) else 0.0
    except OSError:
        before = 0.0
    proc = subprocess.run([sys.executable, os.path.join(me.PROJ, "scripts", "champion_scorecard.py")],
                          capture_output=True, text=True, timeout=120)
    check("⑤ e2e rc=0", proc.returncode == 0, (proc.stderr or "").strip()[:80])
    ok_fields = False
    try:
        rep = json.load(open(real_out, encoding="utf-8"))
        fs, pd = rep.get("fold_stats", {}), rep.get("promote_dryrun", {})
        ok_fields = all(k in fs for k in ("folds", "mean", "std", "fold_win_rate")) and bool(pd) \
            and ("purge" in rep) and os.path.getmtime(real_out) >= before
    except Exception as e:                              # noqa: BLE001
        check("⑤ 계약 필드 파싱", False, repr(e)[:80])
    check("⑤ 계약 필드(folds/mean/std/win_rate/purge/dry-run) + mtime 갱신", ok_fields,
          f"before={before:.0f} after={os.path.getmtime(real_out):.0f}"
          if os.path.exists(real_out) else "no file")

    # ⑥ 틱 배선 확인 — 함수만 있고 미배선이면 효과 0
    wired = "refresh_scorecard_if_stale" in me.tick.__code__.co_names
    check("⑥ tick 이 성적표 갱신을 호출", wired, f"co_names={me.tick.__code__.co_names}")

    print(f"\n{'ALL PASS' if not FAILS else 'FAILED: ' + ', '.join(FAILS)}")
    return 1 if FAILS else 0


if __name__ == "__main__":
    raise SystemExit(main())

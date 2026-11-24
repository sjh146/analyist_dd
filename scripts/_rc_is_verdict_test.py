"""판정 인코딩 종료코드(rc_is_verdict) 처리 자체점검.

왜(실측 2026-11-24 XR48): 리서처 probe 는 결론을 rc 로 인코딩한다(채택 0 / 기각 1 / 표본부족 3).
구동기가 rc!=0 을 기계적으로 '실행실패'로 남겨 33-probe 완주 측정이 원장에서 사라졌다.
이 테스트는 ① 로그 마지막 JSON verdict 추출 ② 예외/JSON 없음 → 채택 금지 ③ status 매핑을 고정한다.
"""
import importlib.util
import os
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("mec", ROOT / "scripts" / "model_engineer_cycle.py")
mec = importlib.util.module_from_spec(spec)
sys.modules["mec"] = mec
spec.loader.exec_module(mec)  # noqa: E402


def _tmp(lines):
    fd, p = tempfile.mkstemp(suffix=".log")
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        f.write("\n".join(lines) + "\n")
    return p


def main():
    ok = 0
    fail = []
    cases = []

    p = _tmp([
        "# XR48 회귀",
        '{"error": "중간 진단"}',
        '{"n_probes": 33, "verdict": "07:30_이후_발행", "threshold": "07:30"}',
        "R48 판정: 07:30_이후_발행 · probe 33건",
    ])
    d = mec.rc_verdict_from_log(p)
    cases.append(("1 verdict 추출", bool(d) and d.get("verdict") == "07:30_이후_발행", d))
    os.remove(p)

    p = _tmp(['{"error": "실행 실패", "rc": 1}'])
    cases.append(("2 verdict 없는 JSON 은 채택 금지", mec.rc_verdict_from_log(p) is None, ""))
    os.remove(p)

    p = _tmp(["Traceback (most recent call last):", "RuntimeError: boom"])
    cases.append(("3 예외 로그는 채택 금지", mec.rc_verdict_from_log(p) is None, ""))
    os.remove(p)

    cases.append(("4 없는 파일은 None", mec.rc_verdict_from_log("/nonexistent/x.log") is None, ""))

    st, _ = mec.backlog_status_after(0, "", False, 1)
    cases.append(("5 판정 인코딩 항목 status=done", st == "done", st))

    st, _ = mec.backlog_status_after(1, "종료코드 1(로그 꼬리에서 예외 줄 미발견)", False, 1)
    cases.append(("6 일반 rc=1 은 failed 유지", st == "failed", st))

    for label, passed, got in cases:
        if passed:
            ok += 1
        else:
            fail.append(f"{label} → {got!r}")

    print(f"PASS {ok}/{len(cases)}")
    for f in fail:
        print("FAIL", f)
    return 0 if ok == len(cases) else 1


if __name__ == "__main__":
    raise SystemExit(main())

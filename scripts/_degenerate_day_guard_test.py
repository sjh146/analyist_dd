#!/usr/bin/env python3
"""퇴화일 가드(CG160) 자체점검 — 컨테이너에서 실행한다.

정상일에는 **파일이 생기지 않고**, 퇴화 입력에서는 **생긴다**를 증명한다.
입력 예측 목록은 어떤 경로로도 수정되지 않아야 한다(발행 계약 무변경).

실행: docker exec stock_xgboost_ml sh -c 'cd /app && python -u scripts/_degenerate_day_guard_test.py'
"""
from __future__ import annotations

import os
import shutil
import sys
import tempfile

sys.path.insert(0, "/app")

from app.inference.day_guard import (  # noqa: E402
    FILE_PREFIX,
    PRIOR_RUN_FILE_PREFIX,
    check_and_report_degenerate_day,
    check_and_report_prior_run,
    confidence_uniqueness,
    degenerate_reason,
    prior_run_reason,
)

PASS = 0
FAIL = 0


def check(name: str, cond: bool, detail: str = "") -> None:
    global PASS, FAIL
    if cond:
        PASS += 1
        print(f"  ok   {name}")
    else:
        FAIL += 1
        print(f"  FAIL {name} {detail}")


def mk(vals, date="2026-09-22"):
    return [{"stock_code": f"{i:06d}", "prediction_date": date, "confidence": v}
            for i, v in enumerate(vals)]


def files_in(d):
    return sorted(f for f in os.listdir(d) if f.startswith(FILE_PREFIX))


def main() -> int:
    tmp = tempfile.mkdtemp(prefix="cg160_guard_")
    try:
        # 1) 정상일: distinct 비율 높음 → 파일 없음
        normal = mk([0.10 + 0.001 * (i % 300) for i in range(300)])
        r = check_and_report_degenerate_day(normal, report_dir=tmp)
        check("정상일 degenerate=False", r["degenerate"] is False and r["reason"] is None, str(r))
        check("정상일 파일 없음", files_in(tmp) == [], str(files_in(tmp)))

        # 2) 퇴화일(09-22 실측 재현): 2,678행 전부 0.1429
        degen = mk([0.1429] * 2678)
        r = check_and_report_degenerate_day(degen, report_dir=tmp)
        check("퇴화일 degenerate=True", r["degenerate"] is True)
        check("퇴화일 reason=single_value", r["reason"] == "single_value", str(r.get("reason")))
        check("퇴화일 n=2678 · distinct=1", r["n"] == 2678 and r["n_distinct"] == 1, str(r))
        check("퇴화일 최빈값 0.1429", r["top_value"] == 0.1429, str(r.get("top_value")))
        fname = f"{FILE_PREFIX}2026-09-22.json"
        check("퇴화일 파일명 규약", files_in(tmp) == [fname], str(files_in(tmp)))
        check("퇴화일 report_path 일치", r["report_path"] == os.path.join(tmp, fname))
        import json
        with open(os.path.join(tmp, fname), encoding="utf-8") as fh:
            saved = json.load(fh)
        check("증거 파일 내용 일치(n/distinct/reason)",
              saved["n"] == 2678 and saved["n_distinct"] == 1 and saved["reason"] == "single_value")

        # 3) 저분산(비율 < 1%): 5,000행 · distinct 30 = 0.6%
        low = mk([0.30] * 4970 + [0.31 + 0.0001 * i for i in range(30)], date="2026-09-23")
        r = check_and_report_degenerate_day(low, report_dir=tmp)
        check("저분산 reason=low_distinct", r["reason"] == "low_distinct", str(r.get("reason")))
        check("저분산 파일 생성", f"{FILE_PREFIX}2026-09-23.json" in files_in(tmp), str(files_in(tmp)))

        # 4) 경계: 100행 · distinct 2 = 2.0% (> 1%) → 퇴화 아님
        r = check_and_report_degenerate_day(mk([0.5] * 99 + [0.6], date="2026-09-24"), report_dir=tmp)
        check("경계 2% 는 퇴화 아님", r["degenerate"] is False, str(r))
        check("경계 2% 파일 없음", f"{FILE_PREFIX}2026-09-24.json" not in files_in(tmp))

        # 5) 0행·1행 → 퇴화 아님(분모 보호)
        check("빈 목록 n=0 비퇴화", check_and_report_degenerate_day([], report_dir=tmp)["degenerate"] is False)
        check("1행 비퇴화", check_and_report_degenerate_day(mk([0.9], date="2026-09-25"), report_dir=tmp)["degenerate"] is False)

        # 6) NaN·비수치는 집계 제외(정상 판정을 깨지 않는다)
        mixed = mk([0.4 + 0.001 * i for i in range(50)])
        mixed += [{"stock_code": "x1", "prediction_date": "2026-09-26", "confidence": float("nan")},
                  {"stock_code": "x2", "prediction_date": "2026-09-26", "confidence": None},
                  {"stock_code": "x3", "prediction_date": "2026-09-26"}]
        r = check_and_report_degenerate_day(mixed, report_dir=tmp)
        check("NaN/결측 제외 후 n=50", r["n"] == 50, str(r.get("n")))
        check("NaN/결측 섞여도 비퇴화", r["degenerate"] is False, str(r))

        # 7) 입력 불변성(발행 계약 무변경)
        payload = mk([0.1429] * 100, date="2026-09-27")
        before = [dict(p) for p in payload]
        check_and_report_degenerate_day(payload, report_dir=tmp)
        check("입력 목록 미수정", len(payload) == 100 and payload == before, "입력이 바뀌었다")

        # 8) 날짜 추론(인자 없이 prediction_date 사용) + 날짜 지정 우선
        r = check_and_report_degenerate_day(mk([0.2] * 10, date="2026-09-28"), report_dir=tmp)
        check("날짜 추론=predictions 의 prediction_date", r["date"] == "2026-09-28", str(r.get("date")))
        r2 = check_and_report_degenerate_day(mk([0.2] * 10, date="2026-09-28"), date="2026-01-01", report_dir=tmp)
        check("명시 date 우선", r2["date"] == "2026-01-01", str(r2.get("date")))

        # 9) 도우미 직접 검증
        s = confidence_uniqueness(mk([0.1, 0.1, 0.2]))
        check("confidence_uniqueness n/distinct", s["n"] == 3 and s["n_distinct"] == 2 and s["top_share"] == round(2/3, 6), str(s))
        check("degenerate_reason 단일값", degenerate_reason({"n": 2, "n_distinct": 1, "distinct_ratio": 0.5}) == "single_value")
        check("degenerate_reason 비퇴화", degenerate_reason({"n": 100, "n_distinct": 90, "distinct_ratio": 0.9}) is None)

        # 10) 배선 가드 — main.py::run_predictions 가 저장 루프 **앞**에서 호출하는가
        main_py = "/app/app/main.py"
        if os.path.exists(main_py):
            src = open(main_py, encoding="utf-8").read()
            i_guard = src.find("check_and_report_degenerate_day(predictions)")
            i_save = src.find("for pred in predictions:")
            check("main.py 배선 존재", i_guard != -1)
            check("main.py 저장 루프 앞 호출", i_guard != -1 and i_save != -1 and i_guard < i_save,
                  f"guard@{i_guard} save@{i_save}")
            check("main.py 임포트 배선", "from app.inference.day_guard import" in src)
        else:
            check("main.py 존재", False, main_py)

        # 11) CG161 재실행 혼합 가드 — 순수 판정
        check("첫 실행(0행) 비경보", prior_run_reason(0, 4314) is None)
        check("조회실패(None) 비경보", prior_run_reason(None, 4314) is None)
        check("09-23 실측(2770→4314) partial_mix", prior_run_reason(2770, 4314) == "partial_mix")
        check("전량중복 rerun_no_add", prior_run_reason(4314, 4314) == "rerun_no_add")
        check("기존>신규 rerun_no_add", prior_run_reason(5000, 4314) == "rerun_no_add")
        check("신규 0행 비경보", prior_run_reason(2770, 0) is None and prior_run_reason(2770, None) is None)

        # 12) CG161 경보 파일 규약
        r = check_and_report_prior_run(2770, 4314, date="2026-09-23", report_dir=tmp)
        check("partial_mix 파일 생성", os.path.exists(os.path.join(tmp, f"{PRIOR_RUN_FILE_PREFIX}2026-09-23.json")))
        check("partial_mix 판정 내용", r["prior_run"] is True and r["reason"] == "partial_mix" and r["existing_rows"] == 2770)
        check("첫 실행은 파일 없음", check_and_report_prior_run(0, 4314, date="2026-10-11", report_dir=tmp)["report_path"] is None)

        # 13) CG161 배선 가드
        if os.path.exists(main_py):
            src2 = open(main_py, encoding="utf-8").read()
            i_pr = src2.find("check_and_report_prior_run(_existing, len(predictions))")
            i_save2 = src2.find("for pred in predictions:")
            check("main.py prior_run 배선", i_pr != -1)
            check("main.py prior_run 저장 루프 앞", i_pr != -1 and i_save2 != -1 and i_pr < i_save2,
                  f"prior@{i_pr} save@{i_save2}")
            check("main.py prior_run 임포트", "check_and_report_prior_run" in src2)
            storage = "/app/app/storage/postgres_storage.py"
            check("count_predictions_for_date 정의", os.path.exists(storage)
                  and "def count_predictions_for_date" in open(storage, encoding="utf-8").read())

        print(f"\nPASS {PASS} · FAIL {FAIL}")
        return 0 if FAIL == 0 else 1
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


if __name__ == "__main__":
    raise SystemExit(main())

#!/usr/bin/env python3
"""metric `intraday_screen` 자체점검 — 파서·판정기·경로 배선 (CG128, 2026-10-06).

이 스택에는 pytest 가 없다 → PASS/FAIL 을 직접 세고 실패 시 exit 1.

검사:
 1) summary_path: `--json-out /app/...` → 호스트 경로로 매핑된다
 2) summary_path: 플래그가 없으면 기본 경로(cg128_intraday_screen.json)
 3) parse: 합성 요약에서 horizons/best_feature(|IC| 최대)를 뽑는다
 4) parse: 파일 없음 / mtime floor 이하 → error
 5) judge: n_dates < min_dates → '판정불가(표본 부족)' (문턱 하향 금지)
 6) judge: n_dates 충분 + |IC|·|t| 통과 → '정보있음'
 7) judge: n_dates 충분 + 미달 → '노이즈'
 8) judge: 알 수 없는 intraday_test → '판정불가'
 9) parse 결과에 per_exp 가 없다(scoreboard arm 오독 방지)
"""
from __future__ import annotations

import json
import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import model_engineer_cycle as m  # noqa: E402

PASS = FAIL = 0


def check(name: str, ok: bool, extra: str = "") -> None:
    global PASS, FAIL
    if ok:
        PASS += 1
        print(f"[PASS] {name}")
    else:
        FAIL += 1
        print(f"[FAIL] {name} {extra}")


def _summary(n_dates_h1=4, ic=-0.105, t=-2.61, n_dates_h5=0):
    def blk(nd, icv, tv):
        # 실측 요약(2026-10-06)에서 h5 는 표본일수가 0 이라 모든 피처의 ic/t 가 None 이다.
        def f(auc, im, it):
            if nd <= 0:
                im, it = None, None
            return {"daily_auc_mean": auc, "daily_auc_std": 0.01,
                    "daily_auc_list": [auc, auc], "ic_mean": im, "ic_t": it}
        return {
            "n_dates": nd,
            "dates": [f"2026-09-2{d}" for d in range(min(nd, 9))],
            "features": {
                "l30_ret": f(0.4642, -0.0307, -0.91),
                "return_1d": f(0.4718, icv, tv),
                "null": f(0.4817, -0.0063, -0.41),
            },
        }

    return {"full_days": ["2026-09-28", "2026-09-29"], "n_rows": 1500,
            "horizons": {"h1": blk(n_dates_h1, ic, t), "h5": blk(n_dates_h5, None, None)}}


def main() -> int:
    # 1) 경로 배선 — 컨테이너 경로 → 호스트 경로
    cmd = ("docker exec stock_xgboost_ml sh -c \"cd /app && python -u scripts/intraday_feature_screen.py "
           "--json-out /app/reports/overnight/cg128_intraday_screen.json\"")
    sp = m.summary_path("intraday_screen", cmd)
    check("summary_path(--json-out /app) → 호스트 매핑",
          sp.endswith("services/xgboost-ml/reports/overnight/cg128_intraday_screen.json"), sp)

    # 2) 플래그 없으면 기본 경로
    sp2 = m.summary_path("intraday_screen", "python scripts/intraday_feature_screen.py")
    check("summary_path(플래그 없음) → 기본 경로",
          sp2.endswith("services/xgboost-ml/reports/overnight/cg128_intraday_screen.json"), sp2)

    with tempfile.TemporaryDirectory() as td:
        p = os.path.join(td, "s.json")

        # 3) 파서 — best_feature 는 |IC| 최대
        with open(p, "w", encoding="utf-8") as f:
            json.dump(_summary(), f)
        parsed = m.parse_intraday_screen(p, 0.0)
        check("parse: error 없음", not parsed.get("error"), str(parsed.get("error")))
        check("parse: h1 best_feature=return_1d", parsed["horizons"]["h1"]["best_feature"] == "return_1d",
              str(parsed["horizons"]["h1"].get("best_feature")))
        check("parse: h1 n_dates=4", parsed["horizons"]["h1"]["n_dates"] == 4)
        check("parse: h5 n_dates=0 · best_ic None", parsed["horizons"]["h5"]["n_dates"] == 0
              and parsed["horizons"]["h5"]["best_ic"] is None)

        # 4) 오류 경로
        check("parse: 없는 파일 → error", bool(m.parse_intraday_screen(os.path.join(td, "nope.json"), 0.0).get("error")))
        mt = os.path.getmtime(p)
        check("parse: mtime floor 이하 → error",
              bool(m.parse_intraday_screen(p, mt + 10).get("error")))

        item = {"id": "CG128", "metric": "intraday_screen", "intraday_test": "feature_ic",
                "min_delta_ic": 0.03, "min_t": 2.0, "min_dates": 20}

        # 5) 표본 부족 → 판정불가 (실측 2026-10-06 그대로: n_dates 4, return_1d IC −0.105 t −2.61)
        pv = m.parse_intraday_screen(p, 0.0)
        v, d, _ = m.judge_intraday_screen(item, pv)
        check("judge: 표본 부족 → 판정불가", v == "판정불가", f"verdict={v}")
        check("judge: 사유에 '표본 부족'", "표본 부족" in d, d[:120])

        # 6) 표본 충분 + 통과 → 정보있음
        with open(p, "w", encoding="utf-8") as f:
            json.dump(_summary(n_dates_h1=25, ic=-0.050, t=-2.6), f)
        v, d, dl = m.judge_intraday_screen(item, m.parse_intraday_screen(p, 0.0))
        check("judge: 표본 충분 + |IC|0.05 t2.6 → 정보있음", v == "정보있음", f"verdict={v} / {d[:120]}")
        check("judge: delta = |IC|", dl == 0.05, str(dl))

        # 7) 표본 충분 + 미달 → 노이즈
        with open(p, "w", encoding="utf-8") as f:
            json.dump(_summary(n_dates_h1=25, ic=-0.005, t=-0.3), f)
        v, d, _ = m.judge_intraday_screen(item, m.parse_intraday_screen(p, 0.0))
        check("judge: 표본 충분 + 미달 → 노이즈", v == "노이즈", f"verdict={v}")

        # 8) 알 수 없는 플래그 → 판정불가 (조용한 폴백 금지)
        v, d, _ = m.judge_intraday_screen({"metric": "intraday_screen", "intraday_test": "bogus"}, pv)
        check("judge: 알 수 없는 intraday_test → 판정불가", v == "판정불가", f"verdict={v}")

        # 9) per_exp 미생성 (scoreboard arm 오독 방지)
        check("parse: per_exp 없음", "per_exp" not in pv, str(list(pv.keys())))

        # 10) error 전달 시 판정불가
        v, d, _ = m.judge_intraday_screen(item, {"error": "요약 파일 없음"})
        check("judge: parsed.error → 판정불가", v == "판정불가" and "요약" in d, f"verdict={v}")

    print(f"\n{PASS} PASS / {FAIL} FAIL")
    return 1 if FAIL else 0


if __name__ == "__main__":
    sys.exit(main())

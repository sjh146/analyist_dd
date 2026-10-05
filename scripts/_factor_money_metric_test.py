#!/usr/bin/env python3
"""_factor_money_metric_test.py — metric `factor_money_screen` 배선 자체점검.

왜: 새 metric 은 **백로그 등록과 같은 커밋**에서 summary_path·parse·judge 3곳을 배선해야 한다
(실측 2026-09-30 CG43: 파서가 없으면 rc=0 으로 항목이 done 으로 닫히면서 유일한 산출물이
원장에서 사라진다). 이 테스트는 ① 경로 해석 ② 신선도(mtime) 거부 ③ 스키마 파싱
④ 판정 4가지를 합성 파일로 확인한다.

실행(호스트):
  python3 scripts/_factor_money_metric_test.py
"""
import json
import os
import sys
import tempfile
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import model_engineer_cycle as m  # noqa: E402

FAIL = []


def check(name, cond, got=None):
    print(("PASS " if cond else "FAIL ") + name + ("" if cond else f"  ← got={got!r}"))
    if not cond:
        FAIL.append(name)


def payload(mf_ic=0.0275, mf_t=3.12, spread=-1.80, n=274):
    return {
        "panel": {"file": "panel_prod200.npz", "rows": 54800, "codes": 200,
                  "from": "2025-08-04", "to": "2026-09-23", "horizon": 5},
        "asof": {"financial_ratio_features_rcept_gt_date": 0},
        "note": "IC·분위 스프레드(정보)만 — 순기대 아님",
        "factors": {
            "value_score": {"coverage": 0.592,
                            "ic": {"n": n, "mean": 0.0436, "sd": 0.09, "t": 4.25,
                                   "pos_share": 0.60, "first_half": 0.03, "second_half": 0.05},
                            "decile_spread_pct": {"n": n, "mean": 0.7939, "sd": 1.0, "t": 1.24,
                                                  "pos_share": 0.5, "first_half": 0.1, "second_half": 0.2}},
            "quality_score": {"coverage": 0.901,
                              "ic": {"n": n, "mean": 0.0244, "sd": 0.09, "t": 4.23,
                                     "pos_share": 0.60, "first_half": 0.02, "second_half": 0.03},
                              "decile_spread_pct": {"n": n, "mean": -1.0986, "sd": 0.5, "t": -2.31,
                                                    "pos_share": 0.4, "first_half": -0.5, "second_half": -0.6}},
            "momentum_score": {"coverage": 0.782,
                               "ic": {"n": 214, "mean": -0.0218, "sd": 0.09, "t": -2.15,
                                      "pos_share": 0.48, "first_half": -0.01, "second_half": -0.03},
                               "decile_spread_pct": {"n": 214, "mean": -0.4056, "sd": 0.9, "t": -0.55,
                                                     "pos_share": 0.45, "first_half": -0.1, "second_half": -0.2}},
            "lowvol_score": {"coverage": 0.945,
                             "ic": {"n": 259, "mean": 0.0524, "sd": 0.09, "t": 4.08,
                                    "pos_share": 0.59, "first_half": 0.04, "second_half": 0.06},
                             "decile_spread_pct": {"n": 259, "mean": -2.6529, "sd": 0.6, "t": -4.36,
                                                   "pos_share": 0.35, "first_half": -0.9, "second_half": -1.0}},
            "multifactor": {"coverage": 0.994,
                            "ic": {"n": n, "mean": mf_ic, "sd": 0.08, "t": mf_t,
                                   "pos_share": 0.61, "first_half": 0.02, "second_half": 0.04},
                            "decile_spread_pct": {"n": n, "mean": spread, "sd": 0.7, "t": -3.85,
                                                  "pos_share": 0.4, "first_half": -0.6, "second_half": -0.7}},
        },
    }


def write_tmp(obj):
    fd, p = tempfile.mkstemp(suffix=".json", prefix="fms_")
    with os.fdopen(fd, "w") as f:
        json.dump(obj, f, ensure_ascii=False)
    return p


def main():
    cmd = ("docker exec stock_xgboost_ml sh -c 'cd /app && python scripts/factor_money_screen.py "
           "--panel app/models/wf/panel_prod200.npz --out reports/overnight/factor_money_screen.json'")
    # ① summary_path: 커맨드 --out 파싱 → 호스트 경로 변환
    sp = m.summary_path("factor_money_screen", cmd)
    check("summary_path 는 services/xgboost-ml/reports/overnight/factor_money_screen.json",
          sp.endswith("services/xgboost-ml/reports/overnight/factor_money_screen.json"), sp)
    # --out 없는 커맨드 → 기본 경로
    sp2 = m.summary_path("factor_money_screen", "python scripts/factor_money_screen.py")
    check("--out 없으면 기본 경로", sp2.endswith("factor_money_screen.json"), sp2)

    # ② 파일 없음
    p = m.parse_factor_money_screen("/tmp/__no_such_fms.json", 0.0)
    check("파일 없음 → error", bool(p.get("error")), p)
    # ③ 무효 JSON
    bad = write_tmp({"hello": 1})
    p = m.parse_factor_money_screen(bad, 0.0)
    check("factors 없음 → error", bool(p.get("error")), p)
    # ④ mtime 신선도 거부
    good = write_tmp(payload())
    p = m.parse_factor_money_screen(good, time.time() + 10)
    check("mtime <= floor → 미갱신 error", "미갱신" in (p.get("error") or ""), p)
    # ⑤ 정상 파싱
    p = m.parse_factor_money_screen(good, 0.0)
    check("정상 파싱: factors 존재", "multifactor" in (p.get("factors") or {}), list((p.get("factors") or {}).keys()))
    check("정상 파싱: per_exp 미생성(scoreboard 오독 방지)", "per_exp" not in p, list(p.keys()))
    item = {"id": "CG115", "metric": "factor_money_screen"}

    # ⑥ 판정: IC>0 & t>=2 → 정보있음
    v, d, dv = m.judge_factor_money_screen(item, p)
    check("IC +0.0275 t3.12 → 정보있음", v == "정보있음", v)
    check("delta = 멀티팩터 IC", dv == 0.0275, dv)
    check("detail 에 비단조 진단(스프레드 부호) 포함", "스프레드" in d and "lowvol_score" in d, d[:120])
    # ⑦ 판정: t 미달 → 노이즈
    p2 = m.parse_factor_money_screen(write_tmp(payload(mf_ic=0.004, mf_t=0.9)), 0.0)
    v2, _, _ = m.judge_factor_money_screen(item, p2)
    check("IC +0.004 t0.9 → 노이즈", v2 == "노이즈", v2)
    # ⑧ 판정: 부호 음(-) → 노이즈
    p3 = m.parse_factor_money_screen(write_tmp(payload(mf_ic=-0.03, mf_t=-4.0)), 0.0)
    v3, _, _ = m.judge_factor_money_screen(item, p3)
    check("IC 음(-) → 노이즈(부호 요구)", v3 == "노이즈", v3)
    # ⑨ 판정: 파싱 오류 → 판정불가
    v4, _, dv4 = m.judge_factor_money_screen(item, {"error": "요약 파일 없음"})
    check("파싱 오류 → 판정불가", v4 == "판정불가" and dv4 is None, v4)
    # ⑩ CLI 기본값 회귀: 다른 metric 경로가 바뀌지 않았는지
    check("wf_sweep_summary 기본경로 회귀",
          m.summary_path("wf_sweep_summary", "").endswith("wf_label_sweep_summary.json"))
    check("rank_ic_money 는 --json-out 기반 유지",
          m.summary_path("rank_ic_money", "x --json-out /app/reports/overnight/a.json").endswith("reports/overnight/a.json"))

    # ── CG116: 체결성 필터 ON 팩터 top-k vs 풀평균 ─────────────────────────────
    def fillable_block(k3=0.42, t3=2.6, k5=0.31, t5=2.1):
        def v(d, t):
            return {"n_sessions": 274, "mean_delta_pct": d, "sd": 1.0, "t": t,
                    "pos_share": 0.56, "first_half": 0.2, "second_half": 0.3}
        return {"filter": {"max_day_chg_pct": 25.0, "min_value": 1e9, "min_price": 1000.0,
                           "rows_total": 54800, "rows_kept": 41000, "kept_share": 0.748},
                "note": "널=풀평균",
                "factors": {"multifactor": {"top": {"k": {"3": v(k3, t3), "5": v(k5, t5),
                                                         "10": v(0.2, 1.4), "20": v(0.1, 0.9)},
                                                  "pool_mean_pct": v(2.2, 3.3)},
                                            "bottom": {"k": {"3": v(-0.3, -1.1), "5": v(-0.2, -0.9)},
                                                       "pool_mean_pct": v(2.2, 3.3)}}}}

    spf = m.summary_path("factor_money_fillable",
                         "docker exec x python scripts/factor_money_screen.py --fillable "
                         "--out reports/overnight/factor_money_screen_fillable.json")
    check("factor_money_fillable summary_path", spf.endswith("factor_money_screen_fillable.json"), spf)

    no_fl = write_tmp(payload())           # fillable 블록 없음
    pnf = m.parse_factor_money_fillable(no_fl, 0.0)
    check("fillable 블록 없음 → error", "fillable 블록 없음" in (pnf.get("error") or ""), pnf.get("error"))

    with_fl = write_tmp({**payload(), "fillable": fillable_block()})
    pf = m.parse_factor_money_fillable(with_fl, 0.0)
    check("fillable 파싱 정상", pf.get("error") is None and "fillable" in pf, pf.get("error"))
    itemf = {"id": "CG116", "metric": "factor_money_fillable"}
    vf, df_, dvf = m.judge_factor_money_fillable(itemf, pf)
    check("k3 Δ+0.42 t2.6 · k5 Δ+0.31 t2.1 → 신호있음", vf == "신호있음", vf)
    check("fillable delta = k=3 Δ", dvf == 0.42, dvf)
    check("detail 에 널(풀평균) 명시", "풀평균" in df_ and "유지 41000/54800" in df_, df_[:140])
    pf2 = m.parse_factor_money_fillable(
        write_tmp({**payload(), "fillable": fillable_block(k5=0.05, t5=1.2)}), 0.0)
    vf2, _, _ = m.judge_factor_money_fillable(itemf, pf2)
    check("k5 미달 → 노이즈(둘 다 필요)", vf2 == "노이즈", vf2)
    pf3 = m.parse_factor_money_fillable(
        write_tmp({**payload(), "fillable": fillable_block(k3=0.9, t3=4.0, k5=0.8, t5=3.0)}), 0.0)
    vf3, _, _ = m.judge_factor_money_fillable({**itemf, "ks": [3, 5, 10]}, pf3)
    check("ks=[3,5,10] 로 k10(t1.4) 추가하면 노이즈", vf3 == "노이즈", vf3)

    # ── CG117: 덤프 짝(모델 IC vs 팩터 IC) 경로 ────────────────────────────────
    extra = []

    def paired_block(dic=0.032, t=3.4, msess=78, tied=2, err=None):
        b = {"arm": "cg95_q05", "arm_jsonl": "/app/reports/overnight/cg95_q05_all.jsonl",
             "fillable": True,
             "filter": {"max_day_chg_pct": 25.0, "min_value": 1e9, "min_price": 1000.0,
                        "rows_total": 23858, "rows_kept": 12123, "kept_share": 0.508},
             "universe": {"dump_codes": 300, "matched_codes": 296,
                          "panel_code_overlap": 41, "dates": 80},
             "rows": {"dump": 23858, "matched": 23650, "scored": 12100, "sessions_used": msess},
             "ic_model": {"n": msess, "mean": 0.0560, "sd": 0.11, "t": 4.53, "pos_share": 0.66,
                          "first_half": 0.02, "second_half": 0.09},
             "ic_factor": {"n": msess, "mean": 0.0275, "sd": 0.10, "t": 3.12, "pos_share": 0.61,
                           "first_half": 0.02, "second_half": 0.04},
             "paired": {"n_sessions": msess, "n_tied": tied, "mean_delta_ic": dic, "sd": 0.09,
                        "t": t, "pos_session_share": 0.62, "sign_pos": 47, "sign_n": msess - tied,
                        "sign_p": 0.02, "first_half_delta": 0.02, "second_half_delta": 0.04},
             "prereg": "ΔIC ≥ +0.01 AND t ≥ 2.0", "verdict": "모델 우위 있음", "note": "짝 비교"}
        if err:
            b = {"error": err}
        return b

    def _p117(blk):
        p_ = write_tmp({**payload(), "paired_vs_factor": blk})
        extra.append(p_)
        return m.parse_factor_money_screen(p_, 0.0)

    item117 = {"id": "CG117", "metric": "factor_money_screen", "paired_vs_factor": True}
    p117 = _p117(paired_block())
    check("paired_vs_factor 파싱 통과", p117.get("paired_vs_factor") is not None, list(p117.keys()))
    check("paired 경로도 per_exp 미생성", "per_exp" not in p117)
    v117, d117, dv117 = m.judge_factor_money_screen(item117, p117)
    check("ΔIC +0.032 · t 3.4 → 모델 우위 있음", v117 == "모델 우위 있음", v117)
    check("paired delta = ΔIC", dv117 == 0.032, dv117)
    check("detail 에 행 짝·교집합·부호검정 명시",
          "같은 (code,date) 행 짝" in d117 and "panel 교집합 41" in d117 and "부호검정 p" in d117,
          d117[:160])

    v2, _, _ = m.judge_factor_money_screen(item117, _p117(paired_block(dic=0.005)))
    check("ΔIC +0.005 < +0.01 → 모델 우위 없음", v2 == "모델 우위 없음", v2)
    v3, _, _ = m.judge_factor_money_screen(item117, _p117(paired_block(dic=0.05, t=1.5)))
    check("ΔIC 충족·t 1.5 < 2 → 모델 우위 없음(둘 다 필요)", v3 == "모델 우위 없음", v3)
    v4, _, _ = m.judge_factor_money_screen({**item117, "min_delta_ic": 0.05},
                                           _p117(paired_block(dic=0.032)))
    check("item min_delta_ic=0.05 로 조이면 미달", v4 == "모델 우위 없음", v4)
    v5, _, _ = m.judge_factor_money_screen(item117, _p117(paired_block(err="market_data 없음")))
    check("paired 블록 error → 판정불가", v5 == "판정불가", v5)
    blk6 = paired_block()
    blk6["paired"] = {"n_sessions": 1, "error": "공통 세션 부족(>=2 필요)"}
    v6, _, _ = m.judge_factor_money_screen(item117, _p117(blk6))
    check("공통 세션 부족 → 판정불가", v6 == "판정불가", v6)
    # 분기 격리: 플래그 없는데 짝 블록이 있으면 '등록 누락'이다 — CG115 규칙으로 조용히 폴백하지 않는다.
    # (2026-10-05 CG119 실측 계약 변경: 폴백하면 로그 "모델 우위 없음" vs 원장 "정보있음" 이 어긋났다.
    #  종전 이 검사는 v7 == "정보있음" 을 기대했다 — 그 기대가 곧 결함이었다.)
    v7, _, _ = m.judge_factor_money_screen({"id": "CG115", "metric": "factor_money_screen"},
                                           _p117(paired_block()))
    check("플래그 없는데 짝 블록 있음 → 판정불가(등록 누락)", v7 == "판정불가", v7)
    # 진짜 CG115(짝 블록 자체가 없는 요약)는 종전대로 패널 규칙을 쓴다(회귀)
    v7b, _, _ = m.judge_factor_money_screen({"id": "CG115", "metric": "factor_money_screen"},
                                            m.parse_factor_money_screen(good, 0.0))
    check("짝 블록 없는 CG115 요약 → 정보있음(기존 규칙 유지)", v7b == "정보있음", v7b)
    # 짝 블록 없이 플래그만 있으면 판정불가(조용히 CG115 규칙으로 떨어지지 않는다)
    v8, _, _ = m.judge_factor_money_screen(item117, m.parse_factor_money_screen(good, 0.0))
    check("플래그만 있고 짝 블록 없음 → 판정불가", v8 == "판정불가", v8)

    for f in (bad, good) + tuple(extra):
        try:
            os.remove(f)
        except OSError:
            pass
    print(f"\n{'ALL PASS' if not FAIL else 'FAILURES: ' + ', '.join(FAIL)}")
    return 1 if FAIL else 0


if __name__ == "__main__":
    sys.exit(main())

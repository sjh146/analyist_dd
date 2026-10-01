#!/usr/bin/env python3
"""_blend_metric_test.py — metric `blend_eval`(CG56 모델 결합) 배선 검증. (pytest 없음 → 자체 PASS/FAIL)

왜 필요한가(실측 2026-10-01 CG56): 이 실험은 요약 JSON 스키마가 champion_robust_eval 과 다른데
metric 이름을 재사용할 뻔했다 → 그 파서는 `folds`(auc_mean 목록)를 요구하므로 "folds 비어 있음
(유효 창 없음)" = 판정불가로 기록되고 rc=0 으로 항목이 done 으로 닫혀 **항목의 유일한 산출물인
짝 Δ 가 원장에서 사라진다**(2026-09-30 CG43 과 같은 함정 — 신규 metric 은 파서·판정·ingest 배선을
같은 커밋에서 끝내야 한다).

검사 항목
 ① summary_path('blend_eval', …) 이 커맨드의 --out 을 호스트 경로로 옮긴다
 ② parse_blend_eval 이 paired/arms 를 싣고 **per_exp 를 만들지 않는다**(스코어보드 오독 방지)
 ③ judge_blend_eval: Δ≥+0.02 & 양(+)≥80% = 짝 신호 / 그 외 = 짝 노이즈 / paired 결측 = 판정불가
 ④ 회귀: champion_robust_eval 경로·판정이 그대로다(기존 항목 동작 불변)
 ⑤ 깨진 JSON(쓰는 중)은 예외 없이 error 로 반환된다
"""
import json
import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import model_engineer_cycle as m  # noqa: E402

fails = []


def check(name, cond, extra=""):
    print(("PASS " if cond else "FAIL ") + name + (f" — {extra}" if extra else ""))
    if not cond:
        fails.append(name)


def summary(delta, pos, se=0.005, t=None, blend=0.540, champ=0.514, cand=0.527):
    return {
        "measured_at": "2026-10-01T10:00:00+00:00",
        "model_dir": "blend(rank-avg)=champion+cand",
        "protocol": "rank-avg blend · same run/seed/windows · 3창 · 10시드",
        "metric_name": "blend_auc",
        "robust_auc": blend, "auc_std_across_folds": 0.011,
        "fold_means": [blend + 0.001 * (i % 3) for i in range(10)],
        "windows": [{"seed": i, "n_windows": 3, "rows": 1740,
                     "champ": champ, "cand": cand, "blend": blend + 0.001 * (i % 3)}
                    for i in range(10)],
        "rows_scored": 17400, "errors": [],
        "n_seeds": 10, "n_windows": 3,
        "champ_mean": champ, "cand_mean": cand,
        "paired": {
            "delta_blend_minus_champ_mean": delta, "se": se,
            "t": t if t is not None else round(delta / se, 2),
            "pos_seeds": pos, "samples": [delta] * 10,
            "delta_blend_minus_cand_mean": delta - 0.013, "se_vs_cand": se,
            "pos_seeds_vs_cand": pos, "threshold": 0.02, "note": "n/a",
        },
        "arms": {"champ": {"model": "champion", "mean": champ},
                 "cand": {"model": "cand_cg51", "mean": cand},
                 "blend": {"method": "rank-avg within (fold,date)", "mean": blend}},
    }


def write(tmp, obj, name="cg56_summary.json", raw=None):
    p = os.path.join(tmp, name)
    with open(p, "w", encoding="utf-8") as f:
        f.write(raw if raw is not None else json.dumps(obj, ensure_ascii=False))
    return p


tmp = tempfile.mkdtemp(prefix="blend_metric_test_")

# ① summary_path — 커맨드의 첫 --out 을 호스트 경로로
cmd = ("docker exec stock_xgboost_ml sh -c 'cd /app && OMP_NUM_THREADS=2 timeout 3600 sh "
       "scripts/cg56_run.sh --out app/reports/cg56_summary.json'")
sp = m.summary_path("blend_eval", cmd)
check("① summary_path(--out 파싱)", sp.endswith("services/xgboost-ml/app/reports/cg56_summary.json"), sp)
check("① summary_path(--out=/abs 형태)", m.summary_path("blend_eval", "x --out=/app/reports/a.json")
      .endswith("services/xgboost-ml/reports/a.json"))
check("① summary_path(--out 없음 → 빈 경로, 예외 없음)", m.summary_path("blend_eval", "cmd") == "")

# ② parse — paired 싣고 per_exp 는 만들지 않는다
item = {"id": "CG56", "metric": "blend_eval", "command": cmd}
p = write(tmp, summary(0.025, "9/10"))
par = m.parse_by_metric(item, p, 0.0)
check("② paired 파싱", isinstance(par.get("paired"), dict) and par["paired"]["pos_seeds"] == "9/10")
check("② robust_auc(결합 시드평균)", par.get("robust_auc") == 0.540)
check("② per_exp 미생성(스코어보드 오독 방지)", "per_exp" not in par or not par.get("per_exp"))
check("② champ/cand 평균 노출", par.get("champ_mean") == 0.514 and par.get("cand_mean") == 0.527)
check("② n_seeds/n_windows 노출", par.get("n_seeds") == 10 and par.get("n_windows") == 3)
check("② mtime floor 가드", "요약 미갱신" in str(m.parse_by_metric(item, p, 1e18).get("error")))

# ③ 판정
v, d, dl = m.judge_by_metric(item, par)
check("③ Δ+0.025 & 9/10 → 짝 신호", v == "짝 신호", f"{v} · Δ{dl}")
check("③ 신호 근거에 SE·t·양(+) 포함", "SE" in d and "양(+)" in d and "0.514" in d)

p2 = write(tmp, summary(0.005, "6/10"), "n1.json")
v2, d2, dl2 = m.judge_by_metric(item, m.parse_by_metric(item, p2, 0.0))
check("③ Δ+0.005 → 짝 노이즈", v2 == "짝 노이즈", f"{v2} · Δ{dl2}")

p3 = write(tmp, summary(0.030, "7/10"), "n2.json")
v3, _, _ = m.judge_by_metric(item, m.parse_by_metric(item, p3, 0.0))
check("③ Δ+0.030 이지만 양(+) 7/10 → 짝 노이즈(분산 요건)", v3 == "짝 노이즈", v3)

p4 = write(tmp, summary(0.021, "10/10"), "n3.json")
v4, _, _ = m.judge_by_metric(item, m.parse_by_metric(item, p4, 0.0))
check("③ 경계 Δ+0.021 & 10/10 → 짝 신호", v4 == "짝 신호", v4)

p5 = write(tmp, {"robust_auc": 0.54, "folds": []}, "nopaired.json")
v5, d5, _ = m.judge_by_metric(item, m.parse_by_metric(item, p5, 0.0))
check("③ paired 없음 → 판정불가", v5 == "판정불가", f"{v5} · {d5[:40]}")

# ④ 회귀 — champion_robust_eval 경로/판정 불변
cref = ("docker exec stock_xgboost_ml sh -c 'cd /app && python -u scripts/champion_robust_eval.py "
        "--out app/reports/cg55_rep1.json'")
csp = m.summary_path("champion_robust_eval", cref)
check("④ champion_robust_eval --out 경로 불변",
      csp.endswith("services/xgboost-ml/app/reports/cg55_rep1.json"), csp)
champ_par = {"robust_auc": 0.52, "auc_std_across_folds": 0.01, "fold_means": [0.51, 0.53],
             "auc_pooled": 0.52, "auc_per_date_mean": 0.52, "rows_scored": 100}
v6, _, dl6 = m.judge_by_metric({"id": "CGx", "metric": "champion_robust_eval",
                                "counterfactual_value": 0.5139}, champ_par)
check("④ 기준선 실측 짝 경로 불변(Δ+0.0061 → 짝 노즈)", v6 == "짝 노이즈", f"{v6} · Δ{dl6}")
check("④ 미지 metric → parser 없음", "parser 없음" in
      m.parse_by_metric({"id": "X", "metric": "nope"}, "/tmp/x.json", 0.0).get("error", ""))
check("④ metric 없는 항목 → 빈 경로(크래시 없음)", m.summary_path(None, "cmd") == "")

# ⑤ 깨진 JSON
pb = write(tmp, None, "broken.json", raw='{"paired": {"delta_blend_minus_champ_mean":')
eb = m.parse_by_metric(item, pb, 0.0)
check("⑤ 깨진 JSON → error 반환(예외 없음)", "파싱 실패" in str(eb.get("error")), str(eb.get("error"))[:60])

print("\n" + ("전부 PASS" if not fails else f"FAIL {len(fails)}건: {fails}"))
sys.exit(1 if fails else 0)

#!/usr/bin/env python3
"""_audit_pair_metric_test.py — 구동기 metric kind `metric_audit_pair` 자체점검 (CG171).

배선 대상: scripts/model_engineer_cycle.py
  ① summary_path("metric_audit_pair", cmd) — 커맨드의 `--out` 을 요약 경로로 쓴다
  ② parse_metric_audit_pair — 감사 산출물(config/b_robust_auc/c_net_expectancy)에서 폴드·순기대·짝 Δ
  ③ judge_metric_audit_pair — 사전등록 두 다리(AUC Δ≥+0.02 · 돈 arm>0 & Δ≥+0.1%p & both_positive)

왜 이 검사가 필요한가(실측 2026-10-11 CG168): 승격 게이트가 읽는 증거(robust_oos.json)를 만드는
감사 도구의 **같은 런 짝**을 구동기가 판정하지 못해, 챔피언 값은 10-05 파일을 다른 런에서 인용할
수밖에 없었다. 배선이 틀리면 ① 감사 항목이 '판정불가'로 원장에 남거나 ② 기준선 값을 'arm 개선폭'
으로 스코어보드에 싣는 사고(CG31·CG143 동형)가 난다. 그래서 여기서 둘 다 못 박는다.

실행: python3 scripts/_audit_pair_metric_test.py
"""
from __future__ import annotations

import importlib.util
import json
import os
import sys
import tempfile
import time

HERE = os.path.dirname(os.path.abspath(__file__))
PROJ = os.path.dirname(HERE)
spec = importlib.util.spec_from_file_location("mec", os.path.join(HERE, "model_engineer_cycle.py"))
mec = importlib.util.module_from_spec(spec)
spec.loader.exec_module(mec)

RESULTS = []


def check(name, ok, info=""):
    RESULTS.append((name, bool(ok), info))
    print(f"[{'PASS' if ok else 'FAIL'}] {name}" + (f" — {info}" if info else ""))


def audit_doc(folds_a, folds_c, exp_a, exp_c, t_a, t_c, halves_a="both_positive",
              halves_c="both_positive", n_sessions=87, labels=("arm", "ctl"), paired=None):
    def arm(lb, folds, exp, t, halves):
        return {
            "b_robust_auc": {lb: {"robust_auc": round(sum(folds) / len(folds), 4), "folds": folds,
                                  "auc_pooled": round(sum(folds) / len(folds) - 0.005, 4),
                                  "train_start": "2026-07-10", "train_end": "2026-10-08",
                                  "windows_excluded": [["2026-06-09", "2026-08-04"]]}},
            "c_net_expectancy": {lb: {"n_rows_scored": 1340, "n_sessions": n_sessions, "avg_pct": exp,
                                      "t_stat": t, "n_trades": 1340,
                                      "halves": {"front_pct": 0.1, "back_pct": 0.2,
                                                 "stable": halves, "split_date": "2026-07-28"}}},
            "single_split_auc": {lb: 0.6}, "train_windows": {lb: ["2026-07-10", "2026-10-08"]},
            "a_live_signal": {lb: {"deployed_gt_0.55": 1}},
        }

    doc = {"config": {"topk": 3, "max_day_chg": 25.0, "folds": len(folds_a), "dates_per_fold": 10,
                      "stocks": 80, "horizon": 5, "label_kind": "rel",
                      "models": [{"label": labels[0], "dir": "app/models/champion_cand_cg166"},
                                 {"label": labels[1], "dir": "app/models/champion"}]}}
    for k, v in arm(labels[0], folds_a, exp_a, t_a, halves_a).items():
        doc.setdefault(k, {}).update(v)
    for k, v in arm(labels[1], folds_c, exp_c, t_c, halves_c).items():
        doc.setdefault(k, {}).update(v)
    if paired is not None:
        doc["c_paired"] = paired
    return doc


def write_tmp(doc, name="audit_pair.json"):
    p = os.path.join(tempfile.mkdtemp(prefix="cg171_"), name)
    with open(p, "w", encoding="utf-8") as f:
        json.dump(doc, f, ensure_ascii=False)
    return p


def main() -> int:
    # ① summary_path — 커맨드의 --out 을 요약으로 (고정 경로면 여러 arm 산출물을 덮어쓴다)
    cmd = ("docker exec stock_xgboost_ml sh -c 'cd /app && python -u scripts/model_metric_protocol_audit.py "
           '--models \'[["cg166","app/models/champion_cand_cg166"]]\' '
           "--out /app/reports/cg171_audit_pair.json'")
    sp = mec.summary_path("metric_audit_pair", cmd)
    check("① summary_path --out 추출", sp.endswith("services/xgboost-ml/reports/cg171_audit_pair.json"), sp)
    sp_def = mec.summary_path("metric_audit_pair", "python -u scripts/model_metric_protocol_audit.py")
    check("① summary_path 기본 경로", sp_def.endswith("services/xgboost-ml/reports/model_metric_protocol_audit.json"),
          sp_def)

    # ② 실측 산출물(CG168, 단일 arm) 이 '대조 없음' 으로 정직하게 끝나는가
    real = os.path.join(PROJ, "services/xgboost-ml/app/reports/cg168_cand_audit.json")
    item1 = {"id": "T1", "metric": "metric_audit_pair"}
    if os.path.exists(real):
        p = mec.parse_by_metric(item1, real, 0.0)
        check("② 실측 단일 arm 파싱", not p.get("error"), str(p.get("error") or "")[:80])
        check("② 실측 단일 arm per_exp 없음", "per_exp" not in p)
        v, d, dl = mec.judge_by_metric(item1, p)
        check("② 실측 단일 arm 판정=대조 없음", v == "대조 없음" and dl is None, f"{v} · {d[:90]}")
    else:
        check("② 실측 산출물 존재", False, real)

    # ③ 짝 Δ 계산 + 판정 (두 다리)
    folds_a, folds_c = [0.5311, 0.5132, 0.5409], [0.4795, 0.5000, 0.4600]
    doc = audit_doc(folds_a, folds_c, exp_a=0.0912, exp_c=-0.182, t_a=0.51, t_c=-0.81)
    path = write_tmp(doc)
    it = {"id": "T3", "metric": "metric_audit_pair"}
    p = mec.parse_by_metric(it, path, 0.0)
    check("③ 파싱 오류 없음", not p.get("error"), str(p.get("error") or "")[:80])
    check("③ per_exp 미생성", "per_exp" not in p)
    pair = p.get("pair") or {}
    want = round((sum(folds_a) / 3 - sum(folds_c) / 3), 6)
    check("③ 짝 Δ(폴드 평균 차) 계산", pair.get("delta_fold_mean") == want,
          f"{pair.get('delta_fold_mean')} vs {want}")
    check("③ 폴드별 Δ = zip(A,C)", pair.get("delta_folds") == [round(a - c, 6) for a, c in zip(folds_a, folds_c)],
          str(pair.get("delta_folds")))
    check("③ 순기대 Δ = arm−대조", pair.get("delta_expectancy_pct") == round(0.0912 - (-0.182), 6),
          str(pair.get("delta_expectancy_pct")))
    v, d, dl = mec.judge_by_metric(it, p)
    # AUC 다리 통과(+0.0512 ≥ +0.02) · 돈 다리: arm>0 이지만 Δ+0.27%p ≥ +0.1 이고 both_positive → 통과
    check("③ 두 다리 통과 → 짝 신호", v == "짝 신호" and dl is None, f"{v} · {d[:120]}")

    # ④ AUC 다리 통과 + 돈 다리 미달 → 'AUC 만 오른 후보' 로 기록되나 (CG168 실측 형태)
    doc = audit_doc([0.5050, 0.5030, 0.5070], [0.4790, 0.4810, 0.4770],
                    exp_a=0.0912, exp_c=-0.182, t_a=0.51, t_c=-0.81, halves_a="unstable")
    p = mec.parse_by_metric(it, write_tmp(doc), 0.0)
    v, d, dl = mec.judge_by_metric(it, p)
    check("④ AUC통과·돈미달 → 노이즈(승격 금지 문구)", v == "노이즈" and "승격 금지" in d and dl is None,
          f"{v} · {d[-90:]}")

    # ⑤ AUC 미달 + 돈 통과 → 미충족(노이즈)
    doc = audit_doc([0.4810, 0.4830, 0.4790], [0.4790, 0.4810, 0.4770],
                    exp_a=0.5, exp_c=0.1, t_a=2.5, t_c=0.5)
    p = mec.parse_by_metric(it, write_tmp(doc), 0.0)
    v, d, dl = mec.judge_by_metric(it, p)
    check("⑤ AUC 미달 → 노이즈", v == "노이즈" and "AUC 미달" in d, f"{v}")

    # ⑥ 낡은 요약(mtime <= 실행 시작) → 판정불가 (옛 결과 오독 방지)
    doc = audit_doc(folds_a, folds_c, 0.09, -0.18, 0.5, -0.8)
    path = write_tmp(doc)
    p = mec.parse_by_metric(it, path, time.time() + 10)
    v, d, dl = mec.judge_by_metric(it, p)
    check("⑥ 낡은 요약 → 판정불가", v == "판정불가" and dl is None, f"{v} · {d[:60]}")

    # ⑦ arm/control 지정(순서 무관) + b 파트 실패 산출물
    doc = audit_doc(folds_a, folds_c, 0.09, -0.18, 0.5, -0.8, labels=("ctl", "arm"))
    p = mec.parse_by_metric({"id": "T7", "metric": "metric_audit_pair", "arm_label": "arm",
                             "control_label": "ctl"}, write_tmp(doc), 0.0)
    check("⑦ arm_label 지정 반영", (p.get("pair") or {}).get("arm") == "arm"
          and (p.get("pair") or {}).get("control") == "ctl", str((p.get("pair") or {}).get("arm")))
    bad = os.path.join(tempfile.mkdtemp(prefix="cg171_"), "bad.json")
    with open(bad, "w", encoding="utf-8") as f:
        json.dump({"config": {"models": [{"label": "a"}, {"label": "b"}]}, "a_live_signal": {"a": {}}}, f)
    p = mec.parse_by_metric(it, bad, 0.0)
    v, d, dl = mec.judge_by_metric(it, p)
    check("⑦ b 파트 없는 산출물 → 판정불가", v == "판정불가", f"{v} · {d[:60]}")

    # ⑧ 세션별 짝 Δ(CG172) 파싱 + 통과 — 감사 도구가 c_paired 를 실으면 돈 다리가 짝으로 판정된다
    paired_ok = {"arm": "arm", "control": "ctl", "n_sessions": 30, "delta_mean": 0.35, "sd": 1.2,
                 "se": 0.22, "t": 2.5, "pos": 18, "neg": 12, "ties": 0,
                 "halves": {"front_pct": 0.1, "back_pct": 0.6, "stable": "both_positive",
                            "split_date": "2026-03-03"},
                 "folds": {"1": "6/10", "2": "6/10", "3": "6/10"}}
    doc = audit_doc(folds_a, folds_c, exp_a=0.0912, exp_c=-0.182, t_a=0.51, t_c=-0.81, paired=paired_ok)
    p = mec.parse_by_metric(it, write_tmp(doc), 0.0)
    pp = (p.get("pair") or {}).get("expectancy_paired") or {}
    check("⑧ c_paired 파싱(delta_mean·t)", pp.get("delta_mean") == 0.35 and pp.get("t") == 2.5, str(pp)[:90])
    v, d, dl = mec.judge_by_metric(it, p)
    check("⑧ 짝 통과(t≥2·both_positive) → 짝 신호", v == "짝 신호" and "사전문턱[짝]" in d, f"{v} · {d[-80:]}")

    # ⑨ 우선순위: 주변은 통과하나 짝 t<2 → 돈 미달(짝이 주변을 덮는다)
    weak = dict(paired_ok, t=1.6)
    doc = audit_doc(folds_a, folds_c, exp_a=0.0912, exp_c=-0.182, t_a=0.51, t_c=-0.81, paired=weak)
    p = mec.parse_by_metric(it, write_tmp(doc), 0.0)
    v, d, dl = mec.judge_by_metric(it, p)
    check("⑨ 짝 t<2 → 돈 미달(주변 통과 무시)", v == "노이즈" and "돈 미달" in d and "사전문턱[짝]" in d,
          f"{v} · {d[-80:]}")

    # ⑩ c_paired 없으면 주변 경로로 폴백(무회귀) — 판정 문구에 [주변] 표기
    doc = audit_doc(folds_a, folds_c, exp_a=0.0912, exp_c=-0.182, t_a=0.51, t_c=-0.81)
    p = mec.parse_by_metric(it, write_tmp(doc), 0.0)
    check("⑩ c_paired 없음 → expectancy_paired 없음",
          "expectancy_paired" not in (p.get("pair") or {}), str(p.get("pair", {}).keys()))
    v, d, dl = mec.judge_by_metric(it, p)
    check("⑩ 주변 폴백 표기", "사전문턱[주변]" in d, f"{v} · {d[-70:]}")

    n_fail = sum(1 for _, ok, _ in RESULTS if not ok)
    print(f"\n{len(RESULTS) - n_fail}/{len(RESULTS)} PASS")
    return 1 if n_fail else 0


if __name__ == "__main__":
    sys.exit(main())

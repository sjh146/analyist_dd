"""CG439 결과 기록 + CG440(재측정) 등록 — 2026-11-23 엔지니어.

실행: python3 scripts/_cg439_record.py [--dry-run]
"""
import json
import sys

PATH = "docs/QUANT_MODEL_BACKLOG.json"


def main(dry=False):
    d = json.load(open(PATH, encoding="utf-8"))
    items = d["items"] if isinstance(d, dict) and "items" in d else d
    idx = {it["id"]: it for it in items}

    cg = idx["CG439"]
    cg["status"] = "done"
    cg["result"] = {
        "verdict": "조건부 신호",
        "detail": (
            "사전등록 3조건 전부 충족(Δ평균 +1.318%p · 5시드 중 4 양수 · seed3 날짜 t=4.16) → '약한 상대 엣지' "
            "문면 통과. 그러나 기여가 seed3 단일(Δ+4.25)에 몰려 있고, 그 시드를 빼면 4시드 평균 Δ+0.587 · 최대 t 1.35 로 "
            "유의성 소멸. 비중첩 stride=5 로 재채점하면 세션 4개로 붕괴(Δ평균 +0.693 · 3/5 양수). → '실재'가 아니라 '미확정'."
        ),
        "delta": 1.318,
        "per_exp": {
            "k5_delta_by_seed": [0.620, -0.212, 0.928, 4.246, 1.010],
            "k5_t_by_seed": [0.91, -0.18, 0.73, 4.16, 1.35],
            "k5_arm_mean_by_seed": [-4.778, -5.017, -4.195, -0.264, -4.347],
            "k5_pool_mean_by_seed": [-5.398, -4.805, -5.124, -4.509, -5.357],
            "k3_delta_mean": 1.689, "k10_delta_mean": 1.058,
            "stride5_delta_by_seed": [-0.458, -2.260, 0.276, 2.790, 3.118],
            "arm_minus_control_k5": [0.308, 0.339, 1.428, 3.108, 1.304],
            "auc_pooled_by_seed": [0.5457, 0.5230, 0.5169, 0.5549, 0.5611],
            "n_sessions_fillable": 18, "n_dates_scored": 18, "rows_per_seed": 1440,
        },
    }
    cg["evidence"] += (
        " | 2026-11-23 05:1x 엔지니어 재채점(학습 산출물 재사용, 태그 수리): k=5 Δ(top-k − 풀평균) 시드별 "
        "+0.620(t 0.91)/−0.212(−0.18)/+0.928(0.73)/+4.246(4.16)/+1.010(1.35) → 평균 +1.318 · 4/5 양수 · "
        "절대 순기대 top −4.78 vs pool −5.40(하락 15/18세션). champion−retrain_cg346 = +0.31/+0.34/+1.43/+3.11/+1.30 (5/5 양수, t 최대 2.66). "
        "비중첩 stride=5: Δ −0.458/−2.260/+0.276/+2.790/+3.118 (평균 +0.693, n=4세션). 패널 AUC 0.5169~0.5611(5시드 평균 0.5403 ≈ 기준선 0.5406). "
        "seed 제외 검정: seed3 제외 시 Δ평균 +0.587 · 전 시드 t<1.4."
    )
    cg["note"] = (
        "실행은 rc=2 로 실패했었다 — 원인은 학습이 아니라 태그 불일치(--dump-tag champion_s0 vs --arm-tag champ_s0). "
        "38.6분 학습 산출물(cg439_*_s{0..4}.jsonl, 각 1,440행)은 정상이어서 재학습 없이 채점만 다시 돌려 결과를 얻었다. "
        "재발 방지로 scripts/fillable_topk_expectancy.py 가 실패 시 파일 내 실제 exp 태그를 출력하도록 수리(음성/양성 검증 완료). "
        "구조적 한계: 챔피언·retrain_cg346 모두 2026-10-01 까지 학습 → OOS 는 2026-10-02 이후로 고정(29거래일·18세션). "
        "날짜 표본은 시간 경과 외엔 늘릴 수 없고 시드 교차는 날짜를 공유해 SE 를 줄이지 못한다."
    )
    cg.pop("attempts", None)

    if "CG440" not in idx:
        items.append({
            "id": "CG440",
            "kind": "experiment",
            "owner": "engineer",
            "affects_model": False,
            "priority": 2,
            "status": "pending",
            "metric": "fillable_topk_vs_pool",
            "summary_out": "/app/reports/overnight/cg440_money_s0.json",
            "est_minutes": 80,
            "not_before": "2026-12-02T20:00:00+09:00",
            "title": "[돈 지표·확정] 챔피언 상대 엣지 재측정 — 세션 18→26 으로 늘려 '단일 시드 지배'를 사전 배제한 판정 (CG439 후속)",
            "arm": "champion",
            "counterfactual": "풀 평균(그날 체결가능 전 종목 평균) = 무작위 k 기대 + retrain_cg346",
            "command": (
                "timeout 9000 docker exec stock_xgboost_ml sh -c 'cd /app && for s in 0 1 2 3 4; do for m in champion retrain_cg346; do "
                "OMP_NUM_THREADS=4 timeout 1800 python -u scripts/champion_robust_eval.py --model-dir app/models/$m "
                "--window-mode post_train --train-start 2025-09-24 --train-end 2026-10-01 --oos-start 2026-10-02 "
                "--asof-date $(date -d tomorrow +%Y-%m-%d) --folds 2 --dates-per-fold 15 --universe training --universe-seed $s "
                "--dump-preds /app/reports/overnight/cg440_${m}_s${s}.jsonl --dump-all --dump-tag ${m}_s${s} "
                "--out /app/reports/overnight/cg440_${m}_s${s}_eval.json || exit 1; done; done; "
                "for s in 0 1 2 3 4; do PYTHONPATH=/app python -u scripts/fillable_topk_expectancy.py "
                "--arm-jsonl /app/reports/overnight/cg440_champion_s${s}.jsonl --arm-tag champion_s${s} "
                "--control-jsonl /app/reports/overnight/cg440_retrain_cg346_s${s}.jsonl --control-tag retrain_cg346_s${s} "
                "--k 5 --sides top,bottom --exit close_h --horizon 5 --stride 1 --json-out /app/reports/overnight/cg440_money_s${s}.json; "
                "PYTHONPATH=/app python -u scripts/fillable_topk_expectancy.py "
                "--arm-jsonl /app/reports/overnight/cg440_champion_s${s}.jsonl --arm-tag champion_s${s} "
                "--control-jsonl /app/reports/overnight/cg440_retrain_cg346_s${s}.jsonl --control-tag retrain_cg346_s${s} "
                "--k 5 --sides top,bottom --exit close_h --horizon 5 --stride 5 --json-out /app/reports/overnight/cg440_money_s${s}_stride5.json; done'"
            ),
            "hypothesis": (
                "CG439 는 사전등록 3조건을 문면상 통과했으나 Δ 기여가 seed3 한 개(+4.25, t 4.16)에 몰려 있어 "
                "‘5시드 반복’이 실제로는 ‘한 유니버스 발견’일 가능성이 남았다. 세션이 18→26 으로 늘면 날짜 t 의 "
                "표본오차가 줄어 seed3 없이도 신호가 서는지 판정할 수 있다."
            ),
            "evidence": (
                "CG439 실측(2026-11-23): k=5 Δ +0.620/−0.212/+0.928/+4.246/+1.010 · 평균 +1.318 · 4/5 양수 · "
                "seed3 제외 시 평균 +0.587·t<1.4 · stride5 Δ평균 +0.693(n=4). champion−retrain 5/5 양수(평균 +1.30, t 최대 2.66). "
                "OOS 상한은 모델 학습 종료(2026-10-01)로 고정 — 2026-12-02 시점 예상 세션 26."
            ),
            "success": (
                "사전 등록(변경 금지): k=5, stride=1 기준 ① Δ평균 ≥ +0.50%p/세션 ② 5시드 중 ≥4 양수 "
                "③ 최소 1시드 날짜 t ≥ 2 ④ **leave-one-seed-out: 어느 한 시드를 빼도 Δ평균 ≥ +0.50**(신설 — CG439 의 단일시드 지배 차단) "
                "⑤ 비중첩 stride=5 에서도 Δ평균 > 0(부호 유지, 유의성 요구 안 함: 세션 5개). ①~⑤ 전부 → '상대 엣지 실재'로 축 확정, "
                "실매매 반영은 별도 승인. ④ 또는 ⑤ 실패 → '모델 랭킹 상대 엣지 없음'으로 축을 닫고 데이터 축 단독으로 간다."
            ),
            "expected": "Δ평균 +0.4~+0.9, seed3 제외 시 +0.2~+0.6 → ④ 미달로 축 종결될 가능성이 높다고 본다.",
            "cost": "학습·채점 5시드×2모델 ≈ 70분 + 채점 5분",
            "created": "2026-11-23T05:45:00+09:00",
        })

    if "CG441" not in idx:
        items.append({
            "id": "CG441",
            "kind": "diagnostic",
            "owner": "engineer",
            "affects_model": False,
            "priority": 1,
            "status": "backlog",
            "metric": "feature_lever_inventory",
            "est_minutes": 10,
            "title": "[새 레버 인벤토리] 기존 패널 안에 '미시도 신호'가 남아 있는가 — 계약 173 vs 패널 유효 63 교차 (측정 완료, 결론: 없음 → 신규 원천 필요)",
            "arm": "panel_prod200_v6 시간가변 피처",
            "counterfactual": "챔피언 계약 173피처(이미 사용 중)",
            "command": "python3 scripts/_cg440_candidates.py  (입력: services/xgboost-ml/reports/overnight/cg407_panel_screen.json)",
            "hypothesis": "'AUC 가 22사이클 정체'의 원인이 축 선택이 아니라 후보 부재인지 확인한다. 패널에 있으나 계약에 없는 시간가변 피처에 |IC t|≥2 신호가 남아 있으면 즉시 A/B 대상이다.",
            "evidence": (
                "실측(2026-11-23 05:4x, cg407_panel_screen 221피처): 누수 0 · 종목상수 31 · 시장레벨 55 · 무정보 151 → "
                "유효 시간가변 63개, 그중 계약 미포함 22개. |ic_t|≥2.0 은 36개인데 **계약 미포함분은 전부 SNS 계열**"
                "(sns_post_count fill 3%, sns_author_quality_score_best_lag fill 4%, sns_sentiment_score_best_lag fill 4%)이고 "
                "이미 CG76 에서 희소 피처 선별 짝 Δ−0.0012 로 종결됐다. 계약 밖 고유 신호 = 0."
            ),
            "success": "판정: '기존 패널 내 미시도 신호 0개' 확정 → 새 레버는 신규 시점정합 원천(분봉 인트라데이 CG388 · 프로그램매매 / 공매도잔고 XR41 · KRX-01 복구)에서만 나온다. 승인 요청 근거로 사용.",
            "expected": "신규 원천 없이는 AUC 문턱(+0.02) 돌파 불가.",
            "created": "2026-11-23T05:45:00+09:00",
        })

    if dry:
        print("[dry-run] CG439 status=done, CG440/CG441 present:",
              [i for i in ("CG440", "CG441") if i in idx] or "would add")
        return
    with open(PATH, "w", encoding="utf-8") as f:
        json.dump(d, f, ensure_ascii=False, indent=2)
        f.write("\n")
    print("기록 완료 · 항목 수", len(items))


if __name__ == "__main__":
    main(dry="--dry-run" in sys.argv)

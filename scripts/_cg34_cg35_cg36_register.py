#!/usr/bin/env python3
"""2026-09-29 틱 등록 — CG34(배포 챔피언 OOS 분해, 완료) · CG35(클린 컷오프 A/B) · CG36(h1 OOS).

배경: 이 틱에서 백로그에 실행 가능한 pending 이 없었고, CG31(창평균 0.5302)이 **학습구간
겹침**으로 오염된 값임을 실측했다(OOS 0.4914 vs 겹침 0.5883). 그 후속으로
① 프로토콜을 코드로 고정(champion_robust_eval --train-start/--train-end)
② 클린 컷오프 A/B 설계(CG35)
③ 지금 바로 돌릴 수 있는 짧은 항목(CG36, h=1 자기과제 OOS)
를 등록한다.
"""
import json
import os
from datetime import datetime, timedelta, timezone

REPO = "/home/jhshi/analyist_dd"
BACKLOG = os.path.join(REPO, "docs/QUANT_MODEL_BACKLOG.json")
LEDGER = os.path.join(REPO, "data/reports/model_engineer_ledger.jsonl")
KST = timezone(timedelta(hours=9))
NOW = datetime.now(KST).isoformat(timespec="seconds")

bl = json.load(open(BACKLOG))
items = bl["items"]
by_id = {i["id"]: i for i in items}


def upsert(item):
    if item["id"] in by_id:
        by_id[item["id"]].update(item)
    else:
        items.append(item)


# ── CG34: 배포 챔피언 견고 AUC 의 학습구간 오염 분해 (이번 틱에 완료) ────────────────
upsert({
    "id": "CG34",
    "title": "배포 챔피언 견고 AUC 의 학습구간 오염 분해 — OOS 0.4914 vs 학습구간 겹침 0.5883",
    "status": "done",
    "priority": 1,
    "affects_model": True,
    "baseline": {"value": 0.5302, "source": "CG31 실측(5창 전부 평균) — 오염된 값"},
    "hypothesis": "CG31 의 창평균 0.5302 는 배포 챔피언의 실제 OOS 성능이 아니다. 챔피언은 "
                  "2026-06-25~09-23 을 학습했고(프로덕션 명령 --days 90), 평가창 5개 중 2개"
                  "(창4 05-29~07-20·창5 07-28~09-15)가 그 구간과 겹친다 → 겹친 창만 부풀려져 있다.",
    "evidence": "DB 실측: 같은 200종목 유니버스에서 06-25~09-23 = 12,600행 ≈ meta n_rows 12,629 · "
                "robust_auc.json 의 model_aucs(0.5492/0.5448/0.5185)가 "
                "training-result-20260923-151854.json 과 일치 → 배포 모델 = 09-23 학습본.",
    "method": "재실행 없이 창 재집계(창은 서로 독립 표본 — 한 창을 채점하는 데 다른 창이 필요 없다). "
              "검증 스크립트: scripts/_cg34_oos_decompose.py. 동시에 champion_robust_eval 에 "
              "--train-start/--train-end 를 신설해 다음부터 자동 배제(테스트 19/19 PASS: "
              "scripts/_robust_oos_test.py).",
    "command": "python3 scripts/_cg34_oos_decompose.py",
    "result": "학습구간 밖(창1~3) 0.4914±0.0351 [0.5399, 0.4763, 0.4580] — 3창 중 1창만 0.5 초과 · "
              "학습구간 겹침(창4~5) 0.5883 [0.6013, 0.5754] · 오염 +0.0969",
    "metric": "champion_robust_eval_oos",
    "success": "분해 자체(완료) — 판정은 '오염이 존재한다'로 확정",
    "conclusion": "배포 챔피언은 **학습구간 밖에서 동전 이하(0.4914)** 다. 따라서 ① 기록 승격 기준선 "
                  "0.5513(단일분할 val) ② scoreboard 기준선 0.5406(49종목 arm) ③ CG31 0.5302(오염 평균) "
                  "— 셋 다 배포 성능의 근거가 될 수 없다. 승격 게이트 기준선을 '엄격 OOS 다중창'으로 "
                  "교체하는 것은 사용자 승인 대상이다.",
    "closed_at": NOW,
    "closed_by": "quant-model-engineer",
})

# ── CG35: 고정 컷오프 클린 대조군 + 엄격 OOS A/B (아직 pending 으로 올리지 않는다) ───
upsert({
    "id": "CG35",
    "title": "고정 컷오프(2026-05-20) 클린 대조군 학습 + 엄격 OOS A/B — 챔피언의 0.5883 은 암기인가",
    "status": "backlog",
    "priority": 4,
    "affects_model": True,
    "depends_on": ["U3"],
    "baseline": {"value": 0.5883,
                 "source": "CG34 분해: 배포 챔피언의 학습구간 겹침 창(창4·5) 성적"},
    "hypothesis": "배포 챔피언이 학습구간 겹침 창에서 기록한 0.5883 은 **암기** 때문이다. 같은 아키텍처"
                  "(xgboost+lgb+catboost 앙상블, 현행 레시피)를 컷오프를 과거(2026-05-20)로 고정해 "
                  "학습하면, 같은 두 창(05-29~07-20·07-28~09-15)에서 0.5 근처로 떨어진다.",
    "evidence": "CG34: OOS 0.4914 vs 겹침 0.5883(오염 +0.0969) · 창4는 학습구간의 약 40%, 창5는 100%. "
                "retrain_champion 에 --end-date/--start-date 신설(meta 에 data_start/data_end 기록) · "
                "champion_robust_eval 에 --train-start/--train-end 신설(겹치는 창 자동 제외, 19/19 PASS).",
    "method": "① `retrain_champion --days 90 --end-date 2026-05-20 --stock-limit 200 --out-dir "
              "app/models/scratch_ctl_20260520`(champion/ 무수정 — 하드룰 #4) ② 같은 런에서 "
              "`champion_robust_eval --model-dir <scratch> --train-start 2026-02-19 --train-end 2026-05-20` "
              "→ 창1(앞) + 창4·5(이후)가 OOS 로 채점된다 ③ 배포 챔피언의 같은 창 값(0.6013/0.5754)과 비교.",
    "command": "docker exec stock_xgboost_ml sh -c 'cd /app && OMP_NUM_THREADS=2 timeout 20000 python -m app.training.retrain_champion --days 90 --end-date 2026-05-20 --stock-limit 200 --out-dir /app/app/models/scratch_ctl_20260520 && OMP_NUM_THREADS=2 timeout 5400 python scripts/champion_robust_eval.py --model-dir /app/app/models/scratch_ctl_20260520 --folds 5 --dates-per-fold 10 --stocks 80 --train-start 2026-02-19 --train-end 2026-05-20 --out /app/reports/ctl_clean_oos.json'",
    "check": "docker exec stock_xgboost_ml cat /app/reports/ctl_clean_oos.json",
    "check_target": {"op": ">=", "value": 1},
    "metric": "champion_robust_eval",
    "est_minutes": 260,
    "success": "컷오프 모델의 창4·5 AUC 평균이 챔피언 0.5883 대비 −0.05 이상 낮게 나오면 '암기' 확정. "
               "비슷하게 나오면 '최근 구간에서 모델이 실제로 낫다'는 뜻이므로 라벨 정렬·재학습 주기 레버가 열린다.",
    "expected": "컷오프 모델 창4·5 ≈ 0.50~0.54 (챔피언 0.5883 은 자기 학습구간 값이므로)",
    "note": "**지금 pending 으로 올리지 않는다** — 평일 저녁 19:00 틱이 집어가면 20:35 U3 런처 창을 "
            "막아 995일 패널(2밤째)을 또 잃는다. panel_995.npz 완성(U3 done) 후 pending 승격.",
    "risk": "학습 3.2h + 평가 0.5h = 밤 한 창(20:35~09:00) 안에 들어간다. 유니버스는 '현재 유동성 상위 "
            "200종목'으로 뽑히므로 과거 구간 학습에 미래 정보가 섞이는 약한 편향이 있다(패널 실험도 동일).",
})

# ── CG36: 배포 챔피언의 '자기 학습 과제(h=1)' OOS 엣지 (짧은 슬롯용, 실행 가능) ─────
upsert({
    "id": "CG36",
    "title": "배포 챔피언은 자기 학습 과제(h=1 방향)에서는 OOS 엣지가 있는가 — 짧은 슬롯(≈12분)",
    "status": "pending",
    "priority": 6,
    "affects_model": True,
    "baseline": {"value": 0.4914,
                 "source": "CG34 실측: 배포 챔피언 학습구간 밖 다중창 AUC(h=5 시장상대 중앙값)"},
    "arm": "champion_robust_eval --horizon 1 (게이트·모델 무수정)",
    "counterfactual": "같은 런 --horizon 5 (= 0.4914, CG34)",
    "hypothesis": "챔피언은 h=1 '방향' 라벨로 학습됐는데 평가는 계속 h=5 시장상대 라벨로 해 왔다"
                  "(과제 불일치). 자기 학습 과제(h=1)로 물으면 OOS 엣지가 살아난다 — 살아나면 "
                  "'라벨 정렬'이 배포 가능한 레버이고, 0.5 근처면 모델 자체에 OOS 엣지가 없는 것이다.",
    "evidence": "전 프로토콜이 h=5 시장상대 라벨(스윕·CG31·CG34). 챔피언 라벨은 `_create_labels` 의 "
                "1일 선행 종가 방향. 두 라벨이 다른 과제인데 같은 숫자(0.53/0.49)로 논의돼 왔다.",
    "method": "같은 프로토콜·같은 OOS 창(창1~3, 학습구간 2026-06-25 이후 제외)에서 horizon 만 1로 바꾼다. "
              "5폴드 × dates-per-fold 3 × 80종목 → 9 날짜표본 ≈ 12분. --write 없음(champion/ 무수정).",
    "command": "docker exec stock_xgboost_ml sh -c 'cd /app && OMP_NUM_THREADS=4 timeout 2700 python scripts/champion_robust_eval.py --horizon 1 --folds 5 --dates-per-fold 3 --stocks 80 --train-start 2026-06-25 --train-end 2026-09-23 --out /app/reports/champion_oos_h1.json'",
    "check": "docker exec stock_xgboost_ml cat /app/reports/champion_oos_h1.json",
    "check_target": {"op": ">=", "value": 1},
    "metric": "champion_robust_eval",
    "est_minutes": 30,
    "success": "h=1 OOS AUC ≥ 0.52 → 과제 불일치가 병목(라벨 정렬 레버 열림, 배포 가능). "
               "0.49~0.51 → 모델 자체에 OOS 엣지 없음 → 새 레버(데이터·유니버스) 승인 근거가 확정된다.",
    "expected": "미지 — 어느 쪽이든 다음 수가 정해진다(둘 다 유효한 판정)",
    "note": "CG34 로 창 배제 로직이 코드에 들어갔으므로 이 실행 자체가 신규 경로의 실전 검증이 된다.",
})

bl["updated_at"] = NOW
json.dump(bl, open(BACKLOG, "w"), ensure_ascii=False, indent=2)
print("backlog updated:", len(items), "items")

# ── 원장 기록 (per_exp 를 만들지 않는다 — scoreboard 오염 방지) ────────────────────
rec = {
    "ts": NOW,
    "id": "CG34",
    "title": "배포 챔피언 견고 AUC 의 학습구간 오염 분해 — OOS 0.4914 vs 학습구간 겹침 0.5883",
    "rc": 0,
    "elapsed_min": None,
    "log": "data/reports/me_cycle/champion_robust_eval.log (CG31 원자료 재집계)",
    "metric": "champion_robust_eval_oos",
    "parsed": {
        "measured_at": "2026-09-29T08:26:29",
        "model_dir": "app/models/champion",
        "protocol": "5-fold 연속 시간창, h=5 시장상대 중앙값 라벨, 크로스섹션 AUC, purge=5거래일",
        "train_start": "2026-06-25",
        "train_end": "2026-09-23",
        "train_window_evidence": ("프로덕션 명령 full_pipeline_dd.sh L311 `retrain_champion --days 90 "
                                  "--stock-limit 200` + DB 실측 200종목 06-25~09-23 = 12,600행 ≈ "
                                  "meta n_rows 12,629 · robust_auc.json model_aucs 일치"),
        "oos_only": True,
        "windows": [
            {"fold": 1, "window": ["2025-11-28", "2026-01-20"], "n_dates": 10, "auc_mean": 0.5399, "oos": True},
            {"fold": 2, "window": ["2026-01-28", "2026-03-23"], "n_dates": 10, "auc_mean": 0.4763, "oos": True},
            {"fold": 3, "window": ["2026-03-31", "2026-05-20"], "n_dates": 10, "auc_mean": 0.458, "oos": True},
            {"fold": 4, "window": ["2026-05-29", "2026-07-20"], "n_dates": 10, "auc_mean": 0.6013, "oos": False},
            {"fold": 5, "window": ["2026-07-28", "2026-09-15"], "n_dates": 10, "auc_mean": 0.5754, "oos": False},
        ],
        "oos_mean": 0.4914,
        "oos_std": 0.0351,
        "overlap_mean": 0.5883,
        "inflation": 0.0969,
        "all_windows_mean": 0.5302,
        "rows_scored": 3755,
        "dates_scored": 50,
        "note": "per_exp 미생성(scoreboard._rec_best_mean 오염 방지 — CG31 사고 재발 금지)",
    },
    "verdict": "기준선 실측",
    "detail": ("배포 챔피언 학습구간 밖(창1~3) 0.4914±0.0351 [0.5399, 0.4763, 0.4580] · 학습구간 겹침"
               "(창4·5) 0.5883 [0.6013, 0.5754] · 오염 +0.0969 → 창평균 0.5302 는 OOS 성능이 아니다. "
               "기록 기준선 0.5513(단일분할)·scoreboard 0.5406(49종목 arm) 도 배포 성능 근거 불가."),
    "reported": True,
}
with open(LEDGER, "a") as f:
    f.write(json.dumps(rec, ensure_ascii=False) + "\n")
print("ledger appended:", rec["id"], rec["verdict"])

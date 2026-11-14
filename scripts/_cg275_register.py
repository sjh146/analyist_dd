#!/usr/bin/env python3
"""CG275 를 pending 으로 승격한다(셋업 구현 완료 기록) — 2026-11-14 엔지니어 자율."""
import json
import os

P = "/home/jhshi/analyist_dd/docs/QUANT_MODEL_BACKLOG.json"
b = json.load(open(P, encoding="utf-8"))
it = next(i for i in b["items"] if i["id"] == "CG275")
it["status"] = "pending"
it["metric"] = "insample_gap"
it["summary_out"] = "services/xgboost-ml/reports/overnight/cg275_insample_gap.json"
it["command"] = "bash scripts/cg275_insample_gap.sh"
it["paired_reference"] = "CO_core30_h5"
it["gap_ceiling"] = 0.30
it["gap_floor"] = 0.10
it["est_minutes"] = 10
it["setup_needed"] = (
    "해소(2026-11-14 17:0x 엔지니어 자율) — 별도 스크립트 신설 대신 **기존 스윕에 플래그**를 넣었다: "
    "`wf_label_sweep.py --report-train-auc` 가 폴드별 train_auc(학습셋 예측 AUC·시드평균 확률)를 요약 folds[] 에 남기고"
    "(기본 OFF = 기존 실행 비트 동일 · 스모크 2폴드×1시드 실측 train 0.6419/0.8719 vs OOS 0.5289/0.5456 로 동작 확인), "
    "`scripts/_insample_gap_report.py` 가 그 요약에서 갭 분포를 계산해 cg275_insample_gap.json 을 쓴다. "
    "구동기 metric insample_gap 파서·판정기도 배선(경계 자체검증: 갭 0.05→용량 / 0.35→정보상한). "
    "2 arm(CO_core30_h5 대조 · CO_smooth_d1_h5 저용량)으로 용량 gradient 를 함께 본다.")
it["added_20261114"] = "arm 2종: CO_core30_h5(대조) · CO_smooth_d1_h5(depth1+스무딩 = 저용량)"
b["updated_at"] = "2026-11-14T17:10:00+09:00"
tmp = P + ".tmp"
with open(tmp, "w", encoding="utf-8") as f:
    json.dump(b, f, ensure_ascii=False, indent=1)
os.replace(tmp, P)
print("ok", it["status"], it["metric"], it["command"], it["est_minutes"])

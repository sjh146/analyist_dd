#!/usr/bin/env python3
"""백로그에 TR1(횡단면 변환 축)·MK1(시장레벨/상수 정리 + rank 결합) 추가 (2026-09-26 06시 틱).

왜 이 두 축인가
  HP4 로 내부 축(라벨·피처풀·유니버스·HP·앙상블·프로토콜·선별크기)이 소진됐다.
  그런데 wf_label_sweep 의 `transform`(rank/z-score) 노브는 **TR_* 6개 config 가 정의만 되고
  한 번도 실행된 적이 없다**(전 로그 grep 'AUC mean' 결과에 TR_* 가 없음 — 실측 확인).
  pooled AUC ≈ 날짜별 AUC ≈ 0.54 로 정체된 상황에서 '날짜별 순위로 바꾸는 것'은
  이 스택에서 가장 큰 미측정 노브다.
  MK1 은 누수 계약 #3(시장레벨 제외) 상태를 유지한 채 성능을 재는 조합으로, 채택되면
  '계약을 지키면서도 성능이 유지된다'는 근거가 된다(계약 위반 상태의 0.5412 와 비교).
"""
import json
import os
import shutil
import time

P = os.path.abspath(os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                 "..", "docs/QUANT_MODEL_BACKLOG.json"))
with open(P, encoding="utf-8") as f:
    b = json.load(f)
by = {i["id"]: i for i in b["items"]}

NEW = [
    {
        "id": "TR1",
        "title": "횡단면 변환 축: rank / z-score 정규화 (TR_* 6개 config 전부 미측정)",
        "status": "pending",
        "priority": 1,
        "arm": "TR_rank_h5",
        "counterfactual": "LS_quant_q30_h5",
        "hypothesis": ("패널 210피처 중 27개는 날짜 내 종목간 값이 같고(시장레벨), 스케일도 "
                       "원/비율/지수가 뒤섞여 있다. 학습 전에 날짜별 rank(pct) 또는 z-score 로 "
                       "바꾸면 종목간 비교 가능한 형태가 되어 폴드 평균이 기준선 대비 +0.02 이상 "
                       "오르는가? (각 날짜의 다른 종목 값만 쓰므로 미래정보 누수는 아니다.)"),
        "evidence": ("전 로그 grep 결과 TR_rank_h5·TR_zscore_h5·TR_rank_h5_t40/t60·TR_rank_h3/h6 "
                     "의 AUC 기록이 하나도 없다 = 정의만 있고 미측정. 기록 기준선은 LS_quant_q30_h5 "
                     "0.5406±0.0316 (5폴드×3시드, panel_420_asofpatch, rows=8184)."),
        "command": ("docker exec stock_xgboost_ml sh -c 'cd /app && OMP_NUM_THREADS=4 timeout 3600 "
                    "python -u scripts/wf_label_sweep.py --panel /app/app/models/wf/panel_420_asofpatch.npz "
                    "--folds 5 --seeds 3 --only LS_quant_q30_h5,TR_rank_h5,TR_zscore_h5'"),
        "metric": "wf_sweep_summary",
        "success": "arm(TR_rank_h5) 폴드 평균이 같은 런 대조군(LS_quant_q30_h5) 대비 +0.02 이상",
        "expected": "미지(양방향 가능) — rank 변환은 시장레벨 성분을 지우므로 개선·악화 모두 해석 가능",
        "cost": "약 10분 (3 config × 5폴드 × 3시드 = 45 학습)",
        "est_minutes": 20,
        "risk": ("rank 변환은 시장레벨 피처를 날짜내 상수로 만들어 정보를 지운다 — MK_nomkt_h5 가 "
                 "0.5412(기준과 동일)였던 것과 같은 이유로 '효과 없음'이 나올 수 있다."),
        "note": "TR1 이 노이즈면 TR_rank_h5_t40/t60(선별 문턱 결합)은 탐색으로만 돌리고 축을 닫는다.",
    },
    {
        "id": "MK1",
        "title": "누수 계약 #3 준수 조합: 시장레벨 제외 + 상수/시간가변 선택 + rank 결합",
        "status": "pending",
        "priority": 2,
        "arm": "MK_timevary_nomkt_h5",
        "counterfactual": "LS_quant_q30_h5",
        "hypothesis": ("시장레벨(날짜내 종목간 동일값) 피처를 제외한 상태에서, ① 시간가변만 쓰고 "
                       "② rank 변환까지 걸면 계약을 지키면서 성능이 유지(+0.02 이상)되는가? "
                       "현재 기준선 0.5412 는 계약 위반 상태(시장레벨 포함)의 값이다."),
        "evidence": ("MK_nomkt_h5 0.5412 = 기준선과 완전 동일(시장레벨 제외가 성능을 바꾸지 않음). "
                     "PO_timevary 0.5298 · PO_const 0.5335 로 풀 제한은 각각 −0.0114/−0.0077. "
                     "MK_timevary_nomkt_h5·PO_timevary_rank_h5·CO_core30_h8 은 미측정."),
        "command": ("docker exec stock_xgboost_ml sh -c 'cd /app && OMP_NUM_THREADS=4 timeout 3600 "
                    "python -u scripts/wf_label_sweep.py --panel /app/app/models/wf/panel_420_asofpatch.npz "
                    "--folds 5 --seeds 3 --only LS_quant_q30_h5,MK_timevary_nomkt_h5,PO_timevary_rank_h5,CO_core30_h8'"),
        "metric": "wf_sweep_summary",
        "success": "arm(MK_timevary_nomkt_h5) 폴드 평균이 같은 런 대조군 대비 +0.02 이상",
        "expected": "−0.01 ~ +0.01 (풀 제한 축은 이미 두 번 악화로 측정됨)",
        "cost": "약 12분 (4 config × 5폴드 × 3시드 = 60 학습)",
        "est_minutes": 25,
        "risk": "이 축이 또 노이즈면 '내부 축 전부 소진'이 확정되고, 남는 레버는 데이터 확장뿐이다(승인 대상).",
        "note": "채택돼도 계약 준수 상태의 성능이므로 승격 판정에는 영향이 없다(기준선 재설정은 승인 대상).",
    },
]

for it in NEW:
    if it["id"] in by:
        by[it["id"]].update(it)
        print(it["id"], "갱신")
    else:
        b["items"].append(it)
        print(it["id"], "추가")

shutil.copy(P, P + ".bak_" + time.strftime("%Y%m%d-%H%M%S"))
b["updated_at"] = time.strftime("%Y-%m-%dT%H:%M:%S+09:00")
with open(P, "w", encoding="utf-8") as f:
    json.dump(b, f, ensure_ascii=False, indent=1)
print("pending:", [i["id"] for i in b["items"] if i.get("status") == "pending"])

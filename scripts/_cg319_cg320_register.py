"""CG319/CG320 백로그 등록 — 계측기 널 바닥 실측 결과 + 원인 분해 항목.

CG319 (done): 피처-라벨 순열 패널에서 등록 기준선 arm 의 폴드 AUC = 널 바닥.
CG320 (pending): 그 편향을 만드는 단계 지목(선별·시드·시장레벨은 이미 기각).
"""
import json
from datetime import datetime, timedelta, timezone

P = "docs/QUANT_MODEL_BACKLOG.json"
KST = timezone(timedelta(hours=9))
now = datetime.now(KST).isoformat(timespec="seconds")

b = json.load(open(P, encoding="utf-8"))
items = b["items"]
ids = {i.get("id") for i in items}

cmd = (
    "bash scripts/_cg319_null_seeds.sh  # 널 6실현(각 5폴드×3시드·LS_quant_q30_h5) "
    "→ /app/reports/overnight/cg319_null_s*.json"
)

cg319 = {
    "id": "CG319",
    "title": "[측정정합성] 계측기 널 바닥 — 피처-라벨 순열 패널(라벨·유니버스 고정, 피처만 날짜내 교란)에서 "
             "등록 기준선 arm 의 폴드 AUC 는 0.5 인가",
    "status": "done",
    "priority": 0,
    "owner": "model_engineer",
    "kind": "measurement",
    "hypothesis": "정보가 0 인 패널에서 워크포워드 폴드 AUC 는 0.50 이어야 한다. 0.50 을 넘는 크기가 곧 "
                  "계측기(파이프라인) 편향이며, 사전문턱 +0.02 의 해석 한계를 정한다.",
    "command": cmd,
    "counterfactual": "같은 패널·같은 폴드·같은 시드의 원본 런 — LS_quant_q30_h5 0.5412±0.0327 "
                      "(등록 기준선 0.5406 재현, Δ+0.0006)",
    "success": "널 폴드평균이 [0.48, 0.52] 안이면 계측기 무편향(이후 Δ 보고 유효). 벗어나면 편향으로 "
               "규정하고 원인 분해 항목(CG320)을 연다 — 어느 쪽이든 판정 근거는 숫자로 남긴다.",
    "est_minutes": 15,
    "metric": "wf_sweep_summary",
    "summary_out": "/app/reports/overnight/cg319_null_summary.json",
    "created_at": now,
    "updated_at": now,
    "note": "2026-11-17 07:1x 엔지니어 자율(장외 틱). 계기: 최근 24시간에 측정 버그 3건(CG314 요약경로 "
            "오지정·CG318 짝 키 (fold,date) 오짝·CG306 폴드수 의존)이 나왔다. 19사이클 연속 '기준선 "
            "+0.02 미달'을 보고하는 계측기가 정보 0 에서 무엇을 내는지 아무도 재본 적이 없어 착수. "
            "순열 패널 생성기 scripts/_cg319_null_panel.py(자체점검 5/5 PASS · 진단 피처0↔price 상관 "
            "0.9117→0.0126 로 교란 확인) · 널 실행 scripts/_cg319_null_seeds.sh · 단일피처 부검 "
            "scripts/_cg319_feature_auc_check.py.",
    "result": {
        "널_실현6": {"means": [0.5158, 0.5134, 0.5093, 0.5121, 0.5022, 0.5153],
                     "mean": 0.5113, "std": 0.0049, "min": 0.5022, "max": 0.5158,
                     "모두_0.5_초과": True},
        "대조군_같은런": {"auc": 0.5412, "std": 0.0327,
                          "folds": [0.5251, 0.5189, 0.5564, 0.5981, 0.5075],
                          "등록기준선": 0.5406, "재현오차": 0.0006},
        "기각된_원인": {
            "시드앙상블": "널·seeds=1 0.5174 (seeds=3 0.5158) → 차이 없음",
            "선별(edge top30)": "널·core48 전량(선별 없음) 0.5086 → 선별 없이도 편향 유지",
            "시장레벨피처": "널·MK_nomkt_h5 0.5158 = LS arm 과 비트 동일(n_market_level_excluded=0 → 선별에 없음)",
        },
        "순열_유효성": {"단일피처_널_edge_mean": 0.0023, "p95": 0.0075, "max": 0.0167,
                        "널_edge>=0.044_피처수": 0, "대조_top": "volatility_volume 0.5563 → 널 0.5007"},
        "판정": "계측기 널 바닥 = +0.0113 (0.50 대비). 순열은 유효(피처 잔존정보 0)하므로 편향은 파이프라인 측. "
                "따라서 절대 AUC 해석(0.55 = 스킬)은 ~0.011 과대이고, 사전문턱 +0.02 는 널 편향의 1.8배다. "
                "단 Δ(짝) 판정은 편향이 arm 간 공통이라 상쇄 → 19사이클 결론은 불변.",
    },
}

cg320 = {
    "id": "CG320",
    "title": "[측정정합성] 널 편향 +0.011 의 원인 분해 — 학습·지표 경로 중 어느 단계가 만드는가 "
             "(선별·시드수·시장레벨은 기각)",
    "status": "pending",
    "priority": 1,
    "owner": "model_engineer",
    "kind": "measurement",
    "hypothesis": "CG319 의 널 0.5113 은 피처에 정보가 없는데도 나온다. 남은 후보는 ① 균형 오버샘플링 후 "
                  "0.67 시간분할(train/val 중복 양성) ② 폴드 창 겹침(테스트 46일 창이 서로 겹쳐 같은 "
                  "순열 실현이 반복 계상) ③ 라벨 정의(꼬리 q0.30 이 날짜내 30/30 인데 폴드 AUC 는 창 전체 "
                  "pooled) ④ 모델 점수의 날짜 레벨 오프셋. 하나를 끄면 널이 0.50 으로 돌아와야 한다.",
    "command": "bash scripts/_cg320_null_bisect.sh",
    "counterfactual": "널 패널·선별없음 0.5086 · 널·선별top30 0.5158 (같은 순열 실현, CG319)",
    "success": "① 후보 4개 중 1개를 지목하고 그 단계를 끈 arm 의 널 폴드평균이 0.50±0.01 로 복귀 "
               "② 지목 단계의 수리 후 등록 기준선·Δ 를 재측정해 결론 불변/변경을 숫자로 남긴다 "
               "③ 지목 실패 시 '원인 미규명'으로 기록하고, 보고는 Δ(짝)만 쓰고 절대 AUC 는 널 보정값을 병기한다.",
    "est_minutes": 240,
    "metric": "wf_sweep_summary",
    "summary_out": "/app/reports/overnight/cg320_null_bisect.json",
    "created_at": now,
    "updated_at": now,
    "note": "2026-11-17 07:2x~07:5x 엔지니어 자율(CG319 직후). ⚠ est_minutes 는 **전체 소요**다: 같은 패널·같은 부하에서 5폴드 quant arm 50초 vs 10폴드·relative arm 900초 초과(rc=124 두 건 = A·B 소실) → arm 별 timeout 을 2700초로 올렸고 est 240분으로 잡아 오전 틱(08:07)이 착수하지 않게 한다(개장 전 CPU 보호). 남은 미측정 가설: 폴드 폭(A)·상대 라벨(B). "
            "⚠ 판정은 폴드평균(널)이 0.50 근처인지로만 하고, 절대 AUC 문턱(+0.02)으로 판정하지 말 것 — "
            "이 항목의 대상이 바로 그 문턱의 해석이다.",
}

added = []
for it in (cg319, cg320):
    if it["id"] in ids:
        # 이미 있으면 결과만 갱신
        for i, old in enumerate(items):
            if old.get("id") == it["id"]:
                old.update(it)
                added.append(f"updated {it['id']}")
    else:
        items.append(it)
        added.append(f"added {it['id']}")

b["updated_at"] = now
b["updated"] = now
json.dump(b, open(P, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
print(added, "total items", len(items))

#!/usr/bin/env python3
"""2026-11-15 18:5x 백로그 갱신 — CG285/CG286/CG287/CG241/CG282 결과 기록.

원칙: 판정·수치는 요약 JSON 실측에서 직접 옮긴다(로그 문구 자기신고 금지).
"""
import json
import sys
from datetime import datetime, timedelta, timezone

P = "/home/jhshi/analyist_dd/docs/QUANT_MODEL_BACKLOG.json"
KST = timezone(timedelta(hours=9))
NOW = datetime.now(KST).replace(microsecond=0).isoformat()

b = json.load(open(P))
items = b["items"] if isinstance(b, dict) else b
by = {i["id"]: i for i in items}

CG285 = {
    "verdict": "노이즈",
    "delta": 0.0001,
    "detail": (
        "같은 런 5폴드×3시드(panel_420_tv · 49종목 · 281거래일): 대조군 0.5518±0.0216 "
        "[0.5595,0.5369,0.5481,0.5888,0.5258] vs 처리군(tv 4종 gate_add) 0.5520±0.0173 "
        "[0.5625,0.5377,0.5456,0.5803,0.5338] → 짝 Δ +0.0001 (폴드 [−0.0085,−0.0025,+0.0008,+0.0030,+0.0080], "
        "승률 3/5). 사전문턱 +0.02 의 1/200 수준 = 노이즈. "
        "선별 진입 4/4(모든 폴드) · 선별 34개 = edge top30 + 강제 4. "
        "누수 게이트: 단일피처 AUC 0.4716~0.4976(<0.75) · 종목상수 ratio ≤0.021 · 절단 재계산 자기검정 8/8 PASS. "
        "**부수 검증**: 대조군이 CG280 의 CO_smooth_d1_h5(0.5518±0.0216)와 4자리 동일 → 4열 가산이 패널을 "
        "오염시키지 않았음이 증명됨(같은 패널에서 A/B 를 재는 프로토콜이 성립)."
    ),
}
CG286 = {
    "verdict": "악화(축 종결)",
    "delta": -0.0549,
    "detail": (
        "같은 런 5폴드×3시드(panel_420_tv): 대조군(전체 행 학습) 0.5518±0.0216 [0.5595,0.5369,0.5481,0.5888,0.5258] "
        "→ liq1(학습에서 거래대금<10억 제외) 0.4970±0.0145 [0.5139,0.5051,0.4877,0.4734,0.5047] Δ−0.0549(0/5) "
        "→ liq3(<30억 제외) 0.4919±0.0246 [0.5004,0.4635,0.4621,0.5201,0.5136] Δ−0.0599(0/5). 단조 반응. "
        "학습행 감소: fold1 1,230→384(10억)/264(30억) · fold5 6,690→2,471/1,838. "
        "**체결가능 부분집합 재채점**(같은 덤프, CG283 측정기): 대조군 0.5530→0.5543 · liq1 0.4979→0.5115 · "
        "liq3 0.4887→0.5096 → 체결가능 표본에서도 대조군 대비 −0.0428 / −0.0447 로 여전히 악화. "
        "즉 '살 수 있는 행'으로 학습 도메인을 좁히면 어느 표본에서도 순위가 나빠진다 → "
        "CG192(Δ+0.0003, 마스크 0.60%뿐)의 '검정력 없음'을 큰 마스크로 재현·확정. 학습 도메인 정합 축 종결."
    ),
}
CG287 = {
    "verdict": "악화(축 종결)",
    "delta": -0.0164,
    "detail": (
        "같은 런 5폴드×3시드(panel_420_tv·49종목): 대조군 0.5518±0.0216 [0.5595,0.5369,0.5481,0.5888,0.5258] "
        "→ wfill(가중 2.0/0.5) 0.5354±0.0181 Δ−0.0164(1/5) → wfill4(4.0/0.25) 0.5212±0.0170 Δ−0.0307(1/5). "
        "가중 강도에 단조 악화. "
        "**체결가능 부분집합 재채점**(같은 덤프, CG283 측정기 — 전 행 지표는 가중 이동에 기계적으로 불리하므로 필수): "
        "대조군 0.5530→0.5543 vs wfill 0.5364→0.5332(대조군 대비 −0.0211) · wfill4 0.5233→0.5273(−0.0270). "
        "즉 '살 수 있는 행'의 순위도 개선되지 않는다 → CG286(행 제거)의 악화는 '표본 손실' 탓이 아니라 "
        "**신호의 소재** 때문이다(illiquid 행의 변동이 순위 정보의 원천). 학습 도메인 축 종결: "
        "행 제거(CG192 Δ+0.0003·CG286 −0.055) · 가중(CG287 −0.016~−0.031) 둘 다 개선 없음. "
        "구현: make_weights(kind=fillable) 신설(기본 미지정 = 비트 동일) · 자체점검 9/9 PASS."
    ),
}
CG241 = {
    "verdict": "측정불가(구조적)",
    "detail": (
        "panel_200liq_ffill(200종목·2025-09-15~2026-11-09·217열) 실측: ffill_sns_* 관측이 "
        "**2026-05-27~2026-10-08 에만** 존재(관측행 7,644/54,497=14.0% · 관측일 91일 · 일별 100종목). "
        "폴드 테스트창 관측률 fold1 0.0% · fold2 0.0% · fold3 0.32% · fold4 39.0% · fold5 45.7% "
        "→ fold1·2 학습창에는 SNS 값이 전량 결측이라 std=0 필터가 gate_add 4종을 전부 탈락시켰다"
        "(로그 'gate_add 통과 0/4') → 하네스가 측정 전에 실패 처리(정상 동작). "
        "대조군은 정상 측정: 0.5088±0.0176(200종목·5폴드). "
        "결론: 이 축은 5폴드 확장창 프로토콜에서 **구조적으로 측정 불가** — 원인은 모델이 아니라 "
        "SNS 원천 이력이 2026-05 부터라는 데이터 사정이다(백필은 수집기 소유·승인 대상)."
    ),
}
CG282 = {
    "verdict": "측정불가(같은 뿌리)",
    "detail": (
        "CG241 실측으로 종결: 전방채움 대상 SNS 관측이 2026-05-27 이후에만 존재해 5폴드 중 2개 폴드에서만 "
        "값이 있다 → 표본 부족으로 판정 자체가 성립하지 않는다(커버리지 12.18%→14.03% 는 전체 기간 평균일 뿐, "
        "폴드 3개는 0). 원천 백필(수집기 소유·승인 대상) 없이 ffill 파라미터를 더 만지는 것은 축 재시험이다."
    ),
}

upd = {
    "CG285": (CG285, "done"),
    "CG286": (CG286, "done"),
    "CG287": (CG287, None),
    "CG241": (CG241, "done"),
    "CG282": (CG282, "done"),
}
NEW_ITEMS = [
    {
        "id": "CG286",
        "status": "done",
        "priority": 1,
        "kind": "model_domain",
        "owner": "engineer",
        "affects_model": True,
        "metric": "다중폴드 OOS AUC(5폴드×3시드)",
        "title": "[학습 도메인] '살 수 있는 행'으로만 배우면 순위가 좋아지는가 — CG192(마스크 0.6%)의 검정력 없는 실험을 71.6% 마스크로 재현",
        "hypothesis": "돈 지표의 체결성 필터가 버리는 71.6% 는 거래대금 10억 미만이다(CG283). 학습 라벨이 그 행들을 포함하면 모델은 평가 모집단(36%)에서 재현되지 않는 순위를 배운다. CG192 는 당일등락 기준이라 0.60% 만 뺐으므로 '효과 없음'이 아니라 '검정력 없음'이었다 — 유동성 기준으로 크게 잘라 다시 잰다.",
        "counterfactual": "CG286_ctl_h5 (전체 행 학습 · 같은 패널·같은 선별·같은 HP)",
        "baseline": "전체 행 학습 0.5518±0.0216 (동일 런)",
        "arm": "학습 행에서 tv_log<log1p(1e9) 제외(10억) · 감도 3e9",
        "command": "docker exec stock_xgboost_ml sh -c 'cd /app && OMP_NUM_THREADS=4 python -u scripts/wf_label_sweep.py --panel /app/app/models/wf/panel_420_tv.npz --only CG286_ctl_h5,CG286_liq1_h5,CG286_liq3_h5 --folds 5 --seeds 3 --summary-out /app/reports/overnight/cg286_sweep_summary.json'",
        "summary_out": "services/xgboost-ml/reports/overnight/cg286_sweep_summary.json",
        "success": "짝 Δ ≥ +0.02 & 폴드승률 ≥ 4/5 (그리고 체결가능 부분집합 재채점에서 대조군 0.5543 대비 ≥+0.02)",
        "expected": "Δ 0~−0.02 — 표본이 36% 로 줄어 잡음이 커지지만 방향은 개선 기대",
        "cost": "5분(기존 tv 패널 재사용 · 재학습 5폴드×3시드×3arm)",
        "est_minutes": 6,
        "ready_script": "scripts/wf_label_sweep.py::apply_train_mask_col (자체점검 scripts/_cg286_mask_test.py 6/6 PASS)",
        "depends_on": ["CG285"],
        "created_at": NOW,
        "updated_at": NOW,
        "result": CG286,
    },
    {
        "id": "CG287",
        "status": "done",
        "priority": 1,
        "kind": "model_domain",
        "owner": "engineer",
        "affects_model": True,
        "metric": "다중폴드 OOS AUC(5폴드×3시드)",
        "title": "[표본 가중] 행을 버리지 말고 '살 수 있는 행'에 가중을 줘라 — CG286(행 제거 −0.055)의 반작용 검정",
        "hypothesis": "CG286 은 학습 도메인을 유동성으로 자르면 AUC 가 0.5518→0.4970(−0.0549, 0/5)로 붕괴함을 보였다. 두 해석이 가능하다: ①신호는 illiquid 행에 있고 배포 모집단으로 전이되지 않는다 ②행을 **버리면** 표본·정보가 함께 사라진다. 가중은 행 도메인을 유지한 채 학습 신호만 fillable 쪽으로 옮기므로 ②를 반증 가능한 형태로 검정한다.",
        "counterfactual": "CG287_ctl_h5 (전체 행 동일 가중 · 같은 패널·같은 선별·같은 HP)",
        "baseline": "전체 행 동일 가중 0.5518±0.0216 (동일 런)",
        "arm": "fillable 가중 2.0/0.5 · 감도 4.0/0.25 (평균 1 정규화)",
        "command": "docker exec stock_xgboost_ml sh -c 'cd /app && OMP_NUM_THREADS=4 python -u scripts/wf_label_sweep.py --panel /app/app/models/wf/panel_420_tv.npz --only CG287_ctl_h5,CG287_wfill_h5,CG287_wfill4_h5 --folds 5 --seeds 3 --summary-out /app/reports/overnight/cg287_sweep_summary.json'",
        "summary_out": "services/xgboost-ml/reports/overnight/cg287_sweep_summary.json",
        "success": "짝 Δ ≥ +0.02 & 폴드승률 ≥ 4/5 (+ 체결가능 부분집합 재채점 ≥+0.02)",
        "expected": "Δ 0~−0.01 — 가중은 행 제거보다 완만하지만, CG283/284(체결가능 표본에서 신호 미검출)와 CG286 을 합치면 개선 기대는 낮다",
        "cost": "5분(기존 tv 패널 재사용)",
        "est_minutes": 6,
        "ready_script": "scripts/wf_label_sweep.py::make_weights(kind=fillable) (자체점검 scripts/_cg287_weight_test.py 9/9 PASS)",
        "depends_on": ["CG286"],
        "created_at": NOW,
        "updated_at": NOW,
        "result": CG287,
    },
]
for it in NEW_ITEMS:
    if it["id"] in by:
        print(f"이미 존재: {it['id']} — 갱신만")
        by[it["id"]].update({k: v for k, v in it.items() if k not in ("created_at",)})
    else:
        items.append(it)
        print(f"추가: {it['id']}")

for iid, (res, status) in upd.items():
    it = by.get(iid)
    if it is None:
        print(f"!! 없음: {iid}")
        continue
    if status:
        it["status"] = status
    it["result"] = res
    it["updated_at"] = NOW
    print(f"{iid}: status={it['status']} verdict={res['verdict']}")

NOTES = {
    "CG285": (
        " ⚠ **등록 문구 정정(2026-11-15 18:0x)**: '거래대금 피처 0개'는 부정확했다 — "
        "`krx_total_trading_value` 가 패널에 있다(비영 88.1%). 다만 median_unique_per_date=1 인 **시장레벨 집계**라 "
        "종목간 비교에 쓸 수 없다. 정확한 진술은 '종목별(횡단면) 거래대금 피처 0개'다. "
        "⚠ **구현 함정(실측)**: `exclude_names` + `gate_add` 는 **배타**다 — exclude 가 cols 에서 파생을 지워 "
        "`_der` 가 비고 '파생 피처가 필터를 통과하지 못했다'로 측정 전에 죽는다(CG241 설계가 이 조합이라 미검증). "
        "gate_add 만으로도 선별 후보에서 빠지므로(exclude 불필요) 두 실험 모두 exclude 없이 측정했다. "
        "⚠ 배포 관점: tv_* 는 횡단면 rank 라 추론이 종목 단위 스트리밍인 현행 경로에서 재현 불가 계약 이슈가 있다"
        "(rank 변환과 같은 제약) — AUC 이득이 없어 이슈화되지 않음."
    ),
    "CG286": (
        "구현: `scripts/wf_label_sweep.py::apply_train_mask_col`(기본 미지정 = 비트 동일 · 기존 실험 무영향) + "
        "config 키 train_mask_col/train_mask_gte · 자체점검 `scripts/_cg286_mask_test.py` 6/6 PASS. "
        "패널: `scripts/_cg285_tv_panel.py` → panel_420_tv.npz(214열, 원본 210열 비트동일 · 절단 재계산 8/8). "
        "실측 생존율(panel_420_tv): 10억 27.69% · 30억 19.80% — CG283 의 '체결가능 36%'와 차이는 "
        "체결성 필터가 당일등락 조건을 함께 걸기 때문(마스크는 거래대금만 본다)."
    ),
    "CG287": (
        "구현: `make_weights(kind='fillable')` 신설(기본 미지정 = 원 경로 비트 동일 · 가중 평균 1 정규화) · "
        "자체점검 `scripts/_cg287_weight_test.py` 9/9 PASS(경계·NaN·길이불일치·미지 kind·uniform 회귀). "
        "실측 가중 범위: 2.0/0.5 → [0.474,1.898] · 4.0/0.25 → [0.153,2.844]. "
        "⚠ 이 실험이 닫은 것은 '배포 모집단 쪽으로 학습 신호를 옮기면 나아지는가'이며, 남은 해석은 "
        "'신호가 illiquid 행에만 있다' 이다 — 즉 **모델 축으로는 더 나아지지 않고, 데이터 축(신규 원천·신규 피처)만 남는다**."
    ),
    "CG241": (
        "교정 이력: 종전 config 는 exclude_names+gate_add 조합이라 하네스가 측정 전에 죽는 구조였고(2026-11-15 발견), "
        "이번에 exclude 를 제거해 측정 가능한 형태로 고친 뒤 실행해 위 실측(SNS 관측 시작 2026-05-27)을 얻었다. "
        "즉 '실험이 실패했다'가 아니라 '실험 설계가 데이터 사정과 맞지 않는다'가 결론이다."
    ),
    "CG282": (
        "등록 시 가정('관측 희소 → 전방채움으로 표본 확대')은 이 패널에서 성립하지 않는다 — 관측이 '희소'가 아니라 "
        "**최근 6개월에만 존재**한다(2026-05-27~). 200종목·279일 창의 앞 2/3 는 SNS 값이 아예 없다."
    ),
}
for iid, txt in NOTES.items():
    it = by.get(iid)
    if it is not None:
        base = (it.get("note") or "").strip()
        it["note"] = (base + txt) if base else txt.strip()

b["updated_at"] = NOW if isinstance(b, dict) else b
json.dump(b, open(P, "w"), ensure_ascii=False, indent=2)
open(P, "a").write("\n")
print("saved", P)

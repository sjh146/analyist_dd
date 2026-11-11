import json

P = "/home/jhshi/analyist_dd/docs/QUANT_MODEL_BACKLOG.json"
d = json.load(open(P, encoding="utf-8"))
items = d["items"] if isinstance(d, dict) else d

CMD = ("docker exec stock_xgboost_ml sh -c 'cd /app && OMP_NUM_THREADS=4 timeout 3300 python -u "
       "scripts/wf_label_sweep.py --panel /app/app/models/wf/panel_200liq_fresh.npz --days 420 "
       "--end-date 2026-11-09 --limit 200 --market all --since 2025-09-15 --min-days 100 "
       "--order value --folds 5 --seeds 5 "
       "--only CG189_ctl_nog_h5,CG192_mask25_nog_h5,CG192_mask10_nog_h5 "
       "--summary-out /app/reports/overnight/cg192_mask_summary.json'")

item = {
    "id": "CG192",
    "priority": 1,
    "status": "pending",
    "kind": "experiment",
    "owner": "engineer",
    "affects_model": True,
    "title": ("[학습·평가 도메인 정합] 학습 라벨에서 '체결 불가 행'(당일등락 ≥ +25%)을 빼면 "
              "랭킹이 돈으로 번역되는가 — 0.25/0.10 두 마스크 vs 같은 런 대조군"),
    "hypothesis": (
        "돈 지표는 언제나 체결성 필터(당일등락 < +25%)를 건다(스킬 최우선 규칙 3). 그런데 학습 라벨은 "
        "그런 행을 그대로 포함한다 → 모델은 '살 수 없는 상승(상한가 연속)'을 예측하도록 학습되고, 그 "
        "예측력은 살 수 있는 행에서 재현되지 않는다. 실측 근거: close_gate_probe 2,251행의 필터 전 "
        "풀평균은 +1.191%/세션이지만 그 이득을 만드는 것은 +29.99% 행들이고, 필터 후 순기대는 모든 "
        "분위에서 음수(CG185 top-k 3/5/10 = -0.112/-0.163/-0.270%p · CG190 10분위 전부 음수). "
        "즉 학습 도메인 ⊃ 평가 도메인 불일치가 'AUC 0.51 · 돈 음수'의 한 축인지 처음으로 직접 잰다."),
    "metric": "wf_sweep_summary",
    "command": CMD,
    "counterfactual": "CG189_ctl_nog_h5 (같은 런·같은 패널·같은 폴드·게이트 OFF·마스크 없음)",
    "success": (
        "① 폴드 std ≤ 0.045(검정력 확보) ② 짝 Δ(mask arm − 대조군) ≥ +0.02 이고 폴드승률 ≥ 4/5 → 신호 "
        "③ 0.25/0.10 두 arm 모두 +0.02 미달이면 '학습 라벨 도메인 오염' 축을 닫는다(마스크 크기 실측 "
        "0.60%/4.17% 병기 — 크기가 작아 null 이면 그 자체가 결론) ④ rc≠0/요약 미갱신은 실행실패"),
    "expected": (
        "마스크가 학습행의 0.60%(0.25 arm)·4.17%(0.10 arm)뿐이라 기대효과 상한이 작다. 그래도 "
        "0.10 arm 이 0.25 arm 보다 큰 Δ 를 보이면 '용량-반응 있음' = 축 유지, 둘 다 Δ≈0 이면 축 종결."),
    "cost": "저 (같은 패널 재사용 · 3 config × 5폴드 × 5시드 ≈ 10분)",
    "est_minutes": 30,
    "created_at": "2026-11-11T16:4x+09:00",
    "note": (
        "2026-11-11 16:4x 엔지니어 자율(장외 틱) 신설. 구현: wf_label_sweep.py 에 "
        "apply_train_domain_mask(tr, threshold) 신설(기본 None = 비트 동일 경로, 기존 실험 무영향) + "
        "config 키 train_mask_daychg_gte. 설계 원칙: **학습 행만** 마스킹하고 테스트 행은 그대로 → "
        "두 arm 의 폴드 AUC 행 도메인이 동일해 Δ 가 비교 가능(테스트까지 빼면 서로 다른 표본 비교 = 무효). "
        "자체점검 scripts/_cg192_mask_test.py **8/8 PASS**(항등 경로·경계 +25.00% 포함 제거·NaN 첫행 보존·"
        "인덱스 정렬·실제 패널 325/54,497=0.60%·tr 전용 배선). 패널 마스크 분포 실측: ≥+5% 12.85% · "
        "≥+10% 4.17% · ≥+15% 1.81% · ≥+20% 0.96% · ≥+25% 0.60%. "
        "⚠ 판정은 같은 런 짝 Δ 로만(절대값은 패널이 달라 참고용) · ⚠ 이 축은 피처변환/HP 축(상한 +0.011)과 "
        "다르지만 '라벨 정의 변경'이므로 **채택(=프로덕션 라벨 변경)은 리뷰보드 승인 대상**이다 — "
        "이 항목은 측정까지만 한다."),
}

ids = {it.get("id") for it in items}
if "CG192" in ids:
    for it in items:
        if it.get("id") == "CG192":
            it.update(item)
            print("CG192 갱신")
else:
    items.append(item)
    print("CG192 추가")

if isinstance(d, dict):
    d["updated_at"] = "2026-11-11T16:45:00+09:00"
json.dump(d, open(P, "w", encoding="utf-8"), ensure_ascii=False, indent=2)
print("총 항목:", len(items))

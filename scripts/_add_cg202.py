#!/usr/bin/env python3
"""CG202 백로그 항목 추가 (2026-11-12 02:0x 장외 틱)."""
import json

P = "/home/jhshi/analyist_dd/docs/QUANT_MODEL_BACKLOG.json"
d = json.load(open(P, encoding="utf-8"))
items = d["items"]
if any(i["id"] == "CG202" for i in items):
    print("already present")
    raise SystemExit(0)

cmd = (
    "docker exec stock_xgboost_ml sh -c 'cd /app && OMP_NUM_THREADS=4 timeout 9000 python -u "
    "scripts/wf_anchor_sweep.py --panel /app/app/models/wf/panel_200liq_fresh.npz "
    "--arms CG202_ctl_h5,CG202_clean_h5 --asofs 2026-10-29,2026-10-30,2026-11-02 "
    "--folds 5 --seeds 5 --summary-out /app/reports/overnight/anchor_sweep/cg202_clean_vs_ctl_summary.json "
    "--tag cg202a --timeout-sec 1500' && "
    "docker exec stock_xgboost_ml sh -c 'cd /app && OMP_NUM_THREADS=4 timeout 9000 python -u "
    "scripts/wf_anchor_sweep.py --panel /app/app/models/wf/panel_200liq_fresh.npz "
    "--arms CG202_clean_h5,CG202_clean_dsns_h5 --asofs 2026-10-29,2026-10-30,2026-11-02 "
    "--folds 5 --seeds 5 --summary-out /app/reports/overnight/anchor_sweep/cg202_dsns_vs_clean_summary.json "
    "--tag cg202b --timeout-sec 1500'"
)

entry = {
    "id": "CG202",
    "title": "[선별 청정화 × Δ파생 상호작용] 종목상수 29개+중복라벨을 걷어낸 선별 위에서 Δ1·Δ5 파생이 살아나는가 (앵커 3개 평균)",
    "status": "pending",
    "priority": 2,
    "kind": "experiment",
    "owner": "engineer",
    "affects_model": True,
    "metric": "protocol_pair_anchor",
    "summary_out": "/app/reports/overnight/anchor_sweep/cg202_dsns_vs_clean_summary.json",
    "command": cmd,
    "counterfactual": (
        "CG202_clean_h5 (종목상수 29개 제거 + drop_dup, 파생 없음) — 처리군 CG202_clean_dsns_h5 는 "
        "같은 청정 선별에 Δ1·Δ5 파생 10컬럼(SNS·공시·이벤트)만 추가한 arm. "
        "보조 짝: CG202_clean_h5 vs CG202_ctl_h5(청정화 단독 효과, 참조용)."
    ),
    "success": (
        "① 앵커 3개 폴드 테스트 창이 실제로 달라야 한다(같으면 측정무효) "
        "② 1차 판정 = 짝 Δ(clean_dsns − clean) 앵커평균 ≥ +0.02 & 부호 3/3 & 폴드승률 ≥4/5 → 신호 후보 "
        "③ |앵커평균 Δ| < 0.005 또는 부호 불일치 → '청정화는 파생 신호를 살리지 못한다'로 조정 축 종결(데이터축 CG73 대기) "
        "④ |앵커평균 Δ| ≥ +0.02 면 프로토콜 결함 의심(CG201 잡음바닥 대비) "
        "⑤ rc≠0/요약 미갱신 = 실행실패(노이즈 아님)"
    ),
    "hypothesis": (
        "CG196 같은-런 실측: 대조군 top30 이 종목상수·중복라벨(__dupN)을 폴드별 최대 7개 포함(실질 고유 23개). "
        "CG195_dsns 의 Δ+0.0056 은 그 오염된 선별 위에서 측정됐고, CG196_noconst·CG198_dupdrop 은 단독으로 "
        "각각 Δ+0.0002 수준이었다. 두 정리를 동시에 켠 상태에서 Δ파생을 추가한 조합은 아직 측정된 적이 없다 "
        "→ 청정 선별에서만 시간가변 파생이 슬롯에 들어와 신호가 산다."
    ),
    "est_minutes": 90,
    "created_at": "2026-11-12T02:05:00+09:00",
    "notes": (
        "보조 요약: /app/reports/overnight/anchor_sweep/cg202_clean_vs_ctl_summary.json (청정화 단독 효과). "
        "arm 정의: scripts/wf_label_sweep.py 의 CG202_* 3개(2026-11-12 02:0x 추가). "
        "앵커는 CG200 과 동일(2026-10-29/10-30/11-02) — 실효앵커 이동이 실측된 날짜만 사용. "
        "계측기 판정 규칙 변화 없음 → 리뷰보드 승인 불필요."
    ),
}
items.append(entry)
d["updated_at"] = "2026-11-12T02:05:00+09:00"
with open(P, "w", encoding="utf-8") as f:
    json.dump(d, f, ensure_ascii=False, indent=1)
print("appended CG202, items:", len(items))
print([(i["id"], i["status"]) for i in items[-3:]])

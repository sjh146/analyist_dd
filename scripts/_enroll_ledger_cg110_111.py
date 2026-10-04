#!/usr/bin/env python3
"""CG110/CG111 원장 기록 — append_ledger 로 넣어야 `validation` 블록이 주입된다(트레이더 계약 유지).

⚠ 직접 파일에 쓰면 최근 5행 스캔(trader_cycle.verify_model_handoff)에서 폴드 통계가 사라져
   핸드오프가 재발행된다(MT49 교훈). 그래서 구동기 함수를 쓴다. reported=True + reported_at=지금.
"""
import datetime as dt
import sys

sys.path.insert(0, "scripts")
import model_engineer_cycle as m  # noqa: E402

now = dt.datetime.now().astimezone().isoformat(timespec="seconds")

RECS = [
    {
        "id": "CG110", "rc": 0, "verdict": "노이즈",
        "metric": "fillable_topk_vs_pool",
        "detail": (
            "비ML 기준 랭킹 돈 스크리닝(scripts/baseline_ranking_money.py 신설). 계측기 검증: 같은 덤프에서 "
            "model arm 이 CG108/CG109 참조 수치와 소수점까지 일치(k3 +0.367/Δ+0.409/t0.98 · k5 +0.667/+0.710/"
            "t2.09 · CG109 k3 +0.714/+0.854/t2.01). 블록[0:150) 150종목·225세션·h5·수수료 0.21%p — 풀평균 "
            "−0.043: mom20 k3 Δ−0.506 · k5 −0.039 · mom60 k5 0.000 · lowvol20 k3 +0.286(t0.86)/k5 +0.269(0.88) "
            "· rev5 k3 +1.064(1.96)/k5 +1.003(2.64) 분할 unstable[+2.43/−0.50] · rand k3 −0.231/k5 −0.493. "
            "블록[150:200) 50종목 225세션: 풀평균 −0.140 · mom20 k3 +0.977(t1.93 unstable) · mom60 k3 −0.337 · "
            "lowvol20 k3 −0.264 · rand k3 +0.259. → 어떤 단순 랭킹도 사전문턱(분할 both_positive 포함) 미통과 "
            "= 'ML 대신 단순 팩터' 경로 없음(모델 k5 +0.710 t2.09 · k10 +0.682 t3.11 은 통과 조건 일부 충족)."),
        "delta": None, "per_exp": None, "reported": True, "reported_at": now,
        "ts": now, "log": "services/xgboost-ml/reports/overnight/cg110_baseline_money.json",
    },
    {
        "id": "CG111", "rc": 0, "verdict": "기준선 실측",
        "metric": "fillable_topk_vs_pool",
        "detail": (
            "돈 지표 검출 바닥(MDE) — 무작위 랭킹 21회(블록[0:150) 150종목·225세션·h5): Δ(top-k−풀평균) sd "
            "k3 0.415[−0.778,+0.744] · k5 0.301[−0.508,+0.503] · k10 0.159[−0.261,+0.340]. 즉 단일 런 Δ 는 "
            "k=3 에서 ±0.42(1sd) 까지 무작위로 나온다 — 모델 k3 Δ+0.409 = z1.0(동전과 구분 불가) · k5 +0.710 "
            "= z2.4 · k10 +0.682 = z4.3. 사전등록 'k=3 AND k=5' 조건은 검출력이 낮은 조건이었다. 문턱은 "
            "그대로 두고 세션 수(검정력)를 올리는 것이 정직한 경로."),
        "delta": None, "per_exp": None, "reported": True, "reported_at": now,
        "ts": now, "log": "services/xgboost-ml/reports/overnight/cg111_mde_rich.json",
    },
]

for r in RECS:
    m.append_ledger(r)
    print("appended", r["id"])

led = m.load_ledger()
print("ledger rows:", len(led), "| last:", led[-1].get("id"), "| validation keys:",
      sorted((led[-1].get("validation") or {}).keys())[:6])

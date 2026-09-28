#!/usr/bin/env python3
"""CG21 셋업 완료 반영 + CG22(라벨 꼬리 레버의 유니버스 재현) 신설 — 2026-09-28.

무엇을 했나:
  ① scripts/wf_label_sweep.py 에 --dump-preds 추가(폴드 앙상블 확률·실현 선행수익 jsonl 저장)
  ② scripts/topk_precision.py 신설(precision@k·바스켓 기대수익 집계, 공통 후보집합 짝 비교)
  ③ scripts/_topk_precision_test.py 자체검증 5/5 PASS
  → CG21 의 setup_needed 를 해소하고 status=needs_setup → pending 으로 올린다.

왜 공통 후보집합(--restrict-q)이 필요한가(셋업 중 실측): q0.05 arm 의 행 집합은 q0.30 arm 의
부분집합이라 **분모가 다르다**. 합성 데이터 실측(각자 후보집합, k=2): q0.05 prec 0.5 vs
q0.30 prec 1.0 → Δ−0.5. 라벨 q 가 다른 두 arm 을 각자 후보 위에서 채점하면 '상위 k 지표'가
뒤집힌다 → (fold,date) 별 실현수익 양쪽 꼬리만 남긴 공통집합에서만 비교한다.

추가로 발견한 집계 함정 2개를 코드에 반영:
  - 정밀도는 이산 지표라 동점(Δ=0)이 흔하다 → 부호검정은 동점 제외(n_eff), 동점 수 별도 보고.
  - k ≥ 후보 풀 크기면 지표가 포화한다(q0.05 공통집합 ≈ 15행/일 → k=10 은 전부 동점).
    실측 경고: 스모크(2폴드·1시드)에서 k=10 Δprec +0.0000·동점 182/182 → **k=10 은 해석 금지**.
"""
import datetime as dt
import json

PATH = "docs/QUANT_MODEL_BACKLOG.json"
KST = dt.timezone(dt.timedelta(hours=9))
now = dt.datetime.now(KST).replace(microsecond=0).isoformat()

b = json.load(open(PATH, encoding="utf-8"))
items = b["items"]
ids = {i["id"] for i in items}

SETUP_NOTE = (
    "완료(2026-09-28 20:0x): ① scripts/wf_label_sweep.py 에 --dump-preds <path> 신설 — 폴드별 "
    "앙상블 확률·실현 선행수익을 (exp, fold, date, code, y_true, y_pred, fwd_ret) jsonl 로 저장"
    "(기본 None → 기존 실행 무영향, 검증: 2폴드 스모크에서 19,285행 덤프 확인) ② "
    "scripts/topk_precision.py 신설 — 날짜별 top-k 정밀도·바스켓 기대수익, (fold,date) 짝 Δ, "
    "동점 제외 부호검정 ③ scripts/_topk_precision_test.py 5/5 PASS(합성값 대조: 자체 후보집합 "
    "Δ−0.5000 · 공통집합 Δ+0.0000 · 포화 감지 · 동점 집계 · JSON 산출)."
)

CG21 = {
    "id": "CG21",
    "title": "라벨 꼬리 이득의 실질성 — 상위 k 정밀도(precision@k)·기대수익 (셋업 완료)",
    "status": "pending",
    "priority": 1,
    "affects_model": True,
    "setup_done": SETUP_NOTE,
    "baseline": {"value": 0.5105,
                 "source": "CG19 같은 런 CO_core30_h5(게이트 ON·5폴드×10시드)"},
    "hypothesis": ("q 를 좁혀 AUC 가 오르는 것이 '더 극단적인 양성을 맞히는 과제'가 되어서인지, 실제로 "
                   "트레이더가 사는 상위 k 종목의 적중률·기대수익이 올라서인지 분해한다. AUC 는 순위 "
                   "지표라 과제 정의가 바뀌어도 값이 비교되지만, 매매는 상위 k 만 사므로 precision@k "
                   "가 최종 KPI 다."),
    "evidence": ("CG19: q0.05 0.5575 vs q0.30 0.5105(Δ+0.0470, 폴드 5/5) · CG20(10폴드): 0.5642±0.0375 "
                 "vs 0.5161±0.0239(Δ+0.0481, 폴드 짝 9/10, 부호검정 p=0.0215) · 게이트 비용 +0.0037. "
                 "행 수 24,742 → 4,399(양성 밀도 45/일 → 7/일). 트레이더 실계좌 한도: 일 3건·종목 10%·"
                 "동시 3종목 → k=3 이 실제 매매 단위."),
    "method": ("panel_150u 게이트 ON 2-arm(CO_core30_h5·CO_q05_h5)을 --dump-preds 로 돌린 뒤, "
               "(fold,date) 별 **실현수익 양쪽 꼬리 공통집합**(--restrict-q 0.05) 위에서 두 arm 을 "
               "채점한다. 각자 후보집합 채점은 분모가 달라 금지(합성 실측: Δ−0.5 로 뒤집힘). "
               "k=3·5 를 주 지표로, k=10 은 포화 확인용."),
    "command": ("docker exec stock_xgboost_ml sh -c 'cd /app && OMP_NUM_THREADS=2 timeout 2400 python -u "
                "scripts/wf_label_sweep.py --panel /app/app/models/wf/panel_150u.npz --folds 5 --seeds 3 "
                "--only CO_core30_h5,CO_q05_h5 --dump-preds /app/scripts/_preds_CG21.jsonl' && "
                "python3 scripts/topk_precision.py /app/scripts/_preds_CG21.jsonl --k 3,5,10 "
                "--arm CO_q05_h5 --control CO_core30_h5 --restrict-q 0.05 "
                "--json-out data/reports/me_cycle/topk_CG21.json"),
    "arm": "CO_q05_h5",
    "counterfactual": "CO_core30_h5",
    "check": "python3 scripts/model_engineer_cycle.py --status",
    "check_target": {"op": ">=", "value": 1},
    "metric": "wf_sweep_summary",
    "est_minutes": 20,
    "cost": "실행 8분(2 config×5폴드×3시드=30 cell) + 집계 수초. (셋업은 완료 — 2026-09-28)",
    "success": ("공통 후보집합에서 k=3·5 의 Δprec 짝 평균 ≥ +0.05 이고 동점제외 부호검정 p < 0.05, "
                "그리고 Δ바스켓 기대수익 > 0 → 라벨 꼬리 레버가 '과제 난이도 효과'가 아님을 확인, "
                "승격 검토로 올린다. Δprec ≤ +0.02 또는 부호 불일치면 난이도 효과로 판정하고 라벨 축 종료."),
    "expected": ("미지 — q0.05 는 실제 상위 5% 만 양성이라 공통집합(≈15행/일)에서 top-10 정밀도는 "
                 "포화한다. k=3·5 만 해석할 것."),
    "caution": ("① k=10 은 포화(스모크 실측: 동점 182/182, Δprec +0.0000) → 해석 금지. "
                "② 정밀도는 이산이라 동점이 흔하다 — 부호검정은 동점 제외값(n_eff)으로만 읽는다. "
                "③ --dump-preds 파일은 /app/scripts(=호스트 scripts/)에 쓴다: 컨테이너 reports/ 는 "
                "root 소유라 호스트 python 이 쓸 수 없다. ④ 이 결과는 보조 KPI 다 — 북극성(AUC) "
                "판정을 대체하지 않는다."),
    "note": ("2026-09-28 19:1x 신설(CG19 직후) → 같은 날 20:0x 셋업 완료·pending 승격. "
             "CG20 통과 후 착수. 셋업 중 발견한 집계 함정 2개(분모·포화)는 코드에 반영·검증 완료."),
}

CG22 = {
    "id": "CG22",
    "title": ("라벨 꼬리(q0.05) 레버의 유니버스 재현 — 생산 유니버스(49종목·panel_420_asofpatch)에서도 "
              "게이트 ON 짝 Δ 가 나오는가"),
    "status": "pending",
    "priority": 2,
    "affects_model": True,
    "baseline": {"value": 0.5363,
                 "source": "CG17 실측 같은 런 게이트 ON 대조군 CO_core30_h5(49종목·5폴드×??시드, 0.5363)"},
    "hypothesis": ("CG19/CG20 은 전부 panel_150u(150종목)에서 나온 신호다. 과거 조정 축 신호가 유니버스를 "
                   "바꾸면 부호가 뒤집힌 전례(CG4 +0.0227 → CG5 −0.0109, CG14 구간 짝 −0.0057)가 있으므로, "
                   "**생산 경로가 실제로 학습하는 49종목 유니버스**(train_curated.UNIVERSE_SQL = KOSDAQ "
                   "코드순 LIMIT 50)에서 같은 런 3-arm 으로 재현되는지 먼저 본다. 재현되지 않으면 "
                   "라벨 꼬리 레버는 승격 후보가 아니라 '150종목 패널 특이'로 기록한다."),
    "evidence": ("CG18/CG19/CG20 은 panel_150u 단일 패널 실측이다. 유니버스가 성능을 지배한 실측 이력: "
                 "CG12(25→150종목 단조 하락) · CG13(서로소 30종목 구간 폴드 평균 0.5204→0.4917, 총폭 "
                 "0.0287 = 사전문턱 +0.02 보다 큼) · CG14(같은 후보를 구간 짝으로 재판정하면 Δ 평균 "
                 "−0.0057). 즉 '한 유니버스에서 +0.02' 만으로는 승격 근거가 못 된다."),
    "method": ("panel_420_asofpatch(49종목·210피처·281거래일, 등록 기준선 패널)에서 게이트 ON 3-arm "
               "동일 런: CO_core30_h5(q0.30 대조군) · CO_q10_h5(중간점) · CO_q05_h5. 판정은 폴드 짝 "
               "Δ(q0.05−q0.30) 와 부호검정(게이트 ON 이므로 게이트 비용 교란 없음)."),
    "command": ("docker exec stock_xgboost_ml sh -c 'cd /app && OMP_NUM_THREADS=2 timeout 3600 python -u "
                "scripts/wf_label_sweep.py --panel /app/app/models/wf/panel_420_asofpatch.npz "
                "--folds 5 --seeds 10 --only CO_core30_h5,CO_q10_h5,CO_q05_h5'"),
    "arm": "CO_q05_h5",
    "counterfactual": "CO_core30_h5",
    "check": "python3 scripts/model_engineer_cycle.py --status",
    "check_target": {"op": ">=", "value": 1},
    "metric": "wf_sweep_summary",
    "est_minutes": 12,
    "cost": "약 6~10분(3 config×5폴드×10시드=150 cell). 49종목 q0.05 는 행 1,400 안팎이라 폴드당 학습표본이 작다.",
    "success": ("폴드 짝 Δ(q0.05−q0.30) ≥ +0.02 이고 양(+) 폴드 ≥ 4/5 → 유니버스 무관 재현 → "
                "CG21(실질성)과 함께 승격 검토로 올린다. |Δ| < 0.02 또는 부호 불일치 → '150종목 패널 "
                "특이'로 닫고 라벨 축 종료(무개선 카운터가 그대로 돈다)."),
    "expected": ("미지 — 49종목 q0.05 는 양성 2.5/일(150종목 7.35/일)로 꼬리가 훨씬 얇아 AUC 분산이 크다. "
                 "밀도가 달라 절대 AUC 는 150종목 값과 비교하지 않는다(같은 런 짝만)."),
    "caution": ("① 꼬리 두께 q 는 '양성 밀도'를 유니버스 크기에 비례해 바꾼다 → 150종목 q0.05(7.35/일)와 "
                "49종목 q0.05(2.5/일)는 같은 과제가 아니다. 중간점 CO_q10_h5(4.9/일)로 단조성을 본다. "
                "② q0.05 는 행이 1,400 안팎 → 폴드 std 가 커진다(단일 폴드로 판정 금지)."),
    "note": ("2026-09-28 20:0x 신설(CG20 통과 후속). 순서: CG21(실질성) → CG22(유니버스 재현). "
             "둘 다 통과해야 '승격 후보'이며 승격 자체는 champion_promote --dry-run 별도 판단."),
}

for it in (CG21, CG22):
    if it["id"] in ids:
        items = [it if i["id"] == it["id"] else i for i in items]
        print(f"교체: {it['id']} (status={it['status']})")
    else:
        items.append(it)
        print(f"추가: {it['id']} (status={it['status']})")

b["items"] = items
b["updated_at"] = now
json.dump(b, open(PATH, "w", encoding="utf-8"), ensure_ascii=False, indent=2)
print("총 항목:", len(items), "| updated_at", now)
print("pending:", [i["id"] for i in items if i.get("status") == "pending"])

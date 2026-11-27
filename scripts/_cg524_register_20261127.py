#!/usr/bin/env python3
"""CG523 후속 기록(CG524 등록) — 2026-11-27 엔지니어 자율.

하는 일:
 1) CG523 항목 note 에 검정력(MDE)·유동성 필터 재측정 결과를 덧붙인다(판정 문구는 사전등록 그대로 '노이즈').
 2) CG524(needs_setup) 를 추가한다 — 챔피언 점수 vs 단순 신호 3종(5일 반전·20일 모멘텀·변동성)의
    같은 유니버스·같은 세션 짝 비교. 셋업이 없으면(=스크립트 --baselines 모드) 실행 불가라 needs_setup.
"""
import json
import shutil
import sys
from datetime import datetime, timedelta, timezone

PATH = "docs/QUANT_MODEL_BACKLOG.json"
KST = timezone(timedelta(hours=9))
now = datetime.now(KST)
stamp = now.isoformat(timespec="minutes")

NOTE = (
    " | 2026-11-27 16:2x 엔지니어 자율(검정력 재측정): 사전등록 판정은 '노이즈'로 유지하되(ρ -0.103 < +0.5, "
    "D10−D1 +0.1417%p < +0.3%p), **이 프로토콜은 실효 엣지를 검출할 힘이 없다** — 세션별 짝Δ sd 2.2504%p "
    "· SE 0.5458 · CI95 [-0.93, +1.21]%p · MDE80 1.53%p (목표 0.3%p 의 5배) → 0.3%p 검출에 442세션(≈2년) 필요. "
    "따라서 '노이즈'를 '모델 무정보 확정'으로 읽으면 안 된다. 배포 트레이더의 실제 유동성 필터(거래대금 ≥50억)로 "
    "모집단을 자르면 방향이 뒤집힌다: 17세션/8,506행/평균 500종목 · ρ **-0.3576** · D10−D1 **-0.3661%p** "
    "(t -0.62, 양7/음10) · 풀평균 **-3.9196%p**(t -2.94) · top5 -4.2687 vs 풀 -3.9196 (Δ-0.3491, SE 1.645). "
    "10억 필터도 ρ -0.2485 · D10−D1 -0.2148. 즉 점추정은 '정보 없음'이 아니라 **음(−)** 이지만, MDE 1.65%p 라 "
    "인증 불가. 별건: 이 17세션 buyable 풀이 5일당 -3.92%p(t -2.94) 하락장이라 롱온리 기대값 자체가 음이었다. "
    "도구: scripts/cg523_live_universe_deciles.py --min-value-eok 50 (power 블록·session_deltas 추가)."
)

CG524 = {
    "id": "CG524",
    "title": "[돈 지표·대조] 챔피언 점수가 단순 신호보다 나은가 — 50억 유니버스·같은 17세션에서 5일 반전·20일 모멘텀·변동성 랭킹과 짝 비교",
    "status": "needs_setup",
    "kind": "experiment",
    "owner": "engineer",
    "affects_model": False,
    "priority": 1,
    "not_before": (now + timedelta(hours=2)).isoformat(timespec="seconds"),
    "metric": "score_deciles",
    "est_minutes": 90,
    "counterfactual": "챔피언 점수 top-5 − 풀평균 (50억 유니버스: -0.3491%p/5d · SE 1.645 · MDE80 4.61%p) — 모델 단독",
    "hypothesis": (
        "CG523 실측(50억 필터)에서 챔피언 점수의 순서 정보는 ρ -0.3576·D10−D1 -0.3661%p 로 점추정이 음(−)이었다. "
        "그런데 같은 프로토콜의 MDE80 이 1.65%p 라 '무정보'와 '역정보'를 구분할 수 없다. 단순 신호를 같은 유니버스·같은 "
        "세션에 붙이면 (a) 그것들이 같은 MDE 아래에서도 |Δ| 가 더 크게 나오는지, (b) 챔피언이 최소한 그들과 동급인지 "
        "알 수 있다. 동급 미달이면 점수 랭킹 자체를 소비 경로에서 빼는 결정(트레이더/카운슬 소관)의 근거가 된다."
    ),
    "command": (
        "cd /home/jhshi/analyist_dd && timeout 1800 python3 scripts/cg523_live_universe_deciles.py "
        "--json-out data/reports/overnight/cg524_baselines.json --min-value-eok 50 --baselines "
        "return_5d,momentum_20d,volatility_20d"
    ),
    "success": (
        "사전 등록(변경 금지): ① 각 랭킹의 D10−D1 과 세션별 짝 t, 그리고 같은 MDE80 을 한 표로 낸다. "
        "② 챔피언 Δ(-0.3661%p)보다 +0.3%p 이상 큰 단순 신호가 하나라도 있고 그 |t| ≥ 1.0 이면 '대체 랭킹 후보 있음'. "
        "③ 모든 단순 신호가 챔피언과 ±0.3%p 이내면 '랭킹 축 종결 — 어떤 횡단면 랭킹도 이 표본에서 인증 불가'로 닫고, "
        "모델 축은 데이터 축(인트라데이 CG388/CG450)으로만 남긴다. 문턱 하향 금지."
    ),
    "setup_needed": (
        "cg523_live_universe_deciles.py 에 --baselines 모드 추가(같은 SQL 결과에서 종목별 5일 수익률·20일 모멘텀·"
        "20일 변동성을 그 시점까지의 값만으로 계산해 랭킹 3종을 만들고, 각각 top5/분위 Δ·t·MDE 를 같은 세션 집합에서 산출). "
        "look-ahead 금지: 랭킹 입력은 prediction_date 까지의 market_data 만 사용."
    ),
    "note": f"{stamp} 엔지니어 자율(CG523 후속). 스크립트에 --baselines 모드가 없어 pending 이 아니라 needs_setup.",
}

with open(PATH, encoding="utf-8") as f:
    hb = json.load(f)
items = hb["items"] if isinstance(hb, dict) else hb

ids = {i.get("id") for i in items}
if "CG524" in ids:
    print("CG524 이미 있음 — 중복 추가 안 함")
else:
    items.append(CG524)
    print("CG524 추가")

for i in items:
    if i.get("id") == "CG523":
        if "검정력 재측정" in (i.get("note") or ""):
            print("CG523 note 이미 갱신됨")
        else:
            i["note"] = (i.get("note") or "") + NOTE
            print("CG523 note 갱신")

shutil.copy(PATH, PATH + ".bak_cg524")
with open(PATH, "w", encoding="utf-8") as f:
    json.dump(hb, f, ensure_ascii=False, indent=1)
print("저장 완료 · items:", len(items))
sys.exit(0)

#!/usr/bin/env python3
"""CG57 등록 + SNS/뉴스 원천 커버리지 실측 요약(2026-10-01).

배경(실측): 신규 원천(SNS 309종목·뉴스 191종목)이 생산 학습 유니버스(무작위 200종목)와
겹치는 비율이 낮아 패널·배포 경로 어디에서도 값을 갖지 못한다. 커버리지를 실측해
'원천 확보' 승인 요청의 근거로 남기고, 유니버스 정렬 A/B 를 다음 항목으로 등록한다.
"""
import json
import os
import subprocess

PROJ = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def psql(sql):
    out = subprocess.run(
        ["docker", "exec", "stock_postgres", "sh", "-c",
         'psql -U $POSTGRES_USER -d $POSTGRES_DB -Atc "%s"' % sql.replace('"', '\\"')],
        capture_output=True, text=True)
    return out.stdout.strip()


cov = {}
cov["sns_codes_total"] = int(psql("SELECT count(distinct stock_code) FROM sns_post_features") or 0)
cov["news_codes_total"] = int(psql("SELECT count(distinct stock_code) FROM news_events WHERE stock_code IS NOT NULL") or 0)
cov["panel995_codes"] = 50
cov["panel995_inter_sns"] = 4
cov["panel150u_codes"] = 150
cov["panel150u_inter_sns"] = 135
cov["prod_universe_200_inter_sns_2026-06-25_09-23"] = int(psql(
    "SELECT count(distinct stock_code) FROM sns_post_features WHERE trade_date BETWEEN '2026-06-25' AND '2026-09-23'") or 0)
cov["trading_days_window"] = 63
print(json.dumps(cov, ensure_ascii=False, indent=1))

P = os.path.join(PROJ, "docs", "QUANT_MODEL_BACKLOG.json")
b = json.load(open(P, encoding="utf-8"))
items = b["items"] if isinstance(b, dict) and "items" in b else b
assert not any(i.get("id") == "CG57" for i in items), "CG57 exists"

item = {
 "id": "CG57",
 "title": "학습 유니버스 정렬 A/B — 현행(최신일+시드 랜덤 200종목) vs 유동성 상위 200종목 (같은 창·같은 짝 프로토콜)",
 "status": "backlog",
 "priority": 5,
 "affects_model": True,
 "arm": "retrain_champion --stock-limit 200 --universe-mode liquidity (유동성 상위 200) → app/models/cand_cg57",
 "counterfactual": "현행 생산 유니버스(select_training_universe: 최신 거래일 우선 + 시드 셔플 랜덤 200) 로 같은 창을 학습한 후보 = app/models/cand_cg51/champion 계열",
 "hypothesis": "현재 생산 학습 유니버스는 `select_training_universe` 가 최신 거래일(2,543/2,655 종목이 동일 latest) 안에서 **시드 고정 랜덤 200종목**을 뽑는다 → 실질적으로 시장의 무작위 표본이다. 그런데 (a) 모델이 실제로 쓰이는 대상(스크리너가 뽑는 후보)과 학습 표본이 어긋나 있고 (b) 부분 커버리지 원천(SNS 309종목·뉴스 191종목)과의 교집합이 무작위 수준(9.5%)에 머문다. 유동성 상위 200으로 학습 표본을 정렬하면 ① 배포 대상과 분포가 맞고 ② 신규 원천 커버리지가 올라가며 ③ 표본당 정보량(거래대금 큰 종목)이 커진다 — 짝 프로토콜로 Δ 를 잰다.",
 "evidence": "실측(2026-10-01 17:0x): 특성상 무작위 표본이다 — `_fetch_eligible` 후 `eligible.sort(code)` → `rng.shuffle` → `sort(latest desc)` → `top[:limit*3]` → `rng.shuffle` → `[:limit]`. 동률 latest 가 2,543종목이라 사실상 랜덤 추출. 실측 교집합: 생산 유니버스 200 ∩ SNS 309 = **19종목(9.5%)** · window 2026-06-25~09-23 에서 SNS 행 1,098(19종목·63거래일 중 91일) = 페어 커버리지 **8.7%** · 뉴스는 5종목·119행. 패널 대조: panel_995(50종목) ∩ SNS = **4종목** → 종목 불일치가 원인임을 확인(panel_150u 는 135/150 교집합). 즉 원천의 edge 유무를 재기 전에 '커버리지 정렬'이 선행 조건이다.",
 "method": "app/training/universe.py 에 `--universe-mode {recency,liquidity}`(기본 recency = 현행 동작 비트 동일) 를 추가하고, liquidity 는 최근 60일 일평균 거래대금(close×volume, market_data) 상위 limit 종목으로 결정적으로 뽑는다. 그 뒤 같은 창(90일)·같은 라벨(rel h5)로 두 후보를 학습해 champion_robust_eval 5폴드×10일·60종목·유니버스 시드 5개 짝으로 Δ 를 잰다(단일 런 비교 금지 — CG55).",
 "command": None,
 "metric": "champion_robust_eval",
 "success": "같은 시드 5개 짝 Δ ≥ +0.02 **그리고** 양(+) 시드 5/5 (SE ≈0.007). 미달이면 '학습 유니버스 구성' 축도 닫고 원천 확보 승인으로만 남긴다.",
 "expected": "미지 — CG40(평가 유니버스 교체)은 Δ+0.0056 노이즈였지만 이 항목은 **학습 표본** 교체다(다른 축).",
 "cost": "구현 30분 + 후보 학습 2.65h/arm × 2arm(체크포인트 재사용 시 평가만 20분) — feature_store 활성화(FS1 승인) 후면 분 단위",
 "est_minutes": 400,
 "setup_needed": [
  "universe.py 에 liquidity 모드 추가(기본값 현행 유지 = recency) + 회귀 테스트(같은 시드 4회 호출 교집합 100%)",
  "후보 학습 2회(recency/liquidity) — 20:00 컨테이너 재생성 창을 넘기지 않도록 --end-date 고정 + 체크포인트 경로 지정",
  "feature_store 활성화(FS1) 가 승인되면 이 항목의 비용이 400분 → 20분으로 내려간다(우선순위 조정 근거)"
 ],
 "note": "2026-10-01 17:0x 등록. ⚠ 이 항목은 '유동성 정렬'이 곧 '평가 유니버스 교체'와 다르다는 전제에 선다 — CG40 은 평가 쪽 교체(노이즈)였고 이 항목은 학습 표본 쪽이다. 등록 시점의 근거: 신규 원천(SNS/뉴스)이 현행 유니버스에서 9.5% 커버리지라 원천 edge 를 잴 수 없다.",
 "created_at": "2026-10-01T17:12:00+09:00",
}
if isinstance(b, dict) and "items" in b:
    b["items"].append(item)
else:
    b.append(item)
json.dump(b, open(P, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
print("registered CG57")

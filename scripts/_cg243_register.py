#!/usr/bin/env python3
"""CG243 신설 — SNS 수집량 붕괴(10월 0건·11월 ×0.11)로 데이터 축이 막혀 있다(수집기 소유)."""
import json
import sys

P = "docs/QUANT_MODEL_BACKLOG.json"
doc = json.load(open(P, encoding="utf-8"))
items = doc["items"] if isinstance(doc, dict) else doc
if any(i.get("id") == "CG243" for i in items):
    sys.exit("CG243 이미 있음")

item = {
    "id": "CG243",
    "status": "needs_setup",
    "priority": 1,
    "kind": "data_contract",
    "owner": "engineer",
    "affects_model": True,
    "title": "[데이터 축·수집기] SNS 수집이 2026-10 전량 0건·11월 일평균 ×0.11 — 신선도 게이트가 못 잡던 조용한 중단",
    "metric": "source_freshness",
    "command": "python3 scripts/source_freshness_gate.py",
    "counterfactual": ("현행: `sns_posts` max(posted_at)=2026-11-13 로 **신선도 게이트상 '정상'**. "
                       "그런데 월별 실측은 2026-09 150,758건(308종목·일평균 5,025) → **2026-10 0건** → "
                       "2026-11 1,594건(15종목·일평균 531). 파생 테이블 `sns_post_features` 는 "
                       "2026-09-30 에서 멈춰 있었고(16,114행), 오늘 60일 백필을 돌려도 신규 (종목,일) "
                       "행이 22개뿐이었다(10월 원천 0건 탓)."),
    "hypothesis": ("SNS 피처가 88% 행에서 NaN 인 것은 '관측이 희소해서'가 아니라 **수집이 멈춰서**다. "
                   "9월에는 일평균 5,025건·308종목이었으므로, 수집이 복구되면 패널 커버리지가 "
                   "12%에서 크게 올라 데이터 축(유일하게 남은 레버)을 다시 시험할 수 있다."),
    "evidence": ("2026-11-13 17:4x 엔지니어 자율 실측(읽기 전용 + 파생 테이블 백필). "
                 "① DB: `SELECT to_char(posted_at,'YYYY-MM'), count(*), count(distinct stock_code) FROM sns_posts` "
                 "→ 2026-08 109,553(194종목) · 2026-09 150,758(308) · **2026-10 0행** · 2026-11 1,594(15). "
                 "최근 일자별: 11-11 1건(1종목) · 11-12 148건(6) · 11-13 1,445건(15). "
                 "② 파생: `sns_post_features` max(trade_date)=2026-09-30·16,114행 → "
                 "`sns_features_writer.py --lookback-days 60` 실행(rc=0, 3,505행 생성·upsert) 후 "
                 "16,136행(신규 (종목,일) 22개) — 원천에 10월이 없어 백필이 채울 것이 없다. "
                 "③ 게이트 결함 실측: `source_freshness_gate.py` 가 '전 원천 정상(지연 <2거래일, 기준 2026-11-12)'을 "
                 "보고했다 — max(날짜)만 보기 때문. 같은 게이트에 VOLUME 검사(최근 7일 일평균 vs 직전 60일 중앙)를 "
                 "추가한 뒤 실측: sns_post_features 3.1/일 = 기준선 196.0/일의 **×0.016 → VOLUME** 플래그, "
                 "다른 7원천은 ×0.57~0.82 로 오탐 0."),
    "success": ("① `sns_posts` 일평균이 2,000건 이상·200종목 이상으로 3거래일 연속 유지 "
                "② `sns_post_features` max(trade_date) 가 시장 최신 거래일과 동일 "
                "③ 그 상태에서 패널 SNS 관측률이 0.12 → 0.30 이상으로 오르면 재측정 항목(CG241 계열)을 pending 으로 올린다."),
    "expected": ("수집 복구만으로 데이터 축 재개가 가능하다 — 9월 실측(5,025건/일·308종목)이 상한이다. "
                 "현행 531건/일은 9월의 11%라 어떤 피처도 폴드 1·2 를 채우지 못한다."),
    "cost": "감사·백필·게이트 수리 완료(이 역할, 자율). 남은 것은 수집기 복구 + 스케줄 = 수집기 소유(승인).",
    "est_minutes": 5,
    "setup_needed": ("**이 역할 밖(수집기 소유)**: ① services/krx-collector(또는 SNS 수집 러너 `scripts/sns_collect_runner.py`)의 "
                     "수집 실패 원인 규명·복구 — 10월 전량 0건은 '스케줄은 도는데 0건'인지 '스케줄이 없음'인지 먼저 구분해야 한다 "
                     "(cron grep 결과 `sns_features_writer.py` 를 부르는 잡은 어디에도 없다 = 파생 단계 스케줄 부재 확정). "
                     "② `sns_features_writer`(파생 집계) 일일 크론 배선 — XR26/CG179 와 같은 '크론 승인' 계열. "
                     "③ (엔지니어 완료) 신선도 게이트에 VOLUME 검사를 추가해 재발을 감지한다(자체검증: 붕괴 소스만 플래그)."),
    "created_at": "2026-11-13T17:52:00+09:00",
    "note": ("2026-11-13 17:5x 엔지니어 자율(장외 틱). CG241('SNS 관측 희소')의 **원인 정정** — 커버리지 12%의 "
             "뿌리는 피처 변환이 아니라 수집 중단이다. ⚠ 이 항목은 성능 실험이 아니라 데이터 계약 항목이다"
             "(축 재시험 아님). 남은 레버가 데이터 축뿐인 상황에서 가장 값싼 해소 경로다."),
    "attempts": [],
}
items.append(item)
json.dump(doc, open(P, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
print("CG243 needs_setup 등록")

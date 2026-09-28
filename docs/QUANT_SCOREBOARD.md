# 퀀트 3역할 목표 스코어보드

- 갱신: 2026-09-28T16:07:20+09:00
- 목표: 최종 목표: 순손익(₩) — 리서처 데이터 → 엔지니어 AUC → 트레이더 돈

```
[퀀트 목표 사슬] 16:07 — 최종 목표: 돈(순손익)
💰 트레이더   : 순손익 +0원 | 0건 승률 n/a% | 기대값 n/a원/건 | 보유 0종목 | 마지막 진입 n/a일 전
📈 모델엔지니어: 로버스트 0.5592±0.0075 (CO_rank_smooth_d1_h5) vs 기준선 0.5406 → Δ+0.0186 [유지] | 챔피언 단일분할 +0.551318
🔬 퀀트리서처: DQ ok | 살아있는 피처 164 (횡단면 138) / 죽은 35 | 종목상수 0.329 | 뉴스신선도 0.27h
[사람 개입 필요]
  - [engineer] 로버스트 AUC 가 26사이클(측정 26회) 연속 기준선 (0.5406) 대비 +0.02 미달 — 새 레버 필요 (사람 승인 대상)
연쇄: 리서처(데이터) → 엔지니어(AUC) → 트레이더(₩)
```

## 🙋 사람 개입 필요

- [engineer] 로버스트 AUC 가 26사이클(측정 26회) 연속 기준선 (0.5406) 대비 +0.02 미달 — 새 레버 필요 (사람 승인 대상)

## 출처

- 트레이더: `/mnt/c/Users/jhshi/analyist_dd/trader-agent/journal/trade_journal.sqlite3` (실현 손익은 청산 거래에서만 계산)
- 엔지니어: `/home/jhshi/analyist_dd/data/reports/model_engineer_ledger.jsonl + /app/app/models/champion/auc.txt`
- 리서처: `/home/jhshi/analyist_dd/data/reports/dq_snapshots/dq_*.json` (최신 dq_20260928-160709.json)

# 퀀트 3역할 목표 스코어보드

- 갱신: 2026-09-25T20:55:44+09:00
- 목표: 최종 목표: 순손익(₩) — 리서처 데이터 → 엔지니어 AUC → 트레이더 돈

```
[퀀트 목표 사슬] 20:55 — 최종 목표: 돈(순손익)
💰 트레이더   : 순손익 -7,839원 | 31건 승률 38.7% | 기대값 -253원/건 | 보유 10종목 | 마지막 진입 4.3일 전
📈 모델엔지니어: 로버스트 0.5406±0.0316 (LS_quant_q30_h5) vs 기준선 0.5406 → Δ+0.0000 [유지] | 챔피언 단일분할 0.551318
🔬 퀀트리서처: DQ ok | 살아있는 피처 115 / 죽은 84 | 종목상수 0.339 | 뉴스신선도 0.30h
[사람 개입 필요]
  - [trader] 순손익 마이너스 -7,839원 — 기대값 -253원/건
  - [trader] 신규 진입 4.3일 없음 (보유 10종목) — 돈이 도는 회전이 멈춤
연쇄: 리서처(데이터) → 엔지니어(AUC) → 트레이더(₩)
```

## 🙋 사람 개입 필요

- [trader] 순손익 마이너스 -7,839원 — 기대값 -253원/건
- [trader] 신규 진입 4.3일 없음 (보유 10종목) — 돈이 도는 회전이 멈춤

## 출처

- 트레이더: `/mnt/c/Users/jhshi/analyist_dd/trader-agent/journal/trade_journal.sqlite3` (실현 손익은 청산 거래에서만 계산)
- 엔지니어: `/home/jhshi/analyist_dd/data/reports/model_engineer_ledger.jsonl + /app/app/models/champion/auc.txt`
- 리서처: `/home/jhshi/analyist_dd/data/reports/dq_snapshots/dq_*.json` (최신 dq_20260925-201927.json)

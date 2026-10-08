# 퀀트 3역할 목표 스코어보드

- 갱신: 2026-10-08T22:41:00+09:00
- 목표: 최종 목표: 순손익(₩) — 리서처 데이터 → 엔지니어 AUC → 트레이더 돈

```
[퀀트 목표 사슬] 22:41 — 최종 목표: 돈(순손익)
💰 트레이더   : 순손익 -2,936원 | 4건 승률 25.0% | 기대값 -734원/건 | 보유 2종목 | 마지막 진입 2.6일 전
📈 모델엔지니어: 로버스트 0.5519±0.0333 (TR_rank_h5) vs 기준선 0.5406 → Δ+0.0113 [유지] | 챔피언 단일분할 +0.551318 · 출처 TR1(2026-09-26T06:08 판정 노이즈)
   ↳ 대조 불가 제외(CG104): 최고 Q5s_120_150 0.5870 (CG89) — q 0.05≠0.3, 게이트 ON≠OFF, 유니버스 슬라이스 (120, 150), 유니버스 panel_prod200.npz/d420/L50≠panel_420_asofpatch.npz/d420/L50 (기준선 0.5406 보존)
🔬 퀀트리서처: DQ breach | 살아있는 피처 191 (횡단면 166) / 죽은 11 | 종목상수 0.366 | 뉴스신선도 0.42h
[사람 개입 필요]
  - [trader] 순손익 마이너스 -2,936원 — 기대값 -734원/건
  - [engineer] 로버스트 AUC 가 19사이클 연속 기준선 (0.5406) 대비 +0.02 미달 — 새 레버 필요 (사람 승인 대상)
  - [researcher] DQ 위반: 결측90%↑ 피처 수(기준선 8)(dq_feature_null_ratio_high_count)=26 ≥ 15
연쇄: 리서처(데이터) → 엔지니어(AUC) → 트레이더(₩)
```

## 🙋 사람 개입 필요

- [trader] 순손익 마이너스 -2,936원 — 기대값 -734원/건
- [engineer] 로버스트 AUC 가 19사이클 연속 기준선 (0.5406) 대비 +0.02 미달 — 새 레버 필요 (사람 승인 대상)
- [researcher] DQ 위반: 결측90%↑ 피처 수(기준선 8)(dq_feature_null_ratio_high_count)=26 ≥ 15

## 출처

- 트레이더: `/mnt/c/Users/jhshi/analyist_dd/trader-agent/journal/trade_journal.sqlite3` (실현 손익은 청산 거래에서만 계산)
- 엔지니어: `/home/jhshi/analyist_dd/data/reports/model_engineer_ledger.jsonl + /app/app/models/champion/auc.txt`
- 리서처: `/home/jhshi/analyist_dd/data/reports/dq_snapshots/dq_*.json` (최신 dq_20261008-220052.json)

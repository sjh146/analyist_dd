구현·적재·검증 완료. 요약:

## 결과

**검증 명령 실행 결과: `5`** (성공 기준 "5개 이상 nonzero" 충족)

```bash
docker exec stock_postgres psql -U stock_user -d stock_trading -tAc \
 "SELECT COUNT(*) FROM feature_coverage WHERE computed_at=(SELECT MAX(computed_at) FROM feature_coverage) AND feature_name IN ('institution_net_buy_5d','foreign_net_buy_5d','short_selling_ratio','momentum_3_12m','market_breadth') AND nonzero_ratio > 0"
# → 5
```

## 구현 상태

- `scripts/build_supply_market_features.py` (539행) — 19개 피처 계산·적재·feature_coverage 재계산·자기신고 완비
- `init-scripts/postgres/18_supply_market_features.sql` — `supply_market_features` DDL(종목×거래일 PK, NULL 허용)
- 실제 실행: `supply_market_features` **0행 → 1,078,479행** 적재, feature_coverage 19행 갱신

## 실측 근거 (DB 조회, docstring에도 기록됨)

| 원천 | 행수 | 비고 |
|---|---|---|
| foreign_institutional | 87,895 | net_buy 전부 비결측 → 5일 누적 생산됨 |
| krx_short_selling | 2,393 | short/total_volume 비결측, **balance_quantity 0행** |
| market_data | 1,113,634 (valid 1,078,479) | 격자 기준 |
| ownership | 225 | foreign_pct 비결측, **institution_pct 0행** |

**16/19 nonzero.** 나머지 3개(`institution_ownership_pct`, `short_interest_ratio`, `days_to_cover`)는 원천 컬럼이 전부 NULL이라 정직하게 **NULL(정보 없음)** 유지 — 스펙의 "결측은 0과 구분" 준수. 원천 백필 시 재실행으로 살아남(멱등: DELETE 후 COPY).

## 자기신고 (소스/파서/저장 3분리)

`dq_runner_claim`에 `record_claim` 기록: `source_rows=1,078,479 / claimed_rows=1,078,479 / persisted_rows=1,078,479` (gap 0).

## 수정한 파일 (최소 수정)

`build_supply_market_features.py`의 `financial_momentum` 1곳: `np.where`가 mask와 무관하게 양쪽 분기를 평가해 p==0에서 `RuntimeWarning: divide by zero`가 18회 반복 노이즈로 나오던 것을 `np.errstate(divide="ignore", invalid="ignore")`로 감쌈. **계산 결과는 불변**(dry-run으로 동일 출력 확인).

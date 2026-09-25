구현·검증이 완료되었습니다. 보고입니다.

## R12 재무 비율 피처 19개 부활 — 구현·검증 보고

### 결과 요약
`scripts/build_financial_ratio_features.py`(675행) + `init-scripts/postgres/20_financial_ratio_features.sql`(DDL)은 **이미 작성 완료**된 상태였고, 실제 실행·DB 반영·검증까지 끝냈습니다. 성공 기준 충족.

### ② 실측 근거 (DB에서 직접 조회한 수치)
| 항목 | 조회 결과 |
|---|---|
| `financial_statements` | 10,370행 = 2023/2024/2025-12-31 2,592·2,592·2,598 + 2026-06-30 2,588 |
| `disclosures` | 212,861행 (2025-01-02 ~ 2026-09-23) |
| `market_data` | 1,113,634행 (격자 3,934종목 × 314거래일) |
| `rcept_dt` 매칭 | 7,472 = 2024-12-31 2,451 + 2025-12-31 2,477 + 2026-06-30 2,544 |
| 접수일 미확보 제외 | 165행 (2025-12-31 121 + 2026-06-30 44 — 가시 시점 증명 불가) |
| 상장주식수 | 2,678종목 (ownership 225 + 시총 역산 2,453), 225/225 정확 일치 재검증 |
| 기간유형 | annual 2,593종목 / semi 2,544종목 (분기 재무제표는 DB에 없음) |

### 실행 결과 (전체 적재)
- 생성/적재 **775,010행** (annual 707,479 · semi 67,531 — 클래스당 1개 이상 ✓)
- nonzero **18/19** (`value_ncav`만 0 — `current_assets` 컬럼 부재, docstring·DDL에 근거 명시)
- as-of 불변식: `rcept_dt>trade_date` **0건**, `report_date>trade_date` **0건**
- 자기신고 `record_claim`: source=17,842 / claimed=775,010 / persisted=775,010

### ⑦ 검증 명령 실행 출력
```
$ docker exec stock_postgres psql ... "SELECT COUNT(*) FROM feature_coverage WHERE computed_at=(SELECT MAX(computed_at) FROM feature_coverage) AND feature_name IN ('quality_roe','quality_roa','value_per','value_pbr','roe') AND nonzero_ratio > 0"
5
```
- `dq_asof_violation_rows` 4개 scope(supply_future_dated/phantom, ownership_future, financial_report_future) 전부 **0** 유지 확인.

### 성공 기준 판정
- 19개 중 3개 이상 nonzero → **18/19** ✓
- `dq_asof_violation_rows` 0 유지 → **0** ✓

### ③ 멱등성 / ④ 자기신고 / ⑤ 파일 변경
- 멱등: `DELETE FROM financial_ratio_features` 후 COPY + `feature_coverage`는 `ON CONFLICT DO UPDATE`.
- 자기신고: `dq_claim.record_claim` 재사용 (소스 수신 / 파서 생성 / 실제 저장 3분리).
- **기존 파일 수정 0건** — 빌더와 DDL은 신규 파일이며, 이미 완성 상태라 수정이 필요 없었습니다. 기존 코드·DDL·`dq_claim`·`feature_coverage` 구조는 변경하지 않았습니다.
- ⑥ git 미실행, ⑧ 리포 밖 파일 미생성.

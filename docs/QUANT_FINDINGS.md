
## [리서처 R2] institution_ownership_pct 소스 발굴 (현재 100% 결측)  (2026-09-25 14:04)
- 결과: [조사]R2: 0 >= 10 → 미달
- 판정: 충족
- 근거: dbg: dq_col_quality_null_ratio{column=institution_ownership_pct} 가 사실상 1.0 (전량 NULL).
- 엔지니어 백로그: `XR2` (command·대조군 기입 필요)

## [리서처 R1] DART 공시 인덱스 확보 (2026-09-25 14:15)
- 결과: `disclosures` 테이블 신설(승인됨) + DART 정기공시 백필. rcept_dt·rcept_no·report_nm·corp_code 적재, 자기신고 원장 기록.
- 의미: 재무 피처의 공시 지연을 **가정(90/45일) → 실제 접수일**로 바꿀 수 있게 됐다.
- 엔지니어 백로그: `XR1` (command·대조군 기입 필요)

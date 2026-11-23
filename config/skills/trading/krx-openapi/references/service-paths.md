# KRX OpenAPI 상세 — 경로·승인·응답·예산

## 경로 전수 (실측)

| 분류 | 경로 | 비고 |
|---|---|---|
| 주식 | `sto/stk_bydd_trd` | 유가증권 일별매매정보 — 2026-09-18 기준 942종목/일 |
| 주식 | `sto/ksq_bydd_trd` | 코스닥 일별매매정보 |
| 주식 | `sto/knx_bydd_trd` | 코넥스 일별매매정보 |
| 주식 | `sto/stk_isu_base_info`, `sto/ksq_isu_base_info` | 종목기본정보: `ISU_CD`(ISIN)·`ISU_SRT_CD`(단축코드)·상장주식수 |
| 지수 | `idx/kospi_dd_trd`, `idx/kosdaq_dd_trd`, `idx/krx_dd_trd` | `IDX_CLSS`/`IDX_NM` 포함 |
| 증권상품 | `etp/etf_bydd_trd` | ETF 일별매매정보 |

404 를 받는 잘못된 접두사(실측): `etf/…`, `sec/etf_bydd_trd`, `drv/etf_bydd_trd`, `etf_bydd_trd`, `sec/etn_bydd_trd`, `idx/bond_dd_trd`.
증권상품 계열은 `etp/` 접두사 계열로 통일돼 있다고 보고, 안 되면 서비스 목록 페이지에서 경로를 확인한다.

## 응답 필드 (일별매매정보)

```
BAS_DD(영업일자) ISU_CD(종목코드) ISU_NM(종목명) MKT_NM(KOSPI/KOSDAQ/KONEX) SECT_TP_NM
TDD_CLSPRC(종가) CMPPREVDD_PRC(대비) FLUC_RT(등락률) TDD_OPNPRC(시가) TDD_HGPRC(고가) TDD_LWPRC(저가)
ACC_TRDVOL(거래량) ACC_TRDVAL(거래대금) MKTCAP(시가총액) LIST_SHRS(상장주식수)
```

- **성공 응답에 `respCode` 가 없다** — `OutBlock_1` 만 온다. 성공 판정은 `OutBlock_1` 존재 여부로 하고, `respCode` 는 **있을 때만** 오류 코드를 검사한다(respCode≠200 같은 조건을 무조건 걸면 정상 응답에서 즉시 중단한다).
- 값은 콤마 없는 문자열이며 빈 문자열·`-` 이 섞인다. `'-' → None`, `'' → None`, `'0' → 0` 규칙으로 정규화하고, 종가가 None 인 행은 휴장/거래정지 행으로 본다.
- 휴장일 판정: 그날 모든 행의 종가가 비어 있으면 휴장 → 휴장 목록(JSON)에 적고 다음 실행에서 건너뛴다(공실 점검 스크립트와 공유하면 probe 호출도 아낀다).
- 일별매매정보는 당일 데이터를 제공하지 않는다(보통 D-2 반영). 구간 종료일은 D-1 로 클램프한다.

## 승인 페이지 경로

| 단계 | URL |
|---|---|
| 인증키 발급내역(마이페이지) | `https://openapi.krx.co.kr/contents/OPP/MYPG/mypage/OPPMYPG001.cmd` |
| API 서비스 목록 | `https://openapi.krx.co.kr/contents/OPP/INFO/service/OPPINFO004.cmd` |
| 주식 서비스 목록 | `https://openapi.krx.co.kr/contents/OPP/USES/service/OPPUSES002_S1.cmd` |
| 이용방법 안내(STEP 01~04) | `https://openapi.krx.co.kr/contents/OPP/INFO/OPPINFO003.jsp` |

## 호출 예산 계산

날짜 1개 = 시장 수만큼 호출(보통 2).

| 구간 | 영업일 | 호출 | 3.0초+지터 |
|---|---|---|---|
| 1개월 | 약 22 | 44 | 약 3분 |
| 1년 | 약 265 | 530 | 약 30분 |
| 3년 | 약 780 | 1,560 | 약 1.5시간 |

같은 구간을 종목별 시세 API로 받으면 종목수(2,600+) × 일수/100콜 규모가 되어 수십 배가 된다 — 이력 백필은 항상 날짜 단위 API 를 먼저 찾는다.

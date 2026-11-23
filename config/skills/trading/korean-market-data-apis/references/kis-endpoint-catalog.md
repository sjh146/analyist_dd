# KIS 엔드포인트 카탈로그 (실측 프로브 결과)

일회성 진단이 아니라 "이 계정으로 무엇이 되는가"를 1콜씩 확인한 결과다. 재확인은 `scripts/kis_probe_all.py` 로 한다.
프로브 방법: 레포의 `services/kis-collector/kis_app/client/kis_client.py` — `KisClient(...)._request(path, tr_id, params)` 를 직접 호출한다
(curl 전송 + 토큰 파일 캐시를 그대로 재사용하므로 새 HTTP 스택을 만들지 않는다). 호출 간 1.2~3.0초, `KisApiError` 를 잡아 `msg_cd`/`msg1` 을 그대로 보고한다.

## 수집 가능 (실측 OK)

| 용도 | path | tr_id |
|---|---|---|
| 국내 현재가 | `/uapi/domestic-stock/v1/quotations/inquire-price` | `FHKST01010100` |
| 국내 일봉(기간별시세) | `/uapi/domestic-stock/v1/quotations/inquire-daily-itemchartprice` | `FHKST03010100` |
| 국내 분봉 | `/uapi/domestic-stock/v1/quotations/inquire-time-itemchartprice` | `FHKST03010200` |
| 국내 호가 | `/uapi/domestic-stock/v1/quotations/inquire-asking-price-exp-ccn` | `FHKST01010200` |
| 국내 체결 | `/uapi/domestic-stock/v1/quotations/inquire-ccnl` | `FHKST01010300` |
| 국내 지수 | `/uapi/domestic-stock/v1/quotations/inquire-index-price` | `FHPUP02100000` |
| 거래량 순위 | `/uapi/domestic-stock/v1/quotations/volume-rank` | `FHPST01710000` |
| 종목정보(마스터) | `/uapi/domestic-stock/v1/quotations/search-info` | `CTPF1002R` |
| 종목별 투자자매매동향(일별) | `/uapi/domestic-stock/v1/quotations/investor-trade-by-stock-daily` | `FHPTJ04160001` |
| 종목별 투자자(현재가계, 최신 30영업일만) | `/uapi/domestic-stock/v1/quotations/inquire-investor` | `FHKST01010900` |
| 국내선물 현재가 | `/uapi/domestic-futureoption/v1/quotations/inquire-price` | `FHMIF10000000` |
| 해외주식 현재가 | `/uapi/overseas-price/v1/quotations/price` | `HHDFS00000300` |
| 해외주식 일봉 | `/uapi/overseas-price/v1/quotations/dailyprice` | `HHDFS76240000` |

- **ETF/ETN은 국내주식과 같은 엔드포인트**로 조회된다(`FID_INPUT_ISCD` 에 ETF 코드). 실측 확인 코드: 069500(KODEX 200), 102110(TIGER 200), 152100(PLUS 200), 233740(KODEX 코스닥150 레버리지) — 현재가·일봉 모두 정상.
- 파라미터 요점: 국내는 `FID_COND_MRKT_DIV_CODE`(J=KOSPI/KOSDAQ, U=지수), 해외주식은 `AUTH`, `EXCD`(NAS/NYS…), `SYMB`. 해외지수도 같은 해외 현재가 TR로 조회된다.
- 원유 노출 확인: 해외주식 경로로 원유 ETF 심볼(예: USO) 현재가 정상 응답 → "유가" 데이터는 이 경로로 확보 가능.

## 실패/별도 확인 필요 (실측)

| 항목 | 결과 | 해석 |
|---|---|---|
| 해외선물 현재가/마스터 (`HHDFC55020100`/`HHDFC55020000`/`HHDFC55010100`, path `/uapi/overseas-futureoption/v1/quotations/...`) | 전부 `EGW-UNKNOWN(rt_cd=1)` | 경로·TR 변형 3개가 모두 실패 → 해당 상품 API 미신청 가능성이 높다. 원유 선물 직접 시세가 필요하면 개발자센터 신청 후 재프로브 |
| 시장별 투자자(시간) `FHPTJ04040000` | `OPSQ2001: ERROR INPUT FIELD NOT FOUND [FID_INPUT_ISCD_1]` | 필드명 오류(권한 문제 아님). 파라미터 교정 후 재시도 |
| ETF 유형별 시세 `FHKST01010400` | `EGW-UNKNOWN` | 경로·TR 불일치. ETF 목록은 종목 마스터(그룹코드 `EF`)로 대체 가능 |

- 권한 거부(DENIED)로 막힌 국내/해외 시세 엔드포인트는 실측에서 **0건**이었다 — 앱 권한은 넓게 열려 있고, 실패는 대부분 경로·파라미터 문제다. 실패를 "미신청"으로 단정하기 전에 변형을 2~3개 돌린다.
- **수급 이력의 깊이는 TR 선택에 달렸다**: `inquire-investor`(`FHKST01010900`)는 날짜 파라미터를 **무시**하고 항상 최신 30영업일만 준다(구간을 어떻게 줘도 같은 행 → 페이지네이션 불성립). 과거 이력은 `investor-trade-by-stock-daily`(`FHPTJ04160001`)의 `FID_INPUT_DATE_1` 을 **창의 끝**으로 쓰고 `d1 = DB 최소일 − 1일` 로 밀어 30영업일씩 얻는다(응답 배열은 `output2` — `output` 이 아니다). 종목당 250영업일이면 9콜이다.

## 호출 정책

- 프로브도 수집과 같은 규칙을 따른다: 호출 간 1.2~3.0초, 토큰은 파일 캐시 재사용(발급은 분당 1회 제한), 실패 시 재시도하지 말고 코드를 그대로 보고.
- 결과는 "수집 가능 / 권한·신청 필요 / 파라미터 오류" 세 갈래로 나눠 보고하고, 가능한 것에는 정확한 path·tr_id 를 함께 적는다 — 사용자가 다음 작업 지시를 그대로 내릴 수 있어야 한다.

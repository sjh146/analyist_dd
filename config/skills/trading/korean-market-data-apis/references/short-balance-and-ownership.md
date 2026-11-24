# 공매도 잔고 · 기관 지분 — 어디에 있고, 없으면 무엇으로 대체하나

"공매도/기관 지분 피처가 0" 류 요청은 **원천이 존재하는지부터** 확인해야 한다. 두 축 모두
"데이터가 없는데 코드만 고치면 살아난다"는 오진이 나오기 쉬운 영역이다.

## 1. 공매도: 체결은 있고 잔고는 없다

| 원천 | 제공 | 실측 결과 |
|---|---|---|
| KIS `FHPST04830000` (국내주식 공매도 일별추이) | 공매도 **체결** 수량·대금·비중 + 전체 거래량 | 정상(1콜로 요청 구간 전체, 거래일 내림차순) |
| KIS 공매도 **잔고** 계열 | — | 후보 6경로(daily-short-sale-balance·inquire-short-sale-balance·short-sale-balance-trend·short-sale·ranking/short-sale-balance·daily-loan-balance) 전부 404 = 엔드포인트 없음 |
| KRX OpenAPI | 목록에 공매도 서비스 없음 | `/svc/apis/srt/*`·`sto/stk_shrt_*` 계열 404 (경로 문제가 아니라 미제공) |
| KRX 정보데이터시스템(pykrx 경로) | 잔고 있음 | 레거시 스크래핑 — 과거 요청제한 위반으로 IP 차단 이력이 있어 **정책상 호출 금지** |

→ 진짜 잔고는 국내 오픈API로 확보 불가. **"못 만든다"가 아니라 "원천이 없다"** 로 보고하고,
아래 프록시를 실데이터로 계산한다.

### 실데이터 프록시 (krx_short_selling / KIS 체결 기반)

```sql
-- 20거래일 누적 체결 기준
SELECT sum(short_volume) FILTER (WHERE trade_date > %(d)s::date - INTERVAL '28 days') AS sh20,
       sum(total_volume) FILTER (WHERE trade_date > %(d)s::date - INTERVAL '28 days') AS vol20,
       avg(total_volume) FILTER (WHERE trade_date > %(d)s::date - INTERVAL '28 days') AS avgvol20,
       avg(short_ratio)  FILTER (WHERE trade_date > %(d)s::date - INTERVAL '7 days')  AS sr5,
       avg(short_ratio) AS sr20
FROM krx_short_selling WHERE stock_code = %(c)s AND trade_date <= %(d)s::date;
```

- `short_interest_ratio` = `sh20 / vol20 * 100` (**잔고비율 아님 — 누적 체결 비중 프록시**)
- `days_to_cover` = `sh20 / (avgvol20 * 20)` (같은 이유로 프록시)
- `short_ratio_5d` = `sr5`, `short_ratio_trend` = `sr5 - sr20` (이 둘은 원값 기반)

피처 이름을 유지하되 **보고·문서에 '프록시'임을 명시**한다. 잔고라고 쓰면 다음 세션이 그 숫자를 잔고로 인용한다.
실측 감도(2026-11-23): 005930 2.74 / 000660 2.89 / 247540 33.19(trend +11.0) — 2차전지처럼 공매도가 몰린
종목이 확실히 구분되므로 변별력은 있다.

## 2. 기관 지분: DART 대량보유(5%룰)가 유일한 공개 원천

- `ownership.institution_ownership_pct` 가 전부 NULL 이고, KRX/KIS 에 기관 **보유비율** 원천이 없다
  (`supply_market_features.institution_ownership_pct` 도 NULL, `krx_short_selling.balance_quantity` 도 전부 NULL —
  `count(*)` 가 아니라 `count(column)` 으로 확인해야 보인다).
- DART `majorstock.json?crtfc_key=...&corp_code=...` 이 5% 이상 보유 보고를 돌려준다(실측 삼성전자 39건).
  필드: `repror`(보고자)·`stkqy`(보유수량)·`stkrt`(보유비율 %)·`rcept_dt`(접수일)·`report_tp`.
- `stock_code ↔ corp_code` 매핑은 `disclosures` 에서 얻는다(`SELECT stock_code, max(corp_code) ... GROUP BY 1`).
- 기관 판별은 보고자 이름 정규식(`자산운용|투자|연금|보험|은행|증권|캐피탈|운용|자문|신탁|Fund|Capital|Investment|Asset|Trust`)으로 하고,
  **보고자별 최신 보고 1건만** 골라 합산한다(같은 보고자가 여러 번 신고하므로 누적하면 과대계상).
- 한계: 5% 미만 기관 지분은 잡히지 않으므로 **하한 추정**이다. 피처·리포트에 그대로 적는다.
- 수집 속도: 종목당 1콜, `sleep 0.05~0.1` 이면 3천 종목 ≈ 10~15분. 실패(status≠000)는 종목 목록에 남겨
  다음 실행에서 재시도한다(전체 재수집은 멱등 upsert 로 무해).

## 3. 보고 문장 골격

"공매도 잔고는 KIS·KRX 오픈API에 없다(후보 6경로 404, 잔고 TR 부재) → 체결 데이터로 4개 프록시를 만들었고
이름에 그 한계를 명시했다. 기관 지분은 DART 5% 보고 기반 하한 추정으로 N종목 확보했다."

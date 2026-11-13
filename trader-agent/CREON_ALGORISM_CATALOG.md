# Creon 기본알고리즘 기능 카탈로그 (v1, 2026-09-04)

대신증권 CybosPlus '크레온 기본알고리즘' 파이썬 코드 전체(37개 기능 섹션) 학습본.
원본 3파일은 노트북 `C:\Users\jhshi` (zip 백업 `_creon_algo.zip`) — BC-250 사본:
`creon_src/`(원문) + `creon_extracted_v1/`(PyQt5 제거 37모듈 + removed/ 원본 보존 + manifest.json).
지문 자동수집: `~/AUTONOMY/tools/creon_corpus_scan.py` (COM 객체/입력값/헤더/실시간 이벤트 추출).

사용 규칙
- 추출 모듈 상단 `## REVIEW` = UI(버튼/시그널)와 섞여 있던 부분. 헤드리스 이식 시 해당 클래스의
  메서드 로직만 재조립하면 됨(UI 실행부는 이미 주석). `removed/*.txt`에 제거 원문 보존.
- 모든 샘플의 공통 전제: 32bit Python + win32com + 관리자 권한 + CybosPlus(CBP) 모의/실 로그인.
- 주문 계열: `CpTd0311`(신규) / `CpTd0313`(정정) / `CpTd0314`(취소), 종목코드는 "A" 접두어.
- 순서 보장 API: `BlockRequest()` + `GetDibStatus()/GetDibMsg1()` (0=정상). 조회 연속분은 `Continue`.
- 실시간: `Dispatch + WithEvents + Subscribe()`, 콜백은 `OnReceived()` → `GetHeaderValue/GetDataValue`.
  이벤트 루프 필요(Qt 없이 headless면 pythoncom.PumpMessages 등 — 브리지 STA 펌프 참고).

---

## ⭐ 돈버는(시세민감/매매시그널) 코드 — 최우선 학습 완료

### 1) 당일 상승률 상위 200 → 실시간 가격 추적  [algorism3 #16, 221줄]
파일: `creon_extracted_v1/creon_algorism3__16__당일_상승률_상위_200_종목_실시간_통신_예제.py`
3단 파이프라인: **랭킹(CpSvrNew7043) → 일괄현재가(MarketEye) → 200종목 실시간 구독(StockCur)**

(a) Cp7043 랭킹 (`CpSysDib.CpSvrNew7043`) — 상승률 상위 목록, 연속조회로 최대 200개:
```
SetInputValue(0, ord('0'))  # 시장: '0'=거래소+코스닥  (주: ord()=ASCII 코드 — 실기 테스트로 확정 필요)
SetInputValue(1, ord('2'))  # 2=상승 (1=하락 등)
SetInputValue(2, ord('1'))  # 1=당일
SetInputValue(3, 21)        # 정렬: 21=전일대비 상위 순
SetInputValue(4, ord('1'))  # 1=관리종목 제외
SetInputValue(5, ord('0'))  # 거래량 필터: 0=전체
SetInputValue(6, ord('0'))  # 표시 항목(시가대비 등)
SetInputValue(7, 0)         # 등락율 시작 0%
SetInputValue(8, 30)        # 등락율 끝 30%  ← 0~30% 구간만 (상한가 임박 30%↑는 제외됨)
```
BlockRequest 후 `GetHeaderValue(0)`=이번 수신 수, (1)=전체 수.
`GetDataValue(0,i)`=코드, (1)=종목명, (3)=대비부호, (4)=대비, (6)=거래량.
`while objRq.Continue:` 로 다음 페이지 → 200개 도달 시 break.

(b) CpMarketEye 일괄 현재가 (`CpSysDib.MarketEye`): 200종목 리스트를 한 번에 조회.
```
SetInputValue(0, rqField)   # 요청 필드 배열 (순서대로 GetDataValue 행 인덱스 매핑)
SetInputValue(1, codes)     # 종목코드 리스트 (또는 단일코드 문자열)
rqField=[0,1,2,3,4,10,17] → (0)코드 (1)종목명 (2)시간 (3)대비부호 (4)대비 (5)현재가 (6)거래량
GetHeaderValue(2) = 수신 종목 수
```

(c) 실시간 구독 (`DsCbo1.StockCur`) 200종목 개별 Subscribe → `OnReceived`:
```
GetHeaderValue(0)=코드 (1)=종목명 (2)=전일대비 (9)=누적거래량 (13)=현재가
(17)=순간체결수량 (18)=체결시각 (19)=예상체결구분('1'=동시호가 예상체결, '2'=장중 체결)
```
→ 이 모듈이 곧 '상승률 상위 급등주 실시간 워치'의 뼈대. 전략에서 쓸 때: 7043 랭킹 결과를
브리지 `/rank` API로 노출하고, 상위 N종목만 StockCur 구독 → 등락률/거래량 변동 이벤트로 시그널.

### 2) 특징주 포착(뉴스/공시/차트/외국인 신호) — 전종목 실시간 감시  [algorism2 #05, 298줄]
파일: `creon_extracted_v1/creon_algorism2__05__특징주_포착_뉴스_공시_차트_외국인_신호__예제.py`
- **1회성 히스토리**: `CpSysDib.CpMarketWatch` — `SetInputValue(0, code)` (`'*'`=전종목),
  `SetInputValue(1, '1,2,10,11,12,13')` (1=종목뉴스 2=공시 10/11=외국계 창구 첫매수/첫매도
  12/13=외국인 순매수/순매도), `SetInputValue(2, 0)` 시작시간. `GetHeaderValue(2)`=건수,
  행: (0)시간 (1)코드 (3)카테고리 (4)특이사항.
- **실시간**: `CpSysDib.CpMarketWatchS` → OnReceived: `hv(0)`=코드, `hv(2)`=건수,
  행 (0)시간 (1)갱신(ord('c')=취소) (2)카테고리. 이벤트 카테고리 딕셔너리 `diccode`가 핵심 자산:
  - 10~13 외국계 창구/외국인 순매수·순매도 / 21~24 거래량 갱신·매물대 돌파
  - 28~32 **최근5일 첫 상한가·신고가 갱신·상한가 직전** / 41~71 기술적 크로스(MACD/RSI/CCI/Stochastic…)
  - 81~83 단기급락 후 5MA 상향돌파·**이동평균 밀집(5% 이내)·눌림목 재상승(20MA 지지)**
- **뉴스/공시 실시간**: `Dscbo1.CpSvr8092S` → OnReceived: hv(0)=갱신(ord('D')=삭제),
  (1)=코드 (2)=시간 (5)=내용.
→ '급등주/외국인/이벤트' 감시를 **개별 종목 구독 없이 전종목 1회 구독**으로 받는 유일한 채널.
자동매매 트리거(뉴스 8092 → 공시 확인 → 매수 후보) 원형으로 최적.

### 3) 종목검색(예제 전략 조건식) 조회 + 실시간 감시  [algorism3 #02 162줄 + algorism #04 356줄]
파일: `creon_extracted_v1/creon_algorism3__02__...예제_전략_조건_조회하기_예제.py`
      `creon_extracted_v1/creon_algorism__04__종목검색_조회_및_실시간_감시_예제.py`
HTS '종목검색'의 전략/조건식을 코드로 실행하는 경로:
1. `CpSysDib.CssStgList` — SetInputValue(0, ord('0')) ('0'=예제전략, '1'=나의전략) →
   전략 목록. DataValue: (0)전략명 (1)ID (2)등록일시 (3)필명 (4)평균종목수 (5)**평균승률** (6)평균수익
2. `CpSysDib.CssStgFind` — SetInputValue(0, 전략ID) → BlockRequest → 검색 결과 종목코드 목록
   (header(0)=검색수 (1)=전체수 (2)=검색시간)
3. 감시: `CssWatchStgSubscribe`(→감시일련번호 monID) → `CssWatchStgControl`
   SetInputValue(0,id)(1,monID)(2, ord('1')=시작/'3'=취소) → 실시간 합격 종목 이벤트 수신
→ '상승률 상위 + 거래량 급증' 같은 HTS 조건식을 그대로 시그널 소스로 재사용 가능.

### 4) (보조) 상승/하락 움직임 감시
- **VI 발동 추이** [3 #13]: `DsCbo1.StockJpBid2` + 실시간 호가(`StockBid`/`StockJpBid2`)로
  VI(변동성완화장치) 발동 종목의 현재가 변화 감시 — 급등주 진입/청산 타이밍 참고.
- **투자자 매매동향** [3 #03 `CpSvr7210d` / 1 #05 `CpSvrNew7221` / 3 #06 `CpSvr7254`]:
  종목별·일자별 투자주체(외국인/기관/개인) 순매수 집계 — 랭킹 필터에 결합 가능.
- **MACD 실시간** [3 #17]: StockChart 500봉 + `CpIndexes` 지표계산으로 MACD 실시간 갱신.

---

## 전체 기능 분류표 (37)

범례: 원본 파일(a1=creon_algorism, a2, a3) · 코드 | 상태: OK=사용가능, FX=수동수정 필요

### 주문/체결
| 모듈 | 기능 | COM 핵심 | 상태 |
|---|---|---|---|
| a1 #01 | 매수 주문 요청·에러처리(최소형) | CpTd0311 | OK 55L |
| a3 #19 / #20 | 현금 매도/매수 주문(최소형) | CpTd0311 | OK 49L×2 |
| a3 #01 | IOC/FOK 주문 테스트 | CpTd0311 조건 1/2 + 체결 실시간 | OK 334L |
| a3 #04 | 분할 주문(수량 쪼개기) | CpTd0311 | OK 298L |
| a3 #10 | 예약 매수/매도/취소/조회 | CpTdNew9061/9064/9065 | OK 375L |
| a3 #08 | 미체결 조회 + 실시간 갱신 + 취소/일괄취소 | CpTd5339 + CpTd0314 | OK 579L |
| a2 #06 | 보유 잔고 일괄 매도(청산) | CpTd6033 + CpTd0311 | OK 433L |
| a3 #14 | 주문 체결 실시간 처리(종합) | CpConclusion/StockCur/StockJpBid | OK 585L |

### 계좌/잔고/손익
| 모듈 | 기능 | COM 핵심 | 상태 |
|---|---|---|---|
| a3 #15 | 잔고 종목 조회 + 실시간 현재가 업데이트 | CpTd6033 + MarketEye + StockCur | OK 230L |
| a2 #04 | 잔고 실시간(현재가·체결 반영) | CpTd6033 + Conclusion | OK 527L |
| a2 #02 | 체결기준 당일 매매 손익 | CpTd6032 | OK 158L |

### 시세 조회(Block)
| 모듈 | 기능 | COM 핵심 | 상태 |
|---|---|---|---|
| a3 #25 | 현재가 조회(단일) | DsCbo1.StockMst | OK 76L |
| a3 #24 | 일자별 조회(연속) | DsCbo1.StockWeek | OK 65L |
| a3 #07 | 현재가 10차호가/시간대별/일자별 | StockMst/StockBid/StockWeek | OK 761L |
| a3 #09 | 현재가 10차 호가 화면 | StockMst/StockBid | OK 357L |
| a2 #01 | 전종목 시가총액(전체 스캔) | MarketEye(rqField=[0,4,20]) + CpIndexes | OK 418L |
| a3 #11 | 주식/ELW/선물/옵션/업종 전종목 시세 | MarketEye + 각종 CodeMgr | OK 324L |
| a3 #05 | 차트 조회 일/주/월/분/틱 | CpSysDib.StockChart | OK 320L |
| a3 #18 | 차트 데이터 최소 예제 | StockChart | OK 42L |
| a3 #26 | 종목정보(코드→이름/상한가 등) | CpUtil.CpCodeMgr | OK 41L |

### 실시간 시세
| 모듈 | 기능 | COM 핵심 | 상태 |
|---|---|---|---|
| a3 #23 | 현재가 조회/실시간 | StockMst + DsCbo1.StockCur | OK 140L |
| a3 #21 | 복수종목 조회 + 실시간 | MarketEye + StockCur | OK 153L |
| a3 #22 | 복수종목 실시간 등록/해지 | StockCur | OK 101L |
| a2 #03 | 실시간 분차트 데이터 만들기 | StockCur 누적 → 분봉 | OK 244L |
| a1 #02 | ETF NAV / ETN IIV 실시간 | (파싱 에러) | **FX 670L** |
| a3 #12 | 시세 연속 조회 제한 진단 | StockMst 반복 + 타이머 | OK 201L |

### 랭킹/검색/동향 (시그널 소스)
| 모듈 | 기능 | COM 핵심 | 상태 |
|---|---|---|---|
| **a3 #16** | **당일 상승률 상위 200 + 실시간** | **CpSvrNew7043** + MarketEye + StockCur | ⭐ OK 221L |
| **a2 #05** | **특징주 포착(뉴스/공시/외국인/기술신호)** | **CpMarketWatch(S)/CpSvr8092S** | ⭐ OK 298L |
| a3 #02 | 종목검색 예제 전략 조건 조회 | CssStgList/Find | ⭐ OK 162L |
| a1 #04 | 종목검색 + 실시간 감시(합격 알림) | CssStg* + CssWatchStg* | ⭐ OK 356L |
| a3 #03 | 종목별 투자자 매매동향 | CpSvr7210d | OK 169L |
| a1 #05 | 투자자별 매매 종합 | CpSvrNew7221 | OK 101L |
| a3 #06 | 매매입체분석(투자주체별) | CpSvr7254 | OK 158L |
| a3 #13 | VI 발동 현재가 변화 추이 | StockJpBid2 + StockBid/9619 | OK 321L |

### 지표/기타
| 모듈 | 기능 | COM 핵심 | 상태 |
|---|---|---|---|
| a3 #17 | MACD 실시간 계산 | StockChart + CpIndexes | OK 229L |
| a1 #03 | BlockRequest vs Request 방식 비교(통신 원리) | StockChart/StockMst/CpIndex | OK 560L |

---

## 이식 시 참고 (브리지/코어 연계)

- 브리지 COM 규약·헤더 표는 `bridge/api_notes.md`가 권위(주문/잔고 계열). 위 표는 시세/랭킹/검색
  계열을 추가할 때의 레시피 소스.
- **ord('0') 등 ord() 인자**: 대신 샘플 공통 관례. 7043/8537 등 'char'형 입력에서 ASCII 코드
  (48=ord('0'))가 아닌 값 0을 의도한 것일 수 있어 **실기 1회 테스트로 확정** 후 하드코딩할 것.
- 상승률 랭킹의 등락율 구간(입력 7~8)과 관리종목 제외(입력4)는 리스크 필터로 그대로 활용 가능.
- 전종목 스캔(a2 #01)은 분당 요청제한(CpCybos.GetLimitRemainCount)에 걸리기 쉬움 —
  7043 랭킹(1회 200종목)이 훨씬 효율적. 스크리너 후보 선정은 BC-250 데이터로, 장중 확정은
  노트북 7043/조건검색으로 분업하는 설계가 자연스러움.

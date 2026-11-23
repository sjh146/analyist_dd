## 9. 종목 마스터 파일 (ETF 유니버스 시딩용)

> ETF **시장가/NAV 괴리율** 수집(레버리지·인버스 계측)은 `references/etf-nav-and-premium.md` —
> 네이버 ETF 목록 **1콜(EUC-KR)** 로 전 종목 `nowVal`·`nav` 를 받는 경로, 장외 스냅샷 오독 주의, 손익분기 계산.

`https://new.real.download.dws.co.kr/common/master/{kospi_code.mst.zip,kosdaq_code.mst.zip}` 를 받아 **그룹코드로 종류를 가른다**:
ETF = `EF`, ETN = `EN`, 외국주권 = `FS`, 수익증권 = `BC`. `idxcode.mst.zip` 는 지수코드라 ETF 목록이 아니다.
- 마스터는 `cp949` 인코딩이고 레코드 폭이 고정이 아니다 → 정규식(코드 6자리 + 이름 + 그룹코드 2자)으로 파싱한다. **고정 오프셋 슬라이싱은 일부 라인에서 어긋나 그룹코드를 오독한다.**
- 이 파일로 ETF/ETN을 `stocks`에 시딩하면 기존 시세 수집 경로가 그대로 ETF를 수집한다(스키마 변경 없음).
- **시딩 순서를 지킨다 — 유니버스가 코드순 정렬이면 신규 종목 삽입이 기존 창을 밀어낸다.** `ORDER BY stock_code LIMIT 50` 류 유니버스(학습·백테스트·스크리너)는 종목이 추가되는 순간 다른 집합이 된다 → ① 종류 컬럼(예: `instrument_type`, 기본 'STOCK') 추가 ② 종목 전용 조회에 `instrument_type='STOCK'` 필터 추가 ③ **삽입 전후 유니버스 쿼리 결과를 diff로 완전 동일 확인** ④ 그 다음 삽입. 순서를 뒤집으면 진행 중인 학습 실험이 조용히 다른 표본으로 돌아간다.
- 시딩 검증은 종류·시장별 건수(`GROUP BY instrument_type, market`) + 컬럼 존재 + 대표 종목 1~2개의 시세 1콜 수집으로 한다. 전량 백필은 별도 작업으로 분리한다(시딩 검증과 섞으면 다른 실험과 자원을 다툰다).
- 대량 DDL이 멈추면 장기 `idle in transaction` 커넥션을 의심한다 — 다른 컨테이너의 트랜잭션이 락을 쥐고 있으면 `pg_stat_activity`에서 찾아 그 백엔드만 종료한다(컨테이너 재기동은 그 안의 작업을 죽인다).

## 함정

- **레포에 쌓인 준비된 패치(`data/reports/*.patch`)를 적용할 때는 "어느 호스트가 그 파일을 실행하는가"를 먼저 확정하라.**
  적용은 `git apply --check -p1 <p>` → `git apply -p1 <p>` → `ast.parse` 로 문법 확인 순서이고,
  수집기(일봉·분봉·수급)는 **호스트 크론**에서 돌기 때문에 호스트 파일을 고치면 재기동·컨테이너 복사 없이 즉시 유효하다.
  `docker cp <file> stock_kis_collector:...` 같은 명령은 그 이름의 컨테이너가 없어 `No such container` 로 실패한다(`docker ps` 로 실제 이름 확인 — krx_collector·yfinance_collector 만 있고 KIS 수집기는 호스트에서 돈다).
  반대로 xgboost-ml·job-runner·strategy-agents 는 컨테이너 안 코드라 호스트 패치만으로는 반영되지 않는다(`docker inspect <c> --format '{{range .Mounts}}...'` 로 마운트 확인).
  검증은 문법이 아니라 **산출물**로 한다(예: 분봉 페이지네이션 수리 후 `15:01~15:30 30봉` 만 적재되던 것이 하루 전체로 늘어나는지).
- **파생 피처 빌더를 좁은 창으로 돌리기 전에 DELETE 범위를 읽어라.** `scripts/build_supply_market_features.py`
  는 오랫동안 `DELETE FROM supply_market_features`(전체) 후 창을 COPY 했다 → `--since`를 짧게 주면
  창 밖 과거 이력이 통째로 사라진다(2026-11-13 실측: 3일 창 실행이 1,194,806행 삭제·9,490행 삽입,
  기본 창 재빌드로 1,198,529행 복구·271초). 백필·검증 목적의 좁은 실행 전에 ①`grep -n "DELETE FROM" <빌더>`로
  범위를 확인하고 ②불가피하면 실행 전 `pg_dump -t <table>` 또는 행수·기간을 먼저 기록하라.
  같은 함정이 다른 빌더(이벤트 피처 등)에도 있을 수 있으므로 'DELETE 후 COPY = 멱등'이라는 주석만 믿지 않는다.
- **`git status -sb` 의 앞섬/뒤짐 표시를 믿지 않는다.** 같은 레포 사본이 여럿(WSL 작업본 + Windows 사본)이면 각 사본의 `origin/*` ref 가 낡아,
  이미 origin 에 있는 커밋을 `ahead N` 으로 표시한다. 보고 전에 `git ls-remote origin <branch>` 와 `git log --oneline -1 origin/<branch>` 를 비교하고, 다르면 그 사본은 `git fetch` 가 필요하다고 말한다.
- **테스트 검증은 서비스/컨테이너 단위로 돌린다.** 레포 루트에서 맨 `pytest` 를 돌리면
  ① `tests/conftest.py` 와 `services/*/tests/conftest.py` 가 `ImportPathMismatchError` 로 충돌하고,
  ② `tests/` 가 특정 서비스의 `app.*`(및 그 의존성)를 import 하는데 호스트 python3 에는 없다.
  둘 다 호스트 환경 문제이며 회귀 신호가 아니므로 **'테스트 실패'로 보고하지 않는다** — 컨테이너 안 또는 `PYTHONPATH=services/<svc>` 로 서비스별 실행을 시도하고, 안 돌렸으면 미검증이라고 쓴다.
- **UTC/KST 혼재**: 스크리너 산출물의 `generated_at` 은 UTC(`...Z`), 발행·서버 로그는 KST(`+09:00`).
  그대로 비교하면 9시간 낡은 파일을 '낡았다'고 오보한다.
- **단타 스크리너는 컨테이너 안에서 돌려야 모델 점수가 붙는다**: 호스트에서 `python3 scripts/daytrading_screener.py` 를
  돌리면 `ModuleNotFoundError: No module named 'app'` 로 챔피언 모델을 못 읽고 **칼만/거래량 점수만으로** 후보를 낸다
  (로그: `챔피언 모델 미가용`). 실행은 `docker exec -w /app -e PYTHONPATH=/app stock_xgboost_ml python scripts/daytrading_screener.py ...`
  — 그러면 `챔피언 모델 예측: N종목` 과 `모델 0.36` 같은 확률이 근거에 붙는다. 두 실행을 섞어 인용하지 말고 어느 쪽 출력인지 밝힌다
  (출력 CSV 경로도 다르다: 호스트는 `data/reports/`, 컨테이너는 `services/xgboost-ml/data/reports/`).
- **`market_data` 컬럼명**: `open_price`/`high_price`/`low_price`/`close_price`/`volume`/`trading_value` (`open`·`close` 아님).
  문자열 라벨과 `UNION ALL` 하려면 `::text` 캐스팅이 필요하다(`UNION types bigint and text cannot be matched`).
- **"0콜/0행 완료" 류 로그를 실패로 오진하지 않는다** — 진행파일이 있는 수집기는 이미 받은 구간을 전부 스킵한다.
  자격증명 차단은 `중단: HTTP 401 ...` 처럼 다르게 찍힌다.
- **데이터 공백의 원인은 '도달성 → 러너 존재 → 러너 내부 의존성' 순서로 갈라라** (휴장·랙 배제 뒤): 실측(2026-10-01)
  disclosures(최신 09-23)와 us_market_data(0행)는 둘 다 '수집이 멈춘 표'였지만 원인이 정반대였다.
  ① **러너·스케줄 부재**: DART 공시 표를 채우는 경로가 수동 백필(`scripts/dart_disclosure_backfill.py`) 하나뿐이고
  `/etc/cron.d`·사용자 crontab·Hermes 크론 어디에도 없었다(서비스 내 DartCollector 는 import 하는 코드가 0곳 = 미배선
  죽은 코드). 네트워크는 정상이었다(list.json 도달 200 direct·proxy 동일). → 결손은 백필 재실행으로 즉시 복구
  (`--since` 창 + `--types`), 재발 방지는 크론 등록이다. R17 SNS 와 같은 유형.
  ② **러너 내부 의존성 결손**: us_market_data 는 `import yfinance` 가 컨테이너 이미지에 없어(requirements 에 항목 자체가
  없음) 08:30 배치가 매일 ModuleNotFoundError 로 죽고 있었다 — 컨테이너 로그에 그 예외가 반복 기록돼 있다.
  ③ **소비자 0곳 확인을 먼저 하라**: us_market_data 를 읽는 코드가 레포에 0곳(정의는 init-sql, 쓰기만 존재)이면
  그 공백은 모델·매매 영향이 0 → 우선순위를 내려라. 공백을 발견하면 '영향(소비자 유무)'을 함께 재서 순위를 정한다.
  표백필 스크립트의 창 크기는 페이지 루프 때문에 콜 수가 급증한다(실측: 2개월 창 × 5유형 = 75콜 3분35초 vs
  이번 달 창 × 4유형 = 5콜 12초) → 크론에는 좁은 daily + 넓은 주 1회 안전망으로 나눠 넣어라.
  크론을 넣은 뒤의 검증은 추측하지 말고 `/var/log/syslog` 의 `cron[...]: (*system*<file>) RELOAD` 줄(파싱 성공 증거)과
  다음 정시 발화 로그 파일로 확인하라. 단 **`/etc/cron.d` 에 새 파일을 넣은 직후에는 RELOAD 줄이 없는 것이 흔하다**
  (Ubuntu cron 은 inotify 로 읽고 그 줄을 남기지 않을 수 있다) → 그 줄의 부재를 파싱 실패로 읽지 말고,
  같은 명령줄을 **root 로 직접 실행해 rc=0** 을 확인한 뒤 **첫 정시 발화 로그**로 판정하고, 그 전까지는
  보고에 '배선 완료 · 실발화 미검증'으로 적는다. 단 **`syslog` 는 rotate·suspend 로 구간이 끊긴다** — 15일짜리 세션을 담은
  `syslog.1` 에 CRON CMD 줄이 **0줄**인 경우가 실제로 있다(첫·끝 줄 시각으로 범위를 먼저 확인한다). 그래서 **CMD 줄 부재를
  '그 잡이 안 돌았다'의 증거로 쓰지 마라** — 판정은 그 잡 자신의 로그(`reports/cron_*.log`) mtime 과 산출물
  (`reports/*_latest.json`) 낡음이고, 크론 파일에 항목이 살아 있는데 그 로그만 멈춰 있으면
  '미실행(원인 미확정)'으로 쓰고 **같은 파일의 다른 잡이 같은 날 정상 실행됐다**는 사실을 함께 인용한다
  (파일 전체 파싱 거부 = 전부 안 돎 → cron 파일을 의심 / 일부만 안 돎 → 그 잡의 래퍼·가드를 의심으로 갈린다).
- **데이터 공백을 실패로 단정하기 전에 휴장·랙을 먼저 배제한다**: ① 휴장일 목록(`data/krx_holidays.json`
  등)에서 그 날짜가 휴장인지 본다 — 연휴·주말 구간에 행이 없는 것은 정상이고, 그것을 "수집 실패"로
  보고하면 없는 장애를 만들어 사람을 부린다. **그 목록이 올해를 덮는지 연도별 항목 수를 먼저 센다** —
  커버리지가 연중에 끊겨 있으면 이후 휴장일이 '거래일'로 판정돼, 휴장 공백을 장애로 오진하고 실험 가드도
  휴장일에 걸린다. ② KRX OpenAPI 일별매매정보 계열은 **D-2 반영**이라
  "오늘 날짜가 비어 있음"도 정상일 수 있다. ③ 공백 판정은 "거래일인데도 없음" + "그 뒤 다른 소스도
  같이 멈췄음"이 함께 성립할 때만 한다. ④ **그 테이블을 실제로 갱신하는 러너를 확인한다** — 낡은 `max(날짜)` 를 보면
  `grep -n 'INSERT INTO' scripts/<수집기>.py` 로 적재 대상 테이블을 먼저 확인하라. 적재 스크립트가 **다른** 테이블에 쓰는
  레거시 테이블(예: `krx_trading` ← 현행 `scripts/krx_daily.py` 는 `market_data` 에 적재)의 낡은 날짜는 수집 실패가 아니므로
  '공백 후보(미검증)'로만 적고 장애로 쓰지 않는다. ⑤ **낡음이 진짜면 원인을 단정하지 말고 이 순서로 좁힌다**:
  ① 그 러너를 부르는 스케줄 항목이 있는가(`/etc/cron.d/*`·`crontab -l`·Hermes 크론·Windows 예약작업에서 스크립트 파일명 grep —
  0건이면 '정기 실행 없음' 확정) ② 스크립트가 사람이 인자를 주는 **수동 전용 백필**인가(docstring 의 '사용' 절)
  ③ 서비스 내 수집기가 배선됐는가(`grep -rn "<ClassName>" --include=*.py .` → 자기 파일만 나오면 미배선 죽은 코드)
  ④ 러너 로그의 반복 예외(`docker logs --since 7d <c> | grep -nE "No module named|Traceback|429|401"`).
  그 다음에만 도달성을 실측한다 — 사용자가 "이제 네트워크는 괜찮다/네트워크 문제겠지"처럼 **원인을 단정해 와도 그 전제를
  프로브로 검증**하고(200 이면 원인은 ①~④ 중 하나), 틀렸으면 그 자리에서 정정 보고한다. 마지막으로 그 표를 **읽는** 코드가
  0곳이면 죽은 출력이므로 모델 영향 0 → 우선순위를 낮춘다. 결손 채우기(수동 백필 실행·검증)와 재발 방지(스케줄 신설 =
  `needs_approval` 백로그 등록·커밋)까지의 절차는 `references/stale-source-diagnosis.md` 를 따른다.
  ⑥ **일정을 약속하기 전에 '다음 거래일'을 캘린더로 확정하라** — 다음 평일이 곧 다음 거래일이 아니다.
  연휴(예: 토 개천절 + 월 대체휴일)가 끼면 다음 거래일이 며칠 뒤로 밀리므로 "내일 08:40 에 복구 확인"이라는 약속이
  틀린다. 그때도 08:30 파이프라인 크론은 캘린더를 모른 채 그대로 돌지만(휴장일에 돌아 산출물을 갱신), 첫 매매
  가능 세션은 달력이 정한다 → 보고에 **'산출물 갱신 시점'과 '첫 매매 가능 세션'을 따로** 적고, 휴장일 틱이 조용한 것은
  정상이라고 라벨을 붙인다.
- **상위 파이프라인이 단계 실패를 삼키고 "완료"로 끝난다.** phase 로그의 마지막 줄(`Pipeline finished`)만
  보면 성공으로 읽히지만 그 본문에 `... 건너뜀(...미설정)`·`Traceback`·`0 rows` 가 있으면 그 소스는
  그날 수집되지 않은 것이다 — 종료코드가 아니라 **소스별 행수**로 판정한다.
  단 **건너뜀 사유가 자격증명 미설정이고 그 경로를 대체하는 별도 크론이 같은 날 돌았으면 결함이 아니라
  '대체됨'으로 적는다**(예: `KRX_ID/KRX_PW 미설정` → `kis_supply`·`kis_short_program`·`krx_offline` 이 대체 수집).
  같은 원인의 다른 증상이 **`KeyError: '지수명'`** 이다 — 지수·투자자별 파싱 버그가 아니라 pykrx 웹 로그인 실패로
  HTML 이 와서 컬럼이 통째로 없는 것이므로, 코드를 뜯기 전에 `.env` 의 `KRX_ID`/`KRX_PW`(OpenAPI 키와 별개) 유무를 본다.
  대체 경로의 로그 시각·행수를 함께 인용해 '정말 메워졌는지'를 보이고, 메운 경로가 없을 때만 공백으로 보고한다.
- **사이클이 계속 '0건'인데 도달성은 정상이면 그 프로세스가 실패 상태로 굳은 것이다 — 재시작으로 새 사이클을 강제하라.**
  순서: ① `docker logs --since 3h <c> | grep -aiE "rss|error|collected"` 로 실패 문구 확인
  (`Temporary failure in name resolution`·`Network is unreachable` = 단절 구간의 흔적) ② **컨테이너 안에서** 같은 URL 을
  직접 받아 200 인지 본다(`docker exec <c> python -c "urllib.request.urlopen(...)"`) — 호스트 `getent hosts`·egress 204 만으로
  '네트워크는 정상'이라 결론내지 않는다 ③ 200 인데도 다음 사이클이 0건이면 `docker restart <c>`.
  실측: 뉴스 RSS 가 19시간 0건(사이클은 32분마다 `Collected 0 articles` 로 정상 기록, 수동 fetch 200) → 재시작 직후 93건 적재.
  보고에는 **공백 구간(마지막 정상 적재 시각 → 복구 시각)과 복구 후 적재량**을 숫자로 적고, 그 공백을 야간 검증 임계가 잡았는지도 함께 쓴다.
- **산출물의 mtime 만으로 '잡이 돌았다'고 결론내지 않는다** — 발행기는 발행 시각으로 `generated_at` 을 갱신하므로 낡은 후보가 신선해 보인다.
  후보의 `signal_date`(기준일)를 같이 본다.
- **`/etc/cron.d` 에 `<name>.disabled` 가 있으면 의도적 중단 상태다**(cron 은 파일명에 `.` 이 있는 파일을 파싱하지 않는다).
  보고에 "무엇이 멈춰 있는지 + 되돌리는 한 줄"을 함께 적고, `systemctl` 유닛(피드 서버 등)은 크론이 아니므로 별도로 본다.
- **Hermes 크론의 `last_status=error` 를 코드 결함으로 읽지 마라.** 오류는 세 부류로 갈린다:
  ① `Interrupted by shutdown before terminal completion` = 실행 중에 스케줄러/호스트가 끝낸 것이라 그 틱은
  다음 슬롯에 다시 돈다(코드 문제 아님). ② `RuntimeError: ... can't reach the model provider` 와
  `last_delivery_error` 의 전달 대상 DNS 실패가 **같은 시각대에 함께** 찍히는 것 = 그때 네트워크가 끊긴
  것이라 코드 문제가 아니다 — 이 경우 **야간 틱이 돌 것이라고 약속하기 전에 지금 도달성을 실측한다**:
  `getent hosts <제공자 호스트>` 와 `curl -s -o /dev/null -w '%{http_code}' <base_url>`(401/200=도달,
  인증 오류도 도달의 증거다) + 전달 대상 호스트도 같이. ③ 같은 오류가 반복되면서 그 역할 원장에 항목이
  계속 안 늘 때가 진짜 결함이다 — 오류 문자열을 인용하고 다음 실행 시각을 함께 적는다.
  그리고 야간·장외 틱의 결과는 전달 대상(Discord 등)으로 가고 **이 세션에는 오지 않는다** — 세션에서
  확인하려면 원장과 폴러 로그를 읽는 수밖에 없으니, 보고에 그 경로를 명시한다.
- **성능·처리량 수치는 load average 와 함께 보고한다.** 다른 학습/수집 작업이 도는 중에 잰 값은 경합으로 왜곡된다
  (같은 임베딩 모델이 스레드 1에서 20~30문장/초, 스레드 4에서 6~8문장/초로 나온 실측). 경합 상태 숫자를 정상치처럼 인용하지 않는다.
- **모델·실험 상태는 "오늘 돌린 작업"과 "라이브에 반영된 것"을 분리해서 보고한다** — 후보 디렉터리에 좋은 AUC 가 생겨도
  게이트 승격 전이면 매매에 쓰이는 모델은 여전히 예전 챔피언이다.
- **브리지 재기동 폭주(restart storm)로 오진하지 마라 — `bridge_sup.log` 를 먼저 읽는다.** 사용자가 Creon 에
  재로그인한 날은 감독기가 의도적으로 브리지를 여러 번 세웠다 내린다: `Creon not running - stopping the bridge
  so the Creon updater can replace NCFSYS data files` → `Creon updater running (spdn/coStarter) - bridge stays
  down until it finishes` → `Creon up and not updating - starting bridge` → `bridge connected and answering
  broker queries` (중간에 `bridge HALF-DEAD: /health connected but /balance failed` 1회 재시작이 끼기도 한다;
  포기하면 `RE-LOGIN REQUIRED` + 백오프). 그래서 `bridge.log` 에 `=== log opened pid=...` 가 몇 분 안에 여러 줄
  찍히는 것은 정상 동작인데, `bridge.log` 만 보면 "재기동 폭주"로 읽힌다 — 판정은 `bridge_sup.log` 의 **마지막
  줄**이 `... answering broker queries` 인지로 한다. 복구가 확인된 뒤에는 **루프를 건드리지 않는다**: 문턱
  미만의 `CHECK failure N: bridge health: not connected` 는 다음 사이클에 스스로 넘어가고(정상 `monitor`
  사이클, `consecutive_failures` 0 으로 복귀), `RESUME (auto)` 줄은 halt 가 실제로 걸렸을 때만 나온다 —
  halt 가 안 걸린 실패에 재기동·`resume` 을 제안하지 않는다. 사용자가 "다시 로그인했어"라고 알려온 경우도 같다 —
  손으로 재기동하지 말고 `bridge_sup.log` 마지막 줄이 `... answering broker queries` 가 되기를 기다린다(감독기는 ~3회
  폴링 ≈90초 만에 스테일 브리지를 정지·재기동하고 스스로 확인한다). 그 사이 `runner.log` 에 남는
  `tick watch failed: bridge unreachable` 한 줄은 `consecutive_failures` 를 올리지 않으므로 실패로 세지 않는다.
  **장외 재로그인에서 복구되는 것은 브리지뿐이다** — `loop_sup.log` 의 `outside 08:30-15:35 on a weekday - loop not started` 는 정상(다음 창에서 기동)이고, `bridge_sup.log` 의 `Creon host processes changed (<old pids> -> <new pids>) - restart budget reset` 이 사람의 재로그인을 감지해 재시도 예산을 되돌린 증거다. `RE-LOGIN REQUIRED` 로 몇 시간 멈춰 있던 브리지도 재로그인만으로 ~90초 안에 `... answering broker queries` 로 돌아오므로, 그 상태를 코드 결함으로 진단하지 않는다.
- **로그인 뒤 업데이터가 '다운로드 오류'로 멈춘 것을 경로 장애로 오진하지 마라 — 부분 다운로드로 다뤄라.**
  서명은 `spdn.exe` 종료 + `coStarter.exe` 잔존(에러창 대기) + `C:/daishin/ncfsys/spednettmp/data/cct.dat.zip`
  mtime 정지다. 그 파일 크기가 곧 진행률이고(전체 24,303,787 bytes) **재시도는 그 크기에서 이어받으므로 반복 재시도가
  정상 복구 절차**다.
  **큰 파일 하나가 끝나도 업데이트는 끝이 아니다** — 이어서 `cct3`·`elsbond`·`expfuture`·`fo_itm_mst`·`fomst` 같은
  소용량 컴포넌트를 순서대로 받는다(목록은 `spednettmp/speddown.lst`). 그래서 진행률은 파일 하나가 아니라
  **디렉터리 총량**(`du -sb /mnt/c/daishin/ncfsys/spednettmp/data` 를 60초 간격으로 두 번)으로 재라. 완료 신호는
  ① 그 디렉터리가 비워지고(`ClearSpednetTmpDir`) ② `CpStart.exe`·`DibServer.exe` 가 뜨는 것이며, 그 뒤 감독기가
  `started bridge pid ...` 로 브리지를 올린다(이때 `/health` 가 `connected:true`). 보고는 "받은 바이트/전체(%) + 평균 속도(증가분÷경과) + 남은 재시도 감"으로 적고, 재실행은 사람이 한다.
  **같은 회선을 쓰는 수집 배치가 돌고 있으면 먼저 그것을 멈출지 물어라** — 이 라인은 프록시 경유 단일 회선이라
  WSL 수집(예: 저녁 파이프라인의 수집 phase)이 업데이터와 대역폭을 나눠 쓰고, "회선 용량(25MB 테스트 ~180KB/s) vs
  실측 속도(수십 KB/s)"의 격차가 경합의 증거다. 브리지는 업데이터가 끝날 때까지 내려가 있는 것이 정상이므로
  (`bridge_sup.log` 의 `Creon updater running ... bridge stays down`) 그 상태를 장애로 쓰지 않는다.
  감시는 20초 간격으로 부분파일 크기·속도를 JSONL 로 남기고 `크기 ≥ 전체` 또는 `/health` 응답 중 하나에서 멈춘다.
- **인터롭이 죽으면 Windows 쪽 프로브가 전부 빈 문자열로 돌아온다** — `curl.exe`·`powershell.exe` 호출이
  `WSL ... UtilAcceptVsock:273: accept4 failed 110` 만 찍고 끝나고, 그 빈 응답은 '브리지 사망'처럼 보인다.
  이때 Windows 의존 수치는 **미검증**으로 라벨을 달고(추정으로 '죽었다'고 쓰지 않는다). **이 고장은 스스로 회복되는
  경우가 있다** — 같은 명령을 1~2분 간격으로 다시 던져 `cmd.exe /c echo ok` 가 돌아오면 그대로 계속 진행하고,
  회복되지 않을 때만 Windows 에서 `wsl --shutdown` 후 재시작을 안내한다(그 복구는 진행 중인 WSL 잡(배치·피드)을
  끊으므로 그것이 끝난 뒤에). 대체 경로도 함께 확인한다: `tailscale status` 에 Windows 노드가 `offline` 이면
  ssh 우회도 없다.
- **"매매 0건" 을 회귀로 단정하기 전에 ① 거래일 여부 ② 저널 `decisions` 행수 ③ 브리지 도달을 *어느 컨텍스트에서* 쟀는지 를 확정한다.**
  휴장·주말에는 브리지가 내려가 있어 0건 + `connection_failed` 가 **정상**이다 → `data/krx_holidays.json` 과
  `weekday()>=5` 로 거래일을 먼저 확정하고, 휴장이면 그 사실을 보고 첫 줄에 쓴다(이 순서를 건너뛰면 정상인 날을 '이틀째 회귀'로 오보한다).
  거래일인데 0건이면 후보를 하나씩 배제한다: ⓐ `decisions` 0행 = 루프가 후보를 평가조차 못 한 것(피드 발행 건수와 함께 인용)
  ⓑ 조회는 Windows 측(`curl.exe --noproxy '*'`)이고 루프는 다른 컨텍스트(예: WSL 안의 `127.0.0.1`)에서 접속하므로,
  한쪽의 connection refused 를 '브로커와 끊겼다'로 쓰지 말고 **양쪽에서 각각 재현해 끊긴 경로를 특정**한다
  ⓒ 그 피드 스키마를 바꾼 직후 첫 소비 세션이면 새 필드 수용 여부를 확인한다. **어느 것인지 특정하기 전에는 원인을 단정하지 않고,
  대신 다음 판정 시각·판정 기준(예: 다음 거래일 09:0x 의 `decisions` 행수)을 함께 적는다.**
- **브리지와 루프는 별개 프로세스다 — 포트/상태파일로 루프 생존을 판정하지 마라.** 브리지만 살아 있고
  루프(`run_market_loop.py run --loop`)가 죽어 있는 상태가 정상 보고와 구분되지 않는다(실측: 상태파일 `halt:false`
  인데 루프 프로세스 0개). 프로세스 목록을 직접 세고, **명령줄까지** 본다:
  **프로세스 목록은 PowerShell 로 받는다** — 명령줄까지 나와 브리지/루프 구분에 바로 쓴다:
  `/mnt/c/Windows/System32/WindowsPowerShell/v1.0/powershell.exe -NoProfile -Command "Get-CimInstance Win32_Process -Filter \"Name='python.exe'\" | ForEach-Object { \$_.ProcessId.ToString() + ' ' + \$_.CommandLine }"`
  → `run_bridge.py` 1개 + `run_market_loop.py` 1개가 정상. 하나만 있으면 그 하나가 죽은 것이다.
  **WSL 쪽에서 `pgrep -f '<패턴>'` 으로 세면 래퍼 셸까지 세어 +1 이 된다**(게이트웨이가 1개인데 2로 보인다) —
  프로세스 수는 `ps -eo pid,etime,args | grep -F '<패턴>' | grep -v grep` 으로 명령줄을 갈라 세고 그 출력을 근거로 보고한다.
  **2개면 중복이다 — 하나만 남긴다(둘 다 주문 경로를 돌면 같은 책을 이중 관리한다).** 재현되는 조합: 감독기
  (`trader-loop-sup`, 08:30 창 기동)가 띄운 루프 위에 08:50 예약작업(`trader-loop` → `loop_start_bg.bat`)이
  하나 더 띄운다. 판정은 `tools/loop_pids.ps1` 이 pid 2개를 찍는 것 + `runner.log` 에 **같은 라인이 두 번씩**
  찍히는 것(`PRE filter swing: 20 -> 18` 중복, `cycle done (monitor)` 중복)이다. bat 의 단일 인스턴스 가드는
  PowerShell 호출이 실패하면 **fail-open(그냥 시작)** 이라 이 경우를 막지 못한다 — 있으면 정상으로 읽지 말고,
  `CreationDate` 가 늦고 `ParentProcessId` 가 이미 사라진(고아) 쪽을 `taskkill /PID <pid> /F` 로 끝낸 뒤
  `loop_pids.ps1` 이 1개를 찍고 `runner.log` 가 같은 간격으로 다시 자라는지로 검증해 보고한다. 재발 방지는
  사람 결정 사항으로 올린다(08:50 런처 태스크 비활성 = 감독기 단일 기동, 또는 가드를 pidfile 기반 fail-closed 로 교체).
  다만 다음 예외가 있다:
  ① **`/FI "IMAGENAME eq X"` 필터는 `cmd.exe /c` 뿐 아니라 WSL 인터롭에서 직접 실행해도 깨진다** — 인자 따옴표가
  재조립되며 `잘못된 부업/옵션 - 'eq'` 로 실패한다(필터를 붙인 tasklist 를 신뢰하지 마라). 필터 없이 `/FO CSV /NH` 로
  전부 받아 캐리지리턴을 제거한 뒤 `grep -a` 로 거르거나(제거 없이는 `binary file matches` 로 멈춘다), 위 PowerShell 한 줄을 쓴다.
  ② **감독기는 python 프로세스가 아니다** — `python.exe` 0개는 '브리지·루프가 없다'는 뜻일 뿐 '감독기 사망'이 아니다.
  `Get-CimInstance Win32_Process -Filter "Name='powershell.exe'"` 에 `supervise_bridge.ps1`·`supervise_loop.ps1`·
  `run_tunnel.ps1` 이 보이면 감독기·터널은 살아 있고, `bridge_sup.log` 마지막 줄이 `Creon not running - waiting for the
  Creon login` 이면 그건 사람 단계다(로그인하면 1~2분 내 브리지 기동, 루프는 창 안에서만). 재부팅 직후의 기본값이 이 상태다.
  ③ **`bridge_sup.log`·`loop_sup.log` 는 상태 변화만 append 한다** — mtime 이 몇 분 멈춘 것을 감독기 정지로 읽지 마라
  (같은 상태가 이어지면 아무 줄도 쓰지 않는다). 생존은 프로세스로, 판정은 마지막 줄로 한다.
  ④ 로그는 `trader-agent/` **루트**의 `runner.log`·`bridge.log`·`bridge_sup.log`·`loop_sup.log`·`loop.out.log` 다
  (`logs/` 하위가 아니다 — `logs/runner.log` 를 tail 하면 `No such file or directory` 로 헛짚는다).
  ⑤ **마감 후에는 브리지만 남는 것이 정상이다** — `loop_state.json` 의 `daily.closed=true` 와 `daily.summary`(실현손익·reconcile)
  가 있으면 close 사이클을 마치고 루프가 **스스로 종료**한 것이므로 '루프 사망'으로 보고하지 말고, 다음 개장 전 기동이
  필요하다는 사람 단계로 적는다.
- **`loop_sup.lock` 이나 `another supervisor holds loop_sup.lock - exiting` 줄을 '스테일 락 스팸'으로 오진하지 마라.**
  락 파일 내용이 곧 소유 pid 다 — `Get-Content <repo>\loop_sup.lock` → `Get-Process -Id <pid>` 로 생존을 확인한다.
  **살아 있으면 정상**이다: 30분 트리거가 상주 감독자와 겹쳐 그 사본이 exit 0 한 것이고, 주문 경로는 그 감독자가 계속
  감시 중이다. 죽어 있으면 다음 시도가 락을 지우고 뜨므로 그것도 자가 복구다 — 둘 다 결함이 아니다.
  실측: 하루 39줄을 '스테일 락'으로 보고했지만 소유자는 살아있는 `supervise_loop.ps1` 이었고, 실제 결함은 그 줄에
  보유 pid 가 없어 파일을 열지 않고는 판정이 안 된다는 점뿐이었다. 같은 유형을 만나면 그 줄에 pid 를 남기도록
  스크립트를 고친다 — 검증은 ① AST 파싱(`[System.Management.Automation.Language.Parser]::ParseFile`) ② 임시 repo
  디렉터리에 살아있는 pid 를 써 넣고 `TRADER_REPO` 를 그 임시 경로로 주어 메시지만 재현(실 락 파일·실행 중 감독기는
  건드리지 않는다). patch 를 적용한 뒤에도 감독자 생존(`Get-Process -Id <pid>`)과 `loop_sup.lock` 내용 불변을 확인해 보고한다.
- **`loop_state.json` 은 버전 래핑 구조다** — `{"version":1,"loop":{"halt":false,...}}`. 최상위 `halt` 를 읽으면
  조용히 `None` 이 나오고 "상태 없음/미설정"으로 오독한다 → `d["loop"]["halt"]` 로 읽고, 그 값이 `false` 라도
  프로세스 생존 확인을 대체하지 못한다(래치는 재기동해야 의미가 있다).
  **`loop.last_cycle` 이 `None` 이어도 루프는 살아있을 수 있다** — 사이클이 300초 간격이라 그 순간엔 비어 보이고,
  키 구조도 버전마다 다르다. 생존은 **프로세스 + `runner.log` mtime/크기 증가**로 판정하고, 상태파일의 빈
  필드를 사망 근거로 쓰지 않는다.
- **`runner.log` 의 `cycle done (halted); sleeping 60s` 줄에는 타임스탬프가 없다** — 오늘 것인지 알 수 없으므로
  날짜를 붙여 해석하지 말고, 루프 생존은 프로세스 목록으로만 판정한다(`loop.out.log` 도 기동 시 1줄만 남긴다).
- **죽은 런의 원인을 컨테이너 재생성이나 OOM 으로 단정하지 마라.** 진행 중이던 실험이 `rc=137`(SIGKILL)로 끝나고
  드라이버가 "컨테이너 재생성 추정"을 기록해도, `docker inspect -f '{{.RestartCount}} {{.State.StartedAt}} {{.State.OOMKilled}}'`
  과 해당 cgroup 의 `memory.events`(`oom_kill`)·`StartedAt` 이 **모두 0/부팅 시각 그대로**면 인프라 사망이 아니다.
  실측 원인은 **같은 워크스페이스에서 병렬로 돌던 다른 Hermes 세션(역할 크론)이 판단으로 정지**시킨 것이었고,
  그 근거는 원장 `attempts`/`retry_note` 의 "판단 정지" 문구와 `est_minutes` 정정이었다.
  진단 순서: ①`ps -eo pid,etimes,args | grep -E 'cron.scheduler|external-worker|<학습 스크립트>'` 로 병렬 세션·워커 확인
  ②원장/백로그의 재시도 기록 확인 ③그래도 모르면 컨테이너 지표 배제 사실을 보고에 쓴다. 모르는 원인을 추정으로 확정하지 않는다.
- **재기동이 필요한 상태를 보고할 때는 실행 파일명과 검증 방법을 함께 준다** (예: `loop_start_bg.bat` → 검증은
  python 프로세스가 2개가 되는 것 + `runner.log` 에 `(halted)` 없는 `cycle done`).
- **루프 로그의 괄호 안 숫자는 *위치*다 — 필드 이름을 사유로 읽지 마라.** `PRE filter close: 20 -> 0
  (stale/blocked 0, window 20, price 0)` 의 `price 0` 은 "가격 조회 실패/가격 0원"이 아니라 R3 편차 검사로
  걸러진 **건수**다(포맷 문자열은 `runner/loop.py`). 로그 한 줄에서 결함을 명명하기 전에 그 줄을 만든 format
  문자열을 읽는다 — 안 그러면 없는 장애를 만들어 사람을 부린다.
- **문턱·게이트로 막힌 경로를 결함으로 보고하지 않는다.** `R1 block swing: r1: avg prob X < Y (calibrated)` 는
  설계된 방어다. 피드의 `ml_auc` 를 함께 인용해 "모델 품질 때문에 닫힌 경로"로 적고, 문턱을 낮추는 제안은
  하지 않는다(리스크만 늘린다). 다만 **처방으로 "AUC 개선"을 쓰지 마라** — 실측(2026-10-04, 8모델 4축):
  단일분할 val AUC ↔ 다중폴더 OOS AUC 순위 상관 **−0.81**(거의 반대), AUC ↔ 체결가능 순기대 상관 **+0.10/−0.24**,
  어떤 모델도 풀평균(시장 베타)을 넘지 못했다. 승격·개선의 판정 기리는 **돈 지표**다 — 체결 가능 OOS 순기대 > 0,
  **풀(세션 전체 매수) 기준선 대비 초과 ≥ +0.1%p**, t ≥ 2, 앞/뒤 절반 모두 양수. AUC 는 설명·경보용이다.
  (경로가 닫힌 원인을 모델 탓으로 단정하기 전에 **풀 기준선을 대보라** — 양(+) 겉모습이 베타인 경우가 흔하다.)
  **게이트 사유의 평균을, 피드 `items` 에서 직접 계산한 평균으로 갈아치우지 마라** — 두 값은 집계 대상이 달라
  어긋난다(실측: 같은 피드에서 피드항목 평균 85.9 vs 사유 문자열 `avg 87.8`). 보고에는 **사유 문자열을 그대로 인용**하고,
  스스로 계산한 값은 '피드 항목 기준'이라고 라벨을 붙여 따로 쓴다(섞어 쓰면 어느 쪽이 문턱 판정값인지 읽는 사람이 알 수 없다).
  게이트 이름도 뭉개지 마라 — 같은 날 R1 이 통과하고 HEAT 가 막는 경우가 있으므로 "R1+HEAT 이중 차단"처럼 합쳐 쓰면
  다음 행동(문턱 완화 vs 모델 개선)을 잘못 고르게 한다: **막고 있는 게이트 하나를 실측으로 특정해 인용한다**. close 경로의 두 문턱은 **다른 지표**를 본다 — R1 은 `R1 block close: r1: avg score 85.3 < 88.0`(후보 점수 평균), HEAT 는 `HEAT block close: heat: avg +25.3% > +15.0%`(당일 평균 상승률)로 찍힌다. 창이 열리기 전에는 `PRE filter close: 20 -> 0 (stale/blocked 20, window 0, ...)` 처럼 **stale/blocked 쪽 숫자가 전체**이고, 창이 지나면 `STALE skip <code>: valid_until ... passed` 가 붙는다 — 둘 다 정상이며 결함으로 쓰지 않는다.
  **다만 사용자가 강세장에서 못 산 것을 아쉬워하면, 그 정책을 "설계된 방어"라고 반복하지 말고 실측으로 답한다** — 순서:
  ① 시장 지수 등락 실측(코스닥/코스피) ② **차단된 후보 리스트 자체의 실측 등락**(지수보다 이 숫자가 답이다 — 리스트가 실제로 올랐는지)
  ③ 계좌 한도(종목 %·동시 슬롯)로 환산한 기회비용 ④ 사유를 **게이트(설계) vs 운영(루프 기동 지연·세션 끊김)** 으로 분리
  ⑤ 1일 표본 경고 + 그 게이트가 생긴 근거 수치(추격 손실). '리스트가 올랐다'와 '게이트가 옳았다'는 양립한다.
  게이트 비용을 재는 절차(후보 재생성 → **체결 가능성 필터(상한가 부근·저유동 제외)** → 게이트와 같은 집계로 부분집합 분리 →
  트레이더 프로파일과 같은 수익률 정의 → 세션 단위 평균·t)와 실측 결론(close 경로는 R1 이 사실상 죽은 문턱이고 HEAT 가
  실제로 닫는 문턱이며, **필터를 적용하면 두 게이트 모두 손실을 막고 있었다 — 완화 근거 없음**)은
  `references/gate-cost-measurement.md` 를 따른다. 원시 평균만 인용해 "게이트가 돈을 막는다"고 쓰지 않는다.
  **반대편 질문("왜 팔았지 / 아쉽다" = 청산 아쉬움)도 같은 태도로 답한다** — 순서:
  ① 청산 규칙을 프로파일에서 먼저 인용해 '손절이 아니라 설계된 청산'인지 가른다(close=익일 09:00-09:10 시가 매도,
  daytrading=당일 15:10-15:25, swing=손절·익절) + 저널 `hold_hours` 로 보유기간을 붙인다.
  ② 저널 `exit_reason` 이 `reconcile: broker flat` 이면 **우리 주문이 아니라 브로커가 이미 평탄**해진 것을 대조가
  사후 기록한 것이다 → 청산 창에 루프 사이클이 있었는지(`runner.log` 의 `=== log opened` 시각 + 창 안 `cycle: ts=` 줄)로
  '시스템이 팔았는가'를 판정하고, 창에 사이클이 없으면 **주문 주체는 로그로 확정 불가**라고 쓰고 사람/외부 체결로 단정하지 않는다.
  ③ 수량×(청산가 이후 최신가 − 청산가)를 **종목별로 +/− 를 나눠** 계산하고 계좌 대비 % 를 붙인다(세 건 중 잘 판 건도 있다
  — 합계만 쓰면 사실이 뭉개진다). 최신가는 외부 일봉
  `https://api.finance.naver.com/siseJson.naver?symbol=<code>&requestType=1&startTime=YYYYMMDD&endTime=YYYYMMDD&timeframe=day`
  로 확인하고, 모바일 `m.stock.naver.com/api/stock/<code>/basic` 의 `closePrice` 는 거래정지(HALTED) 표기가 붙은
  종목에 옛 체결가를 돌려주므로 그대로 인용하지 않는다(우리 DB 일봉과 교차 확인).
  ④ 표본 3건 이하면 전략 변경 근거가 아님을 명시하고, 원인을 **청산 규칙(설계) / 경로 미가동(운영) / 모델 품질**로 분리한다.
- **"왜 샀지 / 왜 잃었나" 는 진입 사유 텍스트가 아니라 `algo_json`·`decisions`·상대약세 세 곳으로 재구성한다** — 저널의
  `entry_reason` 은 시스템 진입에서 **빈 문자열이 정상**이다(사유 문장을 쓰지 않는다). 빈 필드를 '기록 누락'으로 단정하지 말고:
  ① 매수 근거 = `trades.algo_json` 의 `candidate`(`ml_prob`·`score`·`ml_auc`) · `sizing`(`equity`·`kelly_f`·`final_qty`) ·
     `stop_pct`/`tp_pct`/`exit_mode`, 게이트 통과 근거 = 같은 시각 `decisions` 행의 `gates_json`/`plan_json`.
     `entry_reason` 이 `reconcile: adopted from broker` 면 시스템 진입이 아니다(사람이 산 것).
  ④ **`algo_json.ml_prob` 이 비어 있으면 그 진입은 모델을 거치지 않았다.** 최근 진입 전건이 `ml_prob=None` 이면
     실매매는 규칙 기반(스크리너 `score`·`edge_score`)이고 챔피언 모델은 주문 경로 **밖**이다 — 그 상태에서
     "AUC 를 올리면 돈이 된다"로 보고하거나 AUC 대비 실적을 비교하면 전제가 틀린다. 배선(피드가 ml_prob 을
     싣는지, 트레이더가 읽는지)을 먼저 확인하고, 손실 피드백이 `signal`(ml_prob<문턱)로 분류되는 것도
     문턱 미달이 아니라 **값 자체가 없어서**일 수 있음을 함께 적는다.
  ② 진입가의 성격 = 진입가 vs **당일 종가**·직전일 종가로 추격(프리미엄 %)/눌림을 판정해 붙인다.
  ③ 손실 원인 = 손절 체결(`tick stop`·−7% 등)은 **설계된 방어**이므로 원인이 아니다. 원인은 진입 품질이고, 판정 축은
     **보유 구간 종목 누적수익률 − 같은 구간 유니버스 평균**(`market_data` 두 날짜로 계산)이다: 시장이 플러스/보합인데
     −3%p 이하면 '종목 선택 실패(signal)' → 엔지니어, 시장과 같이 빠졌으면 '시장 베타' → 리서처. 분류·인계 산출물은
     `trade_loss_reviews`(컬럼 `ret_pct`·`net_pnl`·`cause`·`rel5_pct`·`owner`)와 `data/reports/loss_reviews.jsonl`.
  "사유 텍스트가 남지 않는 구조" 자체를 개선 항목으로 올려라(테제/진입 사유 기록).
- **0건 보고에는 '정상 방어'와 '원인은 따로 있음'을 함께 적는다**: 창 밖 탈락·문턱 미달은 방어지만, 청산 지연으로
  슬롯이 잠긴 것은 운영 결함이다. 둘 다 0건의 원인이므로 한 덩어리로 쓰지 않는다.
  **원인은 3분류로 갈라라 — 게이트 / 평가 대상 제외 / 슬롯·자금.** 판별은 한 줄이다:
  `SELECT screener, count(*) FROM decisions WHERE substr(ts,1,10)=<오늘> GROUP BY 1` — 그 스크리너 **행 자체가 0**이면
  "게이트가 막았다"가 아니라 **루프가 그 경로를 평가조차 안 한 것**(`RunnerConfig.screeners` 미포함)이다.
  프로파일·피드 키·크론을 다 배선해도 거래 목록에 없으면 진입은 영원히 0건이고, 로그에는 PRE 필터 집계만 남아
  배선된 것처럼 보인다 → "왜 안 사나"에 게이트 사유를 인용하기 전에 이 분포를 먼저 찍는다.
- **청산가가 추정치면 추정이라고 쓰고 교차검증 수치를 붙여라.** 브리지의 시장가 주문 응답은
  `order_no: 0`·`price: 0` 인 경우가 있어 체결가가 저널에 안 남는다(주문 시점 bid 로 기록하는 것이 유일한 규약).
  그때 `fees` 를 매도대금×0.002 로 넣고 **청산 전후 `/balance` 현금 차이 = 실제 매도대금**으로 검증해
  두 숫자의 차이(원)를 보고에 적는다. `fees=0` 인 청산 행은 gross 이므로 "손익 +29원(수수료 미계상)" 처럼
  라벨을 달고, 수수료 계상 크론(16:05 류) 반영 뒤 재집계한 값을 최종치로 쓴다.
  **같은 거래의 저널 `exit_price` 와 브로커 `avg_sell_price` 가 다를 수 있다 — 돈은 브로커 숫자로 쓴다.**
  저널이 목표가·직전가를 청산가로 남긴 경우(실측: 저널 38,100 vs 브로커 평균매도 37,968) 그 차이만큼 실현손익이
  달라진다. 보고에는 두 수치를 함께 적고 "실제 돈은 브로커 기준(합계 −N원)"이라고 라벨을 붙이며, `fees=0` 행이면
  gross 임을 같이 쓴다. 부분체결·호가 차이를 단정하지 말고 차이 사실과 금액만 적는다.

- **"역할들이 어떻게 통신하나" 질문은 `references/role-handoff-architecture.md` 를 따른다** — 6계층(크론 context_from 체인 · 공유 산출물 · 핸드오프 계약 · 교차 락 · 외부 위임 · 사람/오케스트레이터)과 한계(폴링·산문 계약)를 함께 적는다.

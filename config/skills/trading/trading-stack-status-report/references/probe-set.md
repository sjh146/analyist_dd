## 프로브 세트

1. 시각·스택: `date` · `docker ps --format 'table {{.Names}}\t{{.Status}}'`(17개 Up expected) ·
   `systemctl is-active analyist-feed.service` (+`is-enabled`).
2. 최근 산출물: `find <repo> -mmin -2880 -type f -printf '%TY-%Tm-%Td %TH:%TM %s %p\n' | sort -r | head -40`
   → 어떤 잡이 언제 끝났는지, 어떤 산출물이 갱신되었는지. 이어서 각 잡 로그 tail.
3. 피드: `curl -s --noproxy '*' 127.0.0.1:8090/health`(`items.close`/`items.swing`, `age_seconds`) +
   (`--noproxy '*'` 를 빼면 이 셸의 `http_proxy` 를 타서 **502** 를 받고 살아있는 피드·Prometheus 를
   '죽었다'로 오보한다 — 호스트 스크립트도 같은 함정이므로 로컬 요청은 코드에서도 우회한다)
   `data/feed/screener_latest.json` 파싱(전략별 건수, score min/median/max/spread, `signal_date`).
   **최상위는 `{generated_at, source, candidates}` 이고 `candidates` 는 전략 키의 dict 다**
   (`{"close": {"items": [...]}, "swing": {"items": [...]}}`) — 리스트로 가정하고 `d['candidates'][0]` 을 읽으면
   `KeyError: 0`, `len(candidates)` 는 2(전략 수)가 나와 '후보 2건'으로 오독한다. 후보 1건의 필드는
   `stock_code`·`stock_name`·`score`·`score_kind`·`signal_date`·`valid_until`·`rank`·`reason`(전략별 후보는 `items` 키).
   후보가 0건이면 발행기가 조용히 비운 것이므로 → `references/feed-empty-diagnosis.md` 순서로 내려간다.
   **전략마다 원천 파일이 다르다 — swing 피드의 원천은 `reports/swing_latest.json` 이고 그 파일을 만드는 것은 08:30
   스윙 파이프라인 하나뿐이다**(`scripts/feed_export.py` 는 `reports/*_latest.json` 을 읽어 합치고, 저녁 파이프라인은
   별도로 `reports/swing_candidates.json` 을 쓴다) → 모델·스크리너를 고쳐도 swing 피드는 **다음 평일 08:40 발행**에서야
   바뀌며 20:30 재발행은 같은 산출물을 다시 올린다. "고쳤는데 왜 아직 0건인가"의 답이 여기다 — 원천 mtime 과
   `generated_at` 을 나란히 인용해 다음 발행 시각을 함께 적어라.
   **daytrading 원천도 생성 주체가 따로 있다 — 없으면 0건이 정상값처럼 굳는다**: `reports/daytrading_latest.json` 을
   만드는 잡이 스케줄에 없으면 발행본 `candidates.daytrading.items` 는 매일 0건이고 스크리너 로직은 정상이다(수동 실행 =
   후보 20건). 상시 경로는 `cron/daytrading_pipeline.sh`(평일 09:10·12:10·14:10 = 단타 진입창 09:05~14:30 안)이고
   **컨테이너 안에서 돌려야** 챔피언 예측이 붙는다(호스트 실행은 사유가 전부 `모델미가용`). 0건을 보고할 때
   '원천 파일이 언제·누구에 의해 생성됐는가'를 함께 적어라.
   **close 후보 수가 줄었으면 회귀가 아니라 필터다**: `close_screener.py --max-day-change 0.25` 가 발굴일 등락 +25%
   이상(상한가 잠김 = 종가에 살 수 없다)을 기본 제외하고, 제외 건수는 `close_latest.json` 의
   `screener_meta.excluded_limit_up` 에 남는다(실측 662→658). 이 값을 읽기 전에 '후보 0/감소 = 결함'으로 쓰지 않는다.
4. DB: `market_data` 행수 · `min/max(trade_date)` · 거래일수 · 날짜별 행수와 거래정지 행(`volume=0`) · `stocks` 종목수.
   접속은 `docker exec stock_postgres psql -U stock_user -d stock_trading` — 자격증명은 레포 `.env` 의
   `POSTGRES_USER`/`POSTGRES_DB` 에서 읽고, `-U postgres` 는 role 이 없어 `FATAL: role "postgres" does not exist` 로 실패한다.
   날짜 컬럼명은 테이블마다 다르고 `disclosures` 처럼 `created_at` 이 아예 없고 `rcept_dt` 만 있는 테이블도 있으니 4b 의
   `information_schema.columns` 확인을 먼저 한다.
4b. **"수집·정제·학습 했어?" 류 질문은 데이터 소스별로 신선도를 표로 만든다.** 소스마다
   `<max(날짜컬럼)>` 과 `<max(created_at)>` 을 **나란히** 뽑으면 원인이 갈린다:
   둘 다 낡음 = 아무도 안 돌렸다(스케줄·러너 부재) / created_at 은 최신인데 날짜가 멈침 = **돌았는데
   신규 행이 안 들어왔다** / 둘 다 최신 = 정상. 날짜 컬럼명이 테이블마다 다르므로(`trade_date`·
   `analyzed_at`·`event_date`·`prediction_date`·`date`) `information_schema.columns` 로 먼저 확인한다.
   최소한 시세 · 뉴스 분석 · 뉴스 이벤트 · 수급(외국인/기관) · 공매도 · 프로그램 · 지수/파생 · 거시 · ML 예측을 본다.
   **수집 표와 정제 표를 따로 본다 — 앞은 최신인데 뒤만 멈춘 상태가 흔하다.** 공시 목록(`disclosures.rcept_dt`)은
   매일 갱신되는데 재무 파싱(`financial_statements.report_date`)이 반기에서 멈춰 3분기 보고서 1,181건이 목록에만
   쌓여 있었다. 원인은 파서가 **수동 전용**이라 스케줄에 없는 것(크론 grep 0건 = 결함)이고, 그동안 재무비율 피처는
   구 재무 기준으로 매일 정상처럼 재계산된다. 복구는 1회 백필(`dart_financial_backfill.py --reprt-code 11014 --year <연도>`,
   종목당 1콜·`--max-calls` 필수)이고 완료 판정은 `report_date` 행수다. '수집·정제 중이냐' 질문에는
   단계별로(목록 수집 / 이벤트 피처 / 재무 파싱) 최신값을 따로 인용한다.
   함께 **역할 원장**(`data/reports/{researcher,model_engineer,trader}_ledger.jsonl`)의 오늘 항목 수와 `verdict` 를
   세면 "수집·정제=리서처 / 학습·검증=엔지니어" 를 한 줄로 답할 수 있다.
5. 크론: `cat /etc/cron.d/<name>`(앞으로 몇 시에 무엇이 돌아 산출물을 덮는지까지 보고에 포함) ·
   **크론 로그는 레포 `reports/cron_*.log`** 에 있다(래퍼가 `/home/dduckbeagy/<repo>/reports/...` 로 리다이렉트) — `/home/<user>/cron/*.log` 는 없다.
6. 매매 쪽(Windows): 브리지는 **Windows 루프백 전용 바인딩**이라 WSL에서 직접 못 본다 (WSL에서 plain
   `curl http://127.0.0.1:8100/...` 는 **HTTP 000**, 게이트웨이 IP도 안 된다). **Windows 쪽 프로세스로**
   조회해야 하는데, 가장 싼 프로브는 **Windows 의 curl.exe** 다 — 그 자체가 Windows 프로세스라 루프백이 통한다.
   반드시 `--noproxy '*'` 를 붙인다(이 셸의 `http_proxy` 를 물고 502 를 받는다). 502 는 서비스 사망이 아니라
   프록시를 탄 증거다:

```
/mnt/c/Windows/System32/curl.exe -s --noproxy '*' -m 20 http://127.0.0.1:8100/balance
/mnt/c/Windows/System32/curl.exe -s --noproxy '*' -m 20 http://127.0.0.1:8100/positions
```

   한글 종목명이 깨지거나 JSON 을 코드로 다뤄야 하면 Windows 파이썬이 낫다. 어느 쪽이든 **연결 상태를 문장으로**
   얻는다(포트 테스트보다 강한 증거):
   `/mnt/c/Users/jhshi/Python312-64/python.exe -c "import urllib.request,json;print(json.loads(urllib.request.urlopen('http://127.0.0.1:8100/health',timeout=8).read()))"`
   → `{"ok":true,"connected":true}` 이고 `/balance`·`/positions` 도 같이 조회하면 실시간 값이 나온다(로그인 살아있음의 증거).
   **Windows 파이썬도 `--noproxy` 상당의 우회가 필요하다** — 인터롭 자식은 이 셸의 `http_proxy` 를 물려받아
   `/health`·`/balance`·`/positions` 세 개가 전부 `HTTPError 502` 로 오는 동안, 같은 순간 `curl.exe --noproxy '*'`
   는 정상 JSON 을 준다(실측). 즉 Windows 파이썬에서 502 를 보면 서비스가 아니라 프록시를 의심하고, 코드로 조회할
   때는 프록시를 끄거나(`ProxyHandler({})` / `NO_PROXY=*`) 조회만 curl.exe 로 하고 파싱은 받은 문자열로 한다.
   한글이 깨지면 `PYTHONIOENCODING=utf-8` 또는 `io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8')`.
   이어서 **루프 프로세스 생존**을 별도로 확인한다(아래 합정) · `bridge.log`/`runner.log` tail ·
   `bridge_sup.log`(감독기 판정 이력 — 재로그인 뒤 브리지가 스스로 복구됐는지의 근거) ·
   `loop_state.json` 의 `loop.halt`·`last_cycle.ts`·`reasons`.
   `reasons` 에 `not_connected`/`start_plus_and_retry` 가 있으면 "사용자가 Creon PLUS 로그인 후 브리지·loop 기동" 이 필요한 상태다 — 에이전트가 대신 못 한다.

   **“로그인돼 있나?” 질문의 정답은 두 축이다: 브리지 연결 AND 루프 프로세스.** 하나만 보고 “연결됨”이라 답하면
   자동매매가 죽은 상태를 정상으로 오보한다(실측: 브리지 `connected:true` + 루프 프로세스 없음, 상태파일은 `halt:false`).

   **"지금 크레온 로그인하면 되나?" 는 잡담이 아니라 준비 점검이다** — 순서대로 실측하고 숫자로 답한다(첫 항목은 로그인 성공의 **전제**다):
   · **브리지가 내려가 있는지** — `tasklist | grep -i python` + `powershell -c "(Get-NetTCPConnection -LocalPort 8100 -State Listen).Count"`.
     살아 있는 32비트 브리지는 `cct*.dat` 을 잡고 업데이터를 -1로 죽이므로 **정지 상태가 정상·좋은 소식**이다(고쳐야 할 문제로 쓰지 않는다).
   · **터널·직접 TCP** — `tasklist | grep mihomo` + `curl.exe --noproxy '*' -w '%{http_code}' http://www.gstatic.com/generate_204` → 204.
   · **HTS 상태는 프로세스로 판정** — `StSess*.exe` 뿐이면 HTS 꺼짐(사람이 실행해야 함), `coStarter`/`coMain`/`CpStart`/`DibServer` 가 있으면 켜짐.
     사용자가 "로그인했어"라고 말해도 프로세스로 확인한 뒤 답한다.
   · **내 인터롭 권한** — `whoami /groups | grep -a -oE 'S-1-16-(12288|8192)'`. `whoami` 출력은 인터롭에서 UTF-16이라 `grep -a` 없이는
     `binary file matches` 만 나오고 점검이 빈 것처럼 보인다(`findstr` 도 같은 이유로 안전). `S-1-16-12288`=High ⇒ 브리지·루프 재기동을
     내가 직접 할 수 있으니 사람에게 UAC 단계를 떠넘기지 않는다.
   · **예약작업·기동 이력** — `Get-ScheduledTask trader-bridge,trader-loop` + `Get-ScheduledTaskInfo`(`Disabled` 인 trader-loop 가 스택이 죽은 이유인 경우가 있다).
   · **장 마감까지 남은 시간 + 투자비중** — 14시에 시작하면 사실상 청산 관리만 돈다는 점을 분 단위로 붙여 쓴다.
   · **로그인 직후엔 브리지·루프가 이미 떠 있을 수 있다**(스타터/감독기 또는 사람이 기동). 프로세스 생성시각을 함께 읽고,
     "내가 켠 것이 아니다"를 명시한 뒤 누가 켰는지 확인한다 — 남이 켠 프로세스를 내가 기동한 것처럼 보고하지 않는다.
6b. **매매 현황은 '오늘 몇 건'이 아니라 '청산 창을 놓쳤는가'까지 본다.** 청산은 시각 창에 묶여 있다 —
   `trader_core/profiles.py` 의 DEFAULT_PROFILES 기준: close 는 14:50-15:29 매수 → **다음 세션 09:00-09:10
   시가 매도**(`exit_mode=next_open`), swing 은 09:00-15:20 + 손절·익절, daytrading 은 15:00-15:25 당일 청산.
   루프가 그 창보다 **늦게** 떴다면(예: Creon 로그인이 09:19) 청산은 실행되지 않았고, 창 밖에서 `time_exit_due`
   는 False(`next_open: waiting for the open window`)라 포지션은 **다음 세션 같은 창까지 보유**된다.
   순서대로 확인한다:
   · 루프 기동 시각 — `loop_sup.log` 의 `started loop pid` · `runner.log` 의 `=== log opened ... run --loop` ·
     `loop_state.json` 의 `last_cycle.ts`/`daily.start_equity`.
   · **"요즘 매매가 돌긴 했나" 는 사이클 날짜 분포 한 줄로 끝낸다**: `grep -oE 'cycle: ts=[0-9-]{10}' runner.log | cut -c10-19 | sort | uniq -c`
     → 날짜별 사이클 수가 그날 루프가 **실제로 돈** 증거다(사이클 0인 날은 크론·피드가 정상으로 보여도 매매 경로가 유휴였다).
     재부팅·로그인 복구 보고에서는 이 분포로 "마지막 실매매 세션"을 먼저 특정해 두고, 그 뒤에 오늘 상태를 붙인다.
   · 미청산 — 저널 `select code, ts, screener, exit_ts from trades where exit_ts is null` 과 `/positions` 구성이
     일치하는지. 보유는 있는데 `/orders` 0건이면 청산 시도 자체가 없었던 것이다(`last_cycle.exits` 도 0).
     **저널(`journal/trade_journal.sqlite3`)은 Windows 파이썬으로 읽는다** — 루프가 WAL(`-wal`/`-shm`)을 잡은 채
     쓰는 중이라 WSL 의 `sqlite3` 로 열면 `disk I/O error` 로 죽는다(같은 파일을 Windows 파이썬으로 읽으면 정상
     이므로 저널 손상이 아니라 마운트·WAL 락 문제다).
     **WSL 에서 읽어야 할 때는 파일을 복사해서 읽어라** — `db`+`-wal`+`-shm` 세 개를 `/tmp` 로 `cp` 한 뒤 그 사본을 `sqlite3` 로 연다. 원본을 직접 열지 않으므로 락·drvfs 문제를 모두 피하고, Windows 파이썬의 `C:\` 경로·인코딩 함정도 없다. 읽기 실패 시 **빈 리스트로 폴백하지 말고 exit≠0 + 상태파일 미갱신**(위 Rule 0)으로 처리한다 — 사본 읽기로도 3회 실패하면 그 틱은 '읽기 실패'로 끝내고 숫자를 만들지 않는다. **Windows 파이썬에는 `/mnt/c/...` 가 아니라 `C:\...` 경로를 넘겨라**(헬퍼 **스크립트 경로**도 같다 — `/mnt/c/.../helper.py` 를 주면 예외 없이 stdout 이 **빈 문자열**로 돌아와 '스크립트가 안 돌았다'처럼 보이고 파싱이 조용히 실패한다) —
     Windows 인터프리터는 drvfs 경로를 열지 못해 `sqlite3.OperationalError: unable to open database file` 로 죽는다
     (에러 문구가 '저널 손상'처럼 보이지만 경로 형식 문제다). 컬럼은 `ts`·`code`·`name`·`qty`·`price`(진입가, `entry_price`
     아님)·`exit_ts`·`exit_price`·`exit_reason`·`pnl`·`fees`·`screener`·`entry_reason`. 누적 실적·승률·기대값은 레포의
     `data/reports/trader_fill_stats.json`(평일 16:40 `trader_fills` 크론이 브로커 체결에서 만든다)과 교차 확인해
     두 출처를 함께 인용한다.
   · 슬롯 — `trader_core/config.py` 의 `max_open_positions`(기본 3) 대비 보유 종목수. 소진 상태면 close 창
     (14:50-15:29)이 열려도 신규 진입은 동시보유 상한에 걸린다 → "오늘 0건"의 원인을 ①진입 게이트(창 밖 /
     문턱 미달)와 ②청산 지연으로 **분리해서** 적는다(둘을 뭉뚱그리면 다음 행동이 틀린다).
   · **경로별 슬롯은 `trader_core/slot_rules.py`**(`DEFAULT_SLOTS = {"swing": 3, "daytrading": 2}`,
     `config.max_open_positions_by_screener` 로 오버라이드, 슬롯 미정의 경로는 전역 cap 폴백)이고 **엔진 진입 게이트에 배선돼 있다**
     (`engine._slot_allows` 가 저널 screener 별 보유수를 세고, 조회 실패 시 fail-closed). 배선과 함께 전역 `max_open_positions` 도 상향됐다
     → 보고 전에 `grep -n "_slot_allows" trader_core/engine.py` 와 `config.py` 의 전역 값을 **실측**하고, 값이 다르면 그 사본이 낡은 것이다.
   · **노출 산술을 먼저 맞춰라 — 상한값을 기억으로 쓰지 말고 `Config()` 로 실측한다.** `max_invested_pct`·
     `max_position_pct`·`max_open_positions`·`sizing_mode` 는 사용자 지시로 바뀐다(예: 3종목·예수금 100% 지시 후
     `3` / `1.0` / `1/3` / `equal_weight`). 특히 `sizing_mode="equal_weight"` 이면 종목당 목표금액 =
     `min(남은예산 ÷ 남은슬롯, equity × max_position_pct, 가용현금, max_order_amount)` 이므로 **Kelly·위험예산 기반
     종목당 6~7% 를 전제로 계산하면 전부 틀린다** — 보고 첫머리에 실측 4값을 한 줄로 붙여라.
     슬롯 확대 요구를 받으면 ① 예산 유지+종목당 축소 ② 총 노출 상향 ③ 남는 예산에서만 단타 중 무엇인지
     **사용자에게 확정받고** 구현한다. **예수금 100% 노출을 켰는데 현금이 0이면 매수는 0건이 정상이다** —
     "설정했는데 왜 안 사나"의 답은 게이트가 아니라 가용현금이고, 현금은 매도가 나야 생긴다(이때
     "매도 후 빈 슬롯 수로 균등 배분"이라는 다음 동작을 숫자로 예시해 준다).
   · **daytrading 피드 키는 배선돼 있다**(`candidates` = `close`·`swing`·`daytrading`). 단타 "0건"은 미배선이 아니라
     후보 소비·슬롯·예산 중 어느 관문인지로 갈라 쓰고, 키 자체의 유무는 매번 `candidates.keys()` 로 실측한다.
   · 보고서에 포지션별 현재가 vs 진입가, 미체결 주문 건수, 슬롯 점유(n/3)를 숫자로 붙이고, 사람 단계로
     "다음 세션 09:00 전 로그인·루프 기동"을 명시한다 — 창을 또 놓치면 하루 더 묶인다.
   · 오늘 매매 0건의 사유가 후보 부재가 아닐 때가 많다: 이번 실측의 두 축은 "close 20건 전량이 진입 창
     밖"(정상) 과 "swing 상위10 평균 확률이 R1 문턱 미달"(모델 품질)이었다.
   · **`/positions`·`/balance` 의 `eval_pnl`·`total_eval_pnl` 은 퍼센트다(원 아님).** 원 단위 손익은 항상
     `평가금액 − 평단×수량` 으로 계산한다(검산: 평가 23,031 − 2주×11,291.5 = +448원 = `eval_pnl` 1.98 ✓,
     계좌 총평가 변화량도 이 원 단위와 일치). 이 필드를 원으로 읽으면 손익을 자릿수 틀리게 보고한다.
     보고는 **미실현(보유) / 실현(청산) / 합계**를 나누고, 실현치는 저널 기준이며 `fees=0` 이면 gross 임을 명시한다.
     **장중에 인용한 손익은 확정치가 아니다 — 브로커 정합이 청산가를 하루에도 여러 번 고친다.**
     exit 행이 `broker CpTd6032 avg_sell_price` 로 정정될 때마다 pnl 이 다시 계산되어, 같은 날 실현이
     +1,569 → +375 → -1,103 으로 **부호까지 뒤집힌** 실측이 있다(정정 사유가 `exit_reason` 에 남는다).
     그래서 ① 장중 보고에는 '체결가 정정 전' 라벨을 붙이고 ② 최종치는 마감 후 `/balance` 의 equity 변화로 쓴다
     (전량 현금화 상태면 그 값이 수수료·세금 포함 정본이다) ③ 정정 전 수치를 이미 인용했다면 다음 턴 첫 줄에서
     정정하고, 그 사이 브로커가 움직였는지 저널 오늘 행을 다시 읽는다. 장중 수치를 '오늘 손실'로 단정해 답하지 않는다.
     **`loop_state.daily.closed_pnl`·`summary.pnl`, 그리고 마감 사이클의 `CLOSE summary pnl=... entries=...` 줄은 실현손익이 아니라 당일 equity 변화량이다** — 미실현을 포함하므로 청산 0건인 날에도 플러스가 찍힌다(실측: `CLOSE summary pnl=5786.0 entries=1` 인데 저널 청산 행은 0건). 이 값을 '오늘 실현한 돈'으로 보고하면 틀린다 → 실현은 저널 청산 행 합, 이 필드는 '당일 평가 기준 변동(미실현 포함)'으로 라벨을 붙여 따로 쓴다.
     **마감 사이클은 15:35 이후에 돈다** — 15:29 창 종료 시점에 `daily.closed=false` 인 것은 결함이 아니고, 15:40 전후 `NOTIFY market close` / `CLOSE summary` 줄이 찍히며 그때 `closed=true`·`summary` 가 채워진다. 그 사이 스냅샷을 근거로 '일일 마감이 안 됐다'고 쓰지 않는다.
   · **보유분의 청산 예정을 말하기 전에 그 진입의 `exit_mode` 를 확인하라**(저널 `algo_json`).
     `stop_tp` = 스윙(손절·익절, 며칠 보유 가능 — "내일 시가 청산"이 아니다), `next_open` = close 경로,
     `same_day_close` = 당일. 스윙 보유분을 다음 세션 창 청산으로 안내하면 틀린다.
   · **정합(reconcile)에서 "미체결"과 "거부"를 구분하라.** `filled_qty=0` + `cancelable_qty>0` = **계류 중인
     살아있는 주문** 이므로 정정하면 안 된다(체결·취소·만료로 확정된 뒤에만). 성급히 "원가 청산(손익 0)"으로 닫으면
     **나중에 체결되어 저널↔브로커가 반대로 어긋난다** — 도구가 `reopened` 로 되돌리지만 그 사이 기록이 틀린 상태로 남는다.
     거부의 근거는 주문 응답(`order_no: 0` + 메시지 코드)이고, 정정 전후로 **저널 미청산 코드 집합 == 브로커 보유 코드 집합**을
     양방향 대조한다. `tools/reconcile_journal.py` 는 예행이 기본(`--apply` 로 반영)이고, 적용 전 저널 파일을 백업한다.
     유령 청산이 끼면 청산 건수·순손익 합이 바뀌므로 **정정 후 누적 청산 건수와 합계를 재출력해 대조**하라.
     · **브로커가 평탄한데(보유 0) 시세가 없어 청산가를 못 구하면 `--exit-price` 를 줘야 한다**(거래정지·시세 공백 종목은
       라이브 시세 폴백이 비어 도구가 "no price available"로 멈춘다). 실제 체결가를 모르면 마지막 일봉 종가로 닫고
       `exit_reason`·보고에 **근사치임을 명시**한 뒤, 브로커 실현손익의 실제 단가를 확인하면
       `reconcile_journal.py --fix-exit <code>=<price>` 로 교체한다 — 근사값을 사실처럼 보고하지 않는다.
     · **adopt 된 행은 `stop_pct`/`tp_pct` 가 NULL 이고 `screener` 도 빈 문자열이다** — 모니터는 NULL 이면 config
       기본값(7%/7%)으로 폴백하지만, 그 수준은 저널에 남지 않아 감사가 안 된다 → adopt 직후
       `tools/set_open_tp.py --codes <code> --sl 0.07 --tp 0.07` 로 행에 값을 박아라. 적용 후
       `select code,qty,price,stop_pct,tp_pct,screener from trades where exit_ts is null` 이 **브로커 보유 목록과
       같은지** 다시 찍어 보고에 붙인다(저널에 없는 보유 종목은 손절·익절 감시에서 빠진다).
     · **adopt 행의 `entry_reason` 은 `reconcile: adopted from broker` = 시스템 밖에서 산 사람의 매수다.** `algo_json` 은 `{}` 이고
       진입 근거가 없으며 stop/tp 는 버전에 따라 NULL 이거나 config 기본값으로 채워져 있다(실측: `0.07/0.07` + `screener=''`).
       값이 채워져 있다고 '시스템이 진입한 트레이드'로 취급하지 마라 — 청산 제안은 수량·평단·현재가·평가손실을 붙여
       **사람의 결정 항목**(보유 / 절반 / 전량)으로 올리고 임의로 시장가 청산하지 않는다.
     · **`decisions` 테이블에는 `side`/`action` 컬럼이 없다** — `id, ts, screener, code, name, score, gates_json, plan_json, reason` 이다.
       `select side from decisions` 는 `OperationalError` 로 상태 조회 스크립트를 중간에서 죽이므로, 체결은 `trades`, 차단 사유는 `reason` 으로 읽는다.
       **0건 원인은 사유별 집계 한 줄로 만든다**: `SELECT reason, count(*) FROM decisions WHERE substr(ts,1,10)=<오늘> GROUP BY 1 ORDER BY 2 DESC` →
       `swing 슬롯 포화 또는 산정 불가(경로 상한 N)` / `would breach per-stock 33% equity cap` / `ok` 건수를 그대로 인용하면
       '게이트 미달'과 '자금·슬롯 구조'가 한 번에 갈린다(전자는 모델·문턱, 후자는 설정·청산 지연 문제다).
       `reason` 이 `size rounds to zero shares (sizing mode ..., cash 0)` 로 도배돼 있으면 매수 경로가 죽은 게 아니라 **현금 0 = 전량 투자 상태**다
       (위 예수금 100% 항목과 같은 사실 — 코드 버그를 찾기 전에 `tools/portfolio_check.py` 의 `equity(현금+주식)` 로 확인).
7. 커밋/푸시: `git ls-remote origin <branch>` 로 원격 SHA 를 직접 비교(아래 함정).
   **root 로 시험 실행한 스크립트가 런타임 상태파일을 root 소유로 남기면 `git add .` 가 `error: open("<file>"): Permission denied` 로 통째로 실패한다** — 파일을 지우거나 커밋에서 빼지 말고 소유권만 되돌린다(`wsl.exe -u root -- chown <user>:<user> <file>`). 크론·시험 실행은 root 로 돌기 때문에(예: `state/rank_universe.json` 이 root 0600 이 되어 add 가 막혔다) `git add` 가 이상하게 실패하면 소유자부터 본다 — 지우면 다음 잡이 기대는 진행·상태가 사라진다.
8. **라이브 모델 상태**(ML 스택 보고엔 필수): 챔피언 디렉터리(`services/xgboost-ml/app/models/champion`)의 `auc.txt` 값 · 파일 mtime ·
   `champion_prev_*` 백업 개수(0건이면 아직 승격이 없었음) · 최신 `training-result-*.json` 의 `retrained_at`/`ensemble_auc`.
   여기에 OOS 하네스(`scripts/phase_4_backtest.py --model-dir app/models/champion --days 90`)를 한 번 돌리면
   "지금 매매에 쓰이는 모델의 실제 성능"을 말할 수 있다 — 학습 때 val AUC 만 인용하여 보고하지 않는다.
   **교체·롤백 이력은 mtime 추정이 아니라 기록으로 읽는다**: `data/reports/champion_swaps.jsonl`(교체/롤백 1줄씩,
   파이프라인 승격 뒤 자동 기록)과 `data/reports/promote_live_score_gate.json`(`candidate_signals`·`verdict`) 을 함께 인용한다.
   **저녁 파이프라인 로그의 `라이브 스코어 게이트: 승격 생략` 줄은 실패가 아니다** — 후보가 절대문턱(0.55) 초과
   신호를 0건 내면 승격을 의도적으로 건너뛰고 챔피언을 유지한다(그 상태를 '승격 실패'로 보고하면 없는 장애를 만든다).
   그 줄이 없는데 챔피언 auc 가 그대로면 AUC 게이트가 막은 것이므로 로그의
   `candidate ensemble_auc ... (+0.0200 required)` 문구를 인용한다.
   게이트 판정값·프로브 사용법·롤백 명령·`submodel_auc_spread` 해석은 `references/promotion-live-score-gate.md` 를 따른다.
   **AUC·피처 수를 인용할 때는 `references/model-baseline-reporting.md` 를 먼저 본다** — 같은 라벨도 패널 유니버스가
   다르면 기준선이 뒤집히고(49종목 0.5405 vs 200종목 0.5107), 새 피처 축은 플래그 OFF 기본값에서 수 불변이 확인돼야 한다.
9. **진행 중인 학습·실험 상태**: `docker top <컨테이너>` 로 돌고 있는 학습 프로세스(몇 개가 동시에 도는지) ·
   실험 드라이버가 있으면 `heartbeat.txt` mtime(15분 이상 멈추면 정지) · `results.jsonl` 마지막 줄 · `exp_*`/후보 디렉터리 산출물.
   진행률을 모르면 모른다고 쓰고, 어림값은 어림값이라고 표시한다.
   - **진행률 라인이 아직 없을 때 살아있음의 증거는 로그 크기 변화다** — `ls -la <job>.log` 를 20초 간격으로
     두 번 찍어 바이트가 늘면 그 프로세스는 일하는 중이다(CPU% 나 mtime 만으로는 부족하다).
   - **컨테이너가 쓴 로그의 시각은 UTC 다**: 호스트에서 KST 로 읽으면 9시간 낡아 보인다. "11:56에 멈춘 줄"이
     실제로는 20:56 KST 인 경우가 흔하다 → `+9시간` 환산 후에 낡음/생존을 판정한다.
   - **잡음 줄을 걷어내고 진행 라인만 본다**: 그래프/보안 모듈 알림 같은 경고가 초당 한 건씩 찍히면
     `grep -vE "<노이즈 패턴>"` 로 걷어낸 뒤 tail 한다. 안 그러면 노이즈에 가려진 진짜 진전이 보이지 않는다.
   - **그 잡의 `timeout` 상한과 진행률에서 뽑은 ETA 를 대조해라**: ETA 가 상한보다 크면 "끝나기 전에 죽는다"를
     보고에 함께 쓴다(산출물이 마지막에만 기록되는 잡은 그대로 손실이다).
10. **"지금 도는 것"과 "예약만 된 것"을 분리한다**: Hermes 크론(`cronjob_manage action=list`)의
   `next_run_at`·`last_run_at`·`last_status` 와 Windows 예약작업
   (`Get-ScheduledTask ... | Get-ScheduledTaskInfo | Select TaskName,LastRunTime,LastTaskResult,NextRunTime`).
   "진행 중"은 **실행 중 프로세스 + 그 런의 로그에 찍힌 진행 라인**(예: `Build progress: N/M ... pair/s ETA`)으로만 말하고,
   예약만 된 일·다음 틱을 진행 중이라고 쓰지 않는다. 보고는 '진행 중 / 예약 / 대기(사람 단계·승인)'로 나눈다.
   **역할 틱(Hermes 크론)에는 구조적 공백이 있다**: 리서처·엔지니어 틱은 장중(평일 09:00~15:30) 시작을
   가드가 거부하므로(매매 경로 CPU 보호) 07~16시에 원장이 멈춰 있는 것은 결함이 아니라 **설계**다.
   이 공백을 '역할 유휴·장애'로 보고하지 말고, 그 시간에 사람이 해야 할 일이 있는지만 본다.
   공백을 줄이는 수단은 장 마감 직후(평일 15:35) 자동 틱이며 순서는 **리서처 → 엔지니어 → 트레이더** 다
   (순서를 바꾸면 엔지니어가 그날 갱신 전 데이터로 검증한다). 낮 시간대 틱을 추가해도 `대기: 장중`
   한 줄만 찍고 끝나므로, 그런 제안을 보고에 올리지 않는다.
   **"내일 새벽에 두 역할이 일하는 계획이 있나?" 는 목록의 `schedule` 문자열과 `next_run_at` 을 그대로
   읽어 시각을 나열하고**, 각 잡의 `last_run_at`/`last_status`/`last_error` 를 함께 붙여 "계획은 있다"와
   "마지막 실행은 실패했다"를 분리해 쓴다(둘은 다른 질문이다). 야간까지 지켜볼지는 폴러의 시간 상한
   (`WATCH_HOURS`)으로 정한다.
   **Windows 감독기 태스크(`trader-bridge-sup`·`trader-loop-sup`)는 '30분 주기 작업'이 아니라 상주 폴러다**: `while($true)` 로
   30~60초마다 브리지 `/health`·루프 프로세스를 보고, 창(08:30~15:35) 안이고 브리지가 connected 면 루프를 기동한다
   (`tools/supervise_loop.ps1` · `supervise_bridge.ps1`). `Get-ScheduledTaskInfo` 의 `NextRunTime`(30분 뒤)·
   `LastTaskResult=2147946720`(0x800710E0 = '이미 실행 중이라 새 인스턴스 거부', MultipleInstances=IgnoreNew)은 **실패가 아니다** —
   이 숫자를 장애로 보고하거나, 그걸 근거로 "로그인 후 30분 뒤에야 루프가 뜬다"고 안내하지 마라. 사람 단계는 **Creon 로그인 하나**이고
   로그인 뒤 1~2분 내에 브리지·루프가 자동 기동한다(마감 후 브리지만 남아 있는 것도 정상 = 다음 날 기동 대기).

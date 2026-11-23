---
name: trading-stack-status-report
description: "Use when reporting the trading stack's 진행상황/status."
---

# 트레이딩 스택 진행상황 보고 (analyist_dd → trader-agent)

대상 작업: "진행상황 알려줘" 류의 상태 보고. 두 호스트에 걸린 파이프라인을 다룬다 —
WSL의 Docker/ML 스택(analyist_dd: 수집→ML→스크리너→피드 발행)과 Windows의 매매 쪽(trader-agent + 32비트 Creon COM 브리지).
각 스택의 **구축·재구축 절차**는 별도 스킬(`analyist-dd-stack-ops`, `creon-cybosplus-trader-agent`)에 있다. 이 스킬은 **상태를 실측해 보고하는 절차**만 담는다.

## Rule 0 — 실측만 말한다

기억·파일 타임스탬프·직전 대화의 수치로 답하지 않는다. 아래 프로브를 **한 턴에 배치**로 돌리고 그 출력만 인용한다.
돌리지 않은 검증(서비스 테스트 스위트, 브리지가 필요한 dry-run 스캔)은 반드시 '미검증'이라고 라벨을 달아 보고한다 — 통과했다고 쓰지 않는다.
**읽기 실패를 0 으로 바꾸지 마라.** 모니터·보고 스크립트가 원천 읽기에 실패했을 때 빈 리스트로 폴백하면
"청산 0건 / 실현 +0원 / 손실률 +13.7%" 처럼 **그럴듯한 거짓 수치**가 그대로 사용자에게 간다(실측: Windows WAL 잠금으로
저널 읽기가 실패한 틱에 기준선 대비 +33,765원이 찍혔다). 규약은 세 가지다 — ① 우회 재시도(사본 읽기·Windows 인터프리터)
② 그래도 실패면 **exit≠0 + 상태 파일 미갱신**(직전 정상값 보존) ③ 보고에는 '읽기 실패'로 적고 숫자를 만들지 않는다.
상태 파일을 덮어쓰면 그 다음 틱의 비교 기준까지 오염되어 오류가 조용히 누적된다.
고카디널리티 원장(`decisions` 등)을 `SELECT *` 로 던지지도 마라 — 같은 사유의 수천 행이 출력을 수십만 자로 불려
정작 필요한 집계가 묻힌다. 사유별 `GROUP BY` 집계나 `COUNT` 한 줄로 먼저 줄이고 원본은 파일로 받는다.

**시계도 프로브다 — `date` 한 번으로 '장중/장 마감'을 단정하지 마라.** 이 WSL 의 시계는 호스트가 절전에 들어가면 얼어붙었다가 깨어날 때 크게 점프한다(실측: `date` = 00:35 인데 같은 순간 Windows 사본 파일 mtime 과 외부 `Date` 헤더는 12:22 — 11시간 47분 차). 그 상태로 "마감·보유 0"을 보고하면 몇 시간 뒤 "장중에 1종목 보유 + 미체결 주문"이 사실이 되어, 사용자가 "매매정보 알려줘"로 되짚어야 한다.
- 교차 확인: `curl -sI -m 10 https://www.google.com | grep -i '^date:'`(UTC → +9h) 또는 Windows 라이브 사본 파일의 `ls --time-style=+%FT%T`(루프가 살아 쓰고 있으면 그것이 정본에 가깝다).
- 장 마감/보유 상태를 말하기 전에 **`/positions`·`/orders`·`loop_state.last_cycle.ts` 세 값을 같이** 읽고, 셋의 시각대가 서로 맞는지 확인한다. 절전에서 깨어난 호스트는 08:30 감독기 기동으로 **루프가 이미 매매를 시작한 상태**일 수 있다.
- 어긋난 시각으로 이미 보고했다면 다음 턴 첫 줄에서 **정정**하고, 그 사이 브로커가 움직였는지(`/daily_pnl`·저널 오늘 행) 다시 읽는다. 시계 드리프트는 보고 오류의 원인일 뿐 면책이 아니다.
- **세션 프롬프트에 주입된 '오늘 날짜'도 프로브가 아니다 — 긴 세션은 며칠에 걸친다.** 그 값을 믿고 저널의 11-18·11-19 행을 "미래 날짜 = 데이터 오염"으로 판정했다가, 실제 현재가 `date` 로 11-20 03:43 임을 확인하고 정정한 사례가 있다(오탐 정정은 바로 했지만 사용자에게 틀린 경보를 한 번 보냈다). 어제/오늘/최신 거래일을 말하기 전에 **WSL `date` 와 Windows `Get-Date` 를 함께 찍어 두 값이 같은 분인지** 확인하고, 일치할 때만 타임스탬프를 미래/과거로 판정한다. 스톡은 '날짜가 이상하다'가 아니라 '내 기준일이 낡았다'인 경우가 많다.

0. **호스트 재부팅을 먼저 본다**: `who -b` + syslog(정본) · Windows `(Get-CimInstance Win32_OperatingSystem).LastBootUpTime` · System 로그 이벤트 1074/109/6005. 재부팅은 ①Creon 세션·브리지·루프를 끊고 ②장시간 ML 잡을 rc=137로 죽인다.
   **이 WSL 에서 `uptime -s` 는 정본이 아니다 — 같은 부팅을 두 번 읽으면 값이 달라진다**(실측: syslog `Startup finished in 18.149s` + `who -b` = 11:13 인데 `uptime -s` = 11:22, 한 세션 안에서). suspend/resume 뒤 드리프트가 커지므로 부팅 시각이 필요한 자리에는 `who -b` 와 syslog 의 `Startup finished`/`Reached target multi-user.target` 줄을 쓰고, `uptime -s` 가 그와 어긋나면 그 값을 인용하지 말고 어긋났다는 사실을 적는다(그 불일치를 "또 재부팅됐다"로 오독하면 없는 재기동을 보고하게 된다).
   **Windows 는 며칠 전 부팅인데 WSL만 갈아끼워진 경우를 `last -x` 로 가른다** — 직전 세션의 시작·종료 시각이
   그대로 나오므로 "그 시각에 WSL VM 만 재기동됐다"를 추정이 아니라 사실로 쓸 수 있다(사용자의 `wsl --shutdown`
   복구 절차일 수 있으니 장애로 단정하지 않는다). 그 재기동이 스택 재기동의 원인인지는 컨테이너
   `docker inspect -f '{{.State.StartedAt}}'`(UTC → +9h 환산)가 그 시각과 일치하는지로 확인하고, `RestartCount=0`·
   `OOMKilled=false` 면 인프라 사망이 아니라 정상 기동이다(진행 중이던 장시간 잡이 있으면 그때만 rc=137 손실을 본다).
   '갑자기 안 된다' 신고의 절반은 여기서 설명되고, WSL/Docker 는 부팅 후 자동으로 다시 올라와 **스택만 보면 정상으로 보인다**(원장에는 '기동 없이 사라진 사이클' 한 줄만 남는다 — 실제로는 호스트 종료).
   사용자가 직접 시작 메뉴에서 재시작한 경우(이벤트 1074, 주체 StartMenuExperienceHost)도 있으므로 '장애'로 단정하지 않는다.
   **Windows 부팅 시각 문자열을 그대로 읽지 마라** — PowerShell 출력이 cp949 로 깨져 `오전/오후` 토큰이 사라지고
   (`2026년 11월 9일 ??? 7:55:28`) 아침/저녁을 뒤집어 'PC 는 아침에 켰는데 WSL 만 하루에 세 번 재기동' 같은 틀린 서사를
   만든다. 재부팅 범위는 **① WSL `who -b`·syslog `Startup finished` ② 감독기 로그의 `bridge supervisor start`
   / `loop supervisor start` 줄 ③ Windows 예약작업 `LastRunTime`** 세 시각이 같은 시각대에 몰리는지로 판정하고
   (전부 같은 5분 안이면 PC 전체 재부팅 = Creon 세션·브리지·루프 동시 손실), 시각 값 자체가 필요하면 문화권 고정 포맷으로
   받는다(`Get-CimInstance Win32_OperatingSystem | Select-Object -ExpandProperty LastBootUpTime | Get-Date -Format o`).

0b. **"왜 종료됐나"(에이전트·세션 자체가 멈춘 질문)는 게이트웨이 원장으로 판정한다** — 스택 프로브보다 먼저 본다.
   정본은 `~/.hermes/logs/gateway-exit-diag.log` 의 `gateway.previous_unclean_exit`·`gateway.exit_nonzero` 줄과
   `gateway.log` 의 `gateway.lifecycle_ledger` 줄이다. 원인은 세 갈래로 갈린다:
   ① `gateway.shutdown_watchdog: Gateway event loop missed 3 consecutive liveness probes ... exiting with code 75` =
   이벤트 루프 정지(대개 메모리 고갈 — 같은 줄의 `suspected_oom`·`mem_available_kib`·`swap_used_kib` 로 확인).
   처방은 상한(`.wslconfig` 의 memory/swap)이고, **적용은 재기동 후에만** 되므로 그 뒤 ``free -m`` 으로 실측한다.
   ② `exited UNCLEANLY (no exit path ran — SIGKILL / OOM / VM death)` = 프로세스가 아니라 **VM 이 내려간 것**
   (`wsl --shutdown`, 호스트 절전/재부팅). 판정은 직전 생의 마지막 로그 시각과 부팅훅 로그
   (`/var/log/analist-stack-boot.log`, 실제 경로는 `analist-stack-boot.log`)의 새 부팅 줄이 몇 분 안에 붙는지로 한다.
   ③ SIGTERM + 정상 종료 경로가 찍힘 = 사람·서비스 매니저가 내린 것.
   **`wsl --shutdown` 을 장애로 단정하지 마라 — `.wslconfig` 적용 절차일 수 있다.** 그 파일 mtime·주석에
   "적용은 wsl --shutdown 후"가 적혀 있는지 보고, 적용 결과는 예약작업 로그(`C:\Users\<user>\<task>.log` 의
   before/after MemTotal·스왑 장치·컨테이너 수)로 검증해 "종료 원인"과 "적용 성공"을 한 보고에 함께 쓴다.
   세션이 끊긴 것은 실패가 아니며, 끊긴 사이 진행 중이던 틱이 `status=unknown` 으로 남았는지는
   `~/.hermes/cron/executions.db` 로 확인한다. **1회성 예약작업은 소비된 뒤에도 `State=Ready`·`NextRunTime` 없음으로
   남는다** — '아직 예약돼 있다'로 읽지 말고, 정리(등록 해제)까지가 그 작업의 마무리다(스크립트 파일은 참고용으로 남긴다).

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

## 장시간 무인 감시 ("나 나갔다 올 테니까 확인하고 있어")

세션은 사용자 메시지로만 깨어나므로, 자리를 비운 동안의 '확인'은 **스스로 돌아가는 읽기 전용 감시 장치**로
구현한다. 순서는 기준선 → 폴러 기동 → 복귀 시 원자료 재확인이다.

1. **기준선을 먼저 찍는다**(그 자체가 보고 첫 줄이 된다): 역할별 원장의 오늘 항목 수·`rc`·`verdict`·마지막
   항목 id/제목, 그리고 매매 경로(루프 pid · `last_cycle.ts` · `halt` · 브리지 `connected:true` + `/positions` ·
   터널 `--noproxy` 204 · 피드 `age_seconds`).
2. **읽기 전용 폴러를 띄운다**: `scripts/role_watch.py` (이 스킬) — 5분마다 스냅샷 한 줄을 JSONL 로 남기고
   **이상만** stdout(=로그 파일)으로 찍는다. 기동은 `terminal(background=true)` 로 하고 출력 리다이렉트만 붙인다 —
   `nohup`/`setsid`/`disown` 으로 감싼 명령은 거부된다. 감시 항목: 루프·브리지 프로세스 생존, `halt`, 연속 실패,
   터널 204, 피드 신선도, 당일 자산 -2%(R4 임박), `last_cycle.ts` 정지, 역할 원장 오늘 건수, 신규 결정·청산 발생.
   **크론 실행 원장(`~/.hermes/cron/executions.db`)도 넣어라** — 역할 원장 공백 감시로는 "틱이 돌았는데
   끝까지 안 갔다"가 안 잡힌다: 실측(2026-10-01) 엔지니어/리서처 틱 4건이 `status=failed`·
   `error="Interrupted by shutdown before terminal completion"`·`delivery_outcome=NULL` 로 끝나
   **작업은 원장·커밋에 남았지만 보고는 아무 데도 안 갔다**. 새 행(rowid)만 보고 `status != completed`
   또는 `delivery_outcome not in (delivered, suppressed, silent)` 이면 이상으로 남기라(첫 폴링은 기준선만
   잡고 조용히 — 과거 이력을 이상으로 올리면 야간 로그가 과거로 덮인다). agent 잡만 대상으로 하고
   `no_agent` 스크립트 잡(위생·알림)은 의도적 침묵이라 제외한다.
   **기동 직후 첫 스냅샷을 읽어** 필드가 실제로 채워졌는지 확인한다(빈 값으로 조용히 도는 폴러는 감시가 아니다).
   **폴러·백그라운드 잡을 끝낼 때 `pkill -f <스크립트명>` 을 쓰지 마라** — 그 패턴이 실행 중인 셸 자신의 명령줄과도 일치해 셸이 SIGTERM 으로 죽고 뒤따르는 명령이 사라진다(exit −15). 그리고 **패턴 스캔으로 나온 pid 를 전부 kill 하는 것도 같은 사고다**: 래퍼(`bash -lic … <같은 명령 문자열>`)와 그 스캔을 실행 중인 **내 셸 자신**까지 걸려 도구 호출이 exit −15 로 끊기고 검증·보고가 통째로 사라진다(실측). 규율은 둘이다 — ① `terminal(background=true)` 로 띄운 잡은 **런처가 준 session/pid 하나만** 끝낸다 ② 어쩔 수 없이 ps 로 찾을 때는 `ps -eo pid,ppid,etime,args | grep -F <스크립트명> | grep -v grep` 으로 후보를 **출력만** 해 보고 자기 pid·부모 pid·셸 래퍼를 제외한 **실행 본체 pid** 만 죽인다. 종료 확인은 대상 pid 소멸과 그 러너의 상태파일 변화로 하고, 셸이 죽었으면 `echo $?`(exit −15)로 그 사실을 먼저 인지한 뒤 같은 명령을 다시 던진다.
   감시 시간은 상한을 두고 스스로 끝나게 한다 — 끝나지 않는 폴러를 남기지 않는다(기본 9시간 = 낮 감시,
   `WATCH_HOURS=17` = 역할 야간 틱까지 덮기). 감시 항목에 **역할 원장 공백**을 넣는다 — 단 임계는 역할별로 다르고, 실행 중인 실험은 공백이 아니다:
 원장 행은 틱이 아니라 **완료된 항목마다** 생기므로 엔지니어 3시간·리서처 5시간을 넘길 때만 이상으로 올린다
 **역할 상태파일(`me_cycle/state.json`)의 pid 가 죽어 있어도 루프가 죽은 것이 아니다** — 사이클이 자식 프로세스로 돌고 상태파일에는 마지막 시작값만 남기 때문에, pid 부재는 '그 사이클이 끝났다'는 뜻에 가깝다. 생존 판정은 ① 원장(`*_ledger.jsonl`)의 마지막 `ts` ② 그 역할 크론의 `schedule`/`last_run_at`(야간 전용이면 낮에 조용한 것이 정상) 순서로 하고, 상태파일 pid 만으로 "13시간째 멈춤" 같은 경보를 만들지 않는다(실측 오탐: 두 값이 모두 정상이었는데 pid 부재만 보고 정지로 단정).
 (공통 3시간 임계는 오탐을 낸다 — 리서처 실측 행 간격 06:01 → 16:02 → 20:12). 엔지니어가 여러 시간짜리
 실험을 돌리는 동안에는 그 실행을 나타내는 `data/reports/me_cycle/running.pid` 가 살아 있으므로 그 경우
 공백 경보를 끈다(안 그러면 밤새 5분마다 같은 경보가 찍혀 진짜 이상이 묻힌다). 틱 자체의 생존은
 Hermes 크론 목록의 `last_run_at`/`last_status` 로 따로 확인한다 — 원장 공백만으로 "틱이 죽었다"고 쓰지 않는다.
3. **보고에 쓸 때 지키는 두 가지**: ① 역할 틱이 장중(09:00~15:30)에 거부되는 것은 설계이므로 낮에 원장이
   조용한 것을 '유휴·장애'로 쓰지 않는다 — 오늘의 판정 지점은 15:35 순서다. ② 역할이 자기 백로그 항목을
   실행하면 원장에 `ran: {id, rc, status}` 가 붙는다 → **틱 자신의 `rc=0` 은 '틱이 돌았다'는 뜻일 뿐**이므로
   실행된 작업의 `rc` 까지 함께 보고한다(한 틱 안에서 백로그 작업만 실패하는 경우가 실제로 있다).
   여기서 **`ran.status='failed'` 를 '그 명령이 실패했다'로 번역하지 마라** — 백로그 항목의 `status` 는 그 항목의
   `check` 지표(예: `metric: fees_sum`, `check: >= 1`)가 아직 충족되지 않으면 failed 로 남는다(측정 대기 항목이
   영구 failed 로 보인다). 항목의 `metric`/`check`/`note` 를 읽어 ①명령이 죽은 것과 ②지표 미충족을 갈라 쓰고,
   후자면 그 지표를 올리는 사람 단계(예: 백필 스크립트 실행)를 제안한다. `ran.rc` 와 같은 항목의 실행 로그
   (`data/reports/tr_cycle/logs/tr_<id>_<ts>.log`)의 `rc=` 가 어긋나면 그 로그가 **같은 실행인지**(시각) 먼저 확인한다.
   핸드오프 누적치(`handoffs_total` 대비 `handoffs_unfilled`)도 같이 적는다 — 생성은 자동인데 소비가 0인 상태는
   수치로만 드러난다.
4. **복귀 시에는 폴러의 요약이 아니라 원자료로 다시 읽는다.** JSONL 은 '언제 무엇이 바뀌였는지'만 알려주므로,
   청산·진입 건수와 잔고는 그 시점의 저널·원장·브로커 조회로 재확인해 보고한다(모니터링 로그를 실적으로 인용하지 않는다).
5. **상시 감시 틱은 `no_agent` 스크립트 크론 + 래퍼 셸로 만든다**(토큰 0, 매일 도는 점검에 LLM 을 붙이지 않는다).
   래퍼 규약 3개 — 기존 `~/.hermes/scripts/hygiene_tick.sh`·`alert_tick.sh` 와 동일하게:
   ① 정상이면 **아무것도 출력하지 않는다**(빈 stdout = 전달 없음, watchdog 패턴) ② 감사 스크립트의 종료코드
   2(경고)/3(사람 단계)를 **항상 0 으로 정규화**한다 — 그대로 두면 스케줄러가 '잡 실패'로 해석해 별도 오류
   알림을 보낸다 ③ 같은 경보가 반복되면 상태파일(`~/.hermes/state/<이름>.last`) 비교로 **재전송하지 않는다**
   (정상 복귀 시 상태파일을 지워 재발 때는 다시 알린다). 검증은 **2회 연속 실행**해서 1회차 출력·2회차 침묵을
   확인하는 것이다.
   **수동 선실행이 첫 크론 전달을 삼킨다** — 래퍼를 손으로 돌려 출력을 확인하면 그 출력이 상태파일에 남아,
   배선 직후의 첫 크론 실행이 `delivery_outcome=suppressed`(빈 stdout)로 조용히 끝난다(실측). 그래서 ① 배선 검증은
   수동 실행이 아니라 **그 잡의 `executions.db` 행**(`status`·`delivered_at`·`delivery_outcome`)으로 하고
   ② 손으로 먼저 돌렸다면 상태파일을 지워 다음 실행에서 한 번은 전달되게 만든 뒤, 보고에 '첫 전달은 내 수동 실행
   때문에 억제됐다'를 적는다(`suppressed` 를 '전달 실패'나 '경보 없음'으로 오독하면 감시가 죽은 걸 정상으로 쓴다).
   **같은 함정이 “마지막 실행 이후 새 것”을 세는 원장 전반에 있다** — 손실 피드백(`loss_feedback`의 '새 손실 N건')·
   시그니처 기반 알림·신규 행 감시 모두, 손으로 한 번 돌리면 그 구간을 소진해 **정규 크론은 '새 것 없음'으로 지나간다**.
   손으로 돌려야 할 때는 ① 그 실행 결과를 정본으로 보관하고(원장·DB 에 이미 적재된다) ② 보고에 "정규 틱은 이 구간을
   건너뛴다"를 적고 ③ 워처 류는 상태파일을 지워 다음 실행에서 한 번은 전달되게 한다. 반대로 정규 틱 출력만 보고
   "손실이 없다"고 쓰면 실제로는 내가 먼저 처리해버린 것이다. `deliver` 는 살아있는 채널(`platform:chat_id`)로 명시한다 — `origin` 은 해석될 곳이 없어
   알림이 아무 데도 가지 않는다(스크립트 크론의 기본값이 이 함정이다).
   **배선한 경로는 '산출물 관문 점검기'로 매일 판정하라 — 로그 문구는 자기신고라 배선 증명이 안 된다.**
   점검기는 경로마다 `{관문, ok, detail, how}` 를 만들고 **산출물·행수**로 판정한다(피드 items>0,
   `close_latest.json.screener_meta.excluded_limit_up` 존재, 재시도 로그의 그날 판정 줄 등). 같은 날 그 시각이
   아직 안 됐으면 FAIL 이 아니라 **'미도래'** 로 라벨해 거짓 FAIL 을 만들지 않고, 종료코드 2(실패)만 두고 래퍼가
   0 으로 정규화한다. 배선 직후 손으로 돌려 보면 FAIL 로 시작하는 것이 정상이다(그게 사전 상태의 증거다).
   **개장 전 점검 틱은 휴장·주말을 먼저 걸러라.** 매매가 없는 날 "루프 미기동" 경보는 오탐이고 사람이
   헛걸음한다 → 휴장 캘린더(`data/krx_holidays.json`)와 `weekday()>=5` 로 조용히 종료한다(검증: 일요일·휴장일
   침묵, 다음 거래일 실행). 점검 항목은 **루프 생존(상태파일 mtime 임계, 예 25분) · 브리지 연결 · 킬스위치 유무 ·
   오늘 후보 수(스윙 원천 포함)** 이고, 경보 본문에 **창 마감과 실측 비용**을 함께 적는다("루프 미기동(loop_state
   N초 경과) — 09:00 청산창 전에 기동 필요" 처럼; 그 유형의 실측 손실이 −1,814원이다). 게이트(HEAT·R1) 완화는
   점검 대상이 아니다 — 완화는 실측상 손실을 키우므로 점검은 '살아있음'만 본다.
   **이 점검을 새로 만들지 말고 이미 배선된 것을 쓴다**: `/home/jhshi/preopen_watch.py` — 기본은 다음 08:20~08:50을
   절대시각으로 기다려 5분 간격 스냅샷을 `data/reports/preopen_watch_<날짜>.jsonl` 에 남기고, `--once` 는 1회 점검이다
   (경보: 킬스위치 존재 · `/health` 미연결 · `/balance` 실패 · 08:35 이후 python 프로세스<2 · 08:40 이후 오늘 사이클 없음,
   휴장·주말이면 침묵). 래퍼 `~/.hermes/scripts/preopen_check.sh` + Hermes 크론 `trader-preopen-check`(평일 08:20,
   `no_agent`, `deliver=local`)로 배선돼 있으므로 **사람에게 알림이 가지 않는다** — 결과는 jsonl 과 그 잡의
   `~/.hermes/cron/output/<job_id>/` 로 읽어 보고한다(CLI 세션에는 크론 전달 경로가 없다는 사실을 보고에 함께 적는다).
   **상태 조회는 한 명령으로 고정하라.** 크론 실행 원장·목표 상태·실험 registry·CEO 결정·야간 컨테이너 잡·git 을
   매번 개별 조회하면 도구 호출 4~6회가 되고 같은 것을 다시 조립하게 된다(레포 `scripts/status_snapshot.py` 처럼
   한 스크립트로 묶는다). 조회별로 개별 try 를 씌워 하나가 없거나 깨져도 나머지가 출력되게 하고, 못 읽은 값은
   '없음/실패'로 쓴다(조용한 0 금지). **원장 상태값의 의미를 정확히 읽어라**: 성공 표기는 `success` 가 아니라
   `completed` 이고, `failed` 중 `Interrupted by shutdown before terminal completion` 은 스케줄러·호스트 재시작으로
   끊긴 것이라 결함이 아니다(그 잡은 다음 슬롯에 다시 돌고 작업은 원장·커밋에 남는다) — 이를 실패로 세면 없는
   장애를 보고한다.
6. **여러 관문을 시각에 맞춰 관측할 때는 '절대시각 대기' 스케줄러 한 프로세스로 묶어라**(주기 폴러와 다른 도구다).
   진입·청산 창 종료 · 방금 만든 크론의 첫 실행 · 저녁 파이프라인 발행처럼 **몇 시간 뒤에야 결론이 나는** 관문들은
   sleep 누적이나 턴 사이 대기로 잡으면 어긋난다 — 매 반복마다 현재 시각과 목표를 다시 비교하는
   `wait_until("H:MM")` + `[(시각, 함수), ...]` 스케줄을 백그라운드 프로세스 하나에 넣는다(절대시각 재비교라
   세션이 길어지거나 시계가 드리프트해도 순서가 유지된다). 관문마다 **JSONL 한 줄**(`kind`·수치·경로)을 append 하고
   마지막에 전체 요약을 stdout 으로 찍어, 자리를 비운 동안의 결과가 원자료로 남게 한다(보고는 그 원자료로 한다).
   각 관문 함수는 try/except 로 감싸 하나가 죽어도 나머지가 계속 돌게 하고, 감시 기간 상한을 정해 스스로 끝나게 한다.
   관문 예: 창 종료 직후 진입·청산 건수 / 신규 크론의 `deliver`·`delivery_outcome` / 파이프라인 승격 판정
   (`kept_incumbent`·`PROMOTED`) / 다음 발행의 피드 건수 / 외부 일봉 API 로 잰 차단 후보의 당일 등락.
   완성본은 `scripts/gate_watch.py` — 시각·경로만 바꿔 그대로 쓰면 5분 스냅샷 + 관문 라벨 + 브리지/저널 조회 + JSONL append 가 한 프로세스로 돌고, 기동·종료 알림은 `terminal(background=true, notify=true)` 가 준다(교체 시 위의 pkill 함정 참고).

## 위생 점검 ("위생 점검까지 해서" 로 요청될 때)

상태 보고에 위생 점검을 붙이면 **① 감시 자체 ② 자원 ③ DB 위생 ④ 크론/잡 실패 ⑤ 거래 경로** 다섯 축을
한 턴에 배치로 돌리고, 축마다 실측값을 붙인다.

1. **스택이 자체 위생 점검을 갖고 있으면 그것을 먼저 돌린다**: `python3 scripts/system_hygiene.py`(전체 출력).
   - **stdout 이 비어 있다고 '문제 없음' 으로 읽지 마라.** 직전과 같은 경고면 침묵하도록 설계돼 있다 —
     판정은 상태파일(`data/reports/hygiene/latest.json`)의 `status`(ok/warn/breach)·`warns`·`breaches`·
     `alert_ts` 로 한다(종료코드 2=경고 / 3=위반도 같이 본다).
   - 그런 점검은 스크립트 전용 크론(예: 30분 주기, 에이전트 없음)이 돌리고 있어서, 사람이 손으로 돌리면
     상태파일만 갱신되고 출력은 억제된다 → "지금 돌렸는데 조용하다"가 정상의 증거가 아니다.
2. **자원**: `df -h /` + Windows `C:` 사용률 · `docker ps` 기대 개수 · 유휴 트랜잭션
   (`SELECT state, count(*) FROM pg_stat_activity GROUP BY 1` 에 `idle in transaction` 이 없어야 한다).
3. **크론 실패 감사**: 시스템 크론(`/etc/cron.d/*`·사용자 crontab) + Hermes 크론 + Windows 예약작업을 한
   **동일 스크립트 경로로 양쪽을 grep 해 중복 등록을 먼저 찾아라** — `/etc/cron.d/<file>` 과 `crontab -l` 에 같은
   스크립트가 **같은 분**에 있으면 평일 2회 실행되어 수집 API 호출이 2배가 되고 진행파일을 동시에 쓴다.
   중복을 찾으면 한 곳만 남기고(정본은 더 최근에 손본 쪽) 남은 쪽을 검증한다 — 양쪽 항목 수 합계가 그대로인지,
   제거 후 `crontab -l | grep -c <script>` = 1 인지. 표를 만들고 **오늘 실행되어야 할 것만** 골라 각 로그의 마지막 줄을 읽는다. 로그가 없거나 낡은 잡은
   그 잡의 **자기 산출물**(예: `reports/<pipeline>_<date>.log`)까지 보고 나서 공백으로 판정한다 —
   래퍼가 자식 출력을 리다이렉트하지 않는 잡은 래퍼 로그가 비어 있는 게 정상이다(비어 있는 래퍼 로그를
   '그날 파이프라인이 안 돌았다'로 보고한 실측 오진이 있다).
4. **감시가 살아있는지 — 그리고 그 알림이 실제로 어디로 가는지**: 위생/알림 크론의 `last_run_at`·`last_status` 를 확인한다 — 감시가 죽으면 보고서가
   조용해져 '정상' 처럼 보인다(`last_status=error` 라도 `Interrupted by shutdown ...` 류는 실행 중 종료라
   결함이 아니다).
   **전달 대상(`deliver`)이 실제 채널로 해석되는지까지 본다**: 스크립트가 만든 크론의 `deliver='origin'` 은
   해석될 곳이 없어 **알림이 아무 데도 가지 않는다**(규칙만 평가되고 사람은 영원히 못 받는데, 로그가 조용하니
   '정상'으로 오독한다). 같은 `origin` 이라도 **세션에서 만든 잡은 해석되고 스크립트/세션 없이 만든 잡은
   `delivery_outcome=not_configured`** 로 남는다(2026-11-13 실측: `quant-trader-tick`·`quant-postclose-ticks`
   → `discord:<chat_id>` 로 지정 뒤 수동 발화로 `delivered` 확인). `deliver` 를 `platform:chat_id` 로 명시하고, 검증은 스크립트 stdout 이 아니라
   **그 잡의 기록**(`last_status`·`last_delivery_error`·`last_delivery_unverified`)으로 한다. 알림 스크립트는
   "변화 없음"이면 침묵하도록 설계돼 있으므로, 변화가 있었던 틱(상태파일 `last_sig`/`last_ts` 가 바뀐 시각)을
   찾아 그 틱의 전달 결과를 인용한다.
   **`last_status=ok` 는 '사용자가 받았다'가 아니다 — `~/.hermes/cron/executions.db` 의 `delivery_outcome` 을 직접 읽어라.**
   실측상 `last_delivery_error=null`·`last_status=ok` 인 잡도 `delivery_outcome=not_configured`(스크립트 기반 역할 틱)이거나
   `status=failed` + `error='Interrupted by shutdown before terminal completion'` + `delivery_outcome=None` 으로 남는다 —
   그 보고는 아무 데도 가지 않았다. 전달이 실패해도 본문은 `~/.hermes/cron/output/<job_id>/<시각>.md` 에 남아 있으므로
   (에이전트 프롬프트가 붙은 파일; 본문 추출은 `awk '/^## Response/{flag=1} flag' <파일>`) 그 내용을 다음 보고에 요약해 재보고한다.
   **회선이 끊기면 알림 자체가 사라진다 — `delivery_outcome='failed'` 를 '경보 없음'으로 읽지 마라.** 프록시 전용
   회선에서 upstream 이 내려가면 Discord 전송이 `ClientConnectorDNSError`/`TimeoutError` 로 실패하고, 경보는 생성됐지만
   아무도 못 받는다(실측: 6시간 40분 단절에 개장 전 경보 3건 + 정기 알림 8건 유실 → 그 사이 09:00 창을 통째로 놓쳤다).
   처방은 ① 단절 구간 실패를 `deliveries.db`(`status='failed'`·`error`)로 세고 ② **회선 복구 뒤 실패분을 다시 보내는
   백로그 잡**(다음 실행에서 `executions.delivery_outcome='delivered'` 를 확인한 뒤에만 pending 삭제, 회선이 죽어 있으면
   침묵하며 적재) ③ 회선 도달성 감시(probe 204 + DNS + tunnel 마지막 이벤트, 전이 시 1회·지속 시 60분 간격)다.
   배선·검증 절차는 `references/alert-delivery-reachability.md`.
   **수집 검증기를 만들 때는 소스별 지연을 기준일에 반영하라.** 일봉(D-1/D-2 발행)·수급(익일 반영)·분봉(23:00 적재)·**ML 예측(다음날 04:15 백필)** 을
   '당일' 기준으로 재면 매일 거짓 경보가 나서 진짜 결함이 묻힌다 → 지연 소스는 **직전 거래일**(주말·휴장 건너뜀),
   당일 생성물(피드 발행일·뉴스 24h)만 당일 기준으로 검사하고, 과거 날짜 검증에서는 '오늘 피드'와 비교하지 않는다.
   **`ml_predictions` 의 `created_at` 은 UTC 다** — KST 로 +9 해서 읽어야 하므로 19:1x 로 찍힌 행은 **다음날 04:1x KST
   생성**이다(실측: prediction_date=11-13 의 created_at 19:15 → 그날 밤 점검 시점에는 존재하지 않았다). 날짜만 보고
   '그날 밤에 만들어졌다'고 읽으면 9시간을 거꾸로 세어 정상 데이터를 결함으로 만든다.
   **20:00 파이프라인은 ~2시간 돈다**(실측 20:00:01 → 21:54 / 22:10 종료) — 그 전에 도는 점검은 '파이프라인 미완료·예측 0행'으로
   오보하므로, `reports/full_pipeline_dd_<날짜>_*.log` 의 `Pipeline finished` 와 프로세스 생존을 먼저 보고 **진행 중이면 검증을
   보류(DEFER)** 한다. **휴장·주말에는 시장 데이터·피드·파이프라인 검사를 통째로 생략**한다(컨테이너·뉴스만 본다).
5. **거래 경로**: 터널(외부 204) · 브리지 `connected:true` · 루프 프로세스 + `loop_state.json` ·
   영속 의존 서비스(뉴스 게이트 등)의 포트 응답 · 피드 신선도.
   - **터널은 어댑터·라우트 존재가 아니라 `curl.exe --noproxy "*"` 의 204 로 판정한다.** TUN 어댑터와 정책
     라우트는 업스트림(휴대폰 프록시 주소)이 죽어도 그대로 남아 상태 검사만으로는 정상처럼 보인다. 반대로
     이 회선에서 `--noproxy` 가 000 인 것은 터널이 내려간 **정상 상태**이기도 하므로(직접 egress 없음),
     000 을 "프록시가 죽었다"의 증거로 보고하지 않는다. 함께 볼 것: `run_tunnel.ps1 -Probe`(런타임 검출된
     프록시 주소) · `Get-CimInstance Win32_Process -Filter "Name='mihomo.exe'"`(또는 필터 없는 `tasklist /FO CSV /NH` + `grep -a` —
     `tasklist /FI` 는 인터롭에서 인자가 깨져 실패한다) · `Get-NetRoute -InterfaceIndex <tun>`(0개=터널 없음).
     브라우저·시스템 프록시가 멀쩡한 것은 터널 정상의 증거가 아니다(온링크 /24 가 TUN 의 /17 보다 길어
     그 경로만 살아남는다). 프록시 주소는 핫스팟 재시작 때마다 바뀌므로, 옛 주소를 하드코딩한 파일이 남아
     있으면 `tunnel.log` 의 `proxy <old-ip> not reachable` 무한 대기로 드러난다 — 절차·함정은
     `wsl-host-ops` 의 `references/proxy-only-network-line.md`.
   **`/balance` 의 `equity` 와 `broker_total_eval` 을 같은 값으로 쓰지 마라 — 자본 총액은 `equity` 이고 `daily.start_equity` 와 교차 확인한다.** 증권사 필드 `broker_total_eval` 이 `equity` 의 두 배로 찍힌 상태에서 그것을 자본으로 인용해 "52.7만원"이라 보고했으나 실제 자본은 "24.7만원"이었고, 손실률 임계(-3/-6/-10%)까지 2배로 벌어져 경보가 늦게 울리게 됐다. 보고 첫 줄에 `equity`·`cash`·`daily.start_equity` 세 값을 나란히 찍어 서로 맞는지 먼저 확인하라.
   - **`connected:true` 만으로 세션이 살아있다고 판정하지 않는다**: `/balance`(또는 `/positions`)까지 응답해야 살아있는 세션이다(반쪽 세션이 `connected:true` 로 남는 실측).
   - **`/balance` 에 `cash` 키가 없으면 세션 생존과 별개로 매수 경로가 죽어 있다** — `buyable_error` 문구를 함께 인용하라
     (실계좌 실측: `12275 종목코드를 확인하십시요` / `12340 주문가능금액 확인바랍니다`). 엔진은 현금 미상이면 fail-closed 라
     신규 진입이 **조용히 0건**이 되고 로그에는 게이트 사유만 남는다 → "0건" 보고의 원인 후보에 항상 넣고,
     ①`cash` 유무 ②`buyable_error` ③유도 가용현금(`equity − 보유원가`)을 숫자로 적는다.
     **단 `buyable_error` 문자열의 존재만으로 매수 경로가 죽었다고 쓰지 마라** — `cash`·`d2_deposit` 이 정상 숫자로 오고 그날 실제 진입이 체결된 순간에도 `buyable_error: remote error -1: 12275 종목코드를 확인하십시요(ord.stk.dpmyinqr.selpsblqtyamt)` 가 계속 붙어 있다(주문가능수량 조회에 종목코드를 비워 보낸 응답). 판정은 `cash` 키 유무 + 그날 체결 + 게이트 사유(`size rounds to zero shares ... cash N` 의 N)로 한다.
   - **시간 임계 작업이 걸려 있으면(예: 보유 종목의 익일 09:00~09:10 시가 청산) 그 창 *전에* 세션 생존을 확인하고, 죽어 있으면 창 전에 사람에게 재로그인을 요청한다.** 세션 종료에는 자동 복구 경로가 없다 — 창이 지난 뒤에 보고하면 청산을 놓친 것이 되고, 그 손실은 보고 지연이 만든다.
   - 세션 종료 원인을 수치로 말할 때는 ① Windows 이벤트 로그의 크래시(모듈·예외코드) ② 클라이언트 로그의 소켓 종료 코드(0=정상 종료=회선 무관) ③ 재로그인 시각 목록이 **같은 시각대에서 맞물리는지**를 먼저 확인하고, 안 맞물리면 원인을 단정하지 않고 '모른다'로 보고한다.
     실측 절차(이벤트 Id 1000/1001 필터 · 일자별 크래시 추세 · Wi-Fi 비콘 손실과의 상관 · 복구 서명)는
     `references/session-drop-forensics.md` 를 그대로 따른다 — "로그인이 왜 끊겼어?" 는 이 스킬에서 반복되는 질문이다.

보고는 **정상 / 발견→수리(검증 포함) / 남은 우려** 3단으로 쓰고, 발견한 결함은 그 자리에서 고쳐 검증한 뒤
**그날 커밋·푸시까지** 마친다(위생 점검에서 나온 수리는 커밋까지가 한 세트다).
"오늘 크론 계획" 요청에는 **이미 실행(결과 포함) / 남은 일정(시각 나열) / 사람·승인 대기** 로 나누어 쓰고,
크론 두 종류(시스템 + Hermes/에이전트)와 Windows 예약작업을 모두 포함한다. 숨은 실패 유형과 진단법은
`data-quality-monitoring` 의 '가려진 실패' 절을 같이 본다.

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

## 보고 형식 (사용자 선호)

- 한국어, **평문 번호 목록**. 마크다운 헤더·굵게·표를 쓰지 않는다(CLI 렌더링 없음).
- 섹션을 '실측 확인된 것 / 미검증·대기 / 남은 작업' 으로 분리하고, 각 주장 옆에 근거(로그 줄, 행수, 포트 상태)를 붙인다.
- 0건·차단 상태도 숨기지 않는다. 가드가 자동으로 비운 것은 '정상 방어'와 '원인은 따로 있다'를 함께 적는다.
- **자산 보고는 '오늘 손익'만 쓰지 말고 고점 대비 낙폭과 단일 종목 집중도를 함께 쓴다.** 같은 "현금 0 + 보유 2종목"이라도
  총자산 29만원 · 직전 자본 53만원 대비 −45% · 한 종목이 평가액의 92% 라면 그건 '정상 관리 중'이 아니라 리스크 문장이다.
  숫자는 `/balance` 의 `total_eval_amount`·`equity` 와 `tools/portfolio_check.py`(총매입/평가/차액)에서 가져오고,
  집중도는 `eval_amount ÷ equity` 로 계산해 퍼센트로 적는다. 이 수치가 나온 뒤에야 청산/보유 선택지를 제시한다.
- 끝에 **번호 옵션 + 추천 하나**로 다음 행동을 제시한다(예: "1번부터 시작할까요?").

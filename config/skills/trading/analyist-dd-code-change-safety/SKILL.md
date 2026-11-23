---
name: analyist-dd-code-change-safety
description: "Use when editing code in the analyist_dd stack."
---

# analyist_dd / trader-agent 코드 변경 안전 절차

대상: 이 두 리포에서 **코드를 고치거나 고치게 하는** 일 전부 — 분석·측정 스크립트, 피드 계약, 트레이더 실주문 경로.
리포트 작성은 `trading-stack-status-report`, 스택 구축·수집 함정은 `analyist-dd-stack-ops`(사용자 소유) 를 본다.
좁은 손절/익절 라인의 판정·분봉 재생·자본 제약 시뮬은 `references/intraday-measurement.md`.
스크리너 파이프라인 지연 최적화(종목별 top-N 조회·사전 필터·배치 예측)는 `references/pipeline-latency.md`.
죽은 피처 진단·부활(패널 재빌드 → 종목 연결/커버리지 → IC 성능 게이트)은 `references/feature-revival.md`.

## Rule 0 — 고치기 전에 어느 리포·어느 경로인지 실측한다

- **리포 지배 구조를 기억으로 단정하지 마라 — 사본마다 실측한다.** 실측 한 줄:
  `cd <사본>/trader-agent && git rev-parse --show-toplevel && (ls -d .git >/dev/null 2>&1 || echo no-nested-git) && git remote -v && git branch --show-current`.
  실측 예: Windows 사본(`C:/Users/<user>/analyist_dd/trader-agent`)은 **중첩 리포(`trader-agent.git`, `main`)** 이고
  라이브 루프가 실행하는 코드이며, WSL 사본의 `trader-agent/` 는 **`.git` 없는 스냅샷**(상위 analyist_dd 에 포함)이라
  그쪽에서 import·pytest 하면 옛 코드가 잡힌다(신규 심볼이 `ImportError` 나면 그 사본이 낡은 것이다).
  트레이더 동작을 바꾸는 커밋은 **라이브가 실행하는 사본**에서 하고, 아니면 `scripts/sync_trader_agent.py` 로 동기화한다
  (→ `references/portability-and-sync.md`). 경로는 `pwd`·`ls <그 리포 고유 파일>` 로 확인한다.
- `scripts/ask_claude.sh`·`scripts/protected_paths.py` 는 **WSL 작업본에만** 있고 trader-agent 에는 `docs/` 조차 없다 → 위임 스펙 파일은
  그 리포 안에 만들어야 하고 필요하면 `mkdir -p docs` 를 먼저 한다.
- **`cd` 실패는 조용히 다른 리포에서 실행된다**: `cd <A> ; cmd1 ; cmd2` 처럼 `&&` 없이 나열하면 `cd` 가 실패해도 계속 돌아
  **엉뚱한 리포에 파일·디렉터리를 만든다**. 위임·편집은 `cd <절대경로> && <명령>` 으로 묶고 첫 명령을 `pwd`/`ls` 로 둔다.
- **`.env` 의 DB 값은 컨테이너 내부 기준이다** (`POSTGRES_HOST=postgres`, `POSTGRES_PORT=5432`).
  WSL 호스트에서 스크립트를 돌릴 때는 `POSTGRES_HOST=127.0.0.1` `POSTGRES_PORT=5434`(docker 노출 포트)로
  **덮어써야** 한다. 그대로 쓰면 `could not translate host name "postgres"` 또는 `connection ... 5432 failed`
  로 **후보/행 0건**이 되고, 증상은 스크립트 결함처럼 보인다(실측 2026-11-17). 호출 전 `docker port stock_postgres`
  로 노출 포트를 확인하고, 이 값을 크론 래퍼 안에서 export 한다.

## 피드 계약 변경 (그 피드는 실주문 경로가 소비한다)

1. 백업 → 재발행 → **키별 JSON 동일성 비교**로 기존 키 불변을 증명한 뒤 배포한다:
   `cp data/feed/screener_latest.json /tmp/feed_before.json` … `a['candidates'][k] == b['candidates'][k]`.
2. 새 전략 키의 점수 스케일이 다르면 `score_kind` 를 달리 기록하고 기존 키의 확률 변환 규약을 재사용하지 않는다(모델확률은 별도 필드로 보존).
3. 발행기는 `reports/*_latest.json` 을 읽어 합치므로 **고쳐도 다음 발행 시각**에야 반영된다.
4. 어느 쪽 문제인지 분리한다: **발행 측 건수**(키별 items)와 **소비 측 기록**(트레이더 저널 `decisions` 의 screener별 행수)을 함께 인용한다.
   소비가 0행이면 스키마 수용 여부를 의심하되 **거래일 여부를 먼저 확정**한다(휴장이면 정상).
5. **후보 항목에 새 필드를 더할 때는 발행기의 extras 화이트리스트를 같이 늘려라.** `feed_export` 는 item 을
   명시된 키만 복사해 만들기 때문에(그 뒤 `for extra in (...)` 목록), 소스 JSON 에 값을 넣어도 목록에 없으면
   **조용히 사라진다**. 반대 방향의 함정도 있다: 보강 쿼리가 실패해도 발행은 fail-open 으로 계속되므로
   (`edge_score: null, edge_reason: "db_error"`) 로그만 보면 성공처럼 보인다 → 발행 후 **`edge_scored=N/M` 같은
   커버리지 카운터를 후보 수와 비교**해야 한다(실측 0/60 → 원인은 `ORDER BY` 가 SELECT 목록에 없는 컬럼을 참조해
   매번 SQL 오류).

## 실주문 경로 변경 (`trader_core/engine.py`·`config.py`·한도)

순서를 지킨다. 결과물은 "테스트 그린 + 커밋"이다.

1. `docs/spec_*.md` 에 스펙 고정(파일 목록·필수 필드·검증 명령·금지사항).
2. 위임 실행: `scripts/ask_claude.sh build <스펙> "[구현 대상] … [검증] …"`.
3. 위임 뒤 `git status --porcelain` 으로 **스펙에 적은 파일만 바뀌었는지** 대조하고, 스펙 외 파일이 바뀌었으면 커밋 전에 되돌린다.
   **저장소 사본이 깨끗한데 산출물이 안 보이면 실패로 단정하지 마라** — 위임 에이전트가 저장소 사본이 아니라
   **라이브(Windows) 사본**을 고치는 일이 반복된다(둘 다 유효한 경로다). `python3 scripts/sync_trader_agent.py`
   미리보기로 `내용 다름`/`라이브만` 을 확인 → `--pull --apply` 로 저장소에 가져오고 → **git 이 추적하지 않는
   신규 파일(테스트·프리셋)은 `cp`** 로 옮긴 뒤 저장소에서 테스트를 돌려 검증한다.
4. 검증: `/mnt/c/Users/<user>/Python312-64/python.exe -m pytest trader_core/tests runner/tests -q` — **전체 그린이 커밋 조건**이다.
5. 실패면 **즉시 `git checkout -- <파일>` 로 원복**한다. 반쯤 고친 진입 게이트는 fail-closed 로 그날 매매를 전부 막는다 — "일단 넣고 보자"는 선택지가 없다.

함정(전부 실측):
- **halt 를 풀려면 재기동 절차가 따로 있다.** 브리지 세션이 만료돼(`WinError 10061` 3회) soft HALT 로 떨어지면
  재로그인만으로는 자동 RESUME 이 안 걸릴 수 있다(피드 신선도 조건이 게이트를 막는다). 순서: 크레온 로그인 확인 →
  `python run_market_loop.py resume` → 필요하면 `loop_stop.bat`→`loop_start_bg.bat` 재기동.
  **`cmd /c loop_start_bg.bat` 를 포그라운드로 돌리면 블록돼 타임아웃된다** → background 로 실행하고,
  기동 확인은 PowerShell `Get-CimInstance Win32_Process -Filter "name='python.exe'"` 의 **command line**을 찍는다
  (인자·config 가 실제로 붙었는지까지 본다). 기동 후 `loop_state.json` 의 `last_cycle.ts` 가 5분 이내인지 확인한다.
- **시장가 주문은 체결가를 돌려주지 않는다 → 청산가 기록 규약을 지켜라.** 브리지 `POST /order` 응답은
  `order_no: 0, price: 0` 인 경우가 있어 저널이 `exit_price` 를 모른 채 남는다. 그때 ① 주문 시점 `bid` 를 체결가로 쓰고
  ② `fees = 매도대금 × 0.002` 를 명시하며 ③ **현금 증가분(청산 전후 `/balance` 차이)으로 교차검증**해 보고에
  차이를 적는다. 저널에 0원 체결가·fees 0 을 그대로 두면 그날 기대값·계기판이 전부 틀어진다.
  **이제 브리지에 읽기 전용 `GET /daily_pnl`(CpTd6032) 이 있다** — 청산가 정본은 종목별
  `avg_buy_price`/`avg_sell_price`(원 단위)이고, `Journal.correct_exit(id, exit_price)` 로 재계산한다
  (실측 정정: 35,700→35,649 · 23,450→23,400 · 5,570→5,548 · 15,990→15,946 — 호가 추정은 22~51원 틀렸다).
  **주의: `realized_pnl` 은 원 단위가 아니라 천원 절사로 보인다** — 원 단위 가정인 `fees.derive_fees` 에
  그대로 넣으면 `fee = gross − net` 이 −3,000 대로 나와 `check(음수비용)` 이 붙는다. 단위 확인 전에는
  가격만으로 보정하고 수수료는 기존 기록을 유지한 뒤 보고에 미확정임을 적는다.
- **위임이 `rc=1` 로 끝나도 작업은 남아 있다.** `ask_claude.sh` 는 자체 상한(보통 900~2400초)을 넘기면
  `완료` 대신 실패로 끝나지만, 그 전에 파일을 다 써놓은 경우가 흔하다(스크립트 40KB + 테스트 + 산출물이 있는데
  로그는 `[1차 실패] → 2차 백엔드 없음 → 위임 중단`). 재위임 전에 ① `ls -la <산출물>` mtime ② `pytest <신규 테스트>`
  ③ 스크립트 재실행·멱등 확인으로 **직접 검증**하고, 부족한 부분만 다시 위임한다(전체 재실행은 시간만 두 배).
- **위임이 '조용히 멈추는' 경우를 구분하라 — 산출물 0바이트면 기다리지 말고 쪼개라.** 지시문이 길고
  범위가 여러 파일에 걸치면 에이전트가 진행 없이 수십 분~수 시간 매달릴 수 있다(실측: 2.5시간 동안 산출 파일 0개,
  로그는 첫 줄 `[1차] Claude Code mode=build` 뿐). 판단 기준은 `ls -la <산출물>` + `.tmp.<pid>` 0바이트 +
  프로세스 ELAPSED 가 계속 리셋. 처방: ① PID 를 kill ② **한 파일·한 함수 범위**로 프롬프트를 줄이고
  `ASK_CLAUDE_TIMEOUT` 을 900~1200초로 낮춰 재위임 ③ 두 번째도 멈추면 오케스트레이터가 직접 구현한다
  (테스트는 그대로 커밋하고 보고에 "위임 실패→직접 구현"을 명시).
- **위임 가드는 산출물 경로뿐 아니라 프롬프트 본문도 스캔한다.** `ask_claude.sh build <보고서> "<지시문>"` 은
  지시문에 보호경로 문자열(`trader-agent` 등)이 있으면 `금지 경로 … 위임 거부(needs_human)`(rc=2)로
  **즉시 끝난다** — "그 디렉터리는 절대 건드리지 마라" 같은 금지 문장도 거부 사유가 된다. 그 경로에서 해야 할
  1~2줄 수정은 오케스트레이터가 직접 하고, 지시문에서는 "리포 밖 실주문 경로"처럼 **경로명을 빼고** 서술한다.
- **헬퍼 메서드를 '파일 첫 `def `' 위치에 넣지 마라** — 한 파일에 클래스가 여럿이라 엉뚱한 클래스에 붙고 호출 시점 AttributeError → 진입 전면 차단(44 테스트 실패 실측).
  게이트를 호출하는 **클래스 내부, 호출부 근처**에 넣는다.
- **경로별 재고는 저널에서 센다**: `journal.open_trades(screener=...)`. 브로커 포지션에는 전략 귀속이 없어 저널이 유일한 출처다.
- **한도 축을 섞지 마라**: `max_open_positions`(동시 보유 코드 수)와 `max_invested_pct`(총 노출)는 다른 축이다. 총 노출을 고정한 채 동시 보유 수만 늘리면
  원화 리스크는 그대로이고 종목 수만 는다 — 슬롯 합계×종목당 상한이 예산과 맞는지 먼저 계산한다(5×10%=50% > 30%).
- **일일 진입 상한은 두 곳에 있고, 둘을 같이 움직여라.** 엔진 `Config.daily_max_trades` 와 러너 `RunnerConfig.r2_max_entries_per_day` 가
  각각 게이트를 걸고 `runner/tests/test_entry_limits.py` 가 "둘이 같다"를 고정한다 — 한쪽만 올리면 다른 쪽이 먼저 걸려 원인이 헷갈린다.
  바꾼 뒤에는 두 값을 직접 찍어 확인한다(`python3 -c "from runner.config import RunnerConfig; from trader_core.config import Config; print(RunnerConfig().r2_max_entries_per_day, Config().daily_max_trades)"`).
  **상한을 올려도 실제 건수는 안 늘 수 있다** — 그날 엣지 문턱·청산 후 쿨다운·슬롯·스프레드 게이트가 먼저 걸린다. "오늘 몇 건 가능한가"는
  라이브 피드로 `engine.evaluate(..., daily_count=<현재>)` 를 돌려 **통과 후보 수를 세어** 보고한다(실측: 상한 10인데 통과 1건).
- **의도된 fail-closed 도 잘못 배선하면 매매 정지다** — 배선·한도 변경 뒤 다음 개장 전 점검에서 "실제 진입이 일어나는가"까지 본다.
- **사이징을 키울 때 실제로 수량을 묶는 것은 Kelly f* 와 위험예산 두 개다.** `kelly_cap`·`max_position_pct` 만 올려도
  수량은 안 늘어난다: f* = p − (1−p)/b 이고 콜드스타트 prior(승률 0.53, b=1)는 f*=0.06, 동일위험 캡은
  `risk_per_trade_pct`(0.005)÷stop(0.07)≈7% 다 — 종목당 상한을 1/3 로 올린 뒤에도 6%에서 막힌다. 그래서
  "예수금 100% 배분" 같은 요구는 **`sizing_mode="equal_weight"`(남은 예산 ÷ 남은 슬롯, 종목당 상한·가용현금·1회 주문한도로
  클램프)를 새로 넣고 Kelly 경로는 `sizing_mode="kelly"` 로 보존**하는 식으로 구현한다. 세 축(동시 보유수·총노출·종목당)을
  한꺼번에 바꾸면 어느 축이 실제로 수량을 정했는지 못 밝힌다 → 변경 전에 `Config()` 를 import 해 실효값을 찍고,
  계산한 기대 수량(예: 예수금 3,000,000·price 10,000 → 100주)을 테스트로 고정한다.
- **기본값을 바꾸면 수량을 단정하는 기존 테스트가 깨진다 — 기대값을 고치는 대신 모드를 고정하라.** 수량 단정
  (qty 60/41/100 같은)은 '게이트·동일위험 로직'을 검증하는 것이므로 그 테스트 Config 에 `sizing_mode="kelly"`
  (필요하면 `max_position_pct`)를 명시하고, `trader_core/tests` + `runner/tests` **두 스위트**가 그린인지 확인한 뒤 커밋한다.
  전역 상한을 낮추면 '종목당 상한 초과' 케이스의 보유 수량도 같이 키워야 한다(19%가 33% 상한 아래로 내려가 게이트가 안 걸린다).
- **여러 줄 블록을 교체할 때는 `old_string`/`new_string` 양쪽에 다음 줄 첫 줄까지 포함하라.** 주석 줄을 남긴 채
  그 뒤에 코드를 이어 붙이면 한 줄로 합쳐져 `IndentationError` 가 나고, 그 상태로 커밋·검증을 돌리면 전 테스트가 죽는다.
  패치 직후 lint/컴파일 결과를 확인하고(`python3 -c "import ast; ast.parse(open(f).read())"` 또는 도구의 lint), 깨졌으면
  같은 자리를 다시 온전한 블록으로 교체한다.
- **현금 미제공과 진짜 0원을 뭉개지 마라.** 브리지가 매수 가능금액 조회에 실패하면(`12275`/`12340`) `/balance` 에 `cash`
  키가 아예 없다 → 그걸 0 으로 읽으면 모든 매수가 조용히 차단되고, 유도값만 쓰면 빈 계좌가 전액 배분으로 나간다.
  `AccountState.cash_known` 같은 플래그로 갈라 **미제공일 때만** `equity − 보유원가` 로 유도하고 진짜 0 은 fail-closed 로 남긴다.
- **새 게이트는 '차단일 때만' `GateResult` 를 남겨라.** `test_engine.py::test_all_gates_pass_produces_plan` 이 통과 결정의
  게이트 목록을 6개로 고정해 두어, 통과 시에도 append 하면 그 테스트가 깨진다(스펙이 "기존 테스트 기대값 수정 금지"라면
  코드를 고쳐야 한다). 차단 사유 문자열에 분·%·`rr` 같은 **수치를 넣어** 사후분석 근거로 남긴다.
- **엔진 안에서 브리지 호가를 새로 부르면 러너의 청크 규약을 지켜라.** `runner.PRE_FILTER_QUOTE_CHUNK=5` 이고
  `runner/tests/test_loop.py` 가 **모든 `quotes()` 호출 인자가 5개 이하**인지 spy 로 단정한다 — 엔진에서 후보 15개를
  한 번에 넘기면 그 테스트가 깨진다. 처방: `Config.spread_quote_chunk`(기본 5)로 사이클당 1회 청크 조회 후 병합.
  같은 이유로 `MockBridge.quotes()` 는 `set_quote` 로 심은 종목만 돌려준다 → 신규 호가 게이트 테스트는 후보 코드를
  심거나 서브클래스에서 채워라. 안 채우면 빈 응답 → fail-open 통과로 **테스트가 거짓 초록**이 된다.
  검증은 두 스위트(`trader_core/tests` + `runner/tests`) 전부.
- **모르면 막지 않아도 되는 필터는 fail-open + 사유 기록으로 둬라.** 스프레드·유동성 같은 조언적(advisory) 게이트가
  호가 1회 실패로 fail-closed 면 그날 진입이 통째로 막힌다. 값이 없으면 통과시키고 차단할 때만 `GateResult` 를 남기되
  사유 문자열에 실측 수치(`spread_too_wide (0.412% > 0.30%)`)를 넣어 사후분석 근거로 쓴다. 프리셋 JSON 에 넣어도
  `_coerce` 가 미지 키를 버려 엔진에 도달하지 않는다 → 경로별 프로파일의 정본은 `trader_core/profiles.py` 의
  `DEFAULT_PROFILES` 이고, dict 를 실제로 받는 것은 `RunnerConfig`(loop/monitor 경로)다. 엔진은
  `getattr(config, "screener_profiles", None)` 폴백으로 DEFAULT_PROFILES 를 읽는다. 값을 바꿀 때는 profiles.py 한 곳을
  고치고 `Config()`·`profile_for()` 를 찍어 **어느 쪽이 실제로 먹는지** 확인한 뒤 프리셋 사본도 동기화한다.
- **프로파일을 배선해도 `RunnerConfig.screeners` 에 없으면 엔진이 평가조차 하지 않는다.** 기본값은 `["close","swing"]`
  이라 새 스크리너(예: daytrading)는 PRE 필터 집계에만 나타나고 저널 `decisions` 는 **0행**이다 — "게이트가 막았다"와
  구분되지 않으므로, 배선 후 `select screener,count(*) from decisions where ts like '<오늘>%'` 로 **스크리너별 행이
  생기는지**부터 확인한다. 기본값을 바꾸면 그 값을 단정하는 기존 테스트가 수십 건 깨진다(36건 실측) → 기본값은 두고
  `config_live.json` 같은 **루프 전용 설정 파일**로 넘긴다.
- **기동 스크립트·설정은 저장소 사본이 아니라 라이브 사본이 실행된다.** `loop_start_bg.bat` 을 저장소에서 고쳐도
  라이브에서 `cmd /c loop_start_bg.bat` 를 돌리면 **라이브 배치의 옛 내용**이 실행돼 새 `--config` 가 조용히 빠진다
  (프로세스 command line 을 PowerShell `Get-CimInstance Win32_Process` 로 찍어 확인). 저장소에서 고친 뒤 **라이브 사본으로
  `cp`** 하고, 재기동 후 command line 에 인자가 들어갔는지 본다. 배치는 CRLF 이므로 리포에서 고칠 때 개행을 보존한다.
- **루프를 되살리는 주체가 따로 있다: `tools/supervise_loop.ps1`(loop_sup).** 감독자는 루프를
  `run_market_loop.py run --loop --live --yes --feed <url>` 로 다시 띄우고, 여기에 `--config` 가 없으면
  screeners 가 기본값으로 **조용히 되돌아간다**(재기동 직후 저널 `decisions` 에 swing 이 다시 나타나는 것으로
  발견). ps1 을 고쳐도 **이미 도는 감독자 프로세스는 옛 코드를 메모리에 들고 있다** → 감독자 PID 를 찾아
  (`Get-CimInstance Win32_Process | ? CommandLine -match 'supervise'`) 종료하고 같은 명령으로 재기동한 뒤,
  새 루프의 command line 에 `--config` 가 붙었는지 확인한다.
  **감독자가 살아 있으면 `loop_stop.bat` 만으로 재기동이 끝난다** — 킬 직후 수 초 안에 감독자가 같은 인자로 새 루프를 띄우므로
  `loop_start_bg.bat` 를 또 돌릴 필요가 없다(단일 인스턴스 가드가 중복은 막지만 기동 로그가 헷갈린다).
  확인은 `loop_sup.log` 의 `started loop pid <N>` 과 `runner.log` 의 `=== log opened pid=<N>` 두 줄의 pid 일치로 한다.
  **로그를 통째로 grep 하면 옛 프로세스 줄이 섞여 변경이 반영 안 된 것처럼 보인다** — 설정값 변경 검증은
  `awk '/log opened pid=<N>/{f=1} f' runner.log | grep -E '<확인할 문구>'` 로 **새 pid 이후 줄만** 본다
  (실측: 상한을 10으로 올린 뒤 전체 grep 이 이전 프로세스의 `max 3` 줄을 잡아 미반영으로 오독).
  **재기동 직후 `loop_state.json` 은 아직 이전 프로세스의 값이다** — 그 파일의 `check.bear_guard`/일일 카운터를 새 정책의
  증거로 읽으면 옛 상태를 새 코드로 오독한다. 새 pid 의 첫 `cycle done (...)` 로그가 나온 **뒤에** 상태를 읽고,
  새 동작은 로그 줄(예: `BEAR GUARD L0: risk_off 기본 보수…`, `sleeping 30s`)로 먼저 확인한다.
  재기동 직후 첫 1~2사이클은 브리지 타임아웃이 섞일 수 있고 그때 루프는 `no current quote; holding` 으로 보유를 유지한다(fail-safe)
  → 타임아웃 1회를 재기동 실패로 오독하지 말고 다음 사이클의 `monitor: <code>: holding (px …)` 로 회복을 확인한다.
- **전역 게이트는 스크리너를 가리지 않는다.** `min_edge_score` 같은 Config 필드는 모든 경로를 차단하므로,
  "한 전략만 조이기"는 프로파일/스크리너 단위로 내려야 한다. 켜기 전에 오늘 후보의 점수 분포를 찍어
  (`feed` 의 `edge_score` 히스토그램) **몇 건이 통과하는지**를 먼저 보고한다 — 0건이면 그날 매매는 전부 정지다.
6. **파이프라인 출력과 헬퍼 출력을 비트 비교하는 테스트는 후처리 단계까지 맞춰라.** 발행기(`feed_export.main`)는
   `build_items()` 뒤에 `attach_edge_scores()` 로 엣지 4키를 덧붙이므로, 기대값을 `build_items()` 만으로 만들면
   DB 에 그 종목 봉이 쌓이는 순간 실패한다(그 전까지 통과한 것은 봉이 없어 fail-open 경로였기 때문이다).
   기대값에도 **같은 후처리를 적용**하고, DB 로더(`load_prev_closes`/`load_universe`/`load_edge_bars`)를
   monkeypatch 해 **DB 상태와 무관**하게 만든다. 비교 강도를 낮추는 대신 단계를 맞추는 것이 정답이다.

## 실행 중인 잡을 멈춰야 할 때 (업데이트·회선 경합·오배포)

- 크론이 띄운 파이프라인은 **root 소유**라 일반 사용자 `kill` 이 `Operation not permitted` 로 실패한다 →
  `/mnt/c/Windows/System32/wsl.exe -u root -- kill -TERM <pid> <child>` 로 보낸다(인터롭이 살아 있을 때).
- 컨테이너 안에서 도는 자식(`docker exec ... python3`)은 부모만 죽여도 계속 돈다. 그 이미지에는 `ps`·`pkill`·`pgrep` 이
  **없는 경우가 흔하다**(`exec: "pkill": executable file not found`) → `/proc` 을 직접 열거한다:
  `docker exec <c> sh -c 'grep -la <스크립트명> /proc/[0-9]*/cmdline'` 로 pid 를 얻고 그 pid 로 kill 한다
  (자기 자신의 grep 도 목록에 잡히므로 결과 1건만 나오면 미종료로 오독하지 않는다).
- 정지 뒤에는 ① 부모·자식·컨테이너 프로세스 셋 다 사라졌는지, ② 컨테이너 자체는 Up 인지(서비스는 살려 둔다),
  ③ 진행 파일(부분 다운로드·백필 진행 마커)을 **건드리지 않았는지**를 실측해 보고에 붙인다.
- 멈춘 잡은 **반드시 다시 돌린다**: 재실행 명령과 산출 로그 경로(`reports/full_pipeline_dd_<ts>.log`)를 보고에 적고,
  재실행이 Phase 1 부터 다시 시작해도 되는 잡인지 확인한다(진행 마커가 있으면 이어받는다).

## 운영 데이터 삭제·되돌리기 (미추적이어도 실데이터다)

- **`rm -rf <디렉터리>` 전에 그 안을 열거하고 생성 주체를 찾는다**: `find <dir> -maxdepth 2 | head`, `grep -rn "<파일명 패턴>" --include=*.py`.
  `trader-agent/data/fills/fills_YYYY-MM-DD.json` 처럼 `git status` 에 `??` 로 보이는 파일도 **저널에서 파생된 운영 산출물**이다.
- 지웠다면 파생이므로 **생성기로 재생성해 내용을 검증**한다(예: `tools/export_fills.py --date <일>` → `청산 N건 · 실현 …`). 파생 원본이 당일만 제공되는 데이터면 복구 불가다.
- **`git checkout -- .` 를 쓰지 마라** — 파이프라인 상태 churn 파일 수십 개를 한꺼번에 되돌린다. 되돌릴 파일을 열거해 **개별 경로**로 지정하고,
  되돌리기 전에 `git status --porcelain` 으로 미추적 산출물이 사라지지 않는지 확인한다.

## 개선 큐·보호경로 (자동화에 넘길 때)

- **승인받은 백로그 항목을 실행하기 전에 그 항목의 `check` 를 다시 돌려라 — 등록 시점의 진단이 이미 낡았을 수 있다.**
  승인은 "이 처방을 해도 된다"는 뜻이고 "이 결손이 아직 있다"는 뜻이 아니다. 실측: 미해결 3원천을 한 번에 풀려던
  항목이 실제 재측정에서 3건 중 2건이 이미 해소돼 있었고(다른 경로가 먼저 고침), 남은 1건도 원래 처방(KIS 종목당 1콜
  ≈2.7시간)보다 싼 경로(키 불필요 API 103콜)가 있어 **쿼터 결정 자체가 불필요**해졌다. 순서: ① 항목의 `check`/`check_target`
  명령을 그대로 실행 ② 미달이 아니면 근거 수치와 함께 `done` 으로 닫는다 ③ 미달이면 처방이 다른 항목과 충돌하는지
  확인(실측: "빌더 로드창을 120거래일로 고정" 처방이 12개월 모멘텀을 살리려고 로드창을 분리해 둔 다른 항목과 정면 충돌)
  ④ 충돌하면 코드 주석에 적힌 **왜 그렇게 되어 있는지**를 근거로 처방을 바꿔 제안하고 결정 문서를 남긴다.
  승인 배치를 받았다고 처방을 그대로 기계적으로 실행하면 불필요한 호출·위험한 변경을 만든다.

- 등록: `python3 scripts/improve_queue.py add --title <…> --why <…> --acceptance <…> --priority <정수> [--protected]`.
  `--priority` 는 **정수**다(`high`/`medium` 문자열은 argparse 오류로 전량 미등록). 큐 파일 구조는 `{"updated","tasks"}` 이므로 `items` 키로 읽으면 **0건으로 오독**한다 → 확인은 `git show --stat HEAD` 의 증가 줄수로 한다.
- `trader-agent/**`·`config/objective.json`·`.env`·`kill_switch.txt` 등 **보호경로를 건드는 과제는 `--protected`** 로 넣는다(자동 병합 대신 승인패킷/needs_human).
  보호경로 단일 진실원은 `scripts/protected_paths.py`(`violation()`/`is_protected()`)이고 저작·위임 경로는 **fail-closed** 다.
- `--why` 에는 실측 근거(행수·시각·로그 문구), `--acceptance` 에는 검증 명령과 기대 변화(실패→통과)를 쓴다. 같은 손실원의 중복은 1건으로 합친다.

## 저널·산출물을 읽는 계측 스크립트 (읽기 전용)

- **실주문 저널을 `mode=ro` 로 직접 열지 마라 — drvfs+WAL 이라 `disk I/O error`** (2026-11-17 실측:
  `/mnt/c/.../trader-agent/journal/trade_journal.sqlite3` → `mode=ro` 실패, `mode=ro&nolock=1` 실패,
  **`immutable=1` 만 성공**). 리포 관용구(`trader_cycle._journal_copy`)처럼 `.sqlite3`/`-wal`/`-shm` 을 임시
  디렉터리로 복사한 뒤 복사본을 `mode=ro` 로 열고 끝나면 임시 디렉터리를 지운다(원본 쓰기 0).
- 저널 `trades.pnl` 은 **이미 비용 차감 후 순손익**이다(실측 id=11: (34050−36642.5)×2−146 = −5331 = pnl; 청산 11건 전부 pnl = gross − fees).
  `fees` 를 또 빼면 이중 차감 — `money_scoreboard` 가 `net_pnl = pnl − fees` 로 손실을 146원 과대계상하고 있었다(2026-11-17 정정).
  진입금액은 `order_resp_json` 주문가가 아니라 **`price`(체결 VWAP)×`qty`** 다(3×24200=72,600 ≠ 실측 3×24103.3333=72,310).
- **한도 숫자를 손으로 쓰지 마라.** 정본은 `trader-agent/trader_core/config.py` Config 이고,
  `scripts/risk_limits.py`(`LIMITS`, `LIMITS_META['source'|'loaded']`)가 그것을 읽는 단일 입구다
  (기준: 일 3건·종목 1/3·동시 3·총 100%). `loaded=False` 면 폴백(10%/30%)이므로 그 보고는 '측정 불가'로 쓴다.
  문서·스킬에 낡은 10%/30% 가 다시 들어가면 계기판·감사와 또 갈라진다.
- **일 손실한도는 당일 실현(`realized.today.pnl`)으로만 판정한다.** 누적 실현을 일 한도와 비교하는 것은
  범주 오류다(2026-11-16 오보). 계기판이 `today` 를 주지 않으면 '판정 불가'로 적고 0·누적으로 메우지 않는다.
- **브로커 대조(reconcile)가 체결가를 정정하면 파생 지표를 전부 다시 돌린다.** 저널의 `pnl` 이 바뀌면
  계기판·재투입 원장·`objective_state` 가 그 순간부터 낡은 값이 된다 — 재계산 후 보고에는 **정정 전/후 수치를 함께**
  쓰고(예: 당일 −6,831 → −7,128), 계약 문서(스펙·md)의 수치도 같이 고쳐 둔다. 정정 사실을 모른 채 다음 보고에
  옛 숫자를 넣으면 그게 바로 사용자가 잡아내는 오보가 된다.
- 포지션은 `side='buy'` 행 하나이고 청산 시 같은 행의 `exit_ts/exit_price/exit_reason/pnl` 이 채워진다.
  `exit_reason` 이 `unfilled:`·`reconcile: broker flat` 로 시작하는 행은 포지션이 아니므로 계측에서 제외하고 건수를 남긴다.
- 피드 날짜는 저널에 **기록되지 않는다**(algo_json 의 `version` 은 config 버전). 과거 진입의 피드 asof 는 복구 불가이므로
  `null`+사유로 남기고, 지금부터 `generated_at` 관측을 누적(`data/reports/feed_asof_history.jsonl`, 같은 값 중복 금지)해
  이후 실행에서 "진입 ts 이전 최근 관측"으로 해소한다 — 현재값을 과거에 소급 대입하지 않는다.

- **피처 단위 집계 전에 컬럼 이름 중복을 먼저 센다.** 학습 패널(`services/xgboost-ml/app/models/wf/panel_*.npz`)은
  `X`/`feature_names` 를 따로 갖는데, 213컬럼 중 고유 이름이 199개인 사례가 있다 — 이름이 같은 14쌍이 **값까지 동일**해
  피처 중요도가 쪼개지고 피처 수가 부풀려진다. 이름을 dict 키로 쓰는 집계는 마지막 컬럼이 앞 컬럼을 덮어써
  조용히 틀린다 → `len(names) vs len(set(names))` 를 먼저 찍고, 중복이 있으면 인덱스 기준으로 다룬다.
- **피처 생존은 한 축으로 판정하지 마라 — 분산·커버리지·누수 3축.** `nonzero_ratio` 하나로는
  "값이 있지만 전부 0"(죽음)과 "값이 드물게 있음"(희소)과 "결측을 0으로 세는 착시"가 구분되지 않는다.
  비영률은 **분모를 밝혀** 쓴다(전체 행 기준 = 패널 커버리지, 유효값 기준 = 조건부 밀도; 둘 다 기록).
  누수는 ① 미래 h일 라벨과의 상관(진짜 신호) ② 당일 수익률과의 동시상관(가격 파생 피처는 **정의상 1에 가깝다** —
  룩어헤드로 오독 금지) 로 나누고, 판정 보류·표본부족을 명시한다. 판정기 예: `scripts/feature_survival_report.py`.

## 크론·배치 배선 검증 ("등록했다" != "돈다")

- crontab 에 줄을 넣었을 뿐이면 검증이 아니다. **임시 라인으로 실제 발화를 증명한다**:
  `( crontab -l; printf '%s %s * * * <명령> >> /tmp/cron_probe.log 2>&1\n' <M> <H> ) | crontab -`
  (2분 뒤 시각) → 대기 → `ls -la /tmp/cron_probe.log` 와 **산출물 mtime 변화**를 함께 확인 →
  `crontab -l | grep -v <임시 라인>` 으로 **반드시 제거**하고 잔존 0건을 확인한다.
  `--quiet` 옵션이 있으면 로그는 0바이트가 정상이므로 **파일 생성·산출물 mtime** 으로 판정한다.
- 배치 래퍼는 환경을 스스로 만든다: `set -a; . ./.env; set +a` 후 호스트에서 필요한 값
  (`POSTGRES_HOST=127.0.0.1`, `POSTGRES_PORT` = `docker port` 노출값)을 **덮어쓴다**. 크론 로그 첫 줄에
  실행 여부·건수를 남겨 나중에 로그만 보고 판정할 수 있게 한다.
- **인자 요구·날짜 규칙을 지켜라 — 어기면 배치가 조용히 0건으로 '성공'한다.** 분봉 백필은 `--from/--to` 가
  **필수**인데 크론 줄에 빠져 매일 argparse 오류로 죽었고, `--from == --to` 도 거부한다(엄격한 `<`). crontab 에는
  `$(date +\%F)`·`$(date -d '-7 days' +\%F)` 처럼 **`%` 를 이스케이프**하고, 중첩 따옴표(`date -d \"7 days ago\"`)는
  sh 에서 리터럴 따옴표로 들어가 실패하므로 **작은따옴표**를 쓴다. 등록 후 그 줄을 `sh -c` 로 그대로 실행해
  날짜 확장 결과(`--from 2026-11-11 --to 2026-11-18`)까지 확인한다.
- **백필 래퍼의 프로젝트 경로를 고정하지 마라 — 홈이 둘인 머신에서는 진행파일 쓰기 실패로 절반이 죽는다.**
  스크립트가 `PROJ = os.environ.get("PROJ_DIR", "/home/<옛 사용자>/analyist_dd")` 처럼 **다른 홈을 기본값**으로
  들고 있으면, 진행 마커 파일 소유권이 어깻날 때 `PermissionError` 로 **호출당 한 건씩** 실패하고도 요약은
  정상처럼 찍힌다(실측: 호출 500건 중 250건 실패 · 로그에 같은 파일명만 250줄 · 적재 요약은 정상 출력).
  크론 래퍼에 `PROJ_DIR=<현재 리포 절대경로>` 를 export 하고, 실행 전에 `ls -ld <진행파일 디렉터리>` 로
  쓰기 가능 여부를 확인한다. 판정은 ``ok=N fail=M`` 의 M>0 여부이며, M>0 이면 성공 요약을 믿지 않는다.
- **수집 크론 시각이 벤더 게시 전이면 그 러너는 영원히 T-1 을 받는다.** 스크립트를 고쳐도 안 되고 **시각을 옮기는 것**이
  무비용 레버다(실측: 16:20 수급 크론이 항상 전일 → 커버리지 0.09%). 앞 단계 산출물에 의존하면 그 뒤로 옮긴다
  (예: 일봉 적재 18:55 → 19:10). `/etc/cron.d/*` 는 root 소유라 `wsl.exe -u root -- cp <새파일> <대상>` 로 바꾸고
  `ls -l` 로 `root:root 644` 를 확인한 뒤, **다음 실행 시점의 as-of 가 당일인지**를 검증 계획으로 남긴다.
- **컨테이너 phase 는 리포의 자기신고 헬퍼를 못 쓴다 — 호스트에서 행 델타를 재서 대신 남겨라.**
  `docker exec` 로 도는 수집 단계는 `dq_claim` 배선이 없어 실패해도 `dq_runner_claim` 에 흔적이 없다.
  처방: phase 전/후 `docker exec stock_postgres psql -tAc "select count(*) from <table>"` 델타를 계산해
  `record_claim(..., claimed_rows=델타, persisted_rows=델타, source_rows=None, note="rc=.. count a->b")` 로 기록한다.
  **`source_rows` 는 NULL 로 둬라** — 소스 행수를 모르는 상태에서 0 을 넣으면 `parse_failure`(source>0 & claimed==0) 오탐이 울린다.
  자기신고 헬퍼는 무슨 일이 있어도 `exit 0`(수집을 깨면 안 된다)이고, 실DB에 테스트 runner 로 1행 왕복 후 삭제해 검증한다.
- **"신호 기반 기본 대상" 배치는 신호 없는 날 0건으로 통과한다.** 백필 스크립트의 기본 대상이 capital_sim
  신호 종목이면 신호가 없는 날 `codes=0, rows_written=0` + rc=0 이라 로그만 보면 성공이다 → 당일 처리 대상
  (피드 후보 + 저널 체결)을 파일로 만들어 `--codes-file` 로 넘기고, 적재 후
  `select count(*), count(distinct stock_code) from <table> where trade_date=<날짜>` 로 **행 수를 확인**한다.

## 외부 신호(랭킹·괴리)를 후보 선별에 끌어들일 때

- **화이트리스트(교집합)가 "압축"인지 먼저 재라.** 외부 랭킹 ∩ 자체 스코어는 기준이 직교하면
  후보를 **대체**한다 — 실측: 상승률 상위 60코드 ∩ 칼만/모델 상위 20건 = **교집합 0건**(점수 55.1 vs 65.5).
  배포 전에 `교집합 수 · 점수 분포 · 후보 0건 여부`를 리포트로 남기고, 실사용은 ⓐ 랭킹 상단은
  **배제 필터**(후보 제거) ⓑ 대상 대역은 **정렬 전용 가점**(공개 `score` 불변 — 소비자 문턱 의미 보존)으로
  설계한다. 압축을 그대로 켜면 그날 후보가 0건이 될 수 있다.
- **외부 API 파라미터 타입·인코딩을 1회 실호출로 확인하라.** 같은 계열도 타입을 엄격히 본다
  (실측: `pct_from=2.0` → `invalid_input: pct_from must be an integer`; 네이버 ETF 목록은 **EUC-KR**).
  어댑터에서 정수화·디코딩을 흡수하고, 실패는 **fail-open + 사유 기록**으로 두어 크론 체인이 끊기지 않게 한다.
- **장중 계측을 장외 스냅샷으로 검증하지 마라.** 마감 후 NAV·괴리는 시차·결제 때문에 실제와 동떨어진다
  (실측: 합성 해외 ETF +3.76%/−2.18% — 장중 값이 아니다). 수집기는 `--window` 로 장중만 돌리고,
  분석은 국내형과 해외·합성형을 분리 집계한다.
- **시간 한도는 루프 주기만큼 늦게 집행된다(실측 30분 규칙 → 5분 주기에서 34.6분, 1분 주기 30.2분).**
  보유 한도를 바꿀 때는 주기 구간별 실제 집행 지연을 같이 적고, "그 시간 안에 변동이 왕복비용(약 0.21%)을
  넘는가"를 먼저 잰다 — 실측 단타 2건은 +0.04%/−0.07%로 비용 미달(수수료 비중 5.4%)이었다.

## 국면(regime) 연동 게이트를 넣을 때

시장 국면으로 진입 강도를 조절하는 정책(하락장 수익 보존, 상승장 빈도 확대 등)은 **국면 값의 출처·계약·무효 상태**를
먼저 확정하고 넣는다. 계약이 없으면 코드는 초록인데 라이브에서는 영원히 발동하지 않는다.

1. **국면은 피드로 받는다(트레이더에 WSL 파일 경로를 박지 마라).** 발행기(`feed_export`)가 `data/state/market_regime.json` 을 읽어
   payload 최상위 `regime`(`state`/`asof`/`index`/`close`/`ma20`/`ret5`/`reason`)으로 싣고, 파일이 없거나 `asof` 가 오래되면(예: 5일 초과)
   **키를 아예 넣지 않는다**. 소비 측은 `snapshot.regime` 을 읽는다 — Windows 에서 도는 루프는 WSL 경로를 직접 못 연다.
2. **판정은 순수 함수 + 러너 주입.** `trader_core/market_guard.py` 처럼 `evaluate(regime_state, daily_return_pct, cfg) -> 판정 dataclass` 를 두고
   `engine.set_bear_guard()/set_regime()` 으로 사이클마다 갱신하며 게이트는 그 판정만 읽는다. 판정에 수치를 넣은 `reason` 을 남겨
   `loop_state.check` 에 기록하면 사후 감사가 된다.
3. **근거가 없으면 no-op(fail-open)** — 국면 미상·당일 수익률 미상·ATR 미상이면 아무 것도 조이거나 풀지 않는다.
   보수화는 사이즈·빈도를 줄이고, 확장은 **빈도(일일 진입 상한)와 엣지 문턱만** 푼다 — 상승장에서도 **종목당 사이즈는 늘리지 않는다**
   (사이즈까지 키우면 어느 축이 성과를 냈는지 못 밝힌다). 두 정책은 반대 국면에서 상호배타여야 하고, 같은 후보에 두 국면을 태워 테스트로 고정한다.
4. **확장 문턱은 후보별로 평가한다.** 변동성(ATR%) 문턱을 넘는 종목만 상한을 늘려야 "변동성이 큰 곳에서 빈도를 늘린다"가 되고,
   전역 상한을 올리면 조용히 전 종목 확대가 된다.
5. **배선 ≠ 적용.** 커밋·재기동을 해도 **라이브 피드에 `regime` 키가 실린 발행본이 나오기 전까지는 전부 비활성**이다.
   보고 전 확인 두 가지: ① `data/feed/screener_latest.json` 의 `regime`·`generated_at`(구 발행본이면 아직 아님)
   ② 실제 피드 JSON 으로 `engine.evaluate()` 를 국면별로 돌려 **게이트 캡 분포를 센다**(예: 후보 20 → risk_on 에서 18건 `max 5`,
   ATR 문턱 미달 2건만 `max 3`). 이 실측이 없으면 "적용했다"는 말은 추측이다.
6. **소스가 비었는데 발행하면 라이브 섹션이 0건이 된다.** 스크리너가 `items: [] + reason` 으로 끝난 산출물을 발행기가 그대로 실으면
   피드의 그 전략이 0건이 된다(트레이더는 조용히 후보 없음). 발행 전에 **섹션별 items 수를 세고**, 0건이면 기존 피드 유지 여부를 로그에 남긴다.
7. **호스트 경로와 컨테이너 경로의 코드가 다를 수 있다.** 같은 스크리너라도 호스트 실행은 레짐 게이트(`--regime-block`)가 걸려 0건,
   컨테이너 실행(구 이미지)은 후보 20건을 낼 수 있다 — "게이트가 안 걸린다"를 판정하기 전에 **어느 실행 경로가 그 피드를 만들었는지**
   (크론 래퍼가 `docker exec` 인지 호스트 `python3` 인지) 확인한다. 두 경로가 갈리면 게이트가 아니라 배포가 문제다.
8. **게이트 입력 파일이 컨테이너에 없으면 게이트는 fail-open 으로 '조용히' 통과한다.** 스크립트는 bind mount 라 같아도
   `data/state/*.json` 같은 입력은 마운트되지 않는 경우가 흔하다(실측: 호스트는 risk_off 로 0건, 컨테이너는 같은 날 20건).
   확인 `docker exec <c> test -f /app/data/state/<file>` → 없으면 합성에 `./data/state:/app/data/state` 를 추가하고
   `docker compose up -d --no-deps --force-recreate <service>` (**서비스 키로 지정** — 컨테이너명으로 하면 `no such service`),
   재생성 후 컨테이너 안에서 같은 스크립트를 `--dry-run` 으로 돌려 호스트와 **items 수가 같은지** 대조한다.
   래퍼에는 입력 가시성 WARN 한 줄을 남겨 재발을 로그로 잡는다.
9. **tuple/list 설정 필드는 `as_dict()`↔`with_overrides()` 왕복에서 문자열이 된다.** `Config._coerce` 의 else 가지가
   `str(value)` 라 `('risk_off',)` → `"('risk_off',)"` 이 되고 `state in cfg.<field>` 가 **조용히 False** 가 되어
   보수화·확장·급등 목록이 전부 no-op 이 된다(런타임 증상: `reason="risk_off 아님"`). 시퀀스 필드를 새로 넣으면
   `RunnerConfig().engine_config()` 를 찍어 `isinstance(cfg.<field>, tuple)` 을 테스트로 고정하고 `_coerce` 에 tuple 가지를 추가한다.
10. **정책의 '미발동' 쪽도 명시적 기본값으로 만들어라.** 수익 조건부 보수화만 있으면 손실·본전 구간은 아무 것도 안 조인다
   → 국면 자체를 기준으로 한 baseline(예: risk_off = 사이즈 ×0.5 · 엣지 +2 · 일일 진입 2건)을 별도 판정 함수로 두고,
   조건부 판정이 더 보수적일 때만 덮어쓴다. 두 판정이 동시에 걸리는지(상호배타)를 테스트로 고정한다.
11. **비교 기준선은 '진입 시점 스냅샷'이다.** "오늘 지수가 올랐나"가 아니라 "그 종목을 샀을 때보다 지금이 높은가"다.
   진입 시 지수 수준을 `algo_json.market_index` 에 남기고 모니터가 현재 값과 비교한다(발행 payload 에 `kospi_level`/`kosdaq_level`,
   `"4,684.97"` 콤마 파싱 주의). 진입 기록이 없는 기존 포지션은 연장하지 않는다(fail-safe) — 소급 적용하지 마라.
   시간 청산(시한부) 연장 규칙은 네 조건을 **모두** 요구한다: ① 미실현 이익 > 0 ② 진입 시점 지수 < 현재 지수
   ③ 하드 상한(예: 240분) ④ 이익이 0 이하로 회귀하면 즉시 청산(본전 가드). '오늘 장이 올랐다'는 조건은 틀렸다 —
   그날 지수가 올라도 우리가 산 뒤 하락했으면 연장 근거가 없고, 실측에서 90분 일괄 청산이 +0.16%에서 끊은 포지션이
   같은 날 +1.4%(고점)까지 갔다.
12. **문턱을 올리기 전에 실측 분포로 통과 건수를 세라.** 엣지 +2(4→6)가 후보를 0건으로 만드는지 `edge_score` 히스토그램으로
   먼저 확인한다(실측: 1점 24 · 2점 11 · 3점 13 · 4점 6 · 5점 4 · 6점 2 → 6점 이상 2건이라 '하루 1~2건'이 성립).
   분포를 안 재고 문턱을 올리면 그날 매매가 0건이 되고, 그건 '보수화'가 아니라 정지다.
13. **루프 주기는 `runner/config.py` 의 `interval_seconds`+`focus_windows` 이고, 기본값 변경은 테스트가 잡고 있다.**
   `runner/tests/test_config.py`·`test_schedule.py` 가 기본값(예: 300초·집중창 60/60/30)을 단정하므로 새 정책값으로 **기대값을 갱신**하고,
   재기동 후 `runner.log` 의 `cycle done (phase) in <X>s; sleeping <N>s` 로 실제 주기·소요시간을 실측해 보고한다
   (사이클 자체가 1~2초면 주기를 30초로 줄여도 브로커 부하가 주기의 5% 미만).
14. **사이클 안에서 같은 호가를 두 번 받지 마라.** 프리필터가 받은 quotes dict 를 `engine.evaluate(quotes=...)`·
   `monitor.heartbeat(quotes=...)` 로 넘겨 스프레드/급등 게이트·청산 판정이 재사용하게 한다(사이클당 브리지 4~5콜 절감).
   인자를 안 넘겼을 때만 조회하는 기본값(None)을 유지해야 기존 테스트가 그대로 산다.
15. **'거래를 0건으로 만드는 보수화'는 이 사용자 기대와 어긋난다.** 하락장·불확실 구간이라도 매매가 완전히 멈추면 안 된다는 것이 기준이다
   (요청: "내일부터 매매할 수 있게"). 후보 생성 자체를 0건으로 자르는 대신 **사이즈·엣지 문턱·일일 건수**로 조이고,
   "그래도 하루 몇 건이 통과하는가"를 실측 분포로 보여라. 국면 게이트를 켜는 정책과 후보 생성을 끄는 정책은 다르다.
16. **정책을 뒤집으면 결정문서·고정 테스트를 같은 커밋에서 다시 써라.** 호출 형태(예: `--regime-block off`)나 문턱을 테스트로 못 박아 둔 규칙은
   코드만 바꾸면 반쪽이 된다 — 결정문서에 개정 절을 덧붙이고(낡은 결론은 '폐기'로 표시), 그 규칙을 고정한 테스트를 **새 정책 기준으로 다시 쓴 뒤**
   커밋한다. 되돌린 뒤 "무엇이 다시 가능해졌는지"를 라이브 게이트 로그로 확인해 보고한다(예: 상한 해제 후 같은 날 몇 번째 진입이 가능한지).

## 청산 규칙 변경 (트레일링·시한부)

- **청산 판정은 두 경로에 있다 — 둘 다 고친다.** 사이클 판정 `trader_core/monitor.py::_trailing_verdict` 와
  초 단위 틱 감시 `trader_core/tickwatch.py::tick_exit_candidates` 가 각자 arm/trail 을 계산한다. 한쪽만 바꾸면
  **먼저 발동하는 쪽(틱)** 이 옛 규칙으로 청산한다. 회귀 테스트도 둘(`test_monitor.py`, `test_tickwatch.py`)에 각각 둔다.
- **전역 트레일링이 사실상 죽어 있는지 먼저 본다.** 고정 익절(+3%)이 전역 arm(5.5%)보다 먼저 닿으면 트레일링은 발동 기회가
  없다(설정은 있으나 무효). 프로파일에 `trailing_arm_pct`/`trailing_stop_pct`(0 = 전역 사용)를 두고, 두 경로 모두
  저널 행의 `screener` 로 프로파일을 해석해 덮어쓴다. **고정 익절선은 지우지 말고 상한으로 남긴다** — 지우면 급등 종목이
  무한 보유로 간다. 새 값은 `arm ≥ trail/(1−trail)` 을 만족해야 armed 청산이 진입가 아래로 안 내려간다(1%/0.8% → 0.806% ≤ 1%).
- **사후 반사실은 고점 가정임을 밝힌다.** "그날 고가까지 갔다면 X에서 청산"은 실제 체결가가 아니다 — 방향 근거로만 쓰고
  기대값 개선의 증거로 쓰지 않는다. 표본은 며칠 실현손익으로 다시 본다.
- **이 사용자는 본전~소폭 이익에서 시간 규칙으로 끊는 것을 '조기 청산'으로 본다.** 시한부 연장은 네 조건을 모두 요구한다:
  ① 미실현 이익 > 0 ② 진입 시점 지수 스냅샷 < 현재 지수(`algo_json.market_index` 에 기록) ③ 하드 상한(분)
  ④ 손익 0 이하 회귀 시 즉시 청산. '오늘 장이 올랐다'는 조건은 틀렸다(그날 지수가 올라도 우리가 산 뒤 하락했으면 근거 없음).

## 테스트·린트 게이트 (커밋 전 두 관문)

1. **스위트 러너 한 번으로 검증한다**: `python3 scripts/run_test_suites.py` (5스위트, 약 160초, `data/reports/test_runs/latest.json`).
   러너가 `.env` 를 스스로 읽고 호스트 매핑(`POSTGRES_HOST=127.0.0.1`, `POSTGRES_PORT=5434`)을 강제하며, 실패한 스위트만
   기본 2회 재시도해 **플래키**를 구분한다. 루트만 볼 때는 `set -a; . ./.env; set +a;` 후
   `POSTGRES_HOST=127.0.0.1 POSTGRES_PORT=5434 python3 -m pytest -q tests`.
2. **`.env` 없이 돌린 수치를 회귀로 오독하지 마라.** DB 비밀번호가 없으면 DB 의존 테스트가 1~2건 더 실패해 같은 코드가
   다른 건수로 보인다(수동 5건 vs .env 로딩 4건 실측). 수치가 실행 방식에 따라 달라지면 먼저 환경 로딩을 의심하고,
   판정 로직(env 로드·포트 강제)을 **실행기 안**에 넣어 두 경로가 같은 값을 내게 만든다.
3. **커밋 훅 린트가 커밋 자체를 막는다 — 새 코드에 주석을 달지 마라.** `scripts/lint_agent_rules.py` 가
   `docs/lint_baseline.json` 래칫으로 **C001(주석·인라인 주석 금지, 기계 지시자 예외)** 과 **C003(`except: pass`)** 를
   파일별 허용량으로 검사한다. 허용량 0인 신규 파일은 주석 1줄로 커밋이 실패하고, 실패하면 스테이징만 된 채 끝나
   "테스트는 통과했는데 커밋이 없다" 상태가 된다. 근거는 주석 대신 **문자열**(assert 메시지·로그·`note`)에 쓰고,
   예외는 `except OSError: fh = None` 처럼 **흐름으로** 처리한다. 커밋 전 `python3 scripts/lint_agent_rules.py` 로 먼저 확인한다.
4. **실패한 테스트를 고치기 전에 어느 쪽이 틀렸는지 판정한다** — ① 제품 결함 ② 낡은 스펙(테스트가 옛 규약을 고정)
   ③ 변경된 기본값(한도·주기) ④ 날짜 의존. ④는 두 얼굴이다: 픽스처 날짜 하드코딩(‘오늘 청산’ 카운트가 그날만 통과)과
   `today−N` 이 **주말·휴장일**이라 거래일 목록에 없는 경우. ②~④는 테스트를 새 규약으로 **강하게** 다시 쓴다
   (예: "창이 마지막 적재일까지 닿는가"를 창의 가장 오래된 거래일과 비교, 픽스처는 `date.today()` 기준 생성) —
   비교를 약화시키거나 기대값을 지우는 방식은 금지.
5. **정책을 뒤집으면 고정 테스트도 같은 커밋에서 다시 쓴다** — 문턱·호출형태를 못 박은 테스트가 남아 있으면
   다음 실행에서 옛 정책이 되살아난 것처럼 보인다.

## 마무리 규약

- **`git add` 는 경로 하나만 없어도 전체를 중단한다.** 계산해서 만든 파일 목록을 `git add A B C` 로 한 번에 넘기고 stderr 를 `2>/dev/null` 로 가리면,
  `pathspec 'X' did not match` 로 **아무것도 스테이징되지 않은 채** 종료코드 1 로 끝나고 이어지는 `git commit` 이 `no changes added to commit` 으로 실패한다 —
  로그를 가렸으므로 "수정이 없다"로 오독하기 쉽다. add 전에 `[ -e "$f" ]` 로 존재를 확인하거나 파일별로 add 하고,
  커밋 전에 `git diff --cached --stat` 으로 **스테이징된 파일 수**를 확인한다. 산출물 경로는 위임 결과물마다 달라지므로(존재 확인 없이 목록을 쓰는 것이 원인) 이 확인이 기본이다.
- **루트로 러너를 시험 실행하면 그 러너가 쓴 상태파일이 root 소유가 되어 이후 `git add .` 이 통째로 막힌다.**
  증상은 `error: open("data/state/<x>.json"): Permission denied` + `fatal: updating files failed` 이고,
  같은 명령을 다시 돌려도 계속 실패한다(파일이 목록에 남아 있으므로). 처방: `ls -la <그 파일>` 로 소유자를 확인해
  `wsl.exe -u root -- chown <user>:<user> <파일>` 로 되돌린 뒤 다시 add 한다. 크론 래퍼를 root 로 dry-run 했거나
  `wsl -u root --` 로 스크립트를 돌렸다면 **그 실행이 건드린 `data/state/*`·`reports/*` 목록을 먼저 chown** 해 두면
  커밋 단계에서 멈추지 않는다.
- **두 사본을 동기화할 때 방향을 먼저 확인하라 — 라이브에만 있는 수정이 있다.** `python3 scripts/sync_trader_agent.py`
  (미리보기)로 `내용 다름` 목록을 먼저 보고, 그 파일들이 **라이브에서 고친 것**이면 `--pull --apply` 로 저장소에
  먼저 가져와 커밋한 뒤 `--push` 한다. 미리보기 없이 push 하면 라이브의 핫픽스를 저장소의 옛 버전으로 덮어써
  운영 방어가 소리없이 되돌아간다(실측: 수수료 음수 거부 수정이 라이브에만 있었다).
  같은 이유로 **git 이 추적하지 않는 새 파일은 sync 가 복사하지 않는다** — 추적 목록(`git ls-files`) 기준이므로
  새 테스트·스크립트 파일은 직접 `cp` 해야 한다.
- 완료된 작업은 **커밋·푸시**까지가 한 세트다: 트레이더 코드가 analyist_dd 안에 있어도 `trader-agent/` 가
  **자체 `.git` 을 가진 중첩 리포**인 경우가 있다 — 그 안에서 커밋하면 부모 리포가 그 이력을 받지 못한다.
  커밋 전에 `cd trader-agent && git rev-parse --show-toplevel && git remote -v && git branch --show-current` 를 찍어라.
  실측: 중첩 리포의 `origin` 이 **부모 리포 URL**(`…/analyist_dd.git`)을 가리키고 브랜치도 `main` 이 아니라 `master`
  여서 `git push origin main` 이 `src refspec main does not match any` 로 실패했다. 그 상태에서 억지로 push 하면
  트레이더 이력이 부모 리포로 섮인다 → **로컬 커밋만 남기고 "커밋됨·푸시 안 됨(origin 오지정·브랜치 불일치)"으로
  **원격·브랜치는 단정하지 말고 실측값을 따른다** — 같은 중첩 리포라도 어떤 시점엔 `trader-agent.git`/`main` 으로
  정상 푸시되고, 어떤 시점엔 부모 URL·`master` 로 어긋난다. 푸시 후 `git ls-remote origin refs/heads/<branch>` 로
  원격 SHA 와 로컬 HEAD 를 대조한다. 사본 동기화는 커밋이 아니라 `scripts/sync_trader_agent.py` 의 영역이다.)
- 설정을 바꿨는데 반영이 안 되면 먼저 `Config.as_dict()` 화이트리스트와 프리셋/사본 계층을 본다(`references/portability-and-sync.md`).
- 발견한 결함은 그 자리에서 고치거나(비보호 경로) `--protected` 과제로 등록하고, 보고에는 **고친 것 / 등록한 것 / 사람 단계**를 나눠 쓴다.
- 보고는 한국어 평문 번호 목록(헤더·표 없음): 무엇을 바꿨는지 · 어떤 명령으로 검증했는지(수치) · 남은 것.

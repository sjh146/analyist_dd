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

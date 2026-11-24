# 진입 0건 진단 (후보는 있는데 체결이 없다)

"오늘 왜 한 건도 안 샀나" 의 답은 대부분 게이트 사다리 어딘가에서 나온다. 문턱을 풀기 전에
**어느 단계가 몇 건을 죽였는지** 세고 숫자로 말한다. `trader-agent/runner.log` 의 최근 사이클을
등장 순서대로 읽는다.

## 사다리 (순서 고정)

1. `PRE filter <경로>: a -> b (stale/blocked N, window N, price N)`
   신선도(`signal_date` 4일 초과)·가격·진입창. 스크리너가 `signal_date` 를 갱신하지 않으면 후보 전량이
   주단계에서 사망한다 — 피드 후보 수만 보고 '기회 있음'으로 읽으면 틀린다. 20 → 0 이면 스크리너 버그,
   20 → 5 면 정상 필터링.
2. `G skip ... edge_score_low (n < min_edge_score … bear_guard)`
   문턱은 **`min_edge_score` 기본값 + 가드 가산**. 로그 표기가 `4 bear_guard` 처럼 가산이 붙은 값이다.
3. `spread_too_wide (x% > y%)` — `max_entry_spread_pct`. 후보 스프레드가 0.4~0.6% 대에 몰리는 날,
   0.30% 문턱은 다수를 죽인다(호가가 넓은 저유동성 단타에서 흔하다).
4. `news gate unavailable (HTTP Error NNN) — new entries blocked (fail-closed)`
   **서비스 장애가 매매를 멈춘다.** 뉴스게이트는 별도 프로세스(8082)다.
5. `슬롯 포화 또는 산정 불가(경로 상한 N)` — 경로별 상한(단타 2). 걸리면 그날 그 경로는 끝이다.

"후보 > 0 & 진입 = 0" 이 30분 지속되면 사용자는 조용한 공전으로 기회를 잃는다 — 장 시작 30~60분 안에
사다리를 한 번 세고 보고에 남긴다.

## 뉴스게이트가 죽었는지 확인·재기동

```bash
curl -s --noproxy '*' -w '\nhttp=%{http_code}\n' 'http://127.0.0.1:8082/newsgate?codes=005930'
# 정상: 200 + {"generated_at":...,"gate":{"005930":{"verdict":...}}}
# 500: 본문에 DB 오류(FATAL: password authentication failed for user ...) 가 그대로 온다
```

DB 비밀번호를 회전하거나 postgres 를 재생성한 뒤 이 서비스를 재기동하지 않으면 **매일 500 → fail-closed 로
신규 진입이 전면 차단**된다(로그에는 게이트 사유 한 줄만 남아 원인이 안 보인다).

재기동 시 `.env` 를 통째로 `source` 하지 않는다 — `.env` 의 HOST/PORT 류가 바인딩을 덮어써
`OSError: [Errno 99] Cannot assign requested address` 가 난다. 필요한 값만 넘긴다(스크립트 기본 HOST 는
과거 Tailscale IP 라 반드시 명시):

```bash
NEWS_GATE_HOST=127.0.0.1 NEWS_GATE_PORT=8082 \
POSTGRES_HOST=127.0.0.1 POSTGRES_PORT=5434 POSTGRES_USER=stock_user POSTGRES_DB=stock_trading \
POSTGRES_PASSWORD="$(grep -m1 '^POSTGRES_PASSWORD=' .env | cut -d= -f2-)" \
python3 scripts/news_gate_server.py
```

장기 실행은 `terminal(background=true)` 로 띄운다(`nohup`/`setsid` 래퍼는 거부된다). 기동 확인은 위 curl 의
200 + `gate` 본문이고, `ss -ltn | grep 8082` 로 127.0.0.1 바인딩을 같이 본다.

## 문턱 손잡이 지도 (trader_core/config.py)

| 필드 | 적용 경로 | 비고 |
|---|---|---|
| `risk_off_size_mult` / `risk_off_edge_bonus` | `market_guard.evaluate_risk_off_baseline` = 국면이 risk_off 일 때 상시 | 기본 보수 모드. `edge_bonus` 를 0 으로 두면 문턱은 `min_edge_score` 그대로 |
| `bear_size_mult` / `bear_edge_bonus` | 당일 수익 트리거 레벨1·2 전용 | 손대도 국면 기본값은 안 바뀐다 |
| `min_edge_score` | 엣지 게이트 기본 문턱 | 0/1 = 사실상 해제 |
| `max_entry_spread_pct` | 진입 스프레드 게이트 | 0 = 비활성 |

- 엉뚱한 필드를 고치면 로그의 `guard.edge_bonus` 가 그대로라 '설정이 안 먹었다'로 오해한다. 변경 후
  `trader-agent/loop_state.json` 의 `check.bear_guard` 에서 `size_mult`·`edge_bonus` 를 읽어 확인한다.
- 전부 **config 기본값**이라 반영하려면 루프 재기동이 필요하다(`loop_stop.bat` → `loop_start_bg.bat`,
  pid 와 `runner.log` 첫 사이클로 확인).
- 기본값을 바꾸면 기본값을 단언하는 테스트가 함께 깨진다 — `min_edge_score`·`max_entry_spread_pct`·
  `risk_off_edge_bonus` 각각에 단언이 있고, 게이트 동작 테스트는 그 기본값에 기대는 입력
  (`edge_score=5`, `spread_pct=0.40`)을 쓴다. 새 기본값으로 단언을 갱신하고, 동작 테스트는 임계 밖 값
  (`edge_score=2`, `spread_pct=0.80`)으로 재고정하며, '설정을 주면 그 값이 쓰인다' 테스트는 지우지 말고 남긴다.
- **일시적 완화는 코드 기본값을 고치지 말고 날짜 한정 파일로 한다** — `risk_overrides.json`
  (`{"valid_until":"YYYY-MM-DD","fields":{...}}`)을 `engine_config()` 이 읽어 유효일까지만 덮어쓴다.
  기본값을 직접 낮추면 그 값이 다음 주까지 살아남아 모든 날이 공격적으로 돌아간다(실측: 반등일 완화값이
  그대로 남아 있던 것을 발견). `valid_until` 이 지나면 자동 복귀하므로 원복 커밋을 잊을 위험이 없다.
  회귀 테스트는 3건 — 오늘 적용 / 만료 무시 / 미지 필드 무시.

## 서비스가 죽으면 진입이 멈춘다 — 감시·자동복구 배선(재사용)

수동 재기동은 다음 날 또 납니다. 이미 배선된 것을 먼저 확인하라:

| 대상 | 경로 | 주기 |
|---|---|---|
| 서비스 감시·자동복구 | `scripts/service_supervisor.py` (뉴스게이트·피드서버·brid-도커·디스크) | 5분, 08~16시 |
| 진입/데이터 점검 | `scripts/trading_health_check.py` → `reports/trading_health_latest.json` | 09:12/12:12/14:12 |
| 분봉 누락 복구 | `cron/minute_guard.sh` (KIS 당일분만 제공 → 네이버 백필) | 23:45 / 07:35 |
| DB 백업 | `cron/backup_db.sh` (pg_dump gzip, 7일) | 02:30 |

- 감시기는 **HTTP 200 으로 판정**한다. 프로세스 존재만 보면 500 을 내는 반쪽 서비스를 정상으로 읽는다
  (실측: newsgate 가 500 인 채 살아 있어 fail-closed 로 매매를 막고 있었다).
- 자동복구는 **우리가 띄운 서버만** 한다. 브리지(trader-agent/Creon)는 재기동하지 말고 경보만 — 사람 로그인이 필요하다.
- 데이터 신선도 기대치는 장중(19시 이전)에 **직전 거래일**이다. 당일을 기대하면 09~18시 사이에 매번 거짓 DATA_STALE 이 뜬다
  (`expected_data_day()` 가 이 보정을 한다).
- `loop_start_bg.bat` 을 `cmd.exe /c` 로 부르면 **포그라운드가 끝나지 않는다**(timed out). 루프 재기동은
  `terminal(background=true)` 로 돌리고, 검증은 프로세스 조회(run_market_loop)
  + `runner.log` 첫 사이클의 새 문턱/스프레드 값으로 한다.

## 보고 문장 골격

"후보 20 → PRE filter 5 → 엣지 0건(문턱 6 > 후보 점수 1~4) — 진입 0. 뉴스게이트 500(fail-closed)로
전량 차단되는 경로도 함께 확인함." 처럼 **단계별 잔존 수**와 **차단 사유 원문**을 같이 쓴다.

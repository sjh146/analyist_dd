# Windows에서 trader-agent 처음 띄우기 (다른 사람용)

> 이 문서는 **저장소를 처음 클론한 사람**이 자기 Creon 계좌로 매매 루프를 돌리기까지의 절차다.
> 원 개발 머신의 경로·계좌·한도를 전제하지 않는다. 리눅스/Docker 쪽(analyist_dd)은
> 그 저장소의 `docs/SETUP_NEW_MACHINE.md` 를 먼저 본다.

---

## 0. 안전 원칙 (건너뛰지 말 것)

1. **모의투자 계좌로 2~4주 먼저.** 실계좌는 그 다음, 소액으로.
2. 기본값은 원 계좌 기준으로 **공격적**이다 — `max_invested_pct = 1.0`(예수금 100%),
   종목당 1/3. 그대로 쓰면 한 종목에 자본 1/3이 들어간다.
   → `--preset paper_safe` (총노출 30% · 종목당 10% · Kelly 1/4) 로 시작한다.
3. `dry_run` 은 기본 `True`. 실주문은 `--live` 를 **명시해야만** 나간다(대화형 확인 포함).
4. 자격증명은 각자 발급한다. 남의 KIS/DART/KRX 키를 받아 쓰지 않는다.

---

## 1. Python 두 개 (비트를 섞으면 COM이 죽는다)

| 프로세스 | Python | 비고 |
|---|---|---|
| `run_bridge.py` (Creon COM) | **32bit** 3.11 | `pywin32` 필요. 64bit에서는 COM이 안 붙는다 |
| `run_trader_core.py` / `run_market_loop.py` | **64bit** 3.12 | `pywin32` 설치하지 않는다(의도적) |

```
# 예 (python.org 설치 파일 2개)
C:\Tools\Python311-32\python.exe   ← 32bit
C:\Tools\Python312-64\python.exe   ← 64bit
C:\Tools\Python311-32\python.exe -m pip install pywin32
```

확인:
```
C:\Tools\Python311-32\python.exe -c "import struct,win32com;print(struct.calcsize('P')*8,'bit')"
C:\Tools\Python312-64\python.exe -c "import struct;print(struct.calcsize('P')*8,'bit')"
```

## 2. 저장소

```
git clone https://github.com/sjh146/trader-agent.git %USERPROFILE%\trader-agent
cd %USERPROFILE%\trader-agent
```
* 문서·스크립트의 경로는 `%USERPROFILE%` 기준으로 동작한다(과거 `C:\Users\jhshi\...` 하드코딩은
  `tools/migrate_paths.py` 로 정리했다. 남아 있으면 그 스크립트의 `--check` 로 찾는다).

## 3. Creon PLUS 설치 + 로그인 (사람만 할 수 있는 단계)

1. 대신증권 Creon PLUS 설치 (`C:\CREON\STARTER\coStarter.exe`)
2. 공동인증서 준비 → 실행 → **계좌 선택(모의투자 권장)** → 로그인
3. 로그인 후 창을 닫지 않는다(COM 세션의 주인이다)

## 4. 브리지 (32bit) — 먼저 단독 검증

```
C:\Tools\Python311-32\python.exe run_bridge.py --selftest     :: Creon 없이 도는 순수 테스트
C:\Tools\Python311-32\python.exe run_bridge.py --no-server    :: 계좌 연결만 확인
C:\Tools\Python311-32\python.exe run_bridge.py --port 8100    :: 서버로 기동
```
확인(반드시 `--noproxy`, 프록시 환경에서 502/000 오탐 방지):
```
curl.exe -s --noproxy "*" http://127.0.0.1:8100/health
curl.exe -s --noproxy "*" http://127.0.0.1:8100/balance
```
`{"connected": true}` + `/balance` 가 숫자를 줘야 정상. **Creon 재로그인 뒤에는 브리지를 재시작**한다.

## 5. 피드(후보) 연결 — 두 가지 중 하나

- **A. analyist_dd 피드 사용**: 같은 PC면 `127.0.0.1:8090`, 다른 PC면
  `--feed http://<feed-host>:8090/screener_latest.json` (Tailscale/사내망 등)
- **B. 피드 없이**: `--preset rank_only` — Creon 자체 랭킹(`trader_core/rankfeed.py`)으로 후보를 만든다.
  뉴스 게이트/외부 피드가 필요 없어 **혼자서도 굴러간다**.

## 6. 설정 — 프리셋 → 파일 → 환경변수 → CLI (뒤가 우선)

```
python run_market_loop.py status --preset paper_safe --mock
```

| 방법 | 예 | 쓰임 |
|---|---|---|
| 프리셋 | `--preset paper_safe` / `--preset rank_only` | 처음 시작할 때 |
| 파일 | `--config config\my.json` (`config.example.json` 복사) | 계좌별 고정값 |
| 환경변수 | `TRADER_CORE_MAX_INVESTED_PCT=0.3`, `RUNNER_INTERVAL_SECONDS=300` | 배포·비밀값 |
| CLI | `--feed`, `--bridge`, `--live`, `--interval` | 일회성 |

`TRADER_CORE_*` 는 trader-core 필드(위험·피드·경로), `RUNNER_*` 는 루프 필드(주기·상태파일·게이트)다.
같은 이름이면 `RUNNER_*` 가 이긴다. 전체 키 목록은 `.env.example` 참고.

주요 한도(프리셋 값):
```
max_invested_pct 0.30   총 노출 상한 (예수금 대비)
max_position_pct 0.10   종목당 상한
max_open_positions 3    동시 보유 종목 수 (경로별 swing 2 / daytrading 1)
daily_max_trades 3      하루 진입 건수 상한
stop_loss_pct 0.07 / take_profit_pct 0.07 / trailing 5.5%→5%
```

## 7. 첫 사이클

```
:: 상태(브리지·피드·저널)
C:\Tools\Python312-64\python.exe run_market_loop.py status
:: 1사이클만, 주문 없이
C:\Tools\Python312-64\python.exe run_market_loop.py run --once --preset paper_safe
:: 루프 (모의계좌도 실주문 경로이므로 --live 필요)
C:\Tools\Python312-64\python.exe run_market_loop.py run --loop --live --preset paper_safe --yes
```
`--live` 없이 `--loop` 를 돌리면 계획만 세우고 주문하지 않는다(로그에 dry_run).

## 8. 자동화 (사람이 로그인한 뒤 스스로 돌게)

- 브리지 감독기: `powershell -File tools\supervise_bridge.ps1` (Creon 있으면 기동, 없으면 대기)
- 루프 감독기: `powershell -File tools\supervise_loop.ps1` (08:30~15:35 창에서 루프 기동)
- 둘 다 **상주 폴러**다. 예약작업으로 등록하면 로그온 직후 자동 시작.
  `Get-ScheduledTaskInfo` 의 `NextRunTime`/`LastTaskResult=2147946720`("이미 실행 중")은 실패가 아니다.

## 9. 검증 체크리스트 (첫 주문 전에 전부)

1. `run_bridge.py --selftest` 통과
2. `/health` `connected:true` **그리고** `/balance`·`/positions` 응답
3. `status` 에서 피드 신선도 OK (또는 rank_only)
4. `run --once` 로그에 `decisions` > 0, `acted 0`(dry_run)
5. `--live` 1건 라운드트립: 주문번호 → 체결 확인(`fill_confirm`) → 저널 행
6. 청산 규칙 확인: close = **익일 09:00~09:10 시가 매도**, swing = 손절/익절, daytrading = 당일 15:10~15:25
7. `tools/reconcile_journal.py` 예행(`--apply` 없이) → 저널·브로커 코드 집합 일치

## 10. 자주 걸리는 것

| 증상 | 원인 |
|---|---|
| `NOT CONNECTED`, `/health` 실패 | Creon 미로그인 또는 재로그인 후 브리지 미재시작 |
| `disk I/O error` (저널) | WSL에서 Windows가 잡은 WAL을 열었다 → **Windows 파이썬**으로 읽는다(`C:\...` 경로) |
| 502/000 (로컬 API) | 프록시 환경변수를 탐 → `curl.exe --noproxy "*"` |
| 브리지가 죽었다 살아난다 | Creon 업데이터(spdn/coStarter)가 NCFSYS 파일을 갈아끼우는 중 — 감독기가 의도적으로 내린 것 |
| 루프가 `halted` | check 실패 3회(브리지/피드) — `run_market_loop.py resume` 또는 자동 복귀 대기 |

## 11. 하지 말 것

- 32bit/64bit 혼용, 64bit에 pywin32 설치
- `--live` 로 바로 시작, 프리셋 없이 기본 한도(100% 노출)로 시작
- Creon 로그인 세션을 유지하지 않고 루프만 재기동
- 남의 API 키·계좌번호를 저장소에 커밋 (`.env`, `journal/*.sqlite3` 는 무시 대상)

---

## 12. 사본이 두 개다 — 동기화 확인

2026-11-13 부터 `trader-agent` 는 **`analyist_dd` 저장소 안의 디렉터리**다. 그런데 매매 루프·브리지는
이 PC의 실행 사본(`C:\Users\<user>\analyist_dd\trader-agent`)에서 돈다. 즉 저장소 사본과 실행 사본이
따로 있고, 한쪽만 고치면 조용히 갈라진다. 한 명령으로 차이를 본다:

```
python3 scripts/sync_trader_agent.py                 # 미리보기(양방향 차이)
python3 scripts/sync_trader_agent.py --push --apply  # 저장소 → 실행 사본
python3 scripts/sync_trader_agent.py --pull --apply  # 실행 사본 → 저장소(라이브에서 급히 고쳤을 때)
```

- 비교 대상은 **양쪽 git 이 추적하는 파일**뿐이라 journal·logs·data·`.env` 같은 런타임 산출물은 안전하다
- 복사는 추가·변경만 하고 **삭제는 하지 않는다**
- 파일 복사는 실행 중이어도 안전하지만, 반영은 **다음 기동**부터다(재기동 시점은 사람이 정한다)

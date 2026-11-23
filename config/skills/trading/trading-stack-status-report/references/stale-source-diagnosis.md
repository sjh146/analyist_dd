# 낡은 데이터 소스 진단·복구 (analyist_dd 수집 파이프라인)

용도: 신선도 표(SKILL.md 4b)에서 `max(날짜)` 가 낡은 소스가 나왔을 때, 정상인지 아닌지부터
원인 확정·결손 채우기·재발 방지(승인 등록)까지를 순서대로 처리한다. "수집이 멈췄다" 는 보고는
이 순서를 다 밟은 뒤에만 쓴다.

## 1) '정상일 수 있음' 을 먼저 배제한다

- 휴장·주말 구간: `data/krx_holidays.json` 에 그 날짜가 있는지(그리고 그 목록이 **올해를 덮는지** 연도별 항목 수로 확인).
- KRX OpenAPI 일별매매정보 계열은 **D-2 반영** — 오늘 날짜가 비어 있는 것이 정상일 수 있다.
- 크론 시각표(`/etc/cron.d/analyist_dd`, `crontab -l`)와 대조해 "아직 그 시각이 안 됐다" 를 배제한다.

## 2) '적재 대상 테이블' 을 확인해 레거시를 배제한다

```bash
grep -n 'INSERT INTO' scripts/<수집기>.py      # 이 수집기가 실제로 쓰는 테이블
```

낡은 `max(날짜)` 를 보여주는 테이블이 현행 적재 대상이 아니면(예: `krx_trading` ← 현행
`scripts/krx_daily.py` 는 `market_data` 에 적재) 장애가 아니라 레거시이므로 '공백 후보(미검증)'로만 적는다.

## 3) 실행 주체가 존재하는가 — 이 순서로 좁힌다

a. **스케줄 항목 존재 여부**: 그 러너(스크립트 파일명)를 `/etc/cron.d/*` · `crontab -l` · Hermes 크론 목록 ·
   Windows 예약작업에서 grep. **0건이면 '정기 실행 없음' 으로 확정** — 이게 가장 흔한 원인이고, DQ 지표에는 안 나타난다.
b. **스크립트가 수동 전용 백필인가**: docstring 의 '사용' 절에 사람이 인자를 주는 명령만 있으면
   정기 러너는 존재하지 않는다(예: `scripts/dart_disclosure_backfill.py` 는 `--since/--types/--max-calls` 를 사람이 준다).
c. **서비스 내 수집기가 배선됐는가**: `grep -rn "<ClassName>" --include=*.py .` → 자기 파일만 나오면
   import 하는 코드가 0곳 = 미배선 죽은 코드(서비스 안에 수집기 클래스가 있어도 돌지 않는다).
d. **러너가 돌았는데 실패했는가**: 컨테이너/래퍼 로그에서 반복 예외를 grep.
   `docker logs --since 7d <container> | grep -nE "No module named|Traceback|429|401|rate"`.
   **네트워크로 단정하기 전에 이걸 먼저 본다** — 실측: 표가 0행인 이유가 회선이 아니라 이미지에 없는 모듈이었다.
   **자격증명 부재가 파서 버그처럼 보이는 서명이 있다**: pykrx 기반 수집기(지수·투자자·프로그램·공매도)가
   `KeyError: '지수명'` 으로 죽고 로그 어딘가에 `KRX 로그인 실패: KRX_ID 또는 KRX_PW 환경 변수가 설정되지 않았습니다`
   가 찍히면, 원인은 코드가 아니라 **KRX 웹 계정 미설정**이다(응답이 로그인 실패 페이지라 pykrx 내부가 컬럼을 못 찾는다).
   처방은 파서 수정이 아니라 ① `KRX_ID`/`KRX_PW` 주입(선택) ② 미설정이면 **한 줄 경고 후 수집을 건너뛰는 가드**를 넣고
   호스트 대체 경로(OpenAPI·KIS 러너)에 맡기는 것이다. `KRX_API_KEY`(OpenAPI 키)와 `KRX_ID`/`KRX_PW`(웹 계정)는
   다른 자격증명이므로 `.env.example` 에 둘을 구분해 적어 둔다.
e. **도달성 실측**(사용자가 원인을 단정해 와도 여기서 검증한다):
   `curl -s -o /dev/null -w 'direct=%{http_code}\n' <url>` + `--noproxy '*'` 도 같이 + `.env` 에 키가 있는지
   (`grep -c <KEY> .env`). 200 이면 원인은 a~d 중 하나이므로 **사용자 전제를 그 자리에서 정정 보고**한다.
   같은 URL 이 UA 유무로 429↔200 으로 갈리는 제공자가 있다(Yahoo 계열) — 상태코드만 보고 "차단"이라 쓰지 말고 UA 를 맞춰 재시도한다.
f. **소비자가 있는가**: `grep -rn "<table>" --include=*.py --include=*.sql .` → **읽는** 코드가 0곳이면 죽은 출력이다.
   모델 입력에 안 들어가므로 우선순위를 낮추고, 보고에도 '모델 영향 0' 이라고 적는다(실측: 소비자 0곳인 표는 고쳐도 AUC 가 안 움직인다).

## 4) 결손 채우기 — 수동 백필이 있으면 그 자리에서 돌린다

- 명령은 docstring 의 '사용' 절 그대로. 이 스택의 관례:

```bash
cd /home/jhshi/analyist_dd && set -a; . ./.env; set +a
POSTGRES_HOST=127.0.0.1 POSTGRES_PORT="${POSTGRES_HOST_PORT:-5434}" PROJ_DIR=/home/jhshi/analyist_dd \
  /usr/bin/python3 scripts/<백필>.py --since <YYYY-MM-DD> [--types ...] --max-calls 200
```

  (`/usr/bin/python3` 를 쓴다 — 기본 `python3` 는 psycopg2 없는 venv).
- **유형/창 기본값이 표의 기존 구성보다 좁을 수 있다**: DART 백필의 `DEFAULT_TYPES="A"` 인데 표에는 B/D/E/I 도
  들어 있었다 → 결손 폭을 기존 날짜의 행수와 비교해 확인하고, 같은 유형 집합으로 백필한다.
- 콜 예산은 창 수 × 유형 × 페이지다(월 창 2개 × 유형 4개면 ~20콜). `--max-calls` 를 넉넉히 주고 로그의
  `콜 N / 소스 M행 / 실제 삽입 K / 테이블 델타` 를 읽는다.
- 600초를 넘길 것 같으면 포그라운드로 잡지 말고 `background=true` 로 띄우고 `process_manage(action='poll')` 로 확인한다.
- **날짜 인자 형식이 스크립트마다 다르다** — `--start/--end` 는 `YYYYMMDD`, `--from/--to` 는 `YYYY-MM-DD` 인 식이라
  형식을 틀리면 `ValueError: time data '2026-11-12' does not match format '%Y%m%d'` 로 **수집 전에** 죽는다.
  실행 전에 `--help` 의 metavar 를 읽고, 1일 창으로 먼저 dry-run 해 콜 수를 확인한다.
- **진행파일이 있는 백필은 재실행이 이어받기다**(`data/kis/<name>_progress_<from>_<to>.txt`) — 같은 창으로 다시 돌리면
  이미 받은 종목을 건너뛰고 남은 것만 처리한다. 타임아웃으로 끊겼으면 **같은 명령을 다시** 돌리는 것이 맞고,
  창을 바꾸면 진행파일 키가 달라져 처음부터 다시 부른다(콜 예산 낭비).
- **창을 좁혀 돌리기 전에 그 스크립트의 DELETE 범위를 읽어라**(`grep -n "DELETE FROM" <script>`). 전체 DELETE 후
  COPY 하는 빌더에 좁은 `--since` 를 주면 창 밖 이력이 사라진다 — SKILL.md 함정 항목 참조.
- **검증은 자기신고가 아니라 DB 로**: `max(날짜컬럼)` 이동 + 삽입 행수 델타 + 결손 일자별 행수를 다시 찍는다.

## 5) 재발 방지 = 스케줄 신설은 사람 승인 항목이다

- 크론 파일(/etc/cron.d)·Windows 예약작업 수정은 사람 단계다 → 백로그에 `needs_approval` 로 등록한다.
  필드: `title / status=needs_approval / priority / kind=collect / incremental / affects_model / hypothesis /
  evidence(실측 수치 + 조치 내역) / method / needs(정확한 크론 한 줄) / if_blocked`.
- 백로그 JSON 은 라운드트립으로 편집하면 diff 가 추가분만 남는다(원본이 같은 포맷일 때):
  `json.load → items.append → json.dumps(indent=2, ensure_ascii=False)` → `json.load` 로 유효성 확인 → `git diff --stat`.
  편집 전에 `diff <(원본) <(라운드트립)` 이 몇 줄인지 먼저 확인하면 대량 재포맷을 피한다.
- 커밋은 **대상 파일만** 스테이징한다(`git add <백로그 파일>`): 동시에 역할 틱이 돌아 `data/reports/*`·다른 백로그가
  수정돼 있을 수 있다. push 전에 `git ls-remote origin <branch>` 로 원격 SHA 를 직접 비교하고, 역할이 먼저 올렸으면
  rebase 후 push 한다(자기 커밋 1개라 충돌 위험이 낮다).
- 보고에는 "원인(스케줄 부재) / 조치(백필 N행, max 날짜 이동) / 남은 것(승인 1건)" 3단으로 적는다.

## 6) 같은 유형의 형제 결함을 같이 본다

한 소스에서 '정기 러너 부재' 가 나오면 **같은 클래스의 다른 소스도 확인한다** — 수집 러너를 부르는 항목이
크론·예약 어디에도 없는 표가 여러 개인 경우가 많다(SNS 정기 수집, DART 공시, 피처 빌더 등).
역할 원장의 자기신고 누락 감사(예: `scripts/r23_claim_audit.py`)가 이 유형을 한 번에 세어 주므로,
그 출력의 '누락 러너 N개' 목록을 결손 후보 목록으로 재사용한다.

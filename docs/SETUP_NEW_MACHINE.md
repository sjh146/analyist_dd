# 새 Windows + WSL 머신에서 analyist_dd 띄우기

> 다른 PC 로 옮길 때 이 문서만 따라 하면 됩니다. 3단계(.env)와 5단계(데이터 시딩)만 주의하면 됩니다.
> 이 저장소는 `docker-compose.yml` 이 **상대경로(`./`)** 를 쓰고 `init-scripts/postgres` 가
> 컨테이너 초기화 시 자동 실행되므로, 클론 → .env → 기동만으로 스키마까지 만들어집니다.

---

## 0. 준비물

| 항목 | 필요성 | 비고 |
|---|---|---|
| Windows 10/11 + WSL2 | 필수 | Ubuntu 22.04 / 24.04 |
| Docker (WSL 내부 Engine 권장) | 필수 | 컨테이너 15~17개 |
| git, python3-psycopg2, curl | 필수 | 부트스트랩이 점검함 |
| KIS APP KEY/SECRET | 시세·수급 수집 | https://apiportal.koreainvestment.com |
| DART API KEY | 공시·재무 | https://opendart.fss.or.kr |
| ECOS / KRX / DeepSeek KEY | 선택 | 매크로 / 공매도·파생 / 뉴스 감성 |
| GPU | 불필요 | CPU 동작. GPU 머신이면 `docker-compose.gpu.yml` |

## 1. WSL + Docker

```powershell
# Windows PowerShell(관리자)
wsl --install -d Ubuntu-24.04
```

```bash
# WSL 안에서
sudo apt-get update
sudo apt-get install -y docker.io docker-compose-v2 git python3-psycopg2 curl
sudo service docker start
sudo usermod -aG docker "$USER"     # 적용하려면 WSL 재시작
```

> Docker Desktop 을 쓴다면 3줄 대신 Desktop 실행 + WSL integration 활성화로 충분합니다.

## 2. 클론

```bash
git clone https://github.com/sjh146/analyist_dd.git ~/analyist_dd
cd ~/analyist_dd
```

**⚠ 경로 주의 — 이것이 유일한 함정입니다.**
`scripts/` 18개, `config/` 4개 파일이 `/home/jhshi/analyist_dd` 를 **문자열로** 갖고 있습니다(운영 머신 기준).

- WSL 사용자명이 `jhshi` 이면 `~/analyist_dd` 가 그 경로와 **정확히 일치** → 바로 동작
- 다르면: 부트스트랩 2단계가 심볼릭 링크(`/home/jhshi/analyist_dd → 실제 경로`)를 만들려고 시도하고,
  권한이 없으면 `sudo ln -sfn <실제경로> /home/jhshi/analyist_dd` 를 안내합니다

## 3. .env 만들기 (필수 단계)

```bash
cp .env.example .env
nano .env
```

| 구분 | 키 | 설명 |
|---|---|---|
| **필수** | `POSTGRES_PASSWORD`, `REDIS_PASSWORD` | DB/캐시 비밀번호 |
| **필수** | `INTERNAL_API_KEY`, `API_GATEWAY_KEY` | 컨테이너 간 인증. 비면 무인증 모드 |
| **필수** | `KIS_APP_KEY`, `KIS_APP_SECRET` | 시세·수급 수집 |
| **필수** | `DART_API_KEY` | 재무·공시(접수일) |
| 선택 | `ECOS_API_KEY`, `KRX_API_KEY`, `DEEPSEEK_API_KEY` | 매크로 / 공매도·파생 / 뉴스 감성 |
| 선택 | `KIS_ACCOUNT_NO` | **KIS 주문 API 를 쓸 때만.** 이 스택의 주문은 Creon 브리지로 나가므로 비어 있어도 됨 |
| 선택 | `BRIDGE_VM_IP`, `BRIDGE_VM_PORT` | 트레이딩 브리지 주소 |

랜덤 값 생성: `openssl rand -hex 24`
`.env` 는 `.gitignore` 로 차단돼 있습니다 — **절대 커밋하지 마세요.**

## 4. 기동 + 검증 (한 번에)

```bash
bash scripts/bootstrap_new_machine.sh --check   # 점검만(변경 없음) — 먼저 이것부터
bash scripts/bootstrap_new_machine.sh           # .env 검증 → up -d → 실제 동작 검증
```

부트스트랩이 확인하는 것: WSL/docker/compose/psycopg2, 경로 호환, .env 필수키,
컨테이너 수, **DB 테이블 수(초기화 여부)**, `market_data` 행수, Prometheus up 타겟, DQ 메트릭 노출.

## 5. 데이터 시딩 — 새 DB 는 **비어 있습니다**

`init-scripts` 는 **스키마만** 만듭니다. 데이터는 아래 둘 중 하나로 채웁니다.

### (a) 기존 머신 덤프 이식 — 권장 (수 시간 절약)

```bash
# 기존 머신에서
bash scripts/db_dump.sh                       # dumps/analyist_dd_<시각>.sql.gz (gzip 무결성·테이블수 검증 포함)
# 전송: scp / Tailscale / 공유폴더 등 아무 방법이나
# 새 머신에서
bash scripts/db_restore.sh dumps/analyist_dd_<시각>.sql.gz
```

`db_restore.sh` 는 대상에 데이터가 있으면 **거부**합니다(덮어쓰기 방지). 정말 덮어쓰려면 `--force`.
스키마만 필요하면 `--schema-only`.

### (b) 처음부터 수집

키를 채운 뒤 수집 러너를 순차 실행합니다. KIS 호출 제한(3초) 때문에 **수 시간~수일** 걸립니다.
```bash
set -a && . ./.env && set +a
export POSTGRES_HOST=127.0.0.1 POSTGRES_PORT=5434 PROJ_DIR=/home/jhshi/analyist_dd
/usr/bin/python3 scripts/kis_supply_extend_history.py --target-days 250 --delay 2.0 --max-calls 400
/usr/bin/python3 scripts/dart_disclosure_backfill.py --since 2025-01-01 --max-calls 300
```

## 6. 운영 크론 (데이터 수집 자동화)

```bash
bash scripts/bootstrap_new_machine.sh --with-cron
cat /etc/cron.d/analyist_dd          # 시각 확인/조정
ls ~/cron/                            # 래퍼 원본
```

## 7. 트레이딩 브리지 (선택 — 실제 주문을 낼 때만)

`trader-agent` 는 **별도 저장소**입니다: https://github.com/sjh146/trader-agent
Windows 쪽에 Creon PLUS 설치 + **32비트 Python** 으로 브리지를 띄워야 합니다.
절차는 그 저장소 문서와 Hermes 스킬 `creon-cybosplus-trader-agent` / `live-order-path-safety` 참조.

## 8. 최종 체크리스트

```bash
docker ps --format '{{.Names}}' | wc -l                 # 15~17
curl -s localhost:9090/-/ready && echo OK               # Prometheus
curl -s localhost:3000/api/health                       # Grafana
docker exec stock_postgres psql -U stock_user -d stock_trading -c '\dt' | head
/usr/bin/python3 scripts/dq_snapshot.py --hours 24      # DQ 스냅샷 + 차트 PNG
```
- Grafana: http://localhost:3000 (admin / `GF_ADMIN_PASSWORD`)
- Prometheus: http://localhost:9090

## 9. 알려진 제약 (정직하게)

1. **Hermes 크론은 저장소에 없습니다.** 퀀트 자율 루프(리서처/모델엔지니어)는 Hermes 에 등록된
   크론이라 다른 PC 에는 자동으로 따라오지 않습니다. 필요하면 Hermes 설치 후 `docs/QUANT_BOARD.md`
   의 스케줄·프롬프트 요약을 보고 재등록하세요.
2. **절대경로 22개 파일** — 2단계의 심볼릭 링크가 해결책입니다. 장기적으로는 `PROJ_DIR` 기반으로
   바꾸는 것이 옳지만, 현재는 운영 머신 경로가 기준입니다.
3. **Grafana 이미지 렌더러 미설치** → Grafana 자체 PNG 저장은 안 됩니다. `scripts/dq_snapshot.py` 가
   Prometheus 데이터를 matplotlib 로 그려 차트를 만듭니다(인증·플러그인 불필요).
4. **KIS/KRX 호출 제한** — KIS 3초 간격, 토큰 발급 분당 1회. 병렬 수집을 시도하면 차단됩니다.
5. **모의투자 계좌 권장.** 실계좌로 돌리기 전에 `docs/QUANT_BOARD.md` 와 주문 경로 스킬을 읽으세요.

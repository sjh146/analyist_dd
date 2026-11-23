# 피드/업데이트 릴레이 API 계약

구현 참조: `services/feed_relay/{server.py,build_release.py,publish_feed.py,add_customer.py}`,
클라이언트: `services/feed_relay/client/{feed_pull.py,updater.py,telemetry.py}`.

## 인증

- 헤더: `Authorization: Bearer <token>`
- 서버는 `sha256(token)` 만 보관하고 `customer_id` 와 함께 비교한다. 불일치·누락은 **401**.
- 토큰은 고객별로 발급되고 교체는 재발급으로 한다(원문 복구 불가).

## GET /v1/feed?customer_id=<id>

서버가 오늘 피드에 고객 정보를 주입해 돌려준다. 실패 시 클라이언트는 거래하지 않는다(fail-closed).

```json
{
  "generated_at": "2026-11-24T08:30:00+09:00",
  "published_at": "2026-11-24T08:35:00+09:00",
  "feed_schema_version": 1,
  "customer_id": "pilot1",
  "channel": "stable",
  "regime": {"state": "risk_off", "kospi_level": 4684.97, "kosdaq_level": 632.54, "market_up": false},
  "candidates": {"daytrading": {"items": [{"stock_code": "005930"}]}, "close": {"items": []}},
  "limits": {"max_daily_trades": 10, "max_position_pct": 30}
}
```

- `limits` 는 `customers.json` 의 고객별 값이다. 클라이언트는 로컬 설정과 서버 값을 비교해 **작은 쪽**을 쓴다.
- 피드가 없거나 아직 게시 전이면 **503 feed_not_published**. 어제 파일로 대신 거래하지 않는다.

## GET /v1/manifest?channel=stable

```json
{
  "version": "1.1.0",
  "channel": "stable",
  "created_at": "2026-11-24T15:40:00+09:00",
  "feed_schema_version": 1,
  "min_client_version": "1.0.0",
  "bundle_sha256": "<64hex>",
  "bundle_size": 279347,
  "files": [{"path": "trader_core/engine.py", "sha256": "<64hex>", "size": 18234}],
  "alg": "hmac-sha256",
  "signature": "<64hex>"
}
```

서명 규칙: `signature` 를 제외한 나머지를 `json.dumps(sort_keys=True, separators=(",", ":"), ensure_ascii=False)`
으로 직렬화한 바이트에 HMAC-SHA256. 클라이언트는 문자열 비교가 아니라 `hmac.compare_digest` 를 쓴다.

## GET /v1/pkg/<version>

- `application/zip` 바이트. 받은 뒤 `bundle_sha256` 과 대조하고, 해제 후 **파일별 sha256** 까지 검사한다.
- 버전 문자열에 `/` 나 `..` 가 들어오면 404(경로 탈출 방지).

## POST /v1/ack

```json
{"customer_id": "pilot1", "feed_generated_at": "2026-11-24T08:30:00+09:00", "version": "1.1.0", "acked_at": "..."}
```

- `ack/<YYYYMMDD>.jsonl` 에 append. 서버는 이 파일로 **오늘 피드를 못 받은 고객**을 판정한다.
- ack 실패는 클라이언트에서 경고만 남기고 계속한다(피드는 이미 받았다).

## POST /v1/telemetry

```json
{
  "customer_id": "pilot1", "version": "1.1.0", "collected_at": "...",
  "payload": {
    "daily": {"date": "2026-11-24", "entries": 3, "start_equity": 5000000, "closed": 2},
    "guard": {"active": true, "level": 1, "reason": "risk_off +1.2%"},
    "check": {"consecutive_failures": 0, "last_error": ""},
    "runner_log_tail": ["cycle done (monitor)"]
  }
}
```

- 저장: `telemetry/<customer_id>/<YYYYMMDD>.json`(고객당 하루 1파일, 덮어쓰기).
- 본문 한도 2MB, 초과는 **413**. 계좌번호는 클라이언트에서 `***-****` 로 마스킹한 뒤 보낸다.

## 클라이언트 `config.json` 키

| 키 | 의미 |
|---|---|
| `base_url` | 릴레이 공개 URL(https) |
| `customer_id` / `token` | 인증 |
| `channel` | stable / beta |
| `release_key` | 매니페스트 HMAC 검증 키(토큰과 별개) |
| `feed_schema_version` | 기대 스키마. 서버와 불일치면 거래 중지 |
| `app_dir` / `feed_out` / `log_dir` | 설치 경로 |
| `version` | 현재 설치 버전(텔레메트리에 기록) |

## 고객 PC 일일 시퀀스

| 시각 | 동작 | 실패 시 |
|---|---|---|
| 08:10 | `updater.py`(개장 전 창만) → `preflight` | 이전 버전으로 롤백, 거래 지속 |
| 08:45 | `feed_pull.py` | **거래 안 함**, 텔레메트리로 경고 전송 |
| 08:50 | 트레이더 루프 시작 | — |
| 15:40 | `telemetry.py` → `updater.py` | 다음 날 재시도, 로그만 남김 |

## 오류 코드

| 코드 | 의미 | 클라이언트 행동 |
|---|---|---|
| 401 | 토큰 불일치 | 거래 중지 + 운영자 알림 |
| 404 | 매니페스트/패키지 없음 | 업데이트 생략, 현재 버전 유지 |
| 413 | 본문 초과 | 텔레메트리 축소 재전송 |
| 503 | 피드 미게시 | 거래 중지(fail-closed) |

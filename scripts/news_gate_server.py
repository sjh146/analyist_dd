#!/usr/bin/env python3
"""뉴스 게이트 서버 — 트레이더 runner R5용 (종목별 최근 뉴스 부정 신호 조회).

- GET /newsgate?codes=005930,000660&days=3
  → {"generated_at": ..., "window_days": 3,
     "gate": {code: {"verdict": "block"|"ok"|"unknown", "reasons": [...]}}}

차단(block) 조건 (해당 종목, 최근 N일):
  1. news_event_extraction.event_type 이 부정 이벤트 타입 (아래 NEGATIVE_EVENTS)
  2. news_analysis.sentiment_score <= -0.5 (강한 부정 감성)
부정 판정 근거(제목/이벤트/날짜)를 reasons 로 내려 runner 로그/저널에 남긴다.
뉴스 기록이 없으면 "unknown"(트레이더는 ok/unknown 모두 통과 — 기록 부재 ≠ 신호 없음).
SNS(종토방)는 sns_post_features 스키마 확정 후 확장 예정 (2번 과제 일부).

순수 stdlib + psycopg2. systemd user unit 'trader-feed' 로 상주
(bind: Tailscale IP 100.127.186.48:8082 — screener-http 패턴).
"""
import json
import os
import sys
from datetime import datetime, timedelta
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse

try:
    import psycopg2
except ImportError:  # pragma: no cover - host /usr/bin/python3 has psycopg2
    psycopg2 = None

HOST = os.environ.get("NEWS_GATE_HOST", "100.127.186.48")
PORT = int(os.environ.get("NEWS_GATE_PORT", "8082"))

# 부정 이벤트 타입 (news_event_extraction check constraint 내 값)
NEGATIVE_EVENTS = {
    "부도·상폐·거래정지",
    "소송",
    "규제",
    "리콜",
    "유상증자·감자",
    "CB·BW",
    "자연재해",
}
SENTIMENT_FLOOR = -0.5  # sentiment_score 이하면 block

_QUERY = """
SELECT 'ev' AS kind, na.title, na.published_at, na.sentiment_score,
       nee.event_type, nee.sentiment_score AS ev_sent, nee.core_event_text
FROM news_event_extraction nee
JOIN news_analysis na ON na.id = nee.article_id
WHERE nee.stock_code = %s AND na.published_at >= %s
UNION ALL
SELECT 'na' AS kind, title, published_at, sentiment_score,
       NULL, NULL, NULL
FROM news_analysis
WHERE related_stock_codes::text LIKE %s
  AND sentiment_score IS NOT NULL AND sentiment_score <= %s
  AND published_at >= %s
ORDER BY published_at DESC
LIMIT 60
"""


def _conn():
    # 호스트 실행 컨텍스트: .env의 POSTGRES_HOST(postgres, 도커 호스트명)가
    # 들어와도 로컬 포트로 강제한다 (systemd unit이 아닌 수동 실행 대비).
    host = os.environ.get("POSTGRES_HOST", "127.0.0.1")
    if host in ("postgres", "db"):
        host = "127.0.0.1"
    port = int(os.environ.get("POSTGRES_PORT", "5434"))
    if host in ("127.0.0.1", "localhost") and port == 5432:
        port = 5434  # .env 기본값(컨테이너 내부 5432) → 호스트 매핑 포트로
    return psycopg2.connect(
        host=host,
        port=port,
        user=os.environ.get("POSTGRES_USER", "stock_user"),
        password=os.environ["POSTGRES_PASSWORD"],
        dbname=os.environ.get("POSTGRES_DB", "stock_trading"),
    )


def _score(value):
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def gate_code(cur, code: str, since) -> dict:
    pattern = '%"' + code + '"%'
    cur.execute(_QUERY, (code, since, pattern, SENTIMENT_FLOOR, since))
    rows = cur.fetchall()
    if not rows:
        return {"verdict": "unknown", "reasons": []}
    reasons = []
    for _kind, title, published, sent, ev_type, ev_sent, core_text in rows:
        when = str(published)[:16] if published else ""
        if ev_type and ev_type in NEGATIVE_EVENTS:
            reasons.append(
                "[{0}] {1}: {2}".format(when, ev_type, (core_text or title or "")[:80])
            )
            continue
        s = _score(ev_sent) if ev_sent is not None else _score(sent)
        if s is not None and s <= SENTIMENT_FLOOR:
            label = ev_type or "부정감성"
            reasons.append("[{0}] {1} (감성 {2:.2f}): {3}".format(
                when, label, s, (core_text or title or "")[:80]))
        if len(reasons) >= 10:
            break
    return {"verdict": "block" if reasons else "ok", "reasons": reasons[:10]}


class Handler(BaseHTTPRequestHandler):
    def do_GET(self):  # noqa: N802
        parsed = urlparse(self.path)
        if parsed.path.rstrip("/") != "/newsgate":
            self._send(404, {"error": "not found"})
            return
        qs = parse_qs(parsed.query)
        codes = [c.strip() for c in qs.get("codes", [""])[0].split(",") if c.strip()]
        days = max(1, min(30, int(qs.get("days", ["3"])[0] or 3)))
        if not codes:
            self._send(400, {"error": "codes required"})
            return
        since = datetime.now() - timedelta(days=days)
        gate = {}
        try:
            conn = _conn()
            try:
                cur = conn.cursor()
                for code in codes[:100]:
                    gate[code] = gate_code(cur, code, since)
                cur.close()
            finally:
                conn.close()
        except Exception as exc:  # noqa: BLE001 - never 500 without a body
            self._send(500, {"error": "gate query failed: {0}".format(exc)})
            return
        self._send(200, {
            "generated_at": datetime.now().astimezone().isoformat(),
            "window_days": days,
            "gate": gate,
        })

    def _send(self, status: int, payload: dict) -> None:
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, fmt, *args):  # noqa: N802 - keep stdout clean
        sys.stderr.write("[trader-feed] {0}\n".format(fmt % args))


def main() -> None:
    if psycopg2 is None:
        sys.exit("psycopg2 없음 — /usr/bin/python3 로 실행")
    server = ThreadingHTTPServer((HOST, PORT), Handler)
    print("news gate listening on {0}:{1} (tailscale 전용)".format(HOST, PORT), flush=True)
    server.serve_forever()


if __name__ == "__main__":
    main()

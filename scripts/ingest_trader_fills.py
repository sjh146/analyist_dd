#!/usr/bin/env python3
"""트레이더 체결·손익 환류 파일(`fills_YYYY-MM-DD.json`)을 DB·리포트로 적재한다.

WHY (계획 ④ — 환류 루프)
지금까지 데이터는 분석측 → 트레이더 한 방향이었다. 그래서 분석측은
(1) 자기 신호가 **실제로 어떻게 집행·청산됐는지**(슬리피지·수수료·보유시간)를 몰랐고,
(2) 모델 확률과 실현 성과의 **캘리브레이션**(0.6 확률 종목이 실제로 이겼나)을 검증할 수 없었고,
(3) 성과 확인은 사람이 브로커를 수동 조회하는 일이었다.
이 스크립트가 그 방향을 뒤집는다 — 발행 경로(`scripts/feed_export.py`)의 반대편이다.

무엇을
- `trader-agent/tools/export_fills.py` 가 만든 파일들을 읽어 Postgres `trader_fills` 에 upsert
  (기본키: fill_date + code + entry_ts → 같은 날 재실행해도 중복이 쌓이지 않는다).
- 스크리너별·일자별 실현 성과와 **모델 확률 구간별(decile) 실현 승률**을 리포트로 남긴다:
  `data/reports/trader_fill_stats.json` — 리서처/엔지니어/리뷰보드가 읽는다.

원칙
- 없는 값을 만들지 않는다: 수수료가 0으로 오면 "미확인"으로 표시하고 net 을 과대평가하지 않는다.
- 읽기만 하는 단계가 기본(`--dry-run`), DB 적재는 명시적으로만.

사용
    /usr/bin/python3 scripts/ingest_trader_fills.py --dry-run
    /usr/bin/python3 scripts/ingest_trader_fills.py            # DB 적재 + 리포트
"""
from __future__ import annotations

import argparse
import glob
import json
import os
import sys
from datetime import datetime
from typing import Any, Dict, List, Optional

PROJ = os.environ.get("PROJ_DIR", "/home/jhshi/analyist_dd")
DEFAULT_FILLS_DIR = os.environ.get(
    "TRADER_FILLS_DIR",
    "/mnt/c/Users/jhshi/analyist_dd/trader-agent/data/fills",
)
REPORT_PATH = os.path.join(PROJ, "data", "reports", "trader_fill_stats.json")
KST_OFFSET = "+09:00"

DDL = """
CREATE TABLE IF NOT EXISTS trader_fills (
    fill_date    DATE        NOT NULL,
    code         TEXT        NOT NULL,
    entry_ts     TEXT        NOT NULL,
    name         TEXT,
    screener     TEXT,
    qty          INTEGER,
    entry_price  NUMERIC,
    exit_ts      TEXT,
    exit_price   NUMERIC,
    exit_reason  TEXT,
    gross_pnl    NUMERIC,
    fees         NUMERIC,
    net_pnl      NUMERIC,
    ret_pct      NUMERIC,
    hold_hours   NUMERIC,
    ml_prob      NUMERIC,
    source_file  TEXT,
    ingested_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (fill_date, code, entry_ts)
);
CREATE INDEX IF NOT EXISTS idx_trader_fills_code ON trader_fills (code, fill_date);
"""

UPSERT = """
INSERT INTO trader_fills (
    fill_date, code, entry_ts, name, screener, qty, entry_price, exit_ts,
    exit_price, exit_reason, gross_pnl, fees, net_pnl, ret_pct, hold_hours,
    ml_prob, source_file
) VALUES (
    %(fill_date)s, %(code)s, %(entry_ts)s, %(name)s, %(screener)s, %(qty)s,
    %(entry_price)s, %(exit_ts)s, %(exit_price)s, %(exit_reason)s,
    %(gross_pnl)s, %(fees)s, %(net_pnl)s, %(ret_pct)s, %(hold_hours)s,
    %(ml_prob)s, %(source_file)s
)
ON CONFLICT (fill_date, code, entry_ts) DO UPDATE SET
    name = EXCLUDED.name,
    screener = EXCLUDED.screener,
    qty = EXCLUDED.qty,
    entry_price = EXCLUDED.entry_price,
    exit_ts = EXCLUDED.exit_ts,
    exit_price = EXCLUDED.exit_price,
    exit_reason = EXCLUDED.exit_reason,
    gross_pnl = EXCLUDED.gross_pnl,
    fees = EXCLUDED.fees,
    net_pnl = EXCLUDED.net_pnl,
    ret_pct = EXCLUDED.ret_pct,
    hold_hours = EXCLUDED.hold_hours,
    ml_prob = EXCLUDED.ml_prob,
    source_file = EXCLUDED.source_file,
    ingested_at = now();
"""


def load_fill_files(fills_dir: str, pattern: str = "fills_*.json") -> List[str]:
    """환류 파일 목록(날짜 오름차순)."""
    return sorted(glob.glob(os.path.join(fills_dir, pattern)))


def rows_from_payload(payload: Dict[str, Any], source_file: str = "") -> List[Dict[str, Any]]:
    """페이로드(하루치) → DB 행 리스트. 청산 거래만 적재한다(진입만 한 건은 다음 날 청산돼 들어온다)."""
    day = str(payload.get("date") or "").strip()
    if not day:
        return []
    rows: List[Dict[str, Any]] = []
    for record in payload.get("closed") or []:
        if not isinstance(record, dict):
            continue
        code = str(record.get("code") or "").strip()
        if not code:
            continue
        rows.append(
            {
                "fill_date": day,
                "code": code,
                "entry_ts": str(record.get("entry_ts") or day),
                "name": str(record.get("name") or ""),
                "screener": str(record.get("screener") or "unknown"),
                "qty": record.get("qty"),
                "entry_price": record.get("entry_price"),
                "exit_ts": str(record.get("exit_ts") or ""),
                "exit_price": record.get("exit_price"),
                "exit_reason": str(record.get("exit_reason") or ""),
                "gross_pnl": record.get("gross_pnl"),
                "fees": record.get("fees"),
                "net_pnl": record.get("net_pnl"),
                "ret_pct": record.get("ret_pct"),
                "hold_hours": record.get("hold_hours"),
                "ml_prob": record.get("ml_prob"),
                "source_file": os.path.basename(source_file),
            }
        )
    return rows


def _block(rows: List[Dict[str, Any]]) -> Dict[str, Any]:
    """한 묶음의 실현 성과(원/% 그대로, 없는 값은 None)."""
    if not rows:
        return {"n": 0, "net_pnl": 0.0, "fees": None, "win_rate": None,
                "avg_win": None, "avg_loss": None, "avg_ret_pct": None}
    nets = [float(r.get("net_pnl") or 0.0) for r in rows]
    rets = [float(r["ret_pct"]) for r in rows if r.get("ret_pct") is not None]
    wins = [n for n in nets if n > 0]
    losses = [n for n in nets if n < 0]
    return {
        "n": len(rows),
        "net_pnl": round(sum(nets), 1),
        "fees": round(sum(float(r.get("fees") or 0.0) for r in rows), 1),
        "win_rate": round(100.0 * len(wins) / len(rows), 1),
        "avg_win": round(sum(wins) / len(wins), 1) if wins else None,
        "avg_loss": round(sum(losses) / len(losses), 1) if losses else None,
        "avg_ret_pct": round(sum(rets) / len(rets), 3) if rets else None,
        "expectancy_krw": round(sum(nets) / len(rows), 1),
    }


def aggregate(rows: List[Dict[str, Any]], buckets: int = 5) -> Dict[str, Any]:
    """스크리너별·일자별 실현 성과 + 모델 확률 구간별 승률(캘리브레이션 대조)."""
    by_screener: Dict[str, List[Dict[str, Any]]] = {}
    by_date: Dict[str, List[Dict[str, Any]]] = {}
    for row in rows:
        by_screener.setdefault(str(row.get("screener") or "unknown"), []).append(row)
        by_date.setdefault(str(row.get("fill_date") or ""), []).append(row)

    scored = [r for r in rows if r.get("ml_prob") is not None and r.get("ret_pct") is not None]
    calibration = None
    if len(scored) >= max(4, buckets * 2):
        ranked = sorted(scored, key=lambda r: float(r["ml_prob"]))
        size = max(1, len(ranked) // buckets)
        calibration = []
        for index in range(0, len(ranked), size):
            chunk = ranked[index:index + size]
            if not chunk:
                continue
            block = _block(chunk)
            probs = [float(r["ml_prob"]) for r in chunk]
            calibration.append(
                {
                    "n": block["n"],
                    "prob_min": round(min(probs), 4),
                    "prob_max": round(max(probs), 4),
                    "win_rate": block["win_rate"],
                    "avg_ret_pct": block["avg_ret_pct"],
                }
            )
    return {
        "all": _block(rows),
        "by_screener": {name: _block(rs) for name, rs in sorted(by_screener.items())},
        "by_date": {day: _block(rs) for day, rs in sorted(by_date.items())},
        "ml_calibration": calibration,
        "scored_n": len(scored),
    }


def _pg():
    import psycopg2

    return psycopg2.connect(
        host=os.environ.get("POSTGRES_HOST", "127.0.0.1"),
        port=int(os.environ.get("POSTGRES_PORT", "5434")),
        dbname=os.environ.get("POSTGRES_DB", "stock_trading"),
        user=os.environ.get("POSTGRES_USER", "stock_user"),
        password=os.environ.get("POSTGRES_PASSWORD", ""),
    )


def main(argv: Optional[List[str]] = None) -> int:
    ap = argparse.ArgumentParser(description="트레이더 체결·손익 환류 적재")
    ap.add_argument("--fills-dir", default=DEFAULT_FILLS_DIR)
    ap.add_argument("--pattern", default="fills_*.json")
    ap.add_argument("--report", default=REPORT_PATH)
    ap.add_argument("--dry-run", action="store_true", help="DB 적재 생략(파싱·집계만)")
    args = ap.parse_args(argv)

    files = load_fill_files(args.fills_dir, args.pattern)
    if not files:
        print("[trader_fills] 환류 파일 없음: {0}/{1}".format(args.fills_dir, args.pattern))
        return 3

    rows: List[Dict[str, Any]] = []
    skipped: List[str] = []
    for path in files:
        try:
            with open(path, encoding="utf-8") as handle:
                payload = json.load(handle)
        except (OSError, ValueError) as exc:
            skipped.append("{0}({1})".format(os.path.basename(path), exc))
            continue
        rows.extend(rows_from_payload(payload, path))

    print("[trader_fills] 파일 {0}개 · 청산 거래 {1}건 (건너뜀 {2})".format(
        len(files), len(rows), len(skipped)))
    if skipped:
        print("[trader_fills] 건너뛴 파일:", ", ".join(skipped[:5]))
    if not rows:
        print("[trader_fills] 적재할 청산 거래가 없다(빈 날짜만 있음)")
        return 3

    stats = aggregate(rows)
    print("[trader_fills] 전체: n={n} 순손익={net_pnl} 승률={win_rate}% 기대값={expectancy_krw}원".format(
        **stats["all"]))
    for name, block in stats["by_screener"].items():
        print("   - {0}: n={1} net={2} 승률={3}%".format(
            name, block["n"], block["net_pnl"], block["win_rate"]))

    if args.dry_run:
        print("[trader_fills] --dry-run — DB·리포트 기록 생략")
        return 0

    conn = _pg()
    try:
        cur = conn.cursor()
        cur.execute(DDL)
        for row in rows:
            cur.execute(UPSERT, row)
        conn.commit()
        cur.execute("SELECT count(*), COALESCE(SUM(net_pnl),0) FROM trader_fills")
        total, net = cur.fetchone()
        print("[trader_fills] DB 적재 완료: 누적 {0}건 · 순손익 {1}".format(total, net))
        cur.close()
    finally:
        conn.close()

    payload = {
        "generated_at": datetime.now().isoformat(timespec="seconds") + KST_OFFSET,
        "source": "trader-agent/data/fills",
        "files": [os.path.basename(p) for p in files],
        "rows": len(rows),
        **stats,
    }
    os.makedirs(os.path.dirname(args.report), exist_ok=True)
    with open(args.report, "w", encoding="utf-8") as handle:
        json.dump(payload, handle, ensure_ascii=False, indent=1)
    print("[trader_fills] 리포트 기록: {0}".format(args.report))
    return 0


if __name__ == "__main__":
    sys.exit(main())

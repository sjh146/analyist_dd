#!/usr/bin/env python3
"""오늘 '분봉이 있어야 하는' 종목 목록을 모아 파일로 쓴다 (분봉 백필용).

WHY (2026-11-18 실측): `scripts/naver_minute_backfill.py` 의 기본 대상은 "capital_sim 신호
발생 종목"인데, 신호가 없는 날은 **0종목**이 되어 분봉이 한 줄도 안 쌓인다.
(16:10 크론 실측: `codes=0, rows_written=0` — 11-18 은 49종목/15,755행에 그침)
그래서 당일 **실제로 판단·체결된 종목**(피드 후보 + 저널)을 명시적으로 넘긴다.

출력: 한 줄에 한 코드(6자리). 정렬·중복 제거. 입력은 전부 읽기 전용.
"""
from __future__ import annotations

import argparse
import glob
import json
import os
import sqlite3
import shutil
import tempfile

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _feed_codes(path: str) -> list[str]:
    try:
        data = json.load(open(path, encoding="utf-8"))
    except Exception:
        return []
    out: list[str] = []
    for block in (data.get("candidates") or {}).values():
        for item in (block or {}).get("items") or []:
            code = str(item.get("stock_code") or "").strip()
            if code:
                out.append(code)
    return out


def _journal_codes(journal_path: str, trade_date: str) -> list[str]:
    if not os.path.exists(journal_path):
        return []
    tmpdir = tempfile.mkdtemp()
    dst = os.path.join(tmpdir, "j.sqlite3")
    try:
        for suffix in ("", "-wal", "-shm"):
            if os.path.exists(journal_path + suffix):
                shutil.copyfile(journal_path + suffix, dst + suffix)
        conn = sqlite3.connect("file:%s?mode=ro" % dst, uri=True)
        rows = conn.execute(
            "SELECT stock_code FROM trades WHERE substr(ts,1,10)=? "
            "OR substr(exit_ts,1,10)=?", (trade_date, trade_date)
        ).fetchall()
        conn.close()
        return [str(r[0]).strip() for r in rows if r and r[0]]
    except Exception:
        return []
    finally:
        shutil.rmtree(tmpdir, ignore_errors=True)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="분봉 백필 대상 코드 목록 생성")
    ap.add_argument("--date", required=True, help="YYYY-MM-DD")
    ap.add_argument("--out", default=None, help="기본 reports/minute_codes_<date>.txt")
    ap.add_argument("--feed", default=os.path.join(REPO, "data", "feed", "screener_latest.json"))
    ap.add_argument("--daytrading", default=os.path.join(REPO, "reports", "daytrading_latest.json"))
    ap.add_argument("--journal", default=os.path.join(
        "/mnt/c/Users/jhshi/analyist_dd/trader-agent", "journal", "trade_journal.sqlite3"))
    args = ap.parse_args(argv)

    codes: list[str] = []
    codes += _feed_codes(args.feed)
    codes += _feed_codes(args.daytrading)
    codes += _journal_codes(args.journal, args.date)

    clean = sorted({c for c in codes if c.isdigit() and len(c) == 6})
    out = args.out or os.path.join(REPO, "reports", "minute_codes_%s.txt" % args.date)
    os.makedirs(os.path.dirname(out), exist_ok=True)
    with open(out, "w", encoding="utf-8") as fh:
        fh.write("\n".join(clean) + ("\n" if clean else ""))
    print("minute_codes date=%s n=%d out=%s" % (args.date, len(clean), out))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

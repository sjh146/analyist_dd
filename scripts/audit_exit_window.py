#!/usr/bin/env python3
"""청산창(09:00-09:10) 커버리지 감사 — close 프로파일의 익일 시가 청산이 실제로 창 안에서 일어났나.

WHY (2026-10-02 실측, MT117 후속): close 프로파일은 `exit_mode=EXIT_NEXT_OPEN`,
청산창 09:00-09:10 이다. 그런데 실계좌 첫 청산 3건은 **09:26:48에 'reconcile: broker flat'** 으로
기록됐고, 체결가가 그날 **시가보다 낮았다** — 시가에 팔았다면 +368원이었을 것이 실제로는
−1,446원이었다(차이 **−1,814원**). 즉 실현손실의 전부가 '청산창을 놓친 것'으로 설명된다.
루프가 09:10 이후에 기동하면(실측 09-30 09:19 기동, 10-02 11:20 감사 경보) 이 손실은
보유가 있을 때마다 구조적으로 반복된다.

이 감사는 **사후에도 판정 가능**해야 한다(창을 놓친 뒤 대조청산되면 포지션이 0이라
'보유 있음' 조건의 경보는 울리지 않는다). 그래서 저널의 sell 체결을 날짜별로 훑어
①창 안/밖 분류 ②시가 대비 슬리피지(원) 를 계산한다.

사용:
  python3 scripts/audit_exit_window.py --days 10            # 최근 10일 창 밖 청산 감사
  python3 scripts/audit_exit_window.py --json-out data/reports/audit/exit_window.json
종료코드: 0 문제 없음(또는 청산 없음) / 2 창 밖 청산 존재(경보) / 3 측정 실패
"""
import argparse
import datetime as dt
import json
import os
import subprocess
import sys

import psycopg2

REPO = "/home/jhshi/analyist_dd"
TA = "/mnt/c/Users/jhshi/analyist_dd/trader-agent"
OUT_DIR = os.path.join(REPO, "data", "reports", "audit")
WPY = "/mnt/c/Users/jhshi/Python312-64/python.exe"
WIN_JOURNAL = r"C:\Users\jhshi\analyist_dd\trader-agent\journal\trade_journal.sqlite3"
WINDOW = ("09:00", "09:10")
PG = dict(host=os.environ.get("POSTGRES_HOST", "127.0.0.1"),
          port=int(os.environ.get("POSTGRES_PORT", "5434")),
          user=os.environ.get("POSTGRES_USER", "stock_user"),
          password=os.environ.get("POSTGRES_PASSWORD", ""),
          dbname=os.environ.get("POSTGRES_DB", "stock_trading"))


def journal_sells(days: int):
    """청산 체결 → [{ts, code, qty, price, screener}].

    실측 2026-10-02: **저널(trade_journal.sqlite3)에는 매도 기록이 없다** — trades 테이블에
    buy 3행뿐이고 청산은 trader 의 fills JSON(`data/fills/fills_<date>.json`)의 closed[] 에만
    남는다(exit_ts/exit_price/exit_reason 포함). 그래서 감사 소스는 fills JSON 이다.
    (이 사실 자체가 trader 측 기록 결손 → 별도 보고 대상.)
    """
    import glob
    since = (dt.datetime.now() - dt.timedelta(days=days)).date()
    rows, files = [], []
    for p in sorted(glob.glob(os.path.join(TA, "data", "fills", "fills_*.json"))):
        try:
            d = json.load(open(p, encoding="utf-8"))
        except (OSError, ValueError):
            continue
        files.append(os.path.basename(p))
        for c in (d.get("closed") or []):
            ts_s = c.get("exit_ts")
            if not ts_s:
                continue
            try:
                ts = dt.datetime.fromisoformat(str(ts_s))
            except ValueError:
                continue
            if ts.date() < since:
                continue
            rows.append({"ts": str(ts_s), "code": str(c.get("code") or ""),
                         "qty": float(c.get("qty") or 0), "price": float(c.get("exit_price") or 0),
                         "screener": str(c.get("screener") or ""),
                         "exit_reason": str(c.get("exit_reason") or "")[:60],
                         "entry_ts": str(c.get("entry_ts") or ""),
                         "date": ts.date().isoformat()})
    return rows, None, files


def opens_for(codes_dates):
    """{(code, date)} → 시가. market_data 확정 일봉만."""
    if not codes_dates:
        return {}
    out = {}
    try:
        conn = psycopg2.connect(**PG)
    except Exception as e:  # noqa: BLE001
        return {"__err__": f"{type(e).__name__}: {e}"}
    try:
        cur = conn.cursor()
        for code, d in codes_dates:
            cur.execute("SELECT open_price FROM market_data WHERE stock_code=%s AND trade_date=%s",
                        (code, d))
            row = cur.fetchone()
            if row and row[0] is not None:
                out[(code, d)] = float(row[0])
        cur.close()
    finally:
        conn.close()
    return out


def main() -> int:
    ap = argparse.ArgumentParser(description="청산창(09:00-09:10) 커버리지 감사")
    ap.add_argument("--days", type=int, default=10)
    ap.add_argument("--json-out", default=os.path.join(OUT_DIR, "exit_window.json"))
    a = ap.parse_args()

    sells, err, files = journal_sells(a.days)
    if err:
        print(f"[exit-window] 체결 조회 실패: {err}", file=sys.stderr)
        return 3

    rows, need = [], []
    for s in sells:
        ts = dt.datetime.fromisoformat(s["ts"])
        hm = ts.strftime("%H:%M")
        s["hm"] = hm
        s["in_window"] = WINDOW[0] <= hm <= WINDOW[1]
        rows.append(s)
        need.append((s["code"], ts.date()))
    opens = opens_for(need)

    total_krw, out_rows = 0.0, []
    for r in rows:
        o = opens.get((r["code"], dt.date.fromisoformat(r["date"]))) if isinstance(opens, dict) else None
        if o:
            delta = (r["price"] - o) * float(r["qty"] or 0)
            r["open_price"] = round(o, 4)
            r["vs_open_krw"] = round(delta, 1)
        else:
            r["open_price"] = None
            r["vs_open_krw"] = None          # 시가 미확보(일봉 결손) → 측정 불가로 남긴다
        if not r["in_window"]:
            out_rows.append(r)
            if r["vs_open_krw"] is not None:
                total_krw += r["vs_open_krw"]

    rec = {"ts": dt.datetime.now().isoformat(timespec="seconds"), "days": a.days,
           "window": f"{WINDOW[0]}-{WINDOW[1]}", "n_sells": len(rows),
           "n_out_of_window": len(out_rows), "out_of_window_krw": round(total_krw, 1),
           "sells": rows}
    os.makedirs(os.path.dirname(a.json_out), exist_ok=True)
    with open(a.json_out, "w", encoding="utf-8") as f:
        json.dump(rec, f, ensure_ascii=False, indent=1)

    if not rows:
        print(f"[exit-window] 최근 {a.days}일 매도 체결 없음 — 창 이탈 위험 미발생")
        return 0
    if not out_rows:
        print(f"[exit-window] 정상: 매도 {len(rows)}건 모두 {WINDOW[0]}-{WINDOW[1]} 창 안")
        return 0

    miss = sum(1 for r in out_rows if not r["in_window"])
    unmeasured = [r for r in out_rows if r["vs_open_krw"] is None]
    print(f"[exit-window] ★창 밖 청산 {miss}건 / 총 {len(rows)}건 — 시가 대비 합계 "
          f"{total_krw:+.0f}원 (창 안이었다면 이만큼 달랐다)")
    for r in out_rows:
        v = f"{r['vs_open_krw']:+.0f}원" if r["vs_open_krw"] is not None else "측정 불가(일봉 결손)"
        print(f"  · {r['date']} {r['hm']} {r['code']} x{r['qty']} @{r['price']} "
              f"(시가 {r['open_price']}, 시가대비 {v}) screener={r['screener'] or '?'}")
    if unmeasured:
        print(f"  · 시가 미확보 {len(unmeasured)}건 — 일봉 결손/미상장으로 측정 불가")
    print("  → 루프가 09:00 이전에 기동하는지 확인(감독기 기동 시각·재기동 이력). "
          "청산창은 close 프로파일의 유일한 청산 창이다.")
    return 2


if __name__ == "__main__":
    sys.exit(main())

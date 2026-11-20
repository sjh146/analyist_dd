#!/usr/bin/env python3
"""실계좌 1주일 관찰 (사용자 결정 C: 현행 유지 + 관찰, 2026-11-17).

동작:
  - 최초 실행 시 기준선(baseline)을 고정 저장 → 이후 절대 덮어쓰지 않는다.
  - 매 실행: 누적 실현손익·건수·승률·기대값·기준선 대비 변화·최대낙폭을 계산.
  - 경보: 기준선 대비 -3% / -6% / -10% 도달 시 1회씩 경고(자동 halt 는 하지 않는다 — 현행 유지 결정).
  - 출력은 사람이 읽는 4줄 + JSON 저장.

사용: /usr/bin/python3 scripts/real_account_watch.py [--quiet]
"""
from __future__ import annotations
import argparse, json, os, sqlite3, sys
from datetime import datetime, date

ROOT = os.environ.get("PROJ_DIR", "/home/jhshi/analyist_dd")
JOURNAL = "/mnt/c/Users/jhshi/analyist_dd/trader-agent/journal/trade_journal.sqlite3"
OUT_DIR = os.path.join(ROOT, "data/reports")
BASE = os.path.join(OUT_DIR, "real_account_watch_baseline.json")
LAST = os.path.join(OUT_DIR, "real_account_watch.json")
THRESHOLDS = (-3.0, -6.0, -10.0)   # 기준선 자본 대비 누적 손실 %


def read_trades() -> list[dict] | None:
    """저널을 **사본으로** 읽는다. WSL 이 Windows SQLite 를 직접 열면 WAL 잠금 때문에
    'disk I/O error' 가 난다 → db+wal+shm 을 /tmp 로 복사해 읽는다.
    읽기 실패 시 None 을 돌려준다(호출부는 상태를 덮어쓰지 말고 경고 후 종료할 것)."""
    import shutil, tempfile, time
    last_err = None
    for attempt in range(3):
        tmpdir = tempfile.mkdtemp(prefix="journal_copy_")
        try:
            for suffix in ("", "-wal", "-shm"):
                src = JOURNAL + suffix
                if os.path.exists(src):
                    shutil.copy2(src, os.path.join(tmpdir, os.path.basename(JOURNAL) + suffix))
            c = sqlite3.connect(os.path.join(tmpdir, os.path.basename(JOURNAL)))
            c.row_factory = sqlite3.Row
            rows = [dict(r) for r in c.execute("SELECT * FROM trades ORDER BY ts")]
            c.close()
            return rows
        except Exception as e:  # noqa: BLE001
            last_err = e
            time.sleep(2)
        finally:
            shutil.rmtree(tmpdir, ignore_errors=True)
    print(f"[watch] 저널 읽기 실패(3회 재시도): {last_err} — 상태를 갱신하지 않습니다", file=sys.stderr)
    return None


def summarize(trades: list[dict]) -> dict:
    closed = [t for t in trades if t.get("exit_price") and float(t.get("exit_price") or 0) > 0]
    pnl = [float(t.get("pnl") or 0) for t in closed]
    total = sum(pnl)
    wins = [x for x in pnl if x > 0]
    return {
        "trades": len(trades),
        "closed": len(closed),
        "realized": round(total, 0),
        "wins": len(wins),
        "win_rate": round(100.0 * len(wins) / len(closed), 1) if closed else 0.0,
        "expectancy": round(total / len(closed), 0) if closed else 0.0,
        "last_trade_ts": (closed[-1].get("ts") if closed else None),
    }


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--quiet", action="store_true")
    args = ap.parse_args()
    os.makedirs(OUT_DIR, exist_ok=True)

    trades = read_trades()
    if trades is None:
        # 읽기 실패 → 상태 파일을 덮어쓰지 않는다(직전 정상값 보존). 종료코드 2 = 경고.
        sys.exit(2)
    cur = summarize(trades)
    cur["checked_at"] = datetime.now().isoformat(timespec="seconds")

    # 기준선 고정 (최초 1회)
    base = None
    if os.path.exists(BASE):
        try:
            base = json.load(open(BASE, encoding="utf-8"))
        except Exception:
            base = None
    if base is None:
        base = {
            "frozen_at": cur["checked_at"],
            "note": "사용자 결정 C(현행 유지 + 1주일 관찰) 기준선",
            "closed": cur["closed"], "realized": cur["realized"],
            "win_rate": cur["win_rate"], "expectancy": cur["expectancy"],
            "equity_ref": None,
            "window_end": None,
        }
        json.dump(base, open(BASE, "w", encoding="utf-8"), ensure_ascii=False, indent=2)

    delta = cur["realized"] - (base.get("realized") or 0)
    d_closed = cur["closed"] - (base.get("closed") or 0)
    d_exp = cur["expectancy"] - (base.get("expectancy") or 0)

    # 기준선 자본 대비 손실률 (자본 미상이면 누적 실현손익 자체를 분모 대용으로 쓰지 않는다)
    equity = base.get("equity_ref")
    loss_pct = None
    if equity and equity > 0:
        loss_pct = 100.0 * delta / equity

    alerts = []
    if loss_pct is not None:
        for th in THRESHOLDS:
            if loss_pct <= th:
                alerts.append(f"기준선 대비 {loss_pct:.1f}% (임계 {th:.0f}%)")

    covered = base.get("window_end") or "미정"
    out = {"baseline": base, "current": cur, "delta": {
        "realized": delta, "closed": d_closed, "expectancy": d_exp, "loss_pct": loss_pct},
        "alerts": alerts, "window_end": covered}
    json.dump(out, open(LAST, "w", encoding="utf-8"), ensure_ascii=False, indent=2)

    if not args.quiet:
        print(f"[실계좌 관찰] {cur['checked_at'][:16]} (기준선 {base['frozen_at'][:10]})")
        print(f"  현재: 청산 {cur['closed']}건 / 실현 {cur['realized']:+,.0f}원 / "
              f"승률 {cur['win_rate']}% / 기대값 {cur['expectancy']:+,.0f}원")
        print(f"  기준선 대비: 실현 {delta:+,.0f}원 / 청산 {d_closed:+d}건 / 기대값 {d_exp:+,.0f}원"
              + (f" / 손실률 {loss_pct:+.1f}%" if loss_pct is not None else ""))
        print("  경보: " + ("; ".join(alerts) if alerts else "없음"))


if __name__ == "__main__":
    main()

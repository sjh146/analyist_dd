"""모의계좌 저널을 은퇴시키고 실계좌용 빈 저널을 만든다 (실계좌 전환 1회성 작업).

왜 필요한가 (실측 2026-09-28):
    저널에는 모의계좌 포지션 8건(2026-09-15~09-21 진입)이 열린 채로 남아 있다. 사용자가 실계좌로
    다시 로그인하면 브로커에는 그 종목이 없다. 그 상태로 루프를 켜면
      ① 동시보유 게이트가 8건을 세어 신규 진입을 막고(게이트 과대),
      ② 마감 대조가 '저널 보유 / 브로커 없음'만 반복 보고하고,
      ③ 실계좌에 없는 종목을 팔려는 시도가 생긴다.
    또 실계좌 성과 통계가 모의 실적으로 오염된다. 그래서 모의 저널을 **통째로 아카이브**하고
    스키마만 있는 빈 저널로 새출발한다(사용자 결정 2026-09-28: "A) 아카이브 후 새 저널").

하는 일:
    1) `PRAGMA wal_checkpoint(TRUNCATE)` — -wal 에 남은 4MB 를 본체로 접는다(그냥 복사하면 유실).
    2) `trade_journal.sqlite3` → `journal/paper_<날짜>.sqlite3` 로 이동(있으면 -wal/-shm 도 함께).
    3) `trader_core.journal.Journal` 로 새 저널 생성(스키마 v4, 빈 테이블).
    4) 검증: 아카이브의 청산/미청산 건수·실현손익이 보존됐는지, 새 저널이 0건인지.
    5) 아카이브 옆에 `PAPER_SESSION_<날짜>.md` — 모의 세션 최종 성적표(추적용).

사용(반드시 64비트 Windows 파이썬, 리포 루트에서):
    C:\\Users\\jhshi\\Python312-64\\python.exe tools\\retire_paper_journal.py             # 예행(기본)
    C:\\Users\\jhshi\\Python312-64\\python.exe tools\\retire_paper_journal.py --apply     # 실행

주의: 루프가 꺼져 있는 동안에만 실행한다(실행 중이면 저널을 잡고 있어 충돌).
"""
from __future__ import annotations

import argparse
import datetime as dt
import os
import shutil
import sqlite3
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)


def _counts(path: str) -> dict:
    """(청산 건수, 미청산 건수, 실현손익, 최초/최종 진입시각) — 읽기 전용."""
    con = sqlite3.connect(f"file:{path}?mode=ro", uri=True, timeout=15)
    try:
        cur = con.cursor()
        # 스키마 v4: 진입만 행으로 남고(ts=진입시각, price=진입가), 청산 시 같은 행에 exit_* 를 채운다.
        # (2026-09-28 실측: 41행 전부 side='buy' · 33행 청산 · 8행 미청산)
        cur.execute("SELECT COUNT(*) FROM trades WHERE exit_ts IS NOT NULL")
        closed = cur.fetchone()[0]
        cur.execute("SELECT COUNT(*) FROM trades WHERE exit_ts IS NULL")
        opened = cur.fetchone()[0]
        cur.execute("SELECT COALESCE(SUM(pnl), 0) FROM trades WHERE exit_ts IS NOT NULL")
        pnl = cur.fetchone()[0]
        cur.execute("SELECT MIN(ts), MAX(ts) FROM trades")
        first, last = cur.fetchone()
        cur.execute("PRAGMA user_version")
        ver = cur.fetchone()[0]
        return {"closed": closed, "open": opened, "pnl": pnl,
                "first_entry": first, "last_entry": last, "schema": ver}
    finally:
        con.close()


def _open_rows(path: str) -> list[tuple]:
    con = sqlite3.connect(f"file:{path}?mode=ro", uri=True, timeout=15)
    try:
        cur = con.cursor()
        cur.execute(
            "SELECT code, name, qty, price, ts FROM trades WHERE exit_ts IS NULL ORDER BY ts"
        )
        return cur.fetchall()
    finally:
        con.close()


def _summary_md(path: str, stats: dict, rows: list[tuple], archive: str) -> str:
    lines = [
        f"# 모의계좌 세션 최종 성적표 ({dt.date.today().isoformat()})",
        "",
        "실계좌 전환에 따라 모의 저널을 은퇴시켰다. 이 파일은 그 시점의 사실 기록이다.",
        "",
        f"- 아카이브: `{os.path.relpath(archive, ROOT).replace(os.sep, '/')}` (스키마 v{stats['schema']})",
        f"- 청산 완료: **{stats['closed']}건** / 실현손익 **{stats['pnl']:+,.0f}원** (수수료 기록 0원)",
        f"- 미청산(모의): **{stats['open']}건** — 실계좌에는 존재하지 않는다",
        f"- 거래 기간: {stats['first_entry']} ~ {stats['last_entry']}",
        "",
        "## 은퇴 시점 미청산 포지션 (실계좌로 이월되지 않음)",
        "",
        "| 종목코드 | 종목명 | 수량 | 진입가 | 진입시각 |",
        "|---|---|---|---|---|",
    ]
    for code, name, qty, px, ts in rows:
        lines.append(f"| {code} | {name or '-'} | {qty} | {px:,} | {ts} |")
    lines += [
        "",
        "## 왜 아카이브했나",
        "",
        "- 실계좌에는 위 종목이 없다 → 그대로 두면 동시보유 게이트가 8건을 세고(신규 진입 차단),",
        "  마감 대조가 '저널 보유/브로커 없음'을 반복 보고하며, 없는 종목을 팔려는 시도가 생긴다.",
        "- 실계좌 성과 통계가 모의 실적과 섞이면 승률·기대값 판정이 무의미해진다.",
        "",
        "복원하려면: `trade_journal.sqlite3` 를 지우고 이 파일을 그 이름으로 되돌린다(루프 정지 상태에서).",
        "",
    ]
    return "\n".join(lines)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--journal", default=os.path.join(ROOT, "journal", "trade_journal.sqlite3"))
    ap.add_argument("--archive", default=os.path.join(
        ROOT, "journal", f"paper_{dt.date.today().strftime('%Y%m%d')}.sqlite3"))
    ap.add_argument("--apply", action="store_true", help="실제로 접고/옮기고/새로 만든다")
    args = ap.parse_args()

    if not os.path.exists(args.journal):
        print(f"[중단] 저널이 없다: {args.journal}")
        return 1
    before = _counts(args.journal)
    rows = _open_rows(args.journal)
    print(f"현재 저널: 청산 {before['closed']}건 / 미청산 {before['open']}건 / "
          f"실현 {before['pnl']:+,}원 / 스키마 v{before['schema']}")
    print(f"  거래 기간: {before['first_entry']} ~ {before['last_entry']}")
    print(f"→ 아카이브 대상: {args.archive}")
    if not args.apply:
        print("\n[예행] --apply 를 붙이면 아래를 실행한다:")
        print("  1) PRAGMA wal_checkpoint(TRUNCATE)")
        print(f"  2) {os.path.basename(args.journal)} → {os.path.basename(args.archive)} 이동")
        print("  3) trader_core.journal.Journal 로 빈 저널 생성(스키마 v4)")
        print(f"  4) journal/PAPER_SESSION_{dt.date.today().strftime('%Y%m%d')}.md 기록")
        return 0

    # 1) WAL 접기 — 이걸 빼면 -wal 에 남은 변경분이 아카이브에서 사라진다.
    con = sqlite3.connect(args.journal, timeout=30)
    try:
        cur = con.cursor()
        cur.execute("PRAGMA wal_checkpoint(TRUNCATE)")
        print("checkpoint:", cur.fetchone())
    finally:
        con.close()

    # 2) 이동 (-wal/-shm 함께)
    for suffix in ("", "-wal", "-shm"):
        src = args.journal + suffix
        if os.path.exists(src):
            dst = args.archive + suffix
            if os.path.exists(dst):
                print(f"[중단] 대상이 이미 있다: {dst}")
                return 1
            shutil.move(src, dst)
            print(f"이동: {os.path.basename(src)} → {os.path.basename(dst)}")

    after = _counts(args.archive)
    if (after["closed"], after["open"]) != (before["closed"], before["open"]):
        print(f"[경고] 건수가 달라졌다: {before['closed']}/{before['open']} → "
              f"{after['closed']}/{after['open']} (아카이브를 확인하라)")

    # 3) 빈 저널 생성 (스키마는 코드가 만든다)
    from trader_core.journal import Journal  # noqa: E402  (경로 준비 후 임포트)

    j = Journal(args.journal)
    j.close() if hasattr(j, "close") else None
    fresh = _counts(args.journal)
    print(f"새 저널: 청산 {fresh['closed']}건 / 미청산 {fresh['open']}건 / 스키마 v{fresh['schema']}")

    # 4) 성적표
    md_path = os.path.join(os.path.dirname(args.archive),
                           f"PAPER_SESSION_{dt.date.today().strftime('%Y%m%d')}.md")
    with open(md_path, "w", encoding="utf-8") as fh:
        fh.write(_summary_md(args.journal, after, rows, args.archive))
    print(f"성적표: {md_path}")

    ok = fresh["closed"] == 0 and fresh["open"] == 0 and after["closed"] == before["closed"]
    print("결과:", "OK - 실계좌용 빈 저널 준비 완료" if ok else "확인 필요(diff 있음)")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())

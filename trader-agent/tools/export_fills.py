#!/usr/bin/env python3
"""트레이더 체결·손익을 analyist_dd 로 환류할 파일로 내보낸다 (`data/fills/fills_YYYY-MM-DD.json`).

WHY (계획 ④ — 환류 루프)
지금까지 데이터는 분석측 → 트레이더 방향으로만 흘렀다. 그래서
(1) 모델의 실현 성과(캘리브레이션·피처 기여)를 **실집행 결과로** 평가할 수 없었고,
(2) 리서처/엔지니어가 성과를 확인하려고 KIS·브로커를 **수동 재수집**해야 했다.
이 파일이 그 방향을 뒤집는다: 분석측이 자기 신호가 실제로 어떻게 집행·청산됐는지 받는다.

무엇을
- 청산된 거래(`exit_ts` 있음): gross/net 손익, 수수료, 보유시간, 스크리너, algo_json 의 ml_prob.
- 당일 신규 진입(보유 중): 사이징·손절/익절·kelly_f (모델 확률과 사이징의 대조용).
- 스크리너별 요약(건수·순손익·승률·평균이익/손실) — 발행측 `scoring_summary` 와 대조 가능.

안전
- 저널을 **임시 디렉터리로 복사해서** 읽는다(drvfs 위 WAL 직접 읽기 금지, 실행 중 루프와 락 경합 없음).
- 원자적 교체(임시파일 → rename). 실패해도 트레이더 동작에 영향이 없다(읽기 전용 도구).

사용
    python3 tools/export_fills.py                       # 오늘(KST) 청산분
    python3 tools/export_fills.py --date 2026-09-28     # 특정일
    python3 tools/export_fills.py --days 7              # 최근 7일 각각
    python3 tools/export_fills.py --journal journal/paper_20260928.sqlite3 --date 2026-09-28
"""
from __future__ import annotations

import argparse
import json
import os
import shutil
import sys
import tempfile
from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if REPO not in sys.path:
    sys.path.insert(0, REPO)

from trader_core.config import KST  # noqa: E402
from trader_core.journal import Journal  # noqa: E402

DEFAULT_JOURNAL = os.path.join(REPO, "journal", "trade_journal.sqlite3")
DEFAULT_OUT_DIR = os.path.join(REPO, "data", "fills")


def _to_float(value: Any) -> Optional[float]:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number


def _load_algo(raw: Any) -> Dict[str, Any]:
    if isinstance(raw, dict):
        return raw
    if not raw:
        return {}
    try:
        parsed = json.loads(str(raw))
    except ValueError:
        return {}
    return parsed if isinstance(parsed, dict) else {}


def _hours_between(start: Any, end: Any) -> Optional[float]:
    try:
        first = datetime.fromisoformat(str(start))
        second = datetime.fromisoformat(str(end))
    except (TypeError, ValueError):
        return None
    return round((second - first).total_seconds() / 3600.0, 2)


def _closed_row(row: Dict[str, Any]) -> Dict[str, Any]:
    """한 청산 행 → 환류 레코드. ``pnl`` 은 수수료 차감 후(net)다."""
    qty = int(row.get("qty") or 0)
    entry = _to_float(row.get("price"))
    exit_px = _to_float(row.get("exit_price"))
    fees = _to_float(row.get("fees")) or 0.0
    net = _to_float(row.get("pnl")) or 0.0
    gross = None
    ret_pct = None
    if entry is not None and exit_px is not None:
        sign = -1.0 if str(row.get("side") or "buy").lower() == "sell" else 1.0
        gross = round((exit_px - entry) * qty * sign, 2)
        notional = abs(entry * qty)
        if notional > 0:
            ret_pct = round(net / notional * 100.0, 3)
    algo = _load_algo(row.get("algo_json"))
    record: Dict[str, Any] = {
        "code": str(row.get("code") or ""),
        "name": str(row.get("name") or ""),
        "screener": str(row.get("screener") or ""),
        "qty": qty,
        "entry_ts": str(row.get("ts") or ""),
        "entry_price": entry,
        "exit_ts": str(row.get("exit_ts") or ""),
        "exit_price": exit_px,
        "exit_reason": str(row.get("exit_reason") or ""),
        "gross_pnl": gross,
        "fees": round(fees, 2),
        "net_pnl": round(net, 2),
        "ret_pct": ret_pct,
        "hold_hours": _hours_between(row.get("ts"), row.get("exit_ts")),
    }
    ml_prob = _to_float(algo.get("ml_prob"))
    if ml_prob is None:
        ml_prob = _to_float(algo.get("candidate", {}).get("ml_prob")
                            if isinstance(algo.get("candidate"), dict) else None)
    if ml_prob is not None:
        record["ml_prob"] = ml_prob
    for key in ("rule", "exit_mode", "screener"):
        if algo.get(key) and key != "screener":
            record.setdefault("algo_" + key, algo.get(key))
    return record


def _open_row(row: Dict[str, Any]) -> Dict[str, Any]:
    record: Dict[str, Any] = {
        "code": str(row.get("code") or ""),
        "name": str(row.get("name") or ""),
        "screener": str(row.get("screener") or ""),
        "qty": int(row.get("qty") or 0),
        "entry_ts": str(row.get("ts") or ""),
        "entry_price": _to_float(row.get("price")),
    }
    for key in ("stop_pct", "tp_pct", "kelly_f"):
        value = _to_float(row.get(key))
        if value is not None:
            record[key] = value
    return record


def summarise(closed: List[Dict[str, Any]]) -> Dict[str, Any]:
    """스크리너별·전체 실현 성과(원, %). 비어 있으면 0/None 으로 정직하게 둔다."""
    def block(rows: List[Dict[str, Any]]) -> Dict[str, Any]:
        nets = [float(r.get("net_pnl") or 0.0) for r in rows]
        rets = [float(r["ret_pct"]) for r in rows if r.get("ret_pct") is not None]
        wins = [n for n in nets if n > 0]
        losses = [n for n in nets if n < 0]
        return {
            "closed_n": len(rows),
            "net_pnl": round(sum(nets), 2),
            "gross_pnl": round(sum(float(r.get("gross_pnl") or 0.0) for r in rows), 2),
            "fees": round(sum(float(r.get("fees") or 0.0) for r in rows), 2),
            "win_rate": (round(100.0 * len(wins) / len(rows), 1) if rows else None),
            "avg_win": (round(sum(wins) / len(wins), 1) if wins else None),
            "avg_loss": (round(sum(losses) / len(losses), 1) if losses else None),
            "avg_win_pct": (round(sum(r for r in rets if r > 0) / len([r for r in rets if r > 0]), 3)
                            if [r for r in rets if r > 0] else None),
            "avg_loss_pct": (round(sum(r for r in rets if r < 0) / len([r for r in rets if r < 0]), 3)
                             if [r for r in rets if r < 0] else None),
        }

    by_screener: Dict[str, List[Dict[str, Any]]] = {}
    for row in closed:
        key = str(row.get("screener") or "unknown")
        by_screener.setdefault(key, []).append(row)
    return {
        "all": block(closed),
        "by_screener": {name: block(rows) for name, rows in sorted(by_screener.items())},
        "fees_known": all(float(r.get("fees") or 0) > 0 for r in closed) if closed else None,
    }


def build_payload(journal: Journal, day: str, account: str = "") -> Dict[str, Any]:
    """*day* (KST ``YYYY-MM-DD``) 의 청산/진입/요약을 한 페이로드로 만든다."""
    closed_raw = journal.closed_on(day)
    # 2026-11-10 실측: 체결되지 않은 진입 주문을 '진입가 청산·0원'으로 정리한 행
    # (exit_reason 이 `unfilled:` 로 시작)을 청산 거래로 세면, 시장에 존재한 적 없는
    # 0% 거래가 승률·기대값 표본에 섞여 기대값을 0 쪽으로 끌어올린다(통계 왜곡).
    # 그래서 환류에서는 제외한다 — 주문 시도 자체는 저널과 로그에 그대로 남는다.
    closed_raw = [
        row for row in closed_raw
        if not str(row.get("exit_reason") or "").startswith("unfilled:")
    ]
    skipped_unfilled = len(journal.closed_on(day)) - len(closed_raw)
    closed = [_closed_row(dict(row)) for row in closed_raw]
    opened = []
    for row in journal.open_trades():
        entry_ts = str(row.get("ts") or "")
        if entry_ts.startswith(day):
            opened.append(_open_row(dict(row)))
    payload: Dict[str, Any] = {
        "generated_at": datetime.now(KST).isoformat(timespec="seconds"),
        "source": "trader-agent",
        "date": day,
        "closed": closed,
        "opened": opened,
        "summary": summarise(closed),
        "skipped_unfilled": skipped_unfilled,
    }
    if account:
        payload["account"] = str(account)
    return payload


def _copy_journal(path: str, workdir: str) -> Optional[str]:
    """저널(+WAL/SHM)을 임시 디렉터리로 복사하고 복사본 경로를 돌려준다.

    drvfs(/mnt/c) 위 SQLite-WAL 직접 읽기는 ``disk I/O error`` 가 날 수 있고,
    실행 중인 루프와 락을 다투지 않기 위해서도 복사본을 쓴다. 파일이 없으면 None.
    """
    if not os.path.exists(path):
        return None
    target = os.path.join(workdir, "journal_copy.sqlite3")
    for suffix in ("", "-wal", "-shm"):
        source = path + suffix
        if os.path.exists(source):
            shutil.copy2(source, target + suffix)
    return target


def export_day(journal_path: str, out_dir: str, day: str, account: str = "",
               dry_run: bool = False) -> Optional[Dict[str, Any]]:
    """하루치를 내보낸다(원자적 교체). 복사 실패/저널 없음이면 None."""
    with tempfile.TemporaryDirectory(prefix="fills_") as tmp:
        copy_path = _copy_journal(journal_path, tmp)
        if copy_path is None:
            return None
        journal = Journal(copy_path)
        try:
            payload = build_payload(journal, day, account)
        finally:
            journal.close()
    text = json.dumps(payload, ensure_ascii=False, indent=1)
    if dry_run:
        print(text)
        return payload
    os.makedirs(out_dir, exist_ok=True)
    target = os.path.join(out_dir, "fills_{0}.json".format(day))
    tmp_target = target + ".tmp"
    with open(tmp_target, "w", encoding="utf-8") as handle:
        handle.write(text)
    os.replace(tmp_target, target)
    return payload


def main(argv: Optional[List[str]] = None) -> int:
    ap = argparse.ArgumentParser(description="트레이더 체결·손익 → analyist_dd 환류 파일")
    ap.add_argument("--journal", default=DEFAULT_JOURNAL)
    ap.add_argument("--out-dir", default=DEFAULT_OUT_DIR)
    ap.add_argument("--date", default=None, help="YYYY-MM-DD (기본: 오늘 KST)")
    ap.add_argument("--days", type=int, default=1, help="최근 N일 각각 내보내기")
    ap.add_argument("--account", default="", help="계좌번호(선택, 감사용)")
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args(argv)

    base = (datetime.fromisoformat(args.date).date() if args.date
            else datetime.now(KST).date())
    written, empty_days = 0, []
    for offset in range(max(0, args.days - 1), -1, -1):
        day = (base - timedelta(days=offset)).isoformat()
        payload = export_day(args.journal, args.out_dir, day, args.account,
                             dry_run=args.dry_run)
        if payload is None:
            print("[export_fills] 저널 없음/읽기 실패: {0}".format(args.journal))
            return 2
        closed_n = len(payload["closed"])
        if closed_n or payload["opened"]:
            written += 1
        else:
            empty_days.append(day)
        print("[export_fills] {0}: 청산 {1}건 · 보유 {2}건 · 순손익 {3}".format(
            day, closed_n, len(payload["opened"]),
            payload["summary"]["all"]["net_pnl"]))
    if empty_days and written == 0:
        print("[export_fills] 내보낼 체결 없음 (빈 날: {0})".format(", ".join(empty_days)))
    return 0


if __name__ == "__main__":
    sys.exit(main())

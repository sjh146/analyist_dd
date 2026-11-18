#!/usr/bin/env python3
"""크레온 당일 상승률 랭킹 → 단타 유니버스 압축 파일 (docs/spec_rank_universe.md).

WHY (2026-11-18 사용자 선택 1번): 크레온 `CpSvrNew7043`(상승률순위)는 `bridge/signals.py`
로 이식돼 `GET /rank` 로 조회되지만, 라이브 루프는 analyist_dd 피드(`screener_latest.json`)
를 소비하므로 이 랭킹이 **매매에 쓰이지 않았다**(`trader_core/rankfeed.py` 는 `--feed rank`
전용). 여기서는 랭킹을 **후보 유니버스 압축에만** 사용한다 — 매수 판단(칼만 추세·챔피언
모델·거래량·변동성·엣지 게이트)은 기존 단타스크리너·트레이더 규칙 그대로다.

동작:
  1. `GET {bridge}/rank?direction=up&period=day&market=0&pct_from=..&pct_to=..&pages=..`
     (읽기 전용 — 주문/잔고/체결 API 는 호출하지 않는다)
  2. 코드 정규화(6자리 숫자, `A` 접두 제거, 중복 제거) + 랭킹 순서 보존
  3. `data/state/rank_universe.json` 원자적 쓰기(tmp + os.replace)

fail-open: 브리지 미접속·타임아웃·`ok:false`·빈 랭킹이면 `ok:false` + `reason` + `codes: []`
를 쓰고 **exit 0** (크론 체인을 끊지 않는다). `--strict` 면 exit 1.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
import tempfile
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timedelta, timezone

KST = timezone(timedelta(hours=9))
CODE_RE = re.compile(r"^\d{6}$")
DEFAULT_OUT = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    "data", "state", "rank_universe.json",
)


def normalize_code(value) -> str:
    """'A003780' / ' 003780 ' / 3780 → '003780' (불가하면 '')."""
    text = str(value or "").strip().lstrip("Aa")
    text = text.strip()
    if text.isdigit() and len(text) < 6:
        text = text.zfill(6)
    return text if CODE_RE.match(text) else ""


def build_query(pct_from: float, pct_to: float, pages: int,
                direction: str, market: str) -> str:
    # 브리지는 pct_from/pct_to 를 **정수**로 요구한다(실측 2026-11-18:
    # '2.0' → {"code":"invalid_input","message":"pct_from must be an integer"}).
    return urllib.parse.urlencode({
        "direction": direction,
        "period": "day",
        "market": market,
        "pct_from": int(round(float(pct_from))),
        "pct_to": int(round(float(pct_to))),
        "pages": int(pages),
    })


def fetch_rank(url: str, timeout: float = 20.0) -> dict:
    req = urllib.request.Request(url, headers={"Accept": "application/json"})
    with urllib.request.urlopen(req, timeout=timeout) as resp:  # noqa: S310 (사내 브리지)
        return json.loads(resp.read().decode("utf-8"))


def parse_rank(payload: dict | None) -> tuple[list[dict], list[str]]:
    """브리지 응답 → (items, codes). 랭킹 순서를 보존하고 코드 중복을 제거한다."""
    if not isinstance(payload, dict) or not payload.get("ok"):
        return [], []
    rows = payload.get("rank") or []
    items: list[dict] = []
    codes: list[str] = []
    seen: set[str] = set()
    for row in rows:
        if not isinstance(row, dict):
            continue
        code = normalize_code(row.get("code"))
        if not code or code in seen:
            continue
        seen.add(code)
        codes.append(code)
        items.append({
            "code": code,
            "name": str(row.get("name") or ""),
            "diff": row.get("diff"),
            "volume": row.get("volume"),
            "rank": row.get("rank"),
        })
    return items, codes


def write_json_atomic(path: str, payload: dict) -> None:
    directory = os.path.dirname(os.path.abspath(path)) or "."
    os.makedirs(directory, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=directory, prefix=".rank_universe.", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            json.dump(payload, fh, ensure_ascii=False, indent=1, sort_keys=True)
            fh.write("\n")
        os.replace(tmp, path)
    finally:
        if os.path.exists(tmp):
            os.unlink(tmp)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="크레온 상승률 랭킹 → 단타 유니버스 압축")
    ap.add_argument("--bridge-url", default=os.environ.get("BRIDGE_URL", "http://127.0.0.1:8100"))
    ap.add_argument("--pct-from", type=float, default=2.0,
                    help="상승률 하한(%%). 기본 2 — 1%% 미만 노이즈 제외")
    ap.add_argument("--pct-to", type=float, default=12.0,
                    help="상승률 상한(%%). 기본 12 — 상한가/급등 추격 회피")
    ap.add_argument("--pages", type=int, default=7, help="연속조회 페이지 수(30건/페이지)")
    ap.add_argument("--chase-from", type=float, default=12.0,
                    help="추격 배제 대역 하한(%%). 이 대역 종목은 유니버스에서 제외")
    ap.add_argument("--chase-to", type=float, default=30.0,
                    help="추격 배제 대역 상한(%%). 상한가(+30%%) 포함")
    ap.add_argument("--no-chase", action="store_true", help="추격 배제 대역 조회 생략")
    ap.add_argument("--direction", default="up", choices=("up", "down"))
    ap.add_argument("--market", default="0", help="0=전체, 1=거래소, 2=코스닥")
    ap.add_argument("--out", default=DEFAULT_OUT)
    ap.add_argument("--timeout", type=float, default=20.0)
    ap.add_argument("--quiet", action="store_true")
    ap.add_argument("--strict", action="store_true", help="실패 시 exit 1 (기본 fail-open)")
    ap.add_argument("--url", default=None, help="(테스트용) 완성된 URL 강제")
    args = ap.parse_args(argv)

    url = args.url or (
        args.bridge_url.rstrip("/") + "/rank?" +
        build_query(args.pct_from, args.pct_to, args.pages, args.direction, args.market)
    )
    params = {
        "bridge_url": args.bridge_url, "pct_from": args.pct_from, "pct_to": args.pct_to,
        "pages": args.pages, "direction": args.direction, "market": args.market,
    }

    items: list[dict] = []
    codes: list[str] = []
    ok = False
    reason = ""
    excl_items: list[dict] = []
    excluded: list[str] = []
    try:
        payload = fetch_rank(url, timeout=args.timeout)
        items, codes = parse_rank(payload)
        if codes:
            ok = True
        else:
            reason = "빈 랭킹(상승률 구간에 종목 없음 또는 ok:false)"
            if not isinstance(payload, dict) or not payload.get("ok"):
                reason = "bridge ok:false (" + str((payload or {}).get("error") or "")[:80] + ")"
    except (urllib.error.URLError, TimeoutError, OSError, ValueError) as exc:
        reason = "%s: %s" % (type(exc).__name__, str(exc)[:120])

    # 추격 배제 대역(기본 +12~30%): 이미 많이 오른 종목을 유니버스에서 뺀다.
    # 단타스크리너 상위는 '20일 칼만 추세+모델'로 뽑혀 당일 등락률과 무관하므로
    # 교집합(압축)이 아니라 **배제 필터**로 쓰는 것이 실측(교집합 0건)에 맞다.
    if not args.no_chase:
        chase_url = args.url or (
            args.bridge_url.rstrip("/") + "/rank?" +
            build_query(args.chase_from, args.chase_to, args.pages,
                        args.direction, args.market)
        )
        try:
            chase_payload = fetch_rank(chase_url, timeout=args.timeout)
            excl_items, excluded = parse_rank(chase_payload)
        except (urllib.error.URLError, TimeoutError, OSError, ValueError) as exc:
            excluded = []
            reason = (reason + " | " if reason else "") + "chase 조회 실패: %s" % (
                str(exc)[:80])

    out = {
        "ok": ok,
        "reason": reason,
        "generated_ts": datetime.now(KST).isoformat(timespec="seconds"),
        "params": params,
        "n_codes": len(codes),
        "codes": codes,
        "items": items,
        "n_excluded": len(excluded),
        "excluded_codes": excluded,
        "excluded_items": excl_items,
        "band": {"pct_from": args.pct_from, "pct_to": args.pct_to,
                 "chase_from": args.chase_from, "chase_to": args.chase_to},
        "claim": {
            "source_rows": len(items),
            "claimed_rows": len(codes),
            "persisted_rows": len(codes),
            "dq": "not_called: 유니버스 파일 산출(계측 대상 아님)",
        },
    }
    write_json_atomic(args.out, out)

    if not args.quiet:
        if ok:
            print("rank_universe ok codes=%d pct=%.0f..%.0f out=%s"
                  % (len(codes), args.pct_from, args.pct_to, args.out))
        else:
            print("rank_universe FAIL-OPEN codes=0 reason=%s out=%s" % (reason, args.out))
    if args.strict and not ok:
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())

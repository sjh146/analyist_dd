#!/usr/bin/env python3
"""시장 레짐 판정 (docs/spec_regime_filter.md) — index_daily 기반 하락장 롱단타 게이트 상태.

WHY (스펙 인용 + 실측 — 2026-11-20):
  docs/EXP_rule_expectancy_verdict.md 실측: 2026년 KOSDAQ −31.7%(945 → 646),
  유니버스 기준선 −0.691%/일(비용 후), 상승 추격 규칙 R3 −0.054% / R4 −0.043% /
  R5 −0.251% 전부 음, 유일한 (+)는 급락 후 매수 R8 +0.789%(t 1.38).
  즉 "레짐이 나쁠 때 롱 진입을 계속하는 것 자체가 손실의 주원인" → 진입을 멈추는
  최소 장치(daytrading_screener 게이트가 읽을 상태 파일)만 만든다. 전략 교체는 별도 스펙.

실측 근거 (DB 조회 2026-11-20 — 접속은 프로세스 환경변수만, 비밀번호 출력·기록 금지):
  -- SELECT index_code, COUNT(*), MIN(trade_date), MAX(trade_date)
  --   FROM index_daily GROUP BY index_code ORDER BY 1;
  -- → KOSPI 459행 · KOSDAQ 459행, 창 2025-01-02 ~ 2026-11-20 (백필 완료)
  -- SELECT trade_date, close FROM index_daily
  --   WHERE index_code='KOSDAQ' AND trade_date <= '2026-11-20'
  --   ORDER BY trade_date DESC LIMIT 21;
  -- → 최신 close=645.68(2026-11-20) → 계산: ma20=695.659,
  --   ret5=(645.68/699.66−1)×100 = −7.715% → close<ma20 AND ret5≤−3% → risk_off
  --   (스펙 §4 기대 그대로).

계약 (스펙 §1):
  close = asof 이하 최근 거래일 종가, ma20 = 최근 20거래일 단순평균,
  ret5 = 5거래일 수익률(%), state = risk_off if (close < ma20) or (ret5 ≤ −3%)
  else risk_on. 지수 데이터가 없으면 state="unknown" + 사유 — 0 채움·추정 금지.
  ma20 계산에 행이 부족하면 ma20=None, 급락 신호도 없으면 unknown(스크리너는
  fail-open 이므로 "모른다"고 답하는 것이 안전하다).

멱등: --json-out 을 tmp+os.replace 원자적 저장 — 재실행 시 내용 동일, DB 는 읽기 전용.
자기신고: dq_claim.record_claim 재사용(3분리) — source=DB가 준 행수, claimed=파서가
  만든 레짐 레코드 수(0/1), persisted=JSON 실제 저장 건수(0/1). 실패해도 판정은
  깨지 않는다(dq_claim 규약). 유일한 DB 쓰기는 이 dq_runner_claim 자기신고 행뿐이다.

사용:
  python3 scripts/market_regime.py --asof 2026-11-20
  python3 scripts/market_regime.py --index KOSPI --ma 20 --asof 2026-11-20
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import date, datetime, timedelta, timezone

import psycopg2

try:
    from zoneinfo import ZoneInfo

    KST = ZoneInfo("Asia/Seoul")
except Exception:  # pragma: no cover
    KST = timezone(timedelta(hours=9))

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
if SCRIPT_DIR not in sys.path:
    sys.path.insert(0, SCRIPT_DIR)
import dq_claim  # noqa: E402 — record_claim(자기신고 3분리 — 스펙 규칙 ④)

RUNNER = "market_regime"
TABLE = "market_regime_state"
DEFAULT_JSON_OUT = os.path.join(REPO, "data", "state", "market_regime.json")


def connect():
    """DB 접속 — 좌표·자격증명은 프로세스 환경변수만(비밀번호 출력·파일기록 금지)."""
    return psycopg2.connect(
        host=os.environ.get("POSTGRES_HOST", "127.0.0.1"),
        port=int(os.environ.get("POSTGRES_PORT", "5434")),
        user=os.environ["POSTGRES_USER"], password=os.environ["POSTGRES_PASSWORD"],
        dbname=os.environ.get("POSTGRES_DB", "stock_trading"), connect_timeout=15)


def fetch_closes(conn, index, asof, ma):
    """index_daily → asof 이하 종가 리스트(오름차순, 마지막이 최신). DB 읽기 전용.

    ret5 계산에 선행 5거래일이 더 필요하므로 ma+6 행까지 가져온다. 부족하면
    compute_regime 이 unknown 처리(결측을 0/추정으로 메우지 않음 — 스펙 §5).
    """
    cur = conn.cursor()
    try:
        cur.execute(
            "SELECT close FROM index_daily"
            " WHERE index_code = %s AND trade_date <= %s"
            " ORDER BY trade_date DESC LIMIT %s",
            (index, asof, int(ma) + 6))
        rows = [float(r[0]) for r in cur.fetchall()]
    finally:
        cur.close()
    rows.reverse()
    return rows


def compute_regime(closes, ma=20):
    """종가 리스트(오름차순) → 레짐 판정 dict. 순수 함수(스펙 테스트 ①②③④ 대상).

    반환: {close, ma20, ret5, state, reason}
      · state="risk_off" — close<ma20 또는 ret5≤−3% (reason 에 해당 조건 나열)
      · state="risk_on"  — 둘 다 미해당
      · state="unknown"  — 데이터 없음 / ma20 계산 불가(행 부족) + 사유
    """
    n = len(closes)
    if n == 0:
        return {"close": None, "ma20": None, "ret5": None,
                "state": "unknown",
                "reason": "index_daily 데이터 없음(0행) — 결측 추정 금지"}
    close = closes[-1]
    ma20 = round(sum(closes[-ma:]) / ma, 3) if n >= ma else None
    ret5 = round((closes[-1] / closes[-6] - 1.0) * 100.0, 3) if n >= 6 else None
    hits = []
    if ma20 is not None and close < ma20:
        hits.append("close<ma20")
    if ret5 is not None and ret5 <= -3.0:
        hits.append("ret5<=-3%")
    if hits:
        return {"close": round(close, 2), "ma20": ma20, "ret5": ret5,
                "state": "risk_off", "reason": ",".join(hits)}
    if ma20 is None:  # hits 가 비었는데 ma20 도 없다 → 급락 신호도 없음 → 판정 불가
        return {"close": round(close, 2), "ma20": ma20, "ret5": ret5,
                "state": "unknown",
                "reason": "index_daily 행 부족(%d<%d) — ma%d 불가, 결측 추정 금지"
                          % (n, ma, ma)}
    return {"close": round(close, 2), "ma20": ma20, "ret5": ret5,
            "state": "risk_on", "reason": "ok"}


def write_json(payload, path):
    """원자적 저장(tmp+os.replace) — 재실행 안전(멱등) + 폴링 중 반쪽 파일 방지."""
    path = os.path.abspath(path)
    parent = os.path.dirname(path)
    if parent:
        os.makedirs(parent, exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=1)
    os.replace(tmp, path)
    return path


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="시장 레짐 판정 (docs/spec_regime_filter.md)")
    ap.add_argument("--index", default="KOSDAQ")
    ap.add_argument("--ma", type=int, default=20)
    ap.add_argument("--asof", default=None, help="기준일 YYYY-MM-DD (기본: 오늘 KST)")
    ap.add_argument("--json-out", default=DEFAULT_JSON_OUT)
    args = ap.parse_args(argv)
    if args.ma < 2:
        ap.error("--ma 는 2 이상이어야 합니다")
    asof = args.asof or datetime.now(KST).date().isoformat()
    try:
        date.fromisoformat(asof)
    except ValueError:
        ap.error("--asof 는 YYYY-MM-DD 형식이어야 합니다")

    conn = connect()
    try:
        closes = fetch_closes(conn, args.index, asof, args.ma)
    finally:
        conn.close()
    reg = compute_regime(closes, ma=args.ma)
    payload = {"asof": asof, "index": args.index, "close": reg["close"],
               "ma20": reg["ma20"], "ret5": reg["ret5"],
               "state": reg["state"], "reason": reg["reason"]}
    write_json(payload, args.json_out)
    print("[regime] %s asof=%s close=%s ma20=%s ret5=%s -> %s (%s)"
          % (args.index, asof, reg["close"], reg["ma20"], reg["ret5"],
             reg["state"], reg["reason"]), flush=True)
    _claim(source=len(closes), claimed=1, persisted=1,
           note="index=%s asof=%s state=%s" % (args.index, asof, reg["state"]))
    return 0


def _claim(source, claimed, persisted, note):
    """자기신고(3분리 — 스펙 규칙 ④) — dq_claim.record_claim 재사용.

    source=DB가 준 행수 / claimed=파서가 만든 레짐 레코드 수(0/1) /
    persisted=JSON 실제 저장 건수. 커넥션은 단명(장수명 트랜잭션 교착 사고
    2026-09-28 재발 방지 — index_backfill._claim 과 동일 규약), 실패해도 판정은
    깨지 않는다(stderr 경고 후에도 [claim] 줄은 남긴다).
    """
    try:
        conn = connect()
        try:
            dq_claim.record_claim(conn, RUNNER, TABLE, claimed_rows=claimed,
                                  persisted_rows=persisted, source_rows=source,
                                  note=note)
        finally:
            conn.close()
    except Exception as exc:                     # noqa: BLE001 — 판정을 깨지 않는다
        print("[claim] %s: 자기신고 실패(%s: %s)" % (RUNNER, type(exc).__name__, exc),
              file=sys.stderr)
    print("[claim] %s -> %s source=%d claimed=%d persisted=%d"
          % (RUNNER, TABLE, source, claimed, persisted), flush=True)


if __name__ == "__main__":
    sys.exit(main())

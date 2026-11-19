#!/usr/bin/env python3
"""재배포 문턱 게이트 — 측정된 양(+) 엣지 없이는 배포하지 않는다 (docs/REDEPLOY_THRESHOLD.md).

WHY (정책, 2026-11-19 확정 — 대표 결정 C-1 채택): 새 전략·파라미터·모델을 실계좌에
배포(promote)하려면 아래 두 축이 **동시에** 양(+)이어야 한다. 그때의 실측은 둘 다 음(-)이었다:
  - 라이브 누적(2026-11-08~11-18, 15건): −39,829.5원(진입원금 1,044,598원 대비 −3.81%/거래,
    승률 15.4%) → 추가 배포는 기대값이 측정되지 않은 베팅.
  - 챔피언 OOS: 세션 기대 −0.182%(t −0.81, 87세션), 최신 walk-forward AUC 0.5039.
  - 2026-11-19은 새 규칙 첫날이고 3건 +4,688원(수수료 전) — 표본 3건으로 규칙을 또 바꾸면
    '규칙 변경 → 표본 리셋 → 판정 불가'의 악순환을 반복한다.
이 게이트는 **자동 차단 장치가 아니라 증거 요구 장치**다(실주문 경로에 자동 삽입 금지 —
운영 정지 위험). 배포 기록에 판정 JSON 을 남기는 데 쓴다.

규칙(양보 불가):
  A. 라이브 실측: `--since` 이후 **청산 표본 ≥ --min-trades(20)** AND **순수익 > 0**
     (수수료 포함, 저널 `pnl` 합)
  B. 챔피언 OOS: **세션 기준 순기대 > 0** AND **t ≥ 1**
     — 세션 지표 산출물이 없으면 측정 불가 = 배포 금지(추정으로 통과시키지 않는다).
  둘 중 하나라도 미달 → BLOCK(매매 자체는 계속, 신규 배포만 금지).

실측 근거(2026-11-19 구현 시각, 아래 조회 명령 그대로 실행해 확인 — 저널은 /tmp 복사 후
mode=ro, 원본 직접 열기 금지):
  # cp "/mnt/c/Users/jhshi/analyist_dd/trader-agent/journal/trade_journal.sqlite3"{,"-wal","-shm"} /tmp/rg_probe/
  # python3 -c "import sqlite3; sqlite3.connect('file:/tmp/rg_probe/trade_journal.sqlite3?mode=ro', uri=True)"
  select count(*), coalesce(sum(pnl),0) from trades
   where exit_ts is not null and coalesce(exit_reason,'') not like 'unfilled:%'
     and exit_ts >= '2026-11-19';
  → (3, 4687.999600000006)  — 정책 예시의 live.n=3·net_pnl=4688.0 과 일치(반올림 후)
  select count(*) from trades
   where exit_ts is not null and coalesce(exit_reason,'') not like 'unfilled:%'
     and pnl > 0 and exit_ts >= '2026-11-19';
  → 2  — win_pct 66.7, 정책 예시와 일치
  # OOS 세션 지표는 이 파일이 만들지 않는다. --session-file 로 지정한 산출물을 읽는다.
  # repo 내 실측 산출물(정책 문서 "세션 기대 −0.182%(t −0.81, 87세션)"의 출처):
  #   reports/model_metric_protocol_audit_results.json (2026-10-02 산출)
  #   c_net_expectancy.champion = {n_rows_scored: 1340, n_sessions: 87,
  #                                avg_pct: -0.182, t_stat: -0.81}
  # walk-forward 적립 원장(기본 --oos-file): data/reports/oos_accrual.jsonl 마지막 행
  #   measured_at=2026-11-18T22:16:47 robust_auc=0.5039 source=cg352_champion_s0.json

사용:
  python3 scripts/redeploy_gate.py [--since 2026-11-19] [--min-trades 20]
          [--oos-file data/reports/oos_accrual.jsonl] [--session-file PATH]
          [--json-out data/reports/redeploy_gate.json] [--quiet]
  종료 코드: 0 = PASS(배포 가능) · 1 = BLOCK · 2 = 입력 오류(저널 없음 등)

성질:
  - 읽기 전용: 저널은 /tmp 복사 후 mode=ro(scripts/trader_cycle.py::_journal_copy 와 같은
    관례 — WSL drvfs+WAL 함정 회피), OOS·세션 파일은 read-only.
  - 멱등: 산출물에 실행 시각을 남기지 않는다(같은 입력 → 같은 파일 내용, 원자적 덮어쓰기).
  - 결측은 null + 사유. 0 으로 메우지 않는다.
  - 자기신고(scripts/dq_claim.py::record_claim 재사용, 3분리):
      source_rows    = 저널 전체 trades 행수(소스 수신)
      claimed_rows   = 청산 표본 필터 + since 창 적용 후 행수(파서가 판정에 쓴 행)
      persisted_rows = json-out 산출물에 실제 기록된 live.n(실제 저장 — 기록 후 재읽어 대조)
    자기신고 실패는 판정을 깨지 않는다(stderr 로만 알린다). 테스트는 REDEPLOY_GATE_NO_CLAIM=1.
"""
from __future__ import annotations

import argparse
import json
import os
import shutil
import sqlite3
import sys
import tempfile
from datetime import datetime

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
PROJ = os.path.dirname(SCRIPT_DIR)
sys.path.insert(0, SCRIPT_DIR)  # dq_claim(자기신고 헬퍼)은 scripts/ 에 있다

# ── 입력 좌표 ─────────────────────────────────────────────────────────────
# 실주문 경로 트레이더 저널 — **직접 열지 않고** /tmp 복사 후 mode=ro 로만 읽는다.
JOURNAL = "/mnt/c/Users/jhshi/analyist_dd/trader-agent/journal/trade_journal.sqlite3"
DEFAULT_SINCE = "2026-11-19"
DEFAULT_MIN_TRADES = 20
DEFAULT_OOS_FILE = "data/reports/oos_accrual.jsonl"
DEFAULT_JSON_OUT = "data/reports/redeploy_gate.json"

# 청산 표본 정의 — scripts/trader_cycle.py::_REAL_CLOSED · 계기판(quant_scoreboard.py)과
# 같은 정의. WHY(2026-11-19 수리): `unfilled:` 마커 행은 포지션이 아니었던 주문의 종결
# 기록이라 청산 표본이 아니다(넣으면 0원 거래가 패배로 세어져 승률·기대값이 왜곡된다).
REAL_CLOSED = "exit_ts is not null and coalesce(exit_reason,'') not like 'unfilled:%'"


class GateInputError(Exception):
    """입력 오류 — 종료 코드 2. 판정 대상이 없거나 형식이 틀린 경우만 쓴다."""


def _resolve(path: str | None) -> str | None:
    """상대 경로는 리포 루트 기준으로 해석한다(cwd 와 무관 — 멱등성)."""
    if path is None:
        return None
    return path if os.path.isabs(path) else os.path.join(PROJ, path)


def _display_path(path: str) -> str:
    rel = os.path.relpath(path, PROJ)
    return rel if not rel.startswith("..") else path


# ── A. 라이브 실측 (저널) ─────────────────────────────────────────────────
def _journal_copy(journal_path: str = JOURNAL):
    """저널(+wal+shm)을 /tmp 로 복사해 mode=ro 로 연다. (conn, tmpdir) 또는 (None, 사유).

    WHY: /mnt/c 는 drvfs 이고 저널은 WAL 모드다. 원본을 직접 열면 `disk I/O error` 가 나거나
    -wal 에만 있는 최신 행이 안 보인다(실측 2026-09-28 — trader_cycle 과 같은 관례).
    """
    if not os.path.exists(journal_path):
        return None, f"journal 없음: {journal_path}"
    tmp = tempfile.mkdtemp(prefix="redeploy_gate_journal_")
    try:
        for suffix in ("", "-wal", "-shm"):
            src = journal_path + suffix
            if os.path.exists(src):
                shutil.copyfile(src, os.path.join(tmp, os.path.basename(journal_path) + suffix))
        dst = os.path.join(tmp, os.path.basename(journal_path))
        conn = sqlite3.connect(f"file:{dst}?mode=ro", uri=True, timeout=10)
        return conn, tmp
    except Exception as exc:  # noqa: BLE001 - 어느 경우에도 원본은 건드리지 않는다
        shutil.rmtree(tmp, ignore_errors=True)
        return None, f"journal 복사/열기 실패: {exc}"


def _measure_live(conn: sqlite3.Connection, since: str, min_trades: int):
    """A축: since 이후 청산 표본의 건수·순수익(pnl 합)·승률을 읽는다.

    순수익은 저널 `pnl` 합(수수료 반영분 — 정책 규칙 A "수수료 포함, 저널 pnl 합").
    exit_ts >= since 는 ISO 문자열 비교다(since 는 main 에서 YYYY-MM-DD 로 검증됨).
    """
    try:
        total = int(conn.execute("select count(*) from trades").fetchone()[0])
        n, net, wins = conn.execute(
            "select count(*),"
            "       coalesce(sum(pnl),0),"
            "       coalesce(sum(case when pnl > 0 then 1 else 0 end),0)"
            " from trades where " + REAL_CLOSED + " and exit_ts >= ?",
            (since,),
        ).fetchone()
    except sqlite3.Error as exc:
        raise GateInputError(f"저널 조회 실패: {exc}") from None
    n, net, wins = int(n), float(net), int(wins)
    live = {
        "n": n,
        "net_pnl": round(net, 2),
        "win_pct": round(wins / n * 100.0, 1) if n else None,
        "pass": False,
    }
    reasons = []
    if n < min_trades:
        reasons.append(f"표본 {n} < {min_trades}")
    if net <= 0:
        reasons.append(f"순수익 {net:+,.0f}원 ≤ 0")
    live["pass"] = bool(n >= min_trades and net > 0)
    return live, reasons, total


# ── B. 챔피언 OOS (walk-forward 적립 원장 + 세션 지표 산출물) ──────────────
def _read_oos_accrual(path: str):
    """walk-forward OOS 적립 원장(JSONL)의 마지막 행을 읽는다. (row, err).

    이 파일은 정책의 "최신 walk-forward AUC" 증거다. 측정 불가(파일 없음/파싱 불가)면
    (None, 사유) — 배포 금지 방향으로 보수적으로 판정한다.
    """
    if not os.path.exists(path):
        return None, f"{_display_path(path)}: 파일 없음"
    last = None
    try:
        with open(path, encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    last = json.loads(line)
                except json.JSONDecodeError:
                    last = None
    except OSError as exc:
        return None, f"{_display_path(path)}: 읽기 실패({exc})"
    if last is None:
        return None, f"{_display_path(path)}: 유효한 행 없음"
    return last, None


def _read_session_metrics(path: str):
    """세션 지표 산출물에서 (순기대 %, t, 세션수)를 읽는다. 실패 시 (None, 사유).

    지원 스키마(실측 근거 — 아래 파일이 실제로 이 형식이다):
      1) robust_oos_audit 형식: {"c_net_expectancy": {"champion": {"avg_pct":, "t_stat":,
         "n_sessions":}}} — reports/model_metric_protocol_audit_results.json 이 이 형식이고,
         정책 문서의 "세션 기대 −0.182%(t −0.81, 87세션)"은 이 파일 c_net_expectancy.champion
         의 실측값이다(2026-11-19 조회).
      2) 평면형: {"avg_pct":, "t_stat":} 또는 {"session_expectancy_pct":, "t_stat":}
    결측(키 없음/숫자 아님)은 0 으로 메우지 않고 측정 불가로 돌린다(정책: 추정 금지).
    """
    if not os.path.exists(path):
        return None, f"{_display_path(path)}: 파일 없음"
    try:
        with open(path, encoding="utf-8") as f:
            d = json.load(f)
    except (OSError, json.JSONDecodeError) as exc:
        return None, f"{_display_path(path)}: JSON 파싱 실패({exc})"
    block = d
    ce = d.get("c_net_expectancy")
    if isinstance(ce, dict):
        if "champion" in ce:
            block = ce["champion"]
        elif len(ce) == 1:
            block = next(iter(ce.values()))
        else:
            return None, "c_net_expectancy 에 champion 이 없고 모델이 여러 개"
    if not isinstance(block, dict):
        return None, "세션 지표 블록이 객체가 아님"
    exp = block.get("avg_pct", block.get("session_expectancy_pct"))
    t = block.get("t_stat")
    if not isinstance(exp, (int, float)) or not isinstance(t, (int, float)):
        return None, "세션 지표 결측(avg_pct/session_expectancy_pct 또는 t_stat 없음)"
    ns = block.get("n_sessions")
    return {"expectancy_pct": float(exp), "t_stat": float(t),
            "n_sessions": int(ns) if isinstance(ns, (int, float)) else None}, None


def _judge_oos(oos_path: str, session_path: str | None):
    """B축: 세션 순기대 > 0 AND t ≥ 1. 지표 산출물이 없으면 측정 불가 = 배포 금지.

    반환: (oos dict, 최상위 reasons 용 사유 목록, accrual 마지막 행 or None)
    """
    accrual, acc_err = _read_oos_accrual(oos_path)
    out = {"session_expectancy_pct": None, "t_stat": None, "source": None,
           "pass": False, "reason": None}
    reasons = []
    if session_path is None:
        reasons.append("OOS 세션 지표 없음")
        out["reason"] = "측정 불가"
    else:
        metrics, err = _read_session_metrics(session_path)
        if metrics is None:
            reasons.append(f"OOS 세션 지표 없음 ({err})")
            out["reason"] = "측정 불가"
        else:
            exp, t = metrics["expectancy_pct"], metrics["t_stat"]
            out["session_expectancy_pct"] = exp
            out["t_stat"] = t
            out["source"] = _display_path(session_path)
            fails = []
            if exp <= 0:
                fails.append(f"순기대 {exp:+.3f}% ≤ 0")
            if t < 1:
                fails.append(f"t {t:.2f} < 1")
            if fails:
                out["reason"] = "; ".join(fails)
                reasons.extend(f"OOS {f}" for f in fails)
            else:
                out["pass"] = True
    if accrual is None:
        # 측정 불가 = 배포 금지. accrual 부재도 사유로 남긴다(추정으로 통과 금지).
        tag = ("OOS accrual 파일 없음" if (acc_err or "").endswith("파일 없음")
               else f"OOS accrual 읽기 실패 ({acc_err})")
        reasons.append(tag)
        out["reason"] = (out["reason"] + "; " + tag) if out["reason"] else tag
        out["pass"] = False
    return out, reasons, accrual


# ── 판정 ─────────────────────────────────────────────────────────────────
def judge(journal_path: str, since: str, min_trades: int,
          oos_path: str, session_path: str | None):
    """A·B 두 축을 측정하고 판정을 조립한다. (result, source_rows, accrual_row)"""
    conn, tmp = _journal_copy(journal_path)
    if conn is None:
        raise GateInputError(tmp)
    try:
        live, live_reasons, source_rows = _measure_live(conn, since, min_trades)
    finally:
        conn.close()
        if isinstance(tmp, str) and os.path.isdir(tmp):
            shutil.rmtree(tmp, ignore_errors=True)
    oos, oos_reasons, accrual = _judge_oos(oos_path, session_path)
    verdict = "PASS" if (live["pass"] and oos["pass"]) else "BLOCK"
    result = {"since": since, "live": live, "oos": oos,
              "verdict": verdict, "reasons": live_reasons + oos_reasons}
    return result, source_rows, accrual


def _write_artifact(path: str, result: dict, claimed: int) -> int:
    """판정 JSON 을 원자적으로 쓴다(멱등 — 실행 시각 미기록, 같은 입력 → 같은 내용).

    쓴 뒤 다시 읽어 live.n 이 판정 표본(claimed)과 같은지 대조한다 — 자기신고 3분리 중
    '실제 저장' 검증. 어긋나면 판정은 유지하되 stderr 로 남긴다.
    """
    try:
        os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
        tmp = path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(result, f, ensure_ascii=False, indent=2)
            f.write("\n")
        os.replace(tmp, path)
    except OSError as exc:
        raise GateInputError(f"산출물 쓰기 실패: {exc}") from None
    with open(path, encoding="utf-8") as f:
        stored = (json.load(f).get("live") or {}).get("n")
    if stored != claimed:
        print(f"[redeploy_gate] 경고: 저장된 live.n={stored} != 판정 표본 {claimed}",
              file=sys.stderr)
    return int(stored)


# ── 자기신고 (scripts/dq_claim.py 재사용 — 소스 수신/파서 생성/실제 저장 3분리) ──
def _record_claim(source_rows: int, claimed: int, persisted: int, note: str) -> None:
    if os.environ.get("REDEPLOY_GATE_NO_CLAIM"):
        return
    try:
        import dq_claim  # scripts/ 에 있음(SYS_PATH 위에서 삽입)
        conn = dq_claim._open_conn()
        try:
            dq_claim.record_claim(
                conn, runner="redeploy_gate", table_name="redeploy_gate",
                claimed_rows=claimed, persisted_rows=persisted,
                source_rows=source_rows, note=note)
        finally:
            conn.close()
    except Exception as exc:  # noqa: BLE001 - 자기신고 실패가 판정을 깨면 안 된다(dq_claim 관례)
        print(f"[claim] redeploy_gate: 자기신고 실패({type(exc).__name__}: {exc})",
              file=sys.stderr)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(
        description="재배포 문턱 게이트 — A(라이브 실측)·B(챔피언 OOS) 모두 (+)일 때만 PASS. "
                    "0=PASS 1=BLOCK 2=입력 오류")
    ap.add_argument("--since", default=DEFAULT_SINCE,
                    help=f"이 날짜 이후 청산 표본만 센다 (기본 {DEFAULT_SINCE})")
    ap.add_argument("--min-trades", type=int, default=DEFAULT_MIN_TRADES,
                    help=f"청산 표본 최소 건수 (기본 {DEFAULT_MIN_TRADES})")
    ap.add_argument("--oos-file", default=DEFAULT_OOS_FILE,
                    help=f"walk-forward OOS 적립 원장 (기본 {DEFAULT_OOS_FILE})")
    ap.add_argument("--session-file", default=None,
                    help="챔피언 OOS 세션 지표 산출물. 없으면 측정 불가 = 배포 금지")
    ap.add_argument("--json-out", default=DEFAULT_JSON_OUT,
                    help=f"판정 JSON 출력 경로 (기본 {DEFAULT_JSON_OUT})")
    ap.add_argument("--quiet", action="store_true", help="콘솔 출력 억제")
    a = ap.parse_args(argv)

    try:
        datetime.strptime(a.since, "%Y-%m-%d")
    except ValueError:
        print(f"[redeploy_gate] 입력 오류: --since 형식 불량: {a.since!r} (YYYY-MM-DD)",
              file=sys.stderr)
        return 2
    if a.min_trades < 1:
        print(f"[redeploy_gate] 입력 오류: --min-trades 는 1 이상: {a.min_trades}",
              file=sys.stderr)
        return 2

    oos_path = _resolve(a.oos_file)
    session_path = _resolve(a.session_file)
    out_path = _resolve(a.json_out)
    try:
        result, source_rows, accrual = judge(JOURNAL, a.since, a.min_trades,
                                             oos_path, session_path)
        claimed = result["live"]["n"]
        persisted = _write_artifact(out_path, result, claimed)
    except GateInputError as exc:
        print(f"[redeploy_gate] 입력 오류: {exc}", file=sys.stderr)
        return 2

    _record_claim(source_rows, claimed, persisted,
                  note=(f"verdict={result['verdict']} since={a.since} "
                        f"live_n={claimed} min_trades={a.min_trades} "
                        f"oos={result['oos']['reason'] or '통과'}"))

    if not a.quiet:
        live = result["live"]
        oos = result["oos"]
        print(f"[redeploy_gate] since={a.since} min_trades={a.min_trades}")
        print(f"live: n={live['n']} net_pnl={live['net_pnl']:+,.0f}원 "
              f"win_pct={live['win_pct'] if live['win_pct'] is not None else 'n/a'}% "
              f"pass={live['pass']}")
        if accrual is not None:
            print(f"oos : {oos['reason'] or '통과'} · accrual 마지막 행 "
                  f"{accrual.get('measured_at')} robust_auc={accrual.get('robust_auc')}")
        else:
            print(f"oos : {oos['reason'] or '통과'}")
        print(f"verdict={result['verdict']} (exit "
              f"{'0' if result['verdict'] == 'PASS' else '1'})")
        print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if result["verdict"] == "PASS" else 1


if __name__ == "__main__":
    sys.exit(main())

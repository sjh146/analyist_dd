#!/usr/bin/env python3
"""
퀀트 **트레이더** 역할의 장외 자율 사이클 구동기.

WHY (2026-09-28, 사용자 지시 "리서처·모델엔지니어·트레이더가 협업하도록"):
세 역할 중 트레이더만 **구동기·백로그·원장이 없었다**. 그래서 협업 계약 3번(트레이더 →
리서처 환류)이 사람 손으로만 이뤄졌고(보드 문서의 "확인 필요(리서처 환류)" 항목), 리서처의
`affects_model` 자동 핸드오프 같은 코드 경로가 트레이더 쪽에는 없었다. 이 파일이 그 구멍을
메운다 — 리서처·엔지니어 사이클과 **같은 규약**(백로그 항목 = 재현 명령 + 수치 check, 판정은
산출물에서 직접, 자기신고 금지)을 그대로 따른다.

이 사이클이 하는 일 (한 틱 = 수 초, 비블로킹):
  1. **실현 성과 측정**(북극성=순손익 원): 트레이더에이전트 저널(WSL 에서 drvfs+WAL 로 직접
     못 읽으므로 /tmp 로 복사해서 읽는다) → 청산 건수·승률·기대값·수수료·보유·회전.
  2. **모델 핸드오프 검증**(계약 2번): 엔지니어 원장/백로그에서 폴드 평균±std·폴드 승률·
     purge·승격 dry-run 이 실제로 있는지 확인. 없으면 '자료 미비'로 보고하고 엔지니어 백로그에
     요구 항목을 남긴다(말로 된 승인 금지).
  3. **실행 계약 점검**: 피드 계약(필수 필드·신선도), 한도·킬스위치, 모의/실경로 분리.
  4. **환류**(계약 3번): 피드/데이터가 실제로 기여했는지·어떤 데이터가 필요한지를 리서처
     백로그로 넘긴다(`from_trader` 표식).
  5. 백로그 항목 실행(있으면 1개) — 각 항목은 스스로 재현 명령과 수치 check 를 갖는다.

사용:
  python3 scripts/trader_cycle.py --status          # 백로그·원장·북극성 현황
  python3 scripts/trader_cycle.py --tick            # 크론이 호출(수 초 내 종료)
  python3 scripts/trader_cycle.py --probe fees      # 단일 프로브만 실행(수치 한 줄)
  python3 scripts/trader_cycle.py --json            # 기계 판독용 스냅샷
"""
from __future__ import annotations

import argparse
import glob
import json
import os
import re
import shutil
import sqlite3
import subprocess
import sys
import tempfile
import time
from datetime import datetime, timedelta

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import model_engineer_cycle as base  # noqa: E402  (공용 가드·원장·락 로직 재사용)

PROJ = base.PROJ
TR_BACKLOG = os.path.join(PROJ, "docs/QUANT_TRADER_BACKLOG.json")
TR_LEDGER = os.path.join(PROJ, "data/reports/trader_ledger.jsonl")
TR_RUNTIME = os.path.join(PROJ, "data/reports/tr_cycle")
TR_LOGDIR = os.path.join(TR_RUNTIME, "logs")
FINDINGS = os.path.join(PROJ, "docs/QUANT_FINDINGS.md")
RESEARCH_BACKLOG = os.path.join(PROJ, "docs/QUANT_RESEARCH_BACKLOG.json")
MODEL_BACKLOG = os.path.join(PROJ, "docs/QUANT_MODEL_BACKLOG.json")
FEED_DIR = os.path.join(PROJ, "data/feed")
FEED_CONTRACT = os.path.join(PROJ, "docs/FEED_CONTRACT.md")

# 트레이더에이전트(실주문 경로) — 읽기 전용으로만 만진다.
TRADER_WS_WSL = "/mnt/c/Users/jhshi/analyist_dd/trader-agent"
JOURNAL = os.path.join(TRADER_WS_WSL, "journal/trade_journal.sqlite3")
LOOP_STATE = os.path.join(TRADER_WS_WSL, "loop_state.json")
RUNNER_LOG = os.path.join(TRADER_WS_WSL, "runner.log")
BRIDGE_SUP_LOG = os.path.join(TRADER_WS_WSL, "bridge_sup.log")
BRIDGE_URL = "http://127.0.0.1:8100"

# 공용 헬퍼를 트레이더 경로로 재바인딩(가드·락·원장 로직을 복제하지 않는다).
base.BACKLOG = TR_BACKLOG
base.LEDGER = TR_LEDGER
base.RUNTIME = TR_RUNTIME
base.LOGDIR = TR_LOGDIR
base.PIDFILE = os.path.join(TR_RUNTIME, "running.pid")
base.STATE = os.path.join(TR_RUNTIME, "state.json")
KST = base.KST
now_kst = base.now_kst
log = base.log
load_backlog = base.load_backlog

def save_backlog(b):
    """원본 들여쓰기를 유지하며 저장(공용 save_backlog 는 indent=2 고정)."""
    b["updated_at"] = now_kst().isoformat(timespec="seconds")
    _write_backlog(TR_BACKLOG, b)

load_ledger = base.load_ledger
append_ledger = base.append_ledger


# ── 저널 읽기 (WSL drvfs+WAL 함정 회피) ─────────────────────────────────────
def _journal_copy():
    """.sqlite3 + -wal + -shm 을 /tmp 로 복사해 연다. 실패하면 None.

    WHY: /mnt/c 는 drvfs 이고 저널은 WAL 모드다. 직접 열면 `disk I/O error` 가 나거나
    -wal 에만 있는 최신 행이 안 보인다(실측 2026-09-28: 방금 청산이 안 보였다).
    """
    if not os.path.exists(JOURNAL):
        return None, "journal 없음"
    tmp = tempfile.mkdtemp(prefix="tr_journal_")
    try:
        for suffix in ("", "-wal", "-shm"):
            src = JOURNAL + suffix
            if os.path.exists(src):
                shutil.copyfile(src, os.path.join(tmp, os.path.basename(JOURNAL) + suffix))
        dst = os.path.join(tmp, os.path.basename(JOURNAL))
        conn = sqlite3.connect(f"file:{dst}?mode=ro", uri=True, timeout=10)
        return conn, tmp
    except Exception as exc:  # noqa: BLE001 - 사이클은 어떤 경우에도 죽지 않는다
        shutil.rmtree(tmp, ignore_errors=True)
        return None, f"journal 복사/열기 실패: {exc}"


def _scalar(conn, sql, default=0):
    try:
        row = conn.execute(sql).fetchone()
        return row[0] if row and row[0] is not None else default
    except sqlite3.Error:
        return default


ARCHIVE_JOURNALS = sorted(glob.glob(os.path.join(TRADER_WS_WSL, "journal", "paper_*.sqlite3")))


def _archive_stats() -> dict | None:
    """은퇴한 모의 저널에서 청산 수·수수료·마지막 진입을 읽는다(출처 명시용).

    실계좌 저널(`trade_journal.sqlite3`)은 2026-09-28 신설이라 청산 0건 → 수수료·회전
    프로브가 n/a 가 된다. 모의 아카이브는 41건의 청산 이력을 갖고 있어 **수수료 반영 여부**와
    **마지막 진입 경과일**을 판정할 수 있다. 값은 실계좌 성과와 섞지 않는다.
    """
    best = None
    for src in ARCHIVE_JOURNALS:
        tmp = tempfile.mkdtemp(prefix="tr_arch_")
        try:
            for suffix in ("", "-wal", "-shm"):
                p = src + suffix
                if os.path.exists(p):
                    shutil.copyfile(p, os.path.join(tmp, os.path.basename(src) + suffix))
            conn = sqlite3.connect(f"file:{os.path.join(tmp, os.path.basename(src))}?mode=ro", uri=True, timeout=10)
            try:
                closed = _scalar(conn, "select count(*) from trades where exit_ts is not null")
                fees = _scalar(conn, "select coalesce(sum(fees),0) from trades")
                row = conn.execute("select ts from trades order by ts desc limit 1").fetchone()
                last = row[0] if row else None
            finally:
                conn.close()
        except Exception:  # noqa: BLE001
            continue
        finally:
            shutil.rmtree(tmp, ignore_errors=True)
        if not last:
            continue
        age = None
        try:
            age = round((now_kst() - datetime.fromisoformat(last)).total_seconds() / 86400.0, 2)
        except ValueError:
            pass
        cand = {"journal": os.path.basename(src), "closed": closed, "fees_sum": fees,
                "last_entry_ts": last, "last_entry_age_days": age}
        if best is None or (age or 1e9) < (best.get("last_entry_age_days") or 1e9):
            best = cand
    return best


def measure() -> dict:
    """북극성 + 리스크 지표를 **산출물에서 직접** 읽는다(자기신고 금지)."""
    st = {
        "ts": now_kst().isoformat(timespec="seconds"),
        "north": "실현 순손익(₩)",
        "source_journal": JOURNAL,
        "realized_krw": None,
        "closed": None,
        "win_rate": None,
        "expectancy_krw": None,
        "fees_sum": None,
        "open_positions": None,
        "last_entry_ts": None,
        "last_entry_age_days": None,
        "today_closed": None,
        "today_realized_krw": None,
        "journal_error": None,
    }
    conn, tmp = _journal_copy()
    if conn is None:
        st["journal_error"] = tmp
    else:
        try:
            today = now_kst().date().isoformat()
            st["closed"] = _scalar(conn, "select count(*) from trades where exit_ts is not null")
            st["realized_krw"] = _scalar(conn, "select coalesce(sum(pnl),0) from trades where exit_ts is not null")
            wins = _scalar(
                conn,
                "select count(*) from trades where exit_ts is not null and pnl > 0",
            )
            if st["closed"]:
                st["win_rate"] = wins / st["closed"]
                st["expectancy_krw"] = st["realized_krw"] / st["closed"]
            st["fees_sum"] = _scalar(conn, "select coalesce(sum(fees),0) from trades")
            st["open_positions"] = _scalar(conn, "select count(*) from trades where exit_ts is null")
            row = conn.execute(
                "select ts from trades order by ts desc limit 1"
            ).fetchone()
            if row and row[0]:
                st["last_entry_ts"] = row[0]
                try:
                    entered = datetime.fromisoformat(row[0])
                    st["last_entry_age_days"] = round(
                        (now_kst() - entered).total_seconds() / 86400.0, 2
                    )
                except ValueError:
                    pass
            st["today_closed"] = _scalar(
                conn,
                f"select count(*) from trades where exit_ts like '{today}%'",
            )
            st["today_realized_krw"] = _scalar(
                conn,
                f"select coalesce(sum(pnl),0) from trades where exit_ts like '{today}%'",
            )
        finally:
            conn.close()
            shutil.rmtree(tmp if isinstance(tmp, str) and os.path.isdir(tmp) else "", ignore_errors=True)
    # 실계좌 전환 직후처럼 **활성 저널이 비어 있으면** 회전·수수료 프로브가 'n/a' 로 죽어
    # T1·T2 가 "측정 불가"로 계속 실패한다(2026-09-29 실측). 모의 아카이브에서 같은 수치를
    # 읽어 값을 살리되 **출처를 명시**한다 — 북극성(순손익)은 실계좌 값만 쓴다.
    st["archive"] = None
    if not st.get("closed") and not st.get("last_entry_ts"):
        st["archive"] = _archive_stats()
    st.update(loop_health())
    return st


def loop_health() -> dict:
    """실행 경로 생존 상태(트레이더 고유 리스크 — 2026-09-28 5시간 무감시 사고 근거)."""
    out = {
        "loop_halt": None,
        "loop_failures": None,
        "loop_phase": None,
        "loop_updated_at": None,
        "loop_stale_min": None,
        "bridge_connected": None,
        "bridge_error": None,
    }
    try:
        with open(LOOP_STATE, encoding="utf-8") as f:
            s = json.load(f)
        out["loop_halt"] = bool(s.get("loop", {}).get("halt"))
        out["loop_failures"] = s.get("check", {}).get("consecutive_failures")
        out["loop_phase"] = (s.get("last_cycle") or {}).get("phase")
        out["loop_updated_at"] = s.get("updated_at")
        if s.get("updated_at"):
            try:
                age = (now_kst() - datetime.fromisoformat(s["updated_at"])).total_seconds() / 60.0
                out["loop_stale_min"] = round(age, 1)
            except ValueError:
                pass
    except (OSError, json.JSONDecodeError) as exc:
        out["loop_error"] = str(exc)
    ok, payload, err = _win_curl(f"{BRIDGE_URL}/health", 20)
    out["bridge_connected"] = None
    if ok and isinstance(payload, dict):
        out["bridge_connected"] = bool(payload.get("connected"))
    else:
        out["bridge_error"] = err
    return out


def _win_curl(url: str, timeout_s: int = 25):
    """Windows 쪽 브리지(127.0.0.1:8100)를 WSL 에서 조회한다.

    WSL 은 Windows loopback 으로 못 가므로 interop 으로 cmd 의 curl 을 쓴다(가능할 때만).
    실패는 치명적이지 않다 — 브리지 관련 판정만 '알 수 없음'이 된다.
    """
    curl_exe = "/mnt/c/Windows/System32/curl.exe"
    if not os.path.exists(curl_exe):
        return False, None, "windows interop 없음"
    # ⚠ `--noproxy *` 는 필수다: Windows 시스템 프록시(192.168.196.145:8080)가
    # 127.0.0.1 요청까지 가로채 502/rc=1 을 돌려준다(2026-09-28 보드 기록과 같은 함정).
    # ⚠ interop 은 몇 초간 죽기도 한다(UtilAcceptVsock accept4 failed 110) — 재시도한다.
    last_err = ""
    for attempt in range(3):
        try:
            p = subprocess.run(
                [curl_exe, "-s", "--noproxy", "*", "-m", str(timeout_s), url],
                capture_output=True, timeout=timeout_s + 10,
            )
        except (OSError, subprocess.TimeoutExpired) as exc:
            last_err = f"curl 실행 실패: {exc}"
            time.sleep(1.5)
            continue
        raw = p.stdout or b""
        text = ""
        for enc in ("utf-8", "cp949"):
            try:
                text = raw.decode(enc)
                break
            except UnicodeDecodeError:
                text = raw.decode(enc, errors="replace")
        if p.returncode == 0 and (text or "").strip():
            try:
                return True, json.loads(text), None
            except json.JSONDecodeError as exc:
                return False, None, f"JSON 아님: {exc}"
        last_err = (text or "").strip()[:120] or f"rc={p.returncode}"
        time.sleep(1.5)
    return False, None, last_err


# ── 계약 2번: 모델엔지니어 → 트레이더 검증 성적표 확인 ──────────────────────
def _read_jsonl(path: str, limit: int | None = None) -> list:
    """JSONL 을 직접 읽는다(base.load_ledger 는 자기 LEDGER 경로만 본다)."""
    if not os.path.exists(path):
        return []
    out = []
    with open(path, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                out.append(json.loads(line))
            except json.JSONDecodeError:
                continue
    return out[-limit:] if limit else out


def verify_model_handoff() -> dict:
    """엔지니어 산출물에 계약 필드가 실제로 있는지 본다(없으면 트레이더는 판정 불가)."""
    out = {
        "ledger_rows": 0,
        "last_ledger": None,
        "has": {"folds": False, "mean_std": False, "fold_win_rate": False, "purge": False, "promote_dryrun": False},
        "promoted_auc": None,
        "gaps": [],
    }
    rows = _read_jsonl(os.path.join(PROJ, "data/reports/model_engineer_ledger.jsonl"), limit=50)
    out["ledger_rows"] = len(rows)
    blob = json.dumps(rows[-5:], ensure_ascii=False) if rows else ""
    if rows:
        out["last_ledger"] = {k: rows[-1].get(k) for k in ("ts", "id", "verdict", "detail")}
    for key in ("folds", "fold", "fold_auc"):
        if key in blob:
            out["has"]["folds"] = True
    if "std" in blob or "±" in blob:
        out["has"]["mean_std"] = True
    if "fold_win" in blob or "폴드 승률" in blob or "win_rate" in blob:
        out["has"]["fold_win_rate"] = True
    if "purge" in blob:
        out["has"]["purge"] = True
    if "dry-run" in blob or "dry_run" in blob or "dryrun" in blob:
        out["has"]["promote_dryrun"] = True
    for f in ("/home/jhshi/analyist_dd/models/champion/auc.txt",
              "/home/jhshi/analyist_dd/services/xgboost-ml/models/champion/auc.txt"):
        try:
            with open(f, encoding="utf-8") as fh:
                out["promoted_auc"] = float(fh.read().strip().split()[0])
            out["promoted_auc_source"] = f
            break
        except (OSError, ValueError):
            continue
    for name, present in out["has"].items():
        if not present:
            out["gaps"].append(name)
    return out


# ── 실행 계약 점검 (피드·한도·킬스위치) ─────────────────────────────────────
# 소비자(trader_core/feed.py::_parse_candidate)가 실제로 요구하는 필드.
# WHY(2026-09-29): 종전 값 ("code","name","score","screener") 은 소비자 JSON 키와 달랐고
# (실제 키는 stock_code/stock_name, screener 는 candidates 의 **키**), 무엇보다 블록 형태
# 후보를 아예 못 봤다 → 아래 _feed_strategy_blocks 참고.
FEED_REQUIRED = ("stock_code", "close_price", "score")
FEED_CODE_RE = re.compile(r"^[0-9]{6}$")
FEED_MAX_ERRORS = 40


def _feed_strategy_blocks(doc: dict) -> list:
    """피드 문서에서 (전략명, 후보 리스트) 쌍을 뽑는다 — 두 표현을 모두 지원한다.

    WHY(2026-09-29 실측): 발행물은 `candidates: {close: {items: [...]}, swing: {items: [...]}}`
    인데 점검기가 **최상위**에서만 `items`/`candidates` 를 찾고 리스트가 아니면 검사를
    건너뛰었다(`entry["items"]=None`) → 후보 40건이 한 번도 검사되지 않은 채 "위반 0건"으로
    보고됐다. **거짓 통과**다. 컨테이너가 dict 여도 `items` 를 꺼내 본다.
    """
    raw = doc.get("candidates")
    if isinstance(raw, list):
        return [("(flat)", raw)]
    if not isinstance(raw, dict):
        flat = doc.get("items") or doc.get("picks")
        return [("(flat)", flat if isinstance(flat, list) else [])]
    out = []
    for name, container in raw.items():
        if isinstance(container, list):
            out.append((str(name), container))
        elif isinstance(container, dict) and isinstance(container.get("items"), list):
            out.append((str(name), container["items"]))
        else:
            out.append((str(name), None))  # 형식 위반 — 위반으로 기록된다
    return out
FEED_FRESH_DAYS = 3.0


def feed_contract_check() -> dict:
    out = {"files": [], "violations": [], "max_age_days": None}
    for path in sorted(glob.glob(os.path.join(FEED_DIR, "*.json"))):
        entry = {"file": os.path.relpath(path, PROJ), "items": None, "age_days": None,
                 "strategies": {}, "errors": []}
        try:
            with open(path, encoding="utf-8") as f:
                d = json.load(f)
        except (OSError, json.JSONDecodeError) as exc:
            entry["errors"].append(f"읽기/JSON 실패: {exc}")
            out["files"].append(entry)
            continue
        generated = d.get("generated_at") or d.get("as_of")
        if generated:
            try:
                age = (now_kst() - datetime.fromisoformat(str(generated).replace("Z", "+00:00"))).total_seconds() / 86400.0
                entry["age_days"] = round(age, 2)
                out["max_age_days"] = max(out["max_age_days"] or 0, entry["age_days"])
            except ValueError:
                entry["errors"].append("generated_at 파싱 실패")
        total = 0
        for name, items in _feed_strategy_blocks(d):
            if items is None:
                entry["errors"].append(f"'{name}' 후보 컨테이너 형식 위반 (list 또는 {{items: [...]}} 아님)")
                continue
            entry["strategies"][name] = len(items)
            total += len(items)
            for it in items:
                if not isinstance(it, dict):
                    entry["errors"].append(f"'{name}' 후보가 객체가 아님")
                    continue
                code = str(it.get("stock_code") or "")
                tag = f"'{name}' code={code or '?'}"
                if not FEED_CODE_RE.match(code):
                    entry["errors"].append(f"{tag}: stock_code 형식 위반(6자리 숫자, A 접두어 금지)")
                try:
                    px = float(it.get("close_price"))
                except (TypeError, ValueError):
                    px = 0.0
                if px <= 0:
                    entry["errors"].append(f"{tag}: close_price 누락/0 (주문 지정가로 쓰임)")
                if it.get("score") is None:
                    entry["errors"].append(f"{tag}: score 누락")
                if str(it.get("score_kind") or "").strip() == "calibrated_prob" and it.get("ml_prob") is None:
                    entry["errors"].append(f"{tag}: score_kind=calibrated_prob 인데 ml_prob 없음")
                if len(entry["errors"]) >= FEED_MAX_ERRORS:
                    break
        entry["items"] = total
        if entry["errors"]:
            out["violations"].append({"file": entry["file"], "errors": entry["errors"][:5],
                                      "errors_total": len(entry["errors"])})
        out["files"].append(entry)
    return out


def safety_check() -> dict:
    """주문 경로 안전: 킬스위치·한도·모의/실경로 분리(읽기 전용 확인)."""
    out = {"kill_switch_files": [], "limits": {}, "notes": []}
    for rel in ("kill_switch.txt", "trader-agent/kill_switch.txt"):
        p = os.path.join(PROJ, rel)
        if os.path.exists(p):
            out["kill_switch_files"].append(rel)
    # config/strategy_params 에서 한도값만 읽는다(값이 없으면 '확인 불가'가 아니라 '미설정'으로 표기)
    for rel, key in (("trader-agent/strategy_params.json", "strategy_params"),
                     ("config/r2_limits.json", "r2_limits")):
        p = os.path.join(PROJ, rel)
        if os.path.exists(p):
            try:
                with open(p, encoding="utf-8") as f:
                    out["limits"][key] = json.load(f)
            except (OSError, json.JSONDecodeError) as exc:
                out["notes"].append(f"{rel} 읽기 실패: {exc}")
    return out


def _detect_indent(path: str, default: int = 2) -> int:
    """기존 파일의 JSON 들여쓰기를 재사용한다.

    WHY: `json.dump(indent=2)` 로 통째 다시 쓰면 기존 파일이 1칸 들여쓰기였을 때 diff 가
    수천 줄로 부풀어 리뷰가 불가능해진다(실측: QUANT_MODEL_BACKLOG.json 2,703+/2,221-).
    백로그를 쓰는 쪽은 원본 서식을 보존해야 한다.
    """
    try:
        with open(path, encoding="utf-8") as f:
            for line in f:
                if line.startswith("  "):
                    return len(line) - len(line.lstrip(" "))
                if line.strip().startswith('"'):
                    return default
    except OSError:
        pass
    return default


def _write_backlog(path: str, data: dict) -> None:
    """원본 들여쓰기를 유지하며 원자적으로 쓴다."""
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=_detect_indent(path))
    os.replace(tmp, path)


# ── 협업: 트레이더 결과를 리서처 백로그로 넘긴다 (계약 3번) ─────────────────
def handoff_to_research(title: str, detail: str, *, key: str, affects_model=False, priority=8):
    """리서처 백로그에 환류 항목을 추가한다(중복 방지 키: `from_trader`)."""
    try:
        with open(RESEARCH_BACKLOG, encoding="utf-8") as f:
            b = json.load(f)
    except (OSError, json.JSONDecodeError) as exc:
        log(f"환류 실패(리서처 백로그 읽기): {exc}")
        return None
    if any(i.get("from_trader") == key for i in b.get("items", [])):
        log(f"환류: {key} 는 이미 리서처 백로그에 있음(중복 방지)")
        return None
    new_id = "T" + str(len(b.get("items", [])) + 1)
    b["items"].append({
        "id": new_id,
        "title": title,
        "status": "backlog",
        "priority": priority,
        "from_trader": key,
        "created_at": now_kst().isoformat(timespec="seconds"),
        "affects_model": bool(affects_model),
        "hypothesis": None,
        "command": None,
        "metric": None,
        "check": None,
        "evidence": f"트레이더 사이클 환류: {detail}",
        "success": None,
        "cost": None,
        "note": "트레이더가 넘긴 항목 — 재현 명령과 수치 check 를 리서처가 채워야 실행된다.",
    })
    _write_backlog(RESEARCH_BACKLOG, b)
    with open(FINDINGS, "a", encoding="utf-8") as f:
        f.write(f"\n## [트레이더 환류 {key}] {title}  ({now_kst().strftime('%Y-%m-%d %H:%M')})\n"
                f"- 근거: {detail}\n"
                f"- 리서처 백로그: `{new_id}`\n")
    log(f"환류: 리서처 백로그에 {new_id} 추가 + QUANT_FINDINGS.md 기록")
    return new_id


def handoff_to_model(title: str, detail: str, *, key: str, priority=8):
    """엔지니어 백로그에 요구 항목을 남긴다(계약 2번 미비 시)."""
    try:
        with open(MODEL_BACKLOG, encoding="utf-8") as f:
            b = json.load(f)
    except (OSError, json.JSONDecodeError) as exc:
        log(f"핸드오프 실패(엔지니어 백로그 읽기): {exc}")
        return None
    if any(i.get("from_trader") == key for i in b.get("items", [])):
        return None
    new_id = "MT" + str(len(b.get("items", [])) + 1)
    b["items"].append({
        "id": new_id,
        "title": title,
        "status": "needs_setup",
        "priority": priority,
        "from_trader": key,
        "created_at": now_kst().isoformat(timespec="seconds"),
        "arm": None, "counterfactual": None, "baseline": None,
        "command": None, "metric": "wf_sweep_summary",
        "hypothesis": detail,
        "evidence": f"트레이더 사이클 요구: {detail}",
        "success": None, "expected": "미지", "cost": None,
        "note": "트레이더가 요구한 자료 — command·arm·counterfactual 을 엔지니어가 채워야 실행된다.",
    })
    _write_backlog(MODEL_BACKLOG, b)
    log(f"핸드오프: 엔지니어 백로그에 {new_id} 추가(자료 요구)")
    return new_id


def feed_signal_lag_days() -> float | None:
    """피드 후보의 `signal_date` 가 **가장 낡은 전략**의 경과일(일).

    WHY: 계약 검사(feed_contract_check)는 파일 mtime/generated_at 만 본다. 그래서 발행물이
    방금 쓰였어도 **후보의 근거 데이터가 낡으면 그대로 통과**한다. `close_price` 는 주문
    지정가로 그대로 쓰이므로, 오래된 종가가 지정가가 되면 체결 확률·슬리피지가 통째로 틀어진다.
    실측 2026-09-29 08:40 발행: close 20건 signal_date=2026-09-23(경과 6일, 시장 최신 거래일 9/28)
    vs swing 20건 2026-09-28(경과 1일).
    """
    path = os.path.join(FEED_DIR, "screener_latest.json")
    if not os.path.exists(path):
        cands = sorted(glob.glob(os.path.join(FEED_DIR, "*.json")))
        if not cands:
            return None
        path = cands[-1]
    try:
        with open(path, encoding="utf-8") as f:
            doc = json.load(f)
    except (OSError, json.JSONDecodeError):
        return None
    today = now_kst().date()
    worst = None
    blocks = doc.get("candidates") or {}
    if not isinstance(blocks, dict):
        return None
    for _name, block in blocks.items():
        items = block.get("items") if isinstance(block, dict) else block
        if not isinstance(items, list):
            continue
        dates = []
        for it in items:
            if not isinstance(it, dict):
                continue
            try:
                dates.append(datetime.strptime(str(it.get("signal_date") or ""), "%Y-%m-%d").date())
            except ValueError:
                pass
        if not dates:
            continue
        lag = float((today - max(dates)).days)
        worst = lag if worst is None else max(worst, lag)
    return worst


BRIDGE_RESTART_RE = re.compile(r"^\[(\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2})\] started bridge")


def bridge_churn_peak_per_hour(window_h: float = 24.0) -> float | None:
    """최근 `window_h` 시간 안에서 **시간당 브리지 재기동 최대치**를 센다.

    WHY: 이 항목의 계약은 "시간당 재기동 <= 4회"인데, 처음 구현은 로그 **전체**의
    `grep -c 'started bridge'` 였다(누적값). 누적값은 시간이 지날수록 커지기만 해서
    **다시는 통과할 수 없는 check** 였다(2026-09-29 09:25 판정 214건 = 판정 불가).
    지표 이름(`bridge_restarts_per_hour`)과 측정이 어긋난 것을 맞춘다 — 임계값(4)은 그대로다.
    """
    try:
        with open(BRIDGE_SUP_LOG, encoding="utf-8", errors="replace") as f:
            stamps = []
            for line in f:
                m = BRIDGE_RESTART_RE.match(line.strip())
                if m:
                    try:
                        stamps.append(datetime.strptime(m.group(1), "%Y-%m-%d %H:%M:%S"))
                    except ValueError:
                        pass
    except OSError:
        return None
    if not stamps:
        return 0.0
    now = datetime.now()
    cutoff = now - timedelta(hours=window_h)
    recent = sorted(t for t in stamps if t >= cutoff)
    if not recent:
        return 0.0
    best = 0
    for i, t0 in enumerate(recent):
        n = sum(1 for t in recent[i:] if (t - t0).total_seconds() <= 3600.0)
        best = max(best, n)
    return float(best)


# ── 프로브 (백로그 항목의 check 가 읽는 수치) ───────────────────────────────
def probe(name: str) -> float | None:
    m = measure()
    if name == "fees":
        v = m.get("fees_sum")
        if (v in (None, 0)) and m.get("archive"):
            return m["archive"].get("fees_sum")      # 출처: 모의 아카이브(아래 report 에 명시)
        return v
    if name == "rotation":
        age = m.get("last_entry_age_days")
        if age is None and m.get("archive"):
            return m["archive"].get("last_entry_age_days")
        return age
    if name == "feed":
        return float(len(feed_contract_check()["violations"]))
    if name == "halt":
        return 1.0 if m.get("loop_halt") else 0.0
    if name == "bridge":
        c = m.get("bridge_connected")
        return None if c is None else (1.0 if c else 0.0)
    if name == "expectancy":
        return m.get("expectancy_krw")
    if name == "stale":
        return m.get("loop_stale_min")
    if name == "bridge_churn":
        return bridge_churn_peak_per_hour()
    if name == "feed_signal_lag":
        return feed_signal_lag_days()
    return None


PROBES = {
    "fees": "청산 수수료 합계(원). 0 이면 수수료·세금이 저널에 반영되지 않은 것",
    "rotation": "마지막 진입 후 경과일. 3일 초과면 회전 정지",
    "feed": "피드 계약 위반 파일 수(0 이어야 정상)",
    "halt": "루프 halt 여부(0=정상)",
    "bridge": "브리지 connected(1=정상, None=판정 불가)",
    "expectancy": "건당 기대값(원)",
    "stale": "loop_state 갱신 지연(분). 장중 5분 초과면 감시 단절",
    "bridge_churn": "최근 24시간 안 시간당 브리지 재기동 최대치(회). 4 초과면 감독 churn",
    "feed_signal_lag": "피드 후보 signal_date 중 가장 낡은 전략의 경과일(일). 4 초과면 발행물이 낡은 데이터",
}



# ── 협업 점검: 핸드오프가 '생성'에서 '소비'로 넘어갔는가 ─────────────────────
BACKLOGS = (("engineer", MODEL_BACKLOG), ("researcher", RESEARCH_BACKLOG))


def handoff_report() -> dict:
    """역할 간 핸드오프 항목의 **채택 여부**를 센다.

    WHY: 항목을 만들어 넣는 것만으로는 협업이 아니다. 상대 역할이 재현 명령(command)을
    채워야 실행 가능해진다. 실측(2026-09-28 14:35)에서 리서처→엔지니어 핸드오프 5건이
    전부 command 비어 있는 채로 멈춰 있었다 — 이걸 매 틱 세지 않으면 '조용한 공전'이 된다.
    """
    out = {"total": 0, "unfilled": 0, "items": []}
    for target, path in BACKLOGS:
        try:
            with open(path, encoding="utf-8") as f:
                b = json.load(f)
        except (OSError, json.JSONDecodeError):
            continue
        for i in b.get("items", []):
            if not (i.get("from_trader") or i.get("from_research")):
                continue
            created = i.get("created_at")
            age_h = None
            if created:
                try:
                    age_h = round((now_kst() - datetime.fromisoformat(created)).total_seconds() / 3600.0, 1)
                except ValueError:
                    pass
            filled = bool(i.get("command"))
            out["total"] += 1
            if not filled:
                out["unfilled"] += 1
            out["items"].append({"target": target, "id": i.get("id"), "status": i.get("status"),
                                 "from": i.get("from_trader") or i.get("from_research"),
                                 "filled": filled, "age_h": age_h})
    return out


# ── 백로그 항목 실행 (자체 실행기 — 리서처/엔지니어의 실행기 가정에 의존하지 않는다) ──
def _run_item(item: dict, force: bool = False) -> int:
    """항목의 `command` 를 실행하고 마지막 수치를 `check` 와 비교해 상태·원장을 갱신한다."""
    cmd = item.get("command")
    if not cmd:
        item["status"] = "needs_setup"
        log(f"{item['id']}: command 없음 → needs_setup")
        return 2
    if item.get("status") in ("done", "failed") and not force:
        return 0
    timeout_s = int(item.get("timeout_s") or 180)
    log(f"실행: {cmd} (timeout {timeout_s}s)")
    try:
        p = subprocess.run(cmd, shell=True, capture_output=True, text=True,
                           timeout=timeout_s, cwd=PROJ)
        out = (p.stdout or "").strip()
        err = (p.stderr or "").strip()
        rc = p.returncode
    except subprocess.TimeoutExpired:
        out, err, rc = "", f"시간 초과({timeout_s}s)", 124
    nums = re.findall(r"[-+]?\d+(?:\.\d+)?", out)
    val = float(nums[-1]) if nums else None
    chk = item.get("check") or {}
    op, tgt = chk.get("op", ">="), float(chk.get("value", 0))
    met = None
    if val is not None:
        met = {">=": val >= tgt, ">": val > tgt, "<=": val <= tgt, "<": val < tgt,
               "==": val == tgt}.get(op, False)
    verdict = "충족" if met else ("미달" if met is False else "수치 없음")
    item["status"] = "done" if met else "failed"
    item["result"] = {"ts": now_kst().isoformat(timespec="seconds"), "value": val,
                      "check": f"{val} {op} {tgt}" if val is not None else None,
                      "verdict": verdict, "rc": rc, "stdout_tail": out[-400:],
                      "stderr_tail": err[-300:]}
    log(f"{item['id']} → {verdict} ({item['result']['check'] or 'n/a'})")
    try:
        os.makedirs(TR_LOGDIR, exist_ok=True)
        with open(os.path.join(TR_LOGDIR, "tr_{0}_{1}.log".format(
                item["id"], now_kst().strftime("%Y%m%d-%H%M%S"))), "w", encoding="utf-8") as f:
            f.write("command: {0}\nrc={1}\n\n--- stdout ---\n{2}\n--- stderr ---\n{3}\n".format(
                cmd, rc, out, err))
    except OSError:
        pass
    return 0 if met else 1


# ── 틱 ─────────────────────────────────────────────────────────────────────
def _trader_guard(force=False) -> tuple:
    """트레이더 사이클 전용 가드.

    WHY base.guards() 를 그대로 쓰지 않는가: 공용 가드는 **장중(09:00~15:30) 시작 금지**를
    포함한다(CPU 를 오래 쓰는 학습 역할 보호용). 그런데 트레이더의 본업은 **세션 중/직후 측정**이라
    장중 금지는 정반대다. 이 사이클은 몇 초 안에 끝나고 CPU 를 쓰지 않으므로, 동시 실행만 막고
    부하가 아주 높을 때(load1 > 6)만 양보한다.
    """
    if base.running_pid():
        return False, "이미 사이클 실행 중"
    pid, rel = base.peer_running()
    if pid:
        return False, f"다른 역할이 실행 중(pid={pid}, {rel}) — 동시 실행 금지"
    if not force:
        try:
            load = base.load1()
        except Exception:  # noqa: BLE001
            load = 0.0
        if load and load > 6.0:
            return False, f"load1={load:.2f} — 매매 경로 보호를 위해 대기(--force 로 무시)"
    return True, ""


def tick(force=False) -> int:
    ok, why = _trader_guard(force)
    if not ok:
        print(f"대기: {why}")
        return 0

    m = measure()
    model = verify_model_handoff()
    feed = feed_contract_check()
    safety = safety_check()

    # ① 북극성 한 줄
    if m.get("realized_krw") is None:
        log(f"북극성: 판정 불가 — {m.get('journal_error')}")
    else:
        log("북극성 실현 순손익: {0:+,.0f}원 / 청산 {1}건 / 승률 {2} / 기대값 {3} / 보유 {4} / "
            "수수료 {5:,.0f}원".format(
                m["realized_krw"], m["closed"],
                f"{m['win_rate']:.1%}" if m.get("win_rate") is not None else "n/a",
                f"{m['expectancy_krw']:+,.0f}원" if m.get("expectancy_krw") is not None else "n/a",
                m.get("open_positions"),
                m.get("fees_sum") or 0))

    # ② 실행 경로 생존
    bridge_note = "" if m.get("bridge_connected") is not None else f" (사유: {m.get('bridge_error')})"
    log("실행 경로: halt={0} 실패={1} phase={2} 갱신지연={3}분 브리지={4}{5}".format(
        m.get("loop_halt"), m.get("loop_failures"), m.get("loop_phase"),
        m.get("loop_stale_min"), m.get("bridge_connected"), bridge_note))

    # ③ 계약 2번(엔지니어 → 트레이더) 자료 확인
    if model["gaps"]:
        log(f"계약2 미비: {', '.join(model['gaps'])} (엔지니어 원장 {model['ledger_rows']}행)")
        handoff_to_model(
            "[트레이더 요구] 검증 성적표에 폴드 통계·purge·승격 dry-run 을 포함하라",
            f"트레이더 판정에 필요한 필드 누락: {', '.join(model['gaps'])} "
            f"(엔지니어 원장 {model['ledger_rows']}행, champion AUC {model.get('promoted_auc')})",
            key="need-fold-scorecard",
        )
    else:
        log(f"계약2 충족: 폴드 통계·purge·승격 dry-run 확인 (champion AUC {model.get('promoted_auc')})")

    # ④ 피드/데이터 환류 (계약 3번)
    if feed["violations"]:
        log(f"피드 계약 위반 {len(feed['violations'])}건")
        handoff_to_research(
            "[트레이더 환류] 피드 계약 위반 — 발행 전 차단 필요",
            f"위반 {len(feed['violations'])}건: {json.dumps(feed['violations'][:3], ensure_ascii=False)}",
            key="feed-contract-violation",
        )
    else:
        log(f"피드 계약: 위반 0건 (파일 {len(feed['files'])}개, 최대 경과 {feed['max_age_days']}일)")

    # ⑤ 기여 환류: 실현 손익이 있는 스크리너별 성과를 리서처에 돌려준다
    conn, tmp = _journal_copy()
    if conn is not None:
        try:
            rows = list(conn.execute(
                "select coalesce(screener,'(none)') s, count(*) n, coalesce(sum(pnl),0) p "
                "from trades where exit_ts is not null group by s order by p desc"))
            if rows:
                detail = ", ".join(f"{s}: {n}건 {p:+,.0f}원" for s, n, p in rows)
                log(f"스크리너별 실현: {detail}")
                if any(p < 0 for _, _, p in rows):
                    handoff_to_research(
                        "[트레이더 환류] 스크리너별 실현 성과 — 어느 데이터가 기여했는지 확인 필요",
                        f"스크리너별 실현: {detail}",
                        key="screener-attribution",
                    )
        except sqlite3.Error as exc:
            log(f"스크리너 집계 실패: {exc}")
        finally:
            conn.close()
            if isinstance(tmp, str) and os.path.isdir(tmp):
                shutil.rmtree(tmp, ignore_errors=True)

    # ⑤-2 협업 점검: 핸드오프가 채택되었는가
    ho = handoff_report()
    if ho["total"]:
        stale = [i for i in ho["items"] if not i["filled"] and (i["age_h"] or 0) >= 24]
        log("핸드오프: 총 {0}건 중 미채택 {1}건{2}".format(
            ho["total"], ho["unfilled"],
            f" (24시간 초과 {len(stale)}건: {', '.join(str(i['id']) for i in stale)})" if stale else ""))

    # ⑥ 백로그 항목 1건 실행(있으면) — failed 항목은 12시간 뒤 자동 재시도
    ran = None
    if os.path.exists(TR_BACKLOG):
        b = load_backlog()
        pending = [i for i in sorted(b.get("items", []), key=lambda x: x.get("priority", 99))
                   if i.get("status") == "pending" and i.get("command")]
        if not pending:
            # 재시도 큐: 미달(failed)로 끝난 항목을 일정 시간 뒤 pending 으로 되돌린다.
            # (미달은 영구 실패가 아니라 '아직 개선되지 않음'이다 — 재측정이 본업.)
            for i in b.get("items", []):
                if i.get("status") != "failed":
                    continue
                ts = (i.get("result") or {}).get("ts")
                if i.get("override_status"):
                    i["status"] = i.pop("override_status")
                    pending.append(i)
                    continue
                try:
                    age_h = (now_kst() - datetime.fromisoformat(ts)).total_seconds() / 3600.0
                except (TypeError, ValueError):
                    age_h = 99.0
                if age_h >= float(i.get("retry_after_h") or 12):
                    i["status"] = "pending"
                    log(f"{i['id']}: 재시도 대기 해제({age_h:.1f}시간 경과) → pending")
                    pending.append(i)
        item = pending[0] if pending else None
        if item:
            log(f"백로그 실행: {item['id']} — {item['title']}")
            rc = _run_item(item, force)
            ran = {"id": item["id"], "rc": rc, "status": item.get("status")}
            b["updated_at"] = now_kst().isoformat(timespec="seconds")
            save_backlog(b)
        else:
            log("백로그: 실행 가능한 pending 항목 없음")

    rec = {"ts": m["ts"], "id": "TR-TICK", "rc": 0, "verdict": "측정", "elapsed_min": 0,
           "north_krw": m.get("realized_krw"), "closed": m.get("closed"),
           "win_rate": m.get("win_rate"), "fees": m.get("fees_sum"),
           "open_positions": m.get("open_positions"), "halt": m.get("loop_halt"),
           "bridge": m.get("bridge_connected"), "model_gaps": model["gaps"],
           "feed_violations": len(feed["violations"]),
           "handoffs_total": ho["total"], "handoffs_unfilled": ho["unfilled"], "ran": ran,
           "reported": False}
    append_ledger(rec)

    if os.environ.get("TR_JSON"):
        print(json.dumps({"measure": m, "model": model, "feed": feed, "safety": safety, "ran": ran},
                         ensure_ascii=False))
    return 0


# ── 현황 ──────────────────────────────────────────────────────────────────
def status() -> int:
    m = measure()
    if m.get("realized_krw") is not None:
        print("북극성(실현 순손익): {0:+,.0f}원 / 청산 {1}건 / 승률 {2} / 기대값 {3} / 보유 {4}종목 / "
              "수수료 {5:,.0f}원".format(
                  m["realized_krw"], m["closed"],
                  f"{m['win_rate']:.1%}" if m.get("win_rate") is not None else "n/a",
                  f"{m['expectancy_krw']:+,.0f}원" if m.get("expectancy_krw") is not None else "n/a",
                  m.get("open_positions"), m.get("fees_sum") or 0))
        print("오늘: 청산 {0}건 {1:+,.0f}원".format(m.get("today_closed") or 0, m.get("today_realized_krw") or 0))
    else:
        print(f"북극성: 판정 불가 — {m.get('journal_error')}")
    print("실행 경로: halt={0} 실패={1} phase={2} 갱신지연={3}분 브리지={4} {5}".format(
        m.get("loop_halt"), m.get("loop_failures"), m.get("loop_phase"),
        m.get("loop_stale_min"), m.get("bridge_connected"), m.get("bridge_error") or ""))
    if os.path.exists(TR_BACKLOG):
        b = load_backlog()
        print(f"백로그: {TR_BACKLOG} (updated {b.get('updated_at')}, {len(b.get('items', []))}항목)")
        for i in sorted(b.get("items", []), key=lambda x: x.get("priority", 99)):
            print(f"  [{i.get('status','?'):9s}] {i.get('id','?'):3s} {i.get('title')}")
    else:
        print(f"백로그 없음: {TR_BACKLOG}")
    led = load_ledger(5)
    if led:
        print("최근 원장(5):")
        for r in led:
            print(f"  {r.get('ts')} rc={r.get('rc')} 실현={r.get('north_krw')} halt={r.get('halt')} "
                  f"피드위반={r.get('feed_violations')}")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--status", action="store_true")
    ap.add_argument("--tick", action="store_true")
    ap.add_argument("--probe", choices=sorted(PROBES))
    ap.add_argument("--list-probes", action="store_true")
    ap.add_argument("--handoffs", action="store_true", help="역할 간 핸드오프 채택 현황")
    ap.add_argument("--json", action="store_true", help="틱 결과를 JSON 으로도 출력")
    ap.add_argument("--force", action="store_true")
    a = ap.parse_args()

    if a.handoffs:
        ho = handoff_report()
        print(f"핸드오프 총 {ho['total']}건 · 미채택(재현 명령 없음) {ho['unfilled']}건")
        for i in sorted(ho["items"], key=lambda x: (x["target"], str(x["id"]))):
            print("  [{0:10s}] {1:6s} from={2:18s} status={3:12s} command={4} age={5}h".format(
                i["target"], str(i["id"]), str(i["from"]), str(i["status"]),
                "있음" if i["filled"] else "없음", i["age_h"]))
        return 0
    if a.list_probes:
        for k, v in sorted(PROBES.items()):
            print(f"{k}: {v}")
        return 0
    if a.probe:
        v = probe(a.probe)
        print(v if v is not None else "NA")
        return 0 if v is not None else 1
    if a.status:
        return status()
    if a.tick:
        if a.json:
            os.environ["TR_JSON"] = "1"
        return tick(a.force)
    ap.print_help()
    return 0


if __name__ == "__main__":
    sys.exit(main())

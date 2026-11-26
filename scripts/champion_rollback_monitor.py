#!/usr/bin/env python3
"""champion_rollback_monitor.py — 승격 후 '되돌림(ratchet)' 감시: 돈 증거가 나빠지면 자동 원복.

WHY (2026-10-03): 자율 루프가 스스로 승격하려면 **되돌릴 수 있어야** 한다. 승격은 사전등록 증거로만
하지만, 그 증거는 측정 시점의 데이터에 기댄다 — 나중에 같은 프로토콜로 다시 재보면 나빠져 있을 수 있다.
그때 사람이 개입하지 않으면 루프가 나쁜 챔피언 위에서 계속 개선을 시도한다.

비교는 **같은 프로토콜의 돈 증거**(`<모델>/robust_oos.json` 의 expectancy_pct)로만 한다:
  · 승격 시각 이후 경과 세션 ≥ objective.rollback.monitor_sessions
  · 챔피언과 직전 챔피언(백업 디렉토리)의 증거가 모두 있고 최근(max_age_days) 것
  · 챔피언 순기대 − 백업 순기대 ≤ rollback.degrade_pct  → 원복 권고
  · objective.gates.auto_rollback = true 이면 실제 원복 실행(이전에 라이브였던 모델로 되돌리는 것)

원복은 **이전에 실제로 라이브였던 챔피언으로 되돌리는** 것이므로 위험은 '새 모델 유지'보다 낮다.
그래도 모든 판정·실행은 상태 파일과 스왑 로그에 남긴다.

사용:
  python3 scripts/champion_rollback_monitor.py --check      # 판정만(JSON)
  python3 scripts/champion_rollback_monitor.py --check --write   # + data/state/rollback_monitor.json
  (원복 권고/실행 시 rc=2 — 크론이 경보에 쓴다)
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import subprocess
import sys

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
MODELS = os.path.join(REPO, "services", "xgboost-ml", "app", "models")
SWAPS = os.path.join(REPO, "data", "reports", "champion_swaps.jsonl")
STATE = os.path.join(REPO, "data", "state", "rollback_monitor.json")
REFRESH = os.path.join(REPO, "data", "reports", "rollback_evidence.json")
PAIRED = os.path.join(REPO, "data", "reports", "rollback_paired_test.json")
sys.path.insert(0, os.path.join(REPO, "scripts"))
from objective import load as load_objective  # noqa: E402


def _read_json(path):
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return None


def _evidence(model_name: str):
    d = _read_json(os.path.join(MODELS, model_name, "robust_oos.json"))
    if not d or d.get("expectancy_pct") is None:
        return None
    return d


def last_swap(path: str = SWAPS):
    try:
        lines = [l for l in open(path, encoding="utf-8") if l.strip()]
    except OSError:
        return None
    if not lines:
        return None
    try:
        return json.loads(lines[-1])
    except ValueError:
        return None


def trading_sessions_since(ts_iso: str, repo: str = REPO) -> int | None:
    """스왑 이후 경과 **거래일** 수 — DB(수집된 일봉) 기준. 실패하면 None(판정은 증거만으로).

    WHY: 되돌림 감시는 'K세션 뒤' 재판정이 설계 의도다(즉시 재판정은 잔떨림). 처음엔 리포트 파일
    키에서 날짜를 세려 했는데 실측 2026-10-04 에 키가 없어 None 이 나왔고, 그 결과 스왑 직후에도
    판정해 버렸다 → DB 의 trade_date 를 정본으로 쓰고, 실패하면 None 으로 남겨 '판정 불가'를 숨기지 않는다.
    """
    try:
        d0 = dt.datetime.fromisoformat(ts_iso).date()
    except (ValueError, TypeError):
        return None
    try:
        import psycopg2
        env = dict(os.environ)
        try:                                  # .env 최소 파싱(외부 의존성 없이)
            for line in open(os.path.join(repo, ".env"), encoding="utf-8"):
                line = line.strip()
                if line and not line.startswith("#") and "=" in line:
                    k, v = line.split("=", 1)
                    env.setdefault(k.strip(), v.strip().strip('"').strip("'"))
        except OSError:
            pass
        conn = psycopg2.connect(host="127.0.0.1",
                                port=int(env.get("POSTGRES_HOST_PORT") or 5434),
                                user=env.get("POSTGRES_USER", "stock_user"),
                                password=env.get("POSTGRES_PASSWORD", ""),
                                dbname=env.get("POSTGRES_DB", "stock_trading"),
                                connect_timeout=5)
        with conn, conn.cursor() as cur:
            cur.execute("select count(distinct trade_date) from market_data where trade_date > %s", (d0,))
            row = cur.fetchone()
            n = int(row[0]) if row else 0
        conn.close()
        return n
    except Exception:                     # noqa: BLE001 — DB 없으면 판정 불가로 남긴다
        return None


def _too_old(ev: dict, max_age_days: int) -> bool:
    ca = str(ev.get("created_at") or "")
    try:
        t = dt.datetime.fromisoformat(ca)
    except ValueError:
        return True
    return (dt.datetime.now() - t).days > max_age_days


def _refresh_evidence(path: str | None = None):
    d = _read_json(path or REFRESH)
    if not isinstance(d, dict) or not (d.get("models") or {}):
        return None
    return d


def _evidence_pair(prev_dir: str, max_age_days: int):
    """(챔피언 증거, 백업 증거, 출처, 재측정 파일) — **같은 출처로 짝을 맞춘다**.

    WHY(2026-11-26 실측): 모델 디렉토리의 robust_oos.json 은 승격 시점에 한 번 쓰이고 다시
    갱신되지 않아 챔피언(2026-10-02) 증거가 14일이 지나면 영구히 stale 이 된다 → 자동 원복 게이트가
    매일 '판정 불가'로 침묵했다(7일 연속 stale_evidence). 재측정 파일은 챔피언과 직전 챔피언을
    **같은 표본·같은 topk·같은 수수료**로 잰 짝이므로 이것만 비교에 쓴다. 한쪽만 있으면
    프로토콜이 섞이므로 쓰지 않고 종전(모델 디렉토리) 경로로 폴백한다 — 조용한 혼합 금지.
    """
    r = _refresh_evidence()
    if r:
        models = r.get("models") or {}
        c, p = models.get("champion"), models.get(prev_dir)
        ok = (isinstance(c, dict) and isinstance(p, dict)
              and c.get("expectancy_pct") is not None and p.get("expectancy_pct") is not None)
        if ok and not _too_old(r, max_age_days):
            ca = r.get("created_at")
            c = dict(c, created_at=ca)
            p = dict(p, created_at=ca)
            return c, p, "refresh", r
    return _evidence("champion"), _evidence(prev_dir), "model_files", None


def _paired_note(prev_dir: str):
    """짝 검정 결과를 **주석으로만** 붙인다(판정·문턱 불변).

    WHY(2026-11-26 실측): 점추정 규칙으로 rollback 이 나왔는데 짝 검정은 t −1.00 · CI95
    [−1.44, +0.46] 로 실행 근거가 아니었다. 판정을 바꾸는 것은 문턱 변경(승인 대상)이지만,
    '점추정만 넘었다'는 사실을 상태 파일에 남기지 않으면 다음 세션이 rollback 을 확정으로 읽는다.
    """
    d = _read_json(PAIRED)
    if not isinstance(d, dict) or d.get("prev_dir") != prev_dir:
        return None
    return {"n_sessions": d.get("n_sessions"), "mean": d.get("mean"),
            "t_block": (d.get("boot") or {}).get("t"),
            "ci95": (d.get("boot") or {}).get("ci95"),
            "executable": d.get("executable"), "verdict": d.get("verdict")}


def verdict(obj: dict, swap=None, repo: str = REPO) -> dict:
    rb = obj["goal"].get("rollback", {})
    monitor_sessions = int(rb.get("monitor_sessions", 5))
    degrade_pct = float(rb.get("degrade_pct", -0.2))
    max_age_days = int(rb.get("max_evidence_age_days", 14))
    out = {"monitor_sessions": monitor_sessions, "degrade_pct": degrade_pct}
    swap = swap if swap is not None else last_swap()
    if not swap:
        out.update({"verdict": "no_swap", "detail": "스왑 로그 없음 — 비교할 승격 이력이 없다"})
        return out
    prev_dir = str(swap.get("prev_dir") or "")
    out.update({"swap_ts": swap.get("ts"), "prev_dir": prev_dir})
    champ_ev, prev_ev, src, refresh = _evidence_pair(prev_dir, max_age_days)
    out["evidence_source"] = src
    if refresh:
        out["evidence_sample"] = refresh.get("sample") or {}
        out["evidence_created_at"] = refresh.get("created_at")
    if champ_ev is None or prev_ev is None:
        out.update({"verdict": "no_evidence",
                    "detail": "챔피언 또는 백업의 돈 증거(expectancy_pct)가 없다 — 판정 불가"})
        return out
    if _too_old(champ_ev, max_age_days) or _too_old(prev_ev, max_age_days):
        out.update({"verdict": "stale_evidence",
                    "detail": f"증거가 {max_age_days}일보다 오래됐다 — 다시 측정해야 판정할 수 있다"})
        return out
    sessions = trading_sessions_since(str(swap.get("ts") or ""), repo)
    out["sessions_since"] = sessions
    if sessions is not None and sessions < monitor_sessions:
        out.update({"verdict": "too_early",
                    "detail": f"경과 {sessions}세션 < 감시 {monitor_sessions}세션 — 아직 판정하지 않는다"})
        return out
    champ_exp = float(champ_ev["expectancy_pct"])
    prev_exp = float(prev_ev["expectancy_pct"])
    _pn = _paired_note(prev_dir)
    if _pn:
        out["paired_test"] = _pn
    out.update({"champion_pct": champ_exp, "prev_pct": prev_exp,
                "champion_auc": champ_ev.get("robust_auc"), "prev_auc": prev_ev.get("robust_auc"),
                "degrade": round(champ_exp - prev_exp, 4)})
    if champ_exp - prev_exp <= degrade_pct:
        out.update({"verdict": "rollback",
                    "detail": (f"승격 챔피언 순기대 {champ_exp:+.3f}%p < 백업 {prev_exp:+.3f}%p "
                               f"(차 {champ_exp - prev_exp:+.3f} ≤ {degrade_pct}) → 원복")})
    elif champ_exp - prev_exp < 0:
        out.update({"verdict": "watch",
                    "detail": f"순기대가 백업보다 {champ_exp - prev_exp:+.3f}%p 낮지만 문턱 이내 — 관찰"})
    else:
        out.update({"verdict": "ok", "detail": "승격 유지(증거가 백업 이상)"})
    return out


def execute_rollback(prev_dir: str, log=print) -> dict:
    """직전 챔피언으로 되돌린다(라이브 스코어 토큰은 env 로 우회 — '이전에 라이브였던' 모델이다)."""
    cmd = ["docker", "exec", "-e", "PROMOTE_LIVE_SCORE_ENFORCE=0", "-w", "/app", "stock_xgboost_ml",
           "python", "-m", "app.training.champion_promote",
           "--candidate", f"app/models/{prev_dir}", "--champion", "app/models/champion",
           "--min-auc", "0.0", "--min-improvement", "-1.0",
           "--legacy-baseline-cap", "0.0", "--summary-out", "app/reports/rollback_result.json"]
    log("[rollback] 실행: " + " ".join(cmd))
    try:
        p = subprocess.run(cmd, capture_output=True, text=True, timeout=600)
    except (OSError, subprocess.TimeoutExpired) as e:
        return {"ran": True, "ok": False, "error": repr(e)[:200]}
    ok = "promoted" in (p.stdout or "") and '"promoted": true' in (p.stdout or "")
    out = {"ran": True, "ok": bool(ok), "rc": p.returncode,
           "tail": (p.stdout or "")[-400:]}
    if ok:                       # 스왑 로그(MT117) 기록 — 실패해도 계속
        try:
            subprocess.run(["/usr/bin/python3", os.path.join(REPO, "scripts", "log_champion_swap.py")],
                           capture_output=True, text=True, timeout=120)
        except (OSError, subprocess.TimeoutExpired):
            pass
    return out


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="승격 후 되돌림 감시(돈 증거 기준)")
    ap.add_argument("--check", action="store_true")
    ap.add_argument("--write", action="store_true")
    ap.add_argument("--execute", action="store_true",
                    help="auto_rollback 게이트가 켜져 있을 때만 실제 원복")
    a = ap.parse_args(argv)
    obj = load_objective()
    v = verdict(obj)
    v["updated"] = dt.datetime.now().isoformat(timespec="seconds")
    v["auto_rollback_gate"] = bool(obj.get("gates", {}).get("auto_rollback", False))
    if a.execute and v["verdict"] == "rollback" and v["auto_rollback_gate"]:
        v["action"] = execute_rollback(str(v.get("prev_dir")), log=lambda m: print(m, file=sys.stderr))
    if a.write:
        os.makedirs(os.path.dirname(STATE), exist_ok=True)
        tmp = STATE + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(v, f, ensure_ascii=False, indent=2)
        os.replace(tmp, STATE)
    print(json.dumps(v, ensure_ascii=False, indent=2))
    return 2 if v["verdict"] in ("rollback", "watch") else 0


if __name__ == "__main__":
    sys.exit(main())

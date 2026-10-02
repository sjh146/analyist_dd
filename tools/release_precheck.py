#!/usr/bin/env python3
"""릴리스 사전검사 — 릴리스 전에 '필수 검사 묶음'을 한 번에 돌려 PASS/BLOCK 을 낸다 (읽기 전용).

역할: quant-release (docs/QUANT_ROLE_PLAN_V2.md §5.5) — 감사·안전 검사를 재사용해 판정만 한다.
사용:
  python3 tools/release_precheck.py --target champion [--candidate app/models/champion_cand] [--with-probe]
  python3 tools/release_precheck.py --target feed|cron|image
종료코드: 0 PASS / 2 BLOCK. 산출물: reports/releases/precheck_<날짜>_<target>.json
"""
import argparse
import datetime as dt
import json
import os
import re
import subprocess
import sys

REPO = "/home/jhshi/analyist_dd"
OUT_DIR = os.path.join(REPO, "reports", "releases")
CONF_TS = 0.55


def sh(cmd, timeout=420, env=None):
    e = dict(os.environ)
    if env:
        e.update(env)
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout, cwd=REPO, env=e)
        return r.returncode, (r.stdout or "") + (r.stderr or "")
    except Exception as ex:  # noqa: BLE001
        return 99, f"__ERR__ {type(ex).__name__}: {ex}"


def consumers(pattern):
    """동결: 이 산출물을 읽는 소비자 목록(grep 실측)."""
    rc, out = sh(["/usr/bin/grep", "-rl", pattern, "--include=*.py", "--include=*.sh", "--include=*.md", "."],
                 timeout=60)
    files = [l.strip() for l in out.splitlines() if l.strip() and not l.startswith("__ERR__")]
    return files[:40]


def audits(only=None):
    """감사 스크립트 실행 → {script: rc, tail}."""
    scripts = ["scripts/audit_protocol_lock.py", "scripts/audit_measure.py",
               "scripts/audit_path_daily.py", "scripts/audit_safety.py"]
    if only:
        scripts = [s for s in scripts if os.path.basename(s) in only]
    out = {}
    for s in scripts:
        rc, o = sh([sys.executable, s], timeout=420)
        out[os.path.basename(s)] = {"rc": rc, "tail": "\n".join(o.strip().splitlines()[-6:])}
        out[os.path.basename(s)]["checks"] = audit_checks(os.path.basename(s))
    return out


# 릴리스 판정에서 '경고'로 내리는 항목 — 산출물 갱신/사람 승인 대기라 릴리스 자체를 막지 않는다.
# (다른 검사는 전부 릴리스 차단. 예: 킬스위치·세션 끊김·프로세스 부재·문턱 불일치는 차단.)
SOFT_CHECKS = {
    "audit_measure.py": {"live_score_path_closed", "swing_max_below_threshold"},
    "audit_safety.py": {"fees_unbooked"},
    # 큐 위생 항목은 보고만 한다(문턱 불일치·est 초과·킬스위치·세션은 차단 유지)
    "audit_protocol_lock.py": {"lock_fields_missing", "handoffs_unconsumed"},
}
AUDIT_JSON = {
    "audit_measure.py": ("reports/audit/measure_*.json", "issues"),
    "audit_path_daily.py": ("reports/audit/path_*.json", "issues"),
    "audit_safety.py": ("reports/safety/safety_*.json", "alerts"),
    "audit_protocol_lock.py": ("reports/locks/lock_audit_*.json", "violations"),
}


def audit_checks(script):
    """감사 산출 JSON 에서 check 이름 목록을 꺼낸다(릴리스 판정 분류용)."""
    import glob
    pat, key = AUDIT_JSON.get(script, (None, None))
    if not pat:
        return []
    files = sorted(glob.glob(os.path.join(REPO, pat)), key=os.path.getmtime)
    if not files:
        return []
    try:
        d = json.load(open(files[-1], encoding="utf-8"))
    except Exception:  # noqa: BLE001
        return []
    return [i.get("check") for i in (d.get(key) or [])]


def feed_contract():
    """피드 계약 검사: 전략 키 · 항목 필수 필드 · generated_at."""
    p = os.path.join(REPO, "data", "feed", "screener_latest.json")
    res = {"path": p, "issues": []}
    try:
        d = json.load(open(p, encoding="utf-8"))
    except Exception as e:  # noqa: BLE001
        res["issues"].append(f"피드 읽기 실패: {e}")
        return res
    res["generated_at"] = d.get("generated_at")
    cand = d.get("candidates") or {}
    for strat in ("close", "swing"):
        items = ((cand.get(strat) or {}).get("items")) or []
        res[f"{strat}_n"] = len(items)
        for i, it in enumerate(items[:200]):
            miss = [k for k in ("stock_code", "score") if k not in it]
            if not (it.get("signal_date") or it.get("valid_until")):
                miss.append("signal_date|valid_until")
            if miss:
                res["issues"].append(f"{strat}[{i}] 필수 필드 누락: {miss}")
                break
    return res


def champion_dry_run(candidate, min_improvement):
    """champion_promote --dry-run 상태 인용."""
    rc, out = sh(["docker", "exec", "-w", "/app", "stock_xgboost_ml", "python", "-m",
                  "app.training.champion_promote", "--candidate", candidate,
                  "--champion", "app/models/champion", "--min-improvement", str(min_improvement),
                  "--summary-out", "/tmp/precheck_promote.json", "--dry-run"])
    status = re.search(r'"status":\s*"([^"]+)"', out)
    reason = re.search(r'"reason":\s*"([^"]+)"', out)
    cand_auc = re.search(r'"candidate_auc":\s*([0-9.]+)', out)
    return {"rc": rc, "status": status.group(1) if status else None,
            "reason": reason.group(1) if reason else None,
            "candidate_auc": cand_auc.group(1) if cand_auc else None,
            "raw_tail": "\n".join(out.strip().splitlines()[-6:])}


def live_score_probe(candidate):
    """후보 vs 챔피언의 라이브 스코어 분포 (컨테이너, 약 90초). MT116 재발을 사전에 잡는다."""
    env = {"PROBE_MODEL_DIRS": json.dumps([["candidate", candidate],
                                           ["champion", "/app/app/models/champion"]])}
    rc, out = sh(["docker", "exec", "-e", "PYTHONPATH=/app", "-e",
                  "PROBE_MODEL_DIRS=" + env["PROBE_MODEL_DIRS"], "stock_xgboost_ml",
                  "python", "/app/scripts/_swing_ensemble_weight_probe.py"], timeout=600)
    m = re.search(r"^PROBE_SUMMARY (\{.*\})$", out, re.M)
    summary = json.loads(m.group(1)) if m else None
    return {"rc": rc, "summary": summary,
            "tail": "\n".join(out.strip().splitlines()[-4:])}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--target", default="champion", choices=["champion", "feed", "cron", "image"])
    ap.add_argument("--candidate", default=None, help="컨테이너 경로 (예: app/models/champion_cand)")
    ap.add_argument("--min-improvement", type=float, default=0.02)
    ap.add_argument("--with-probe", action="store_true", help="라이브 스코어 분포 프로브까지(약 90초)")
    ap.add_argument("--allow-warn", action="store_true", help="경고(rc=2)는 통과로 본다")
    args = ap.parse_args()

    now = dt.datetime.now()
    os.makedirs(OUT_DIR, exist_ok=True)
    rec = {"ts": now.isoformat(timespec="seconds"), "target": args.target, "blocks": [], "warns": [],
           "info": {}}

    # 1) 동결(소비자)
    pat = {"champion": "models/champion", "feed": "data/feed", "cron": "cron.d", "image": "docker-compose"}[args.target]
    rec["info"]["freeze_consumers"] = consumers(pat)
    if not rec["info"]["freeze_consumers"]:
        rec["warns"].append(f"소비자를 찾지 못함(grep '{pat}') — 동결 목록을 수동 확인할 것")

    # 2) 감사 묶음 (SOFT_CHECKS 만 경고로 내린다 — 나머지 이상은 릴리스 차단)
    rec["info"]["audits"] = audits()
    for name, v in rec["info"]["audits"].items():
        soft = SOFT_CHECKS.get(name, set())
        checks = set(v.get("checks") or [])
        only_soft = bool(checks) and checks.issubset(soft)
        if v["rc"] == 3:
            rec["blocks"].append(f"{name}: 사람 단계 필요(rc=3) — {v['tail'].splitlines()[-1][:120]}")
        elif v["rc"] == 2 and (args.allow_warn or only_soft):
            why = "allow-warn" if args.allow_warn else f"일시/대기 항목만({sorted(checks)})"
            rec["warns"].append(f"{name}: 이상(rc=2) — {why}")
        elif v["rc"] == 2:
            rec["blocks"].append(f"{name}: 이상(rc=2) — {v['tail'].splitlines()[-1][:120]}")

    # 3) 피드 계약
    fc = feed_contract()
    rec["info"]["feed_contract"] = fc
    for i in fc.get("issues", []):
        rec["blocks"].append(f"feed_contract: {i}")

    # 4) 챔피언 특화
    if args.target == "champion":
        if args.candidate:
            dr = champion_dry_run(args.candidate, args.min_improvement)
            rec["info"]["promote_dry_run"] = dr
            if dr["status"] not in ("would_promote",):
                rec["blocks"].append(f"승격 dry-run 상태 '{dr['status']}' — {dr['reason']}")
            if dr["candidate_auc"] is not None:
                try:
                    champ = json.load(open(os.path.join(
                        REPO, "services/xgboost-ml/app/models/champion/robust_auc.json"), encoding="utf-8"))
                    delta = round(float(dr["candidate_auc"]) - float(champ.get("robust_auc") or 0), 4)
                    rec["info"]["auc_delta"] = delta
                    if delta < args.min_improvement:
                        rec["blocks"].append(f"AUC 델타 {delta} < 사전등록 {args.min_improvement}")
                except Exception:  # noqa: BLE001
                    rec["warns"].append("챔피언 robust_auc 읽기 실패 — 델타 미검증")
        else:
            rec["warns"].append("--candidate 미지정: 승격 dry-run·델타 미검증")

        if args.with_probe and args.candidate:
            pr = live_score_probe(args.candidate)
            rec["info"]["live_score_probe"] = pr
            s = (pr.get("summary") or {}).get("candidate") or {}
            if not s:
                rec["warns"].append("프로브 요약 없음 — 라이브 스코어 분포 미검증")
            elif s.get("deployed_gt_conf", 0) == 0:
                rec["blocks"].append(
                    f"후보의 0.55 초과 신호 0건(max={s.get('deployed_max')}) — 소비자가 배치를 거부한다(MT116 유형)")

    rec["verdict"] = "BLOCK" if rec["blocks"] else ("PASS_WITH_WARN" if rec["warns"] else "PASS")
    path = os.path.join(OUT_DIR, f"precheck_{now.date().isoformat()}_{args.target}.json")
    with open(path, "w", encoding="utf-8") as f:
        json.dump(rec, f, ensure_ascii=False, indent=1)
    print(f"[release-precheck:{args.target}] {rec['verdict']} · {path}")
    for b in rec["blocks"]:
        print(f"  X {b}")
    for w in rec["warns"]:
        print(f"  ~ {w}")
    return 2 if rec["blocks"] else 0


if __name__ == "__main__":
    sys.exit(main())

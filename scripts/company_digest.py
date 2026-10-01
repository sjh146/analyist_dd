#!/usr/bin/env python3
"""company_digest.py — 하루 1회 '회사 다이제스트' (읽기 전용).

헌장 §8: 역할 보고가 흩어져 있으면 사람이 명령해야만 상황을 안다. 이 스크립트는
원장·보드·KPI·자금·장애·전달 상태를 한 장으로 모아 **스스로 보고**한다.

사용
  cd /home/jhshi/analyist_dd && set -a && . ./.env && set +a
  export POSTGRES_HOST=127.0.0.1 POSTGRES_PORT=5434
  /usr/bin/python3 scripts/company_digest.py

어떤 프로브가 실패해도 그 줄만 '확인실패'로 찍고 나머지는 계속한다(다이제스트가 죽으면 안 된다).
"""
from __future__ import annotations

import datetime as dt
import json
import os
import sqlite3
import subprocess
import sys

REPO = "/home/jhshi/analyist_dd"
TA = "/mnt/c/Users/jhshi/analyist_dd/trader-agent"
WIN_CURL = "/mnt/c/Windows/System32/curl.exe"
FEED = "http://127.0.0.1:8090/health"
BRIDGE = "http://127.0.0.1:8100"
CRON_DB = os.path.expanduser("~/.hermes/cron/executions.db")
JOB_NAMES = {"d4070d508732": "엔지니어야간", "866357026fdc": "엔지니어데일리",
             "ab0889242419": "리서처모니터", "783d066890eb": "리서처데일리",
             "4841d139d6ce": "프리오픈", "5773c10b7147": "마감후",
             "5fe32a08500f": "트레이더틱", "43e18dca7279": "마감틱", "6585f88504a7": "리뷰보드"}
OK_DELIVERY = {"delivered", "suppressed", "silent"}


def sh(args, timeout=60):
    try:
        p = subprocess.run(args, capture_output=True, text=True, timeout=timeout)
        return p.returncode, p.stdout or "", p.stderr or ""
    except Exception as exc:  # noqa: BLE001
        return 99, "", str(exc)


def jload(path):
    try:
        with open(path, encoding="utf-8") as fh:
            return json.load(fh)
    except Exception:  # noqa: BLE001
        return None


def ledger_block(name, label):
    rows = []
    try:
        with open(os.path.join(REPO, "data/reports/%s_ledger.jsonl" % name), encoding="utf-8") as fh:
            rows = [json.loads(x) for x in fh if x.strip()]
    except Exception:  # noqa: BLE001
        return "%s: 원장 확인실패" % label
    today = dt.datetime.now().strftime("%Y-%m-%d")
    t = [r for r in rows if str(r.get("ts", ""))[:10] == today]
    last = t[-1] if t else (rows[-1] if rows else {})
    return "%s: 오늘 %d건 · 최근 %s %s rc=%s %s" % (
        label, len(t), str(last.get("ts", ""))[11:16], last.get("id"),
        last.get("rc"), last.get("verdict"))


def scoreboard(stanza):
    rc, out, _ = sh([sys.executable, os.path.join(REPO, "scripts/quant_scoreboard.py"),
                     "--stanza", stanza], timeout=180)
    for line in (out or "").splitlines():
        if line.strip().startswith("[북극성"):
            return line.strip()
    return "북극성(%s): 확인실패" % stanza


def bridge(path):
    rc, out, _ = sh([WIN_CURL, "-s", "--noproxy", "*", "-m", "20", BRIDGE + path], timeout=40)
    try:
        return json.loads(out)
    except Exception:  # noqa: BLE001
        return None


def tick_health():
    """오늘 역할 틱의 실패·미전달 건수와 마지막 성공 시각."""
    try:
        con = sqlite3.connect("file:%s?mode=ro" % CRON_DB, uri=True, timeout=5)
        rows = con.execute(
            "SELECT job_id,status,delivery_outcome,started_at FROM executions "
            "WHERE started_at >= date('now','-1 day','+9 hours') ORDER BY rowid").fetchall()
        con.close()
    except Exception:  # noqa: BLE001
        return "틱 상태: 확인실패"
    bad, good = [], 0
    for jid, status, delivery, started in rows:
        if jid not in JOB_NAMES:
            continue
        if status != "completed" or (delivery or "") not in OK_DELIVERY:
            bad.append("%s %s(%s)" % (JOB_NAMES[jid], str(started)[11:16], delivery or status))
        else:
            good += 1
    if bad:
        return "틱: 정상 %d건 · **미전달/실패 %d건** — %s" % (good, len(bad), ", ".join(bad[:6]))
    return "틱: 정상 %d건 · 실패 0 · 미전달 0" % good


def main():
    now = dt.datetime.now()
    out = ["📋 **회사 다이제스트** %s" % now.strftime("%Y-%m-%d %H:%M")]

    # 1) KPI 북극성
    out.append(scoreboard("engineer"))
    out.append(scoreboard("researcher"))

    # 2) 역할 진행
    for name, label in (("researcher", "리서처"), ("model_engineer", "엔지니어"), ("trader", "트레이더")):
        out.append(ledger_block(name, label))

    # 3) 매매 경로
    st = jload(os.path.join(TA, "loop_state.json")) or {}
    daily = st.get("daily") or {}
    cyc = st.get("last_cycle") or {}
    bal = bridge("/balance")
    frames = []
    if bal and bal.get("ok"):
        b = bal["balance"]
        frames.append("자금 %s원 · 보유 %s · 미체결 %s" % (
            "{:,}".format(int(b.get("equity") or 0)), b.get("positions_count"),
            b.get("total_buy_amount")))
    else:
        frames.append("자금 확인실패(브리지)")
    frames.append("오늘 진입 %s건 · 실현 %s원 · %s · halt=%s" % (
        daily.get("entries"), daily.get("closed_pnl"),
        cyc.get("phase") or "-", (st.get("loop") or {}).get("halt")))
    out.append("매매: " + " · ".join(frames))

    # 4) 데이터·경로 상태
    _, fh, _ = sh(["curl", "-s", "--noproxy", "*", "-m", "10", FEED], timeout=30)
    try:
        f = json.loads(fh)
        out.append("피드: %s 발행 · close %s / swing %s · 경과 %d분" % (
            str(f.get("generated_at"))[11:16], (f.get("items") or {}).get("close"),
            (f.get("items") or {}).get("swing"), int((f.get("age_seconds") or 0) // 60)))
    except Exception:  # noqa: BLE001
        out.append("피드: 확인실패(서버 미응답)")
    hy = jload(os.path.join(REPO, "data/reports/hygiene/latest.json")) or {}
    if hy:
        out.append("위생: %s · warns %d · breaches %d · 디스크 %s%%" % (
            hy.get("status"), len(hy.get("warns") or []), len(hy.get("breaches") or []),
            (hy.get("disk") or {}).get("use_pct")))
    out.append(tick_health())

    # 5) 코드 상태
    rc, local, _ = sh(["git", "-C", REPO, "log", "--oneline", "-1"], timeout=60)
    rc2, remote, _ = sh(["git", "-C", REPO, "ls-remote", "origin", "master"], timeout=120)
    if local.strip():
        ls = local.strip().split()[0]
        rs = (remote.strip().split()[0][:len(ls)] if remote.strip() else "?")
        out.append("코드: %s %s" % (local.strip()[:110],
                                    "· origin 동기" if ls == rs else "· **origin 과 불일치**"))

    # 6) 다음 사람 단계 (헌장 §3-C 를 항상 명시)
    out.append("다음 사람 단계: " + ("내일 개장 전 Creon 로그인 + 브리지/루프 확인"
                                     if now.weekday() < 5 else "없음(휴장)"))
    print("\n".join(out))
    return 0


if __name__ == "__main__":
    sys.exit(main())

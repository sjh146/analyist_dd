#!/usr/bin/env python3
"""researcher_cycle — 장외 자율 데이터 수집·품질 루프 (퀀트 리서처 전용).

무엇을 하는가 (매 틱)
  1) **모니터링**: dq_snapshot.py 를 항상 돌려 DQ/수집 수치와 추세 차트를 남긴다.
     위반(breach)이 있으면 눈에 띄게 출력한다 — 무인 사이클에서 조용한 실패를 막는 게 목적이다.
  2) **수집 집행**: 백로그의 pending 항목을 하나씩 실행한다(장시간 수집은 백그라운드).
  3) **협업**: 결과가 모델에 영향이 있으면(affects_model=true) 모델엔지니어 백로그에 항목을 넘기고
     docs/QUANT_FINDINGS.md 에 근거를 남긴다 — 두 역할이 서로의 산출물을 이어받게 하는 연결부다.

설계 (모델엔지니어 구동기와 동일한 가드 — 코드 중복 대신 재사용)
  `model_engineer_cycle` 의 가드·락·원장·틱 헬퍼를 그대로 쓰고 **경로만** 리서처 것으로 바꾼다.

사용
  python3 scripts/researcher_cycle.py --tick          # 크론이 호출(수 초)
  python3 scripts/researcher_cycle.py --status
  python3 scripts/researcher_cycle.py --run <ID> [--force]
"""

import argparse
import json
import os
import re
import subprocess
import sys
from datetime import datetime

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import model_engineer_cycle as base  # noqa: E402

PROJ = base.PROJ
RES_BACKLOG = os.path.join(PROJ, "docs/QUANT_RESEARCH_BACKLOG.json")
RES_LEDGER = os.path.join(PROJ, "data/reports/researcher_ledger.jsonl")
RES_RUNTIME = os.path.join(PROJ, "data/reports/res_cycle")
RES_LOGDIR = os.path.join(RES_RUNTIME, "logs")
FINDINGS = os.path.join(PROJ, "docs/QUANT_FINDINGS.md")
MODEL_BACKLOG = base.BACKLOG
SNAPSHOT = os.path.join(PROJ, "scripts/dq_snapshot.py")

# 공용 헬퍼를 리서처 경로로 재바인딩(가드·락·원장 로직을 복제하지 않는다).
base.BACKLOG = RES_BACKLOG
base.LEDGER = RES_LEDGER
base.RUNTIME = RES_RUNTIME
base.LOGDIR = RES_LOGDIR
base.PIDFILE = os.path.join(RES_RUNTIME, "running.pid")
base.STATE = os.path.join(RES_RUNTIME, "state.json")
KST = base.KST
now_kst = base.now_kst
log = base.log


# ── 모니터링 ────────────────────────────────────────────────────────────────
def snapshot(hours=24):
    """DQ 스냅샷 + 차트를 남기고 (rc, stdout) 을 돌려준다. 실패해도 예외로 죽지 않는다."""
    try:
        p = subprocess.run(["/usr/bin/python3", SNAPSHOT, "--hours", str(hours)],
                           capture_output=True, text=True, timeout=600, cwd=PROJ)
        return p.returncode, (p.stdout or "") + (p.stderr or "")
    except Exception as exc:   # noqa: BLE001 - 모니터링 실패가 수집을 막으면 안 된다
        return 99, f"스냅샷 실행 실패: {exc}"


def snapshot_brief(out):
    """틱 출력용 요약 — 위반/경고 줄과 차트 경로만 뽑는다."""
    lines = [ln for ln in out.splitlines()
             if ln.strip().startswith(("[WARN]", "[위반]", "★", "·", "차트:"))]
    return lines


# ── check(수치) 판정 ────────────────────────────────────────────────────────
def eval_check(item):
    chk = item.get("check")
    tgt = item.get("check_target") or {}
    if not chk:
        return None, "check 미정의", None
    try:
        p = subprocess.run(chk, shell=True, capture_output=True, text=True, timeout=180, cwd=PROJ)
    except subprocess.TimeoutExpired:
        return None, "check 타임아웃", None
    nums = re.findall(r"[-+]?\d+(?:\.\d+)?", p.stdout or "")
    if not nums:
        return None, f"check 출력에 수치 없음: {(p.stdout or '').strip()[:80]}", None
    val = float(nums[-1])
    op, tval = tgt.get("op", ">="), float(tgt.get("value", 0))
    ok = {">=": val >= tval, ">": val > tval, "==": val == tval, "<=": val <= tval}.get(op, False)
    return val, f"{item['id']}: {val:g} {op} {tval:g} → {'충족' if ok else '미달'}", ok


# ── 협업: 리서처 결과를 엔지니어 백로그로 넘긴다 ─────────────────────────────
def handoff(item, detail, verdict):
    if not item.get("affects_model"):
        return None
    try:
        b = json.load(open(MODEL_BACKLOG, encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        log(f"협업 실패(엔지니어 백로그 읽기): {exc}")
        return None
    new_id = "X" + item["id"]
    if any(i.get("from_research") == item["id"] or i.get("id") == new_id for i in b["items"]):
        log(f"협업: {new_id} 는 이미 엔지니어 백로그에 있음(중복 방지)")
        return new_id
    b["items"].append({
        "id": new_id,
        "title": f"[리서처 {item['id']}] {item['title']}",
        "status": "backlog",
        "priority": 8,
        "from_research": item["id"],
        "arm": None,
        "counterfactual": None,
        "baseline": None,
        "command": None,   # 구동기는 command 가 없으면 실행하지 않는다 → 엔지니어가 채워야 실행된다
        "metric": "wf_sweep_summary",
        "hypothesis": item.get("hypothesis"),
        "evidence": f"리서처 {item['id']} 결과: {detail}",
        "success": item.get("success"),
        "expected": "미지",
        "cost": item.get("cost"),
        "note": ("리서처가 넘긴 항목 — command·arm·counterfactual 을 엔지니어가 채워야 실행된다. "
                 "데이터 준비가 끝났는지 먼저 확인하라."),
    })
    tmp = MODEL_BACKLOG + ".tmp"
    json.dump(b, open(tmp, "w", encoding="utf-8"), ensure_ascii=False, indent=2)
    os.replace(tmp, MODEL_BACKLOG)

    os.makedirs(os.path.dirname(FINDINGS), exist_ok=True)
    with open(FINDINGS, "a", encoding="utf-8") as f:
        f.write(f"\n## [리서처 {item['id']}] {item['title']}  ({now_kst().strftime('%Y-%m-%d %H:%M')})\n"
                f"- 결과: {detail}\n"
                f"- 판정: {verdict}\n"
                f"- 근거: {item.get('evidence', '-')}\n"
                f"- 엔지니어 백로그: `{new_id}` (command·대조군 기입 필요)\n")
    log(f"협업: 엔지니어 백로그에 {new_id} 추가 + {os.path.relpath(FINDINGS, PROJ)} 기록")
    return new_id


# ── 실행 ────────────────────────────────────────────────────────────────────
def ensure_artifact(item, run_log):
    """build형 항목의 산출물이 없으면 Claude Code 로 **저작**한다(ask_claude.sh build 모드).

    WHY(2026-09-25 사용자 승인 ①): 자율 루프는 "등록된 명령을 실행·판정"만 할 수 있어 새 파이프라인을
    쓰지 못한다(실측 한계 — R10~R12 가 그래서 대기 중이었다). 그래서 항목에 `authoring.target` 이
    선언돼 있으면 위임으로 저작하고, 저작물은 ① 파일 존재 ② 구문(py_compile) ③ 항목 check —
    세 단계를 통과해야 완료로 기록한다. **자기신고 금지**: 파일 존재가 증거다.

    위임 자체의 실패(무출력·정지)는 ask_claude.sh 안에서 이미 감시·폴백된다(Claude Code → opencode).
    """
    au = item.get("authoring") or {}
    target = au.get("target")
    if not target:
        return True, "", {}
    tpath = os.path.join(PROJ, target)
    if os.path.exists(tpath):
        return True, f"산출물 이미 존재({target}) — 저작 생략", {"target": target, "skipped": True}

    spec_rel = f"docs/spec_{item['id']}.md"
    prompt = "\n".join(filter(None, [
        f"[구현 대상] {target}",
        f"[항목] {item['id']} {item['title']}",
        f"[가설] {item.get('hypothesis') or ''}",
        f"[방법] {item.get('method') or ''}",
        f"[검증 명령] {item.get('check') or ''}",
        f"[성공 기준] {item.get('success') or ''}",
        f"[추가 지시] {au.get('instructions') or ''}",
    ]))
    log(f"저작 위임: {item['id']} → {target} (ask_claude.sh build)")
    try:
        r = subprocess.run(["./scripts/ask_claude.sh", "build", spec_rel, prompt],
                           cwd=PROJ, capture_output=True, text=True,
                           timeout=int(au.get("timeout_s") or 2400))
        rc, err = r.returncode, (r.stderr or "")[-300:]
    except subprocess.TimeoutExpired:
        return False, f"저작 위임 시간초과({target})", {"target": target, "timeout": True}
    except OSError as exc:
        return False, f"저작 위임 실행 실패({exc})", {"target": target, "oserror": str(exc)[:200]}

    exists = os.path.exists(tpath)
    info = {"target": target, "delegate_rc": rc, "exists": exists, "stderr_tail": err, "spec": spec_rel}
    if not exists:
        return False, f"저작 실패 — 산출물 없음({target})", info
    if target.endswith(".py"):
        c = subprocess.run(["/usr/bin/python3", "-m", "py_compile", tpath],
                           capture_output=True, text=True)
        info["compile_rc"] = c.returncode
        if c.returncode != 0:
            info["compile_err"] = (c.stderr or "")[-200:]
            return False, f"저작물 구문 오류({target})", info
    # 자율 저작이 무엇을 건드렸는지 감사 기록을 남긴다(사후 검증 가능해야 한다).
    try:
        g = subprocess.run(["git", "status", "--porcelain"], cwd=PROJ,
                           capture_output=True, text=True, timeout=60)
        info["git_changed"] = (g.stdout or "").splitlines()[:12]
    except (OSError, subprocess.SubprocessError):
        pass
    return True, f"저작 완료({target}) — 구문검사 통과", info


def execute(item, force=False):
    os.makedirs(RES_RUNTIME, exist_ok=True)
    ok, why = base.guards(force)
    if not ok:
        log(f"시작 보류: {why}")
        return 3

    src_rc, src_out = snapshot(24)
    print(f"[모니터링] dq_snapshot rc={src_rc}")
    for ln in snapshot_brief(src_out):
        print("  " + ln)

    if not item.get("command"):
        log(f"{item['id']} 는 setup 대기(pending 인데 command 없음) — 실행하지 않음")
        return 3

    os.makedirs(RES_LOGDIR, exist_ok=True)
    stamp = now_kst().strftime("%Y%m%d-%H%M%S")
    run_log = os.path.join(RES_LOGDIR, f"res_{item['id']}_{stamp}.log")
    started = now_kst()
    # build형 항목은 실행 전에 산출물 저작을 먼저 확보한다(없으면 위임 — 자율 저작).
    ok_art, art_msg, art_info = ensure_artifact(item, run_log)
    if art_msg:
        log(art_msg)
    log(f"실행: {item['id']} — {item['title']}")
    with open(run_log, "w", encoding="utf-8") as lf:
        lf.write(f"# {item['id']} {item['title']}\n# started {started.isoformat()}\n"
                 f"# command: {item['command']}\n\n")
        lf.flush()
        rc = subprocess.run(item["command"], shell=True, stdout=lf,
                            stderr=subprocess.STDOUT, cwd=PROJ).returncode

    val, detail, passed = eval_check(item)
    # 저작 결과를 판정 문구에 합친다 — 저작 실패면 명령도 실패하므로 원인이 한 줄에 남아야 한다.
    # (⚠ eval_check 뒤에 합쳐야 한다: detail 은 그 호출이 만든다 — 앞에서 참조하면 NameError.)
    if art_msg and art_info:
        detail = f"[저작] {art_msg} | {detail}" if art_info.get("exists") else f"[저작실패] {art_msg}"
    # kind="investigate" 는 수치 목표가 아니라 **증거 수집**이 목적이다(DART/KRX 소스 확인 등).
    # 이 경우 check_target 미달을 실패로 보지 않는다 — 조사가 돌았으면 완료다.
    if item.get("kind") == "investigate":
        verdict = "조사완료" if rc == 0 else "실패"
        passed = (rc == 0)
        # 조사형은 **로그의 증거**가 결과물이다 → 로그 꼬리를 detail 에 붙여 틱이 바로 보여주게 한다.
        # (수치 check 만 보면 "0 >= 10 미달"만 남아 무엇을 알았는지 알 수 없다.)
        tail = ""
        try:
            with open(run_log, encoding="utf-8") as f:
                lines = [ln.strip() for ln in f if ln.strip() and not ln.startswith("#")]
            tail = " | ".join(lines[-3:])[:300]
        except OSError:
            pass
        detail = f"[조사]{detail}" + (f" | 증거: {tail}" if tail else "")
    else:
        verdict = "충족" if passed else ("미달" if passed is False else "판정불가")
    new_id = handoff(item, detail, verdict)

    rec = {"ts": now_kst().isoformat(timespec="seconds"), "id": item["id"], "title": item["title"],
           "rc": rc, "elapsed_min": round((now_kst() - started).total_seconds() / 60.0, 1),
           "log": os.path.relpath(run_log, PROJ), "check_value": val,
           "detail": detail, "verdict": verdict,
           "snapshot_rc": src_rc, "handoff": new_id, "reported": False}
    base.append_ledger(rec)

    b = json.load(open(RES_BACKLOG, encoding="utf-8"))
    for it in b["items"]:
        if it["id"] == item["id"]:
            it.setdefault("attempts", []).append({"ts": rec["ts"], "rc": rc, "detail": detail})
            if item.get("kind") == "investigate":
                it["status"] = "done" if rc == 0 else "failed"
            elif rc == 0 and passed:
                it["status"] = "done"
            elif rc == 0:
                # 수집형은 목표 미달이면 partial — 다음 사이클에 이어서 수집한다(점진 수집이 정상).
                it["status"] = "pending" if item.get("incremental") else "partial"
            else:
                it["status"] = "failed"
            it["result"] = {"detail": detail, "check_value": val, "rc": rc, "log": rec["log"]}
    tmp = RES_BACKLOG + ".tmp"
    json.dump(b, open(tmp, "w", encoding="utf-8"), ensure_ascii=False, indent=2)
    os.replace(tmp, RES_BACKLOG)
    log(f"종료 rc={rc} 경과 {rec['elapsed_min']}분 → {rec['verdict']}: {detail}")
    return 0


# ── 틱 ──────────────────────────────────────────────────────────────────────
def north_star(role):
    """목표 사슬 스코어보드에서 내 북극성 한 줄을 가져온다.

    WHY(2026-09-25 사용자 지시): 각 역할은 "사이클을 돌았다"가 아니라 **자기 목표 지표**로
    판정되어야 한다 — 리서처=데이터 품질, 엔지니어=로버스트 AUC, 트레이더=순손익(₩).
    실패해도 틱은 계속 돌아야 하므로 예외를 삼킨다(정보 줄이지 치명 경로가 아니다).
    """
    try:
        import subprocess
        import sys
        r = subprocess.run(
            [sys.executable, os.path.join(PROJ, "scripts/quant_scoreboard.py"), "--stanza", role],
            capture_output=True, text=True, timeout=90)
        return (r.stdout or "").strip()
    except Exception:   # noqa: BLE001 — 정보 줄이지 치명 경로가 아니다(import 실패까지 포함)
        return ""


def tick(force=False):
    pid = base.running_pid()
    if pid:
        st = {}
        try:
            st = json.load(open(base.STATE, encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            pass
        log(f"수집 실행 중: {st.get('id', '?')} pid={pid} 시작 {st.get('started', '?')}")
        try:
            with open(os.path.join(RES_RUNTIME, f"bg_{st.get('id', '')}.log"), encoding="utf-8") as f:
                tail = "".join(f.readlines()[-4:]).strip()
            if tail:
                print("  최근 출력:", tail.replace("\n", "\n  "))
        except OSError:
            pass
        return 0

    src_rc, src_out = snapshot(24)
    # 북극성 먼저: 데이터 품질이 목표(모델에 학습되는 양질 데이터)에 얼마나 가까운지 매 틱 확인.
    ns = north_star("researcher")
    if ns:
        print(ns)
    print(f"[모니터링] dq_snapshot rc={src_rc} (0=정상 2=경고 3=위반)")
    for ln in snapshot_brief(src_out):
        print("  " + ln)

    led = base.load_ledger()
    unreported = [r for r in led if not r.get("reported")]
    # 위생 상태(30분 주기 크론이 갱신)를 한 줄로 함께 보여준다 — 리포트마다 시스템 건강을 확인.
    try:
        with open(os.path.join(PROJ, "data/reports/hygiene/latest.json"), encoding="utf-8") as f:
            h = json.load(f)
        print(f"[위생] {h.get('status')} / 좀비 {len(h.get('zombies') or [])} / "
              f"디스크 {(h.get('disk') or {}).get('use_pct')}% / "
              f"댕글링볼륨 {(h.get('docker') or {}).get('dangling_volumes')}")
    except (OSError, json.JSONDecodeError):
        pass
    if unreported:
        for r in unreported:
            print(f"=== 결과 도착: {r['id']} — {r['title']}")
            print(f"  rc={r['rc']} 경과 {r['elapsed_min']}분 판정={r['verdict']} | {r['detail']}")
            if r.get("handoff"):
                print(f"  → 모델엔지니어 백로그로 넘김: {r['handoff']}")
            print(f"  로그: {r['log']}")
            r["reported"] = True
        base._rewrite_ledger(led)

    b = json.load(open(RES_BACKLOG, encoding="utf-8"))
    pend = sorted([i for i in b["items"] if i.get("status") == "pending"],
                  key=lambda x: x.get("priority", 99))
    print(f"=== pending {len(pend)}건: {', '.join(i['id'] for i in pend) or '없음'}")
    # 승인/사람 단계는 **백로그에서 자동으로 끌어올린다** — 보고에서 빠뜨리지 않기 위한 장치다.
    for i in sorted(b["items"], key=lambda x: x.get("priority", 99)):
        if i.get("status") in ("pending", "needs_approval", "partial", "failed"):
            for s in (i.get("setup_needed") or []):
                print(f"  ⚠ [{i['id']}] 사람/승인 필요: {s}")
            if i.get("blocked_by"):
                print(f"  ⛔ [{i['id']}] 차단: {i['blocked_by']}")
            if i.get("status") == "failed":
                print(f"  ✗ [{i['id']}] 실패 — 로그 확인 필요: {(i.get('result') or {}).get('log', '-')}")
    if unreported:
        return 0

    ok, why = base.guards(force)
    if not ok:
        print(f"대기: {why}")
        return 0
    nxt = base.next_item(b)
    if not nxt:
        print("실행 가능한 pending 없음 → setup_needed 해소 또는 새 항목 설계가 필요하다.")
        return 0
    cmd = [sys.executable, os.path.abspath(__file__), "--run", nxt["id"]]
    if force:
        cmd.append("--force")
    os.makedirs(RES_RUNTIME, exist_ok=True)
    bg = os.path.join(RES_RUNTIME, f"bg_{nxt['id']}.log")
    with open(bg, "w", encoding="utf-8") as lf:
        p = subprocess.Popen(cmd, stdout=lf, stderr=subprocess.STDOUT,
                             start_new_session=True, cwd=PROJ)
    with open(base.PIDFILE, "w", encoding="utf-8") as f:
        f.write(str(p.pid))
    with open(base.STATE, "w", encoding="utf-8") as f:
        json.dump({"id": nxt["id"], "pid": p.pid,
                   "started": now_kst().isoformat(timespec="seconds")}, f)
    print(f"시작: {nxt['id']} — {nxt['title']} (비용 {nxt.get('cost')})")
    return 0


def status():
    b = json.load(open(RES_BACKLOG, encoding="utf-8"))
    print(f"리서처 백로그: {RES_BACKLOG} (updated {b.get('updated_at')})")
    for i in sorted(b["items"], key=lambda x: x.get("priority", 99)):
        line = f"  [{i['status']:11s}] {i['id']:3s} {i['title']}"
        print(line)
        if i.get("result"):
            # result 는 dict({"detail": ...}) 또는 **문자열**(러너가 남긴 자유서술) 둘 다 온다.
            # 2026-09-25 실측: R9/R5 가 문자열이라 i['result']['detail'] 이 TypeError 로 죽어
            # --status 가 백로그 중간에서 끊겼다(항목들이 안 보여 "무음 실패"처럼 오인된다).
            r = i["result"]
            detail = r.get("detail") if isinstance(r, dict) else r
            print(f"                 → {detail}")
        for s in (i.get("setup_needed") or []):
            print(f"                 ⚠ {s}")
    print(f"실행 중: {base.running_pid() or '없음'}")
    for r in base.load_ledger(3):
        print(f"  원장 {r['ts']} {r['id']} rc={r['rc']} {r['verdict']} {r['elapsed_min']}분")
    print(f"가드: 장중={base.market_hours()} load1={base.load1():.2f} 컨테이너={base.container_up()}")
    return 0


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--status", action="store_true")
    ap.add_argument("--tick", action="store_true")
    ap.add_argument("--run")
    ap.add_argument("--force", action="store_true")
    a = ap.parse_args()
    if a.status:
        return status()
    if a.tick:
        return tick(a.force)
    if a.run:
        b = json.load(open(RES_BACKLOG, encoding="utf-8"))
        it = next((i for i in b["items"] if i["id"] == a.run), None)
        if not it:
            log(f"백로그에 {a.run} 없음")
            return 2
        rc = execute(it, a.force)
        try:
            os.remove(base.PIDFILE)
        except OSError:
            pass
        return rc
    ap.print_help()
    return 0


if __name__ == "__main__":
    sys.exit(main())

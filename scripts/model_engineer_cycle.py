#!/usr/bin/env python3
"""model_engineer_cycle — 장외시간 자율 연구 사이클 구동기 (quant-model-engineer 전용).

무엇을 하는가
  docs/QUANT_MODEL_BACKLOG.json 의 pending 항목을 **앞에서부터 하나씩** 집어 실행하고,
  결과(폴드 평균·std·판정)를 원장(data/reports/model_engineer_ledger.jsonl)과 백로그에 기록한다.
  사용자가 프롬프트를 넣지 않아도 모델 엔지니어가 계속 "다음 실험"을 돌리게 하는 심장부다.

설계 원칙 (실측 교훈 반영)
  1. **직렬화**: CPU 4코어다. lock 파일 + 부하 가드로 학습/평가 동시 실행을 막는다.
  2. **비블로킹 틱**: `--tick` 은 절대 오래 붙잡지 않는다. 오래 걸리는 실험은 nohup 으로
     백그라운드에 던지고 즉시 상태를 출력한다(크론 틱이 70분 붙잡히면 다음 틱이 밀린다).
  3. **장중 금지**: 평일 09:00~15:30 KST 에는 실험을 시작하지 않는다(트레이더/피드가 CPU 우선).
     --force 로만 무시한다. 오늘처럼 휴장이어도 시각 기준으로 보수적으로 막는다.
  4. **낡은 요약 금지**: 실행 전 요약 JSON 의 mtime 을 찍고, 실행 후 갱신되지 않았으면
     "요약 미갱신"으로 실패 처리한다(옛 결과를 새 결과로 오독하는 사고 방지).
  5. **자기신고 신뢰 금지**: 판정은 요약 JSON 의 폴드 값에서 직접 계산한다. 로그의 문구를 믿지 않는다.

사용
  python3 scripts/model_engineer_cycle.py --tick          # 크론이 호출(짧게 끝남)
  python3 scripts/model_engineer_cycle.py --status        # 전체 현황
  python3 scripts/model_engineer_cycle.py --run <ID> [--force]   # 특정 항목을 즉시(포그라운드)
  python3 scripts/model_engineer_cycle.py --start <ID> [--force] # 백그라운드로 시작만
"""

import argparse
import json
import os
import statistics
import subprocess
import sys
import time
from datetime import datetime, timedelta, timezone

PROJ = os.environ.get("ME_PROJ", "/home/jhshi/analyist_dd")
BACKLOG = os.path.join(PROJ, "docs/QUANT_MODEL_BACKLOG.json")
LEDGER = os.path.join(PROJ, "data/reports/model_engineer_ledger.jsonl")
RUNTIME = os.path.join(PROJ, "data/reports/me_cycle")
LOGDIR = os.path.join(PROJ, "data/reports/me_cycle/logs")   # 호스트 사용자 소유 경로.
# ⚠ services/xgboost-ml/reports/overnight 는 **컨테이너(uid 1000)가 소유**하므로 호스트에서
# 직접 쓰면 PermissionError 로 사이클이 죽는다(실측 2026-09-25). 컨테이너가 쓰는 요약 JSON 은
# 읽기만 하므로 문제없다. 호스트 측 사이클 로그는 우리 소유 경로에 남긴다.
PIDFILE = os.path.join(RUNTIME, "running.pid")
LOCKFILE = os.path.join(RUNTIME, "cycle.lock")
STATE = os.path.join(RUNTIME, "state.json")
KST = timezone(timedelta(hours=9))
CONTAINER = "stock_xgboost_ml"
LOAD_MAX = float(os.environ.get("ME_LOAD_MAX", "3.5"))
MARKET_OPEN, MARKET_CLOSE = (9, 0), (15, 30)
# KRX 휴장일 파일 — scripts/data_gap.py 가 데이터 공백을 probe 하며 자동 유지한다.
HOLIDAY_PATH = os.path.join(PROJ, "data/krx_holidays.json")


def now_kst():
    return datetime.now(KST)


def log(msg):
    print(f"[{now_kst().strftime('%H:%M:%S')}] {msg}", flush=True)


# ── 백로그 입출력 ────────────────────────────────────────────────────────────
def load_backlog():
    with open(BACKLOG, encoding="utf-8") as f:
        return json.load(f)


def save_backlog(b):
    b["updated_at"] = now_kst().isoformat(timespec="seconds")
    tmp = BACKLOG + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(b, f, ensure_ascii=False, indent=2)
    os.replace(tmp, BACKLOG)   # 원자적 교체 — 중간에 죽어도 백로그가 깨지지 않는다


def load_ledger(limit=None):
    if not os.path.exists(LEDGER):
        return []
    out = []
    with open(LEDGER, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
                try:
                    out.append(json.loads(line))
                except json.JSONDecodeError:
                    continue
    return out[-limit:] if limit else out


def append_ledger(rec):
    os.makedirs(os.path.dirname(LEDGER), exist_ok=True)
    with open(LEDGER, "a", encoding="utf-8") as f:
        f.write(json.dumps(rec, ensure_ascii=False) + "\n")


# ── 가드 ────────────────────────────────────────────────────────────────────
def running_pid(exclude_self=True):
    """pidfile 의 pid 가 살아 있으면 반환.

    exclude_self: 백그라운드로 뜬 **자식 프로세스 자신**은 부모가 써 둔 자기 pid 를 보고
    스스로를 '다른 사이클'로 오판해 즉시 종료한다(실측 2026-09-25: bg_L1.log =
    "시작 보류: 이미 사이클 실행 중"). 그래서 자기 pid 는 '없음'으로 취급한다.
    """
    try:
        with open(PIDFILE, encoding="utf-8") as f:
            pid = int(f.read().strip())
        if exclude_self and pid == os.getpid():
            return None
        os.kill(pid, 0)
        return pid
    except (OSError, ValueError, FileNotFoundError):
        return None


def market_hours(dt=None) -> bool:
    """평일 09:00~15:30 이면서 **휴장일이 아닐 때만** True.

    ⚠ 이전 구현은 시각만 봐서 **휴장일에도 실험을 전면 차단**했다. 실측(2026-09-25 추석 연휴):
    장중 가드가 하루 종일 걸려 자율 루프가 놀았다 — 휴장일은 거래가 없어 CPU 가 완전히 비는데도.
    휴장 판단은 data/krx_holidays.json 을 쓴다(scripts/data_gap.py 가 자동 유지하는 동일 소스).
    파일이 없거나 깨졌으면 **차단하지 않는다** — '모름'을 휴장으로 단정하면 장중에 무거운 작업이
    돌 수 있고, 반대로 차단하면 휴장일을 버린다. 이 경우 load 가드(load1)가 여전히 보호한다.
    """
    dt = dt or now_kst()
    if dt.weekday() >= 5:
        return False
    try:
        with open(HOLIDAY_PATH, encoding="utf-8") as f:
            if dt.strftime("%Y-%m-%d") in {str(d) for d in json.load(f)}:
                return False
    except (OSError, json.JSONDecodeError):
        pass
    t = (dt.hour, dt.minute)
    return MARKET_OPEN <= t < MARKET_CLOSE


def market_note(dt=None) -> str:
    """가드가 왜 막았는지 사람이 읽을 수 있게."""
    dt = dt or now_kst()
    if dt.weekday() >= 5:
        return "주말"
    if not market_hours(dt):
        return "휴장일(거래 없음) — 실험 가능"
    return "장중(09:00~15:30) — 트레이더/피드가 CPU 우선"


def load1():
    try:
        return os.getloadavg()[0]
    except OSError:
        return 0.0


def container_up():
    r = subprocess.run(["docker", "inspect", "-f", "{{.State.Running}}", CONTAINER],
                       capture_output=True, text=True, timeout=30)
    return r.stdout.strip() == "true"


# 두 역할(모델엔지니어·리서처)이 **각자 락**을 갖고 있어 서로의 작업을 모른다.
# CPU 가 4코어뿐이므로 교차 락이 필요하다 — 없으면 16:00 에 학습(엔지니어)과 수집(리서처)이
# 동시에 시작해 둘 다 느려지거나 OOM/재시작으로 죽는다(과거 패널 빌드 SIGKILL 전례).
PEER_PIDFILES = ("data/reports/me_cycle/running.pid", "data/reports/res_cycle/running.pid")


def peer_running():
    """다른 역할의 실행 중 프로세스가 있으면 (pid, 경로) 를 돌려준다."""
    me = os.path.abspath(PIDFILE)
    for rel in PEER_PIDFILES:
        p = os.path.join(PROJ, rel)
        if os.path.abspath(p) == me:
            continue
        try:
            with open(p, encoding="utf-8") as f:
                pid = int(f.read().strip())
        except (OSError, ValueError):
            continue
        if pid == os.getpid():
            continue
        try:
            os.kill(pid, 0)
            return pid, rel
        except OSError:
            continue
    return None, None


def guards(force=False) -> tuple:
    """(ok, 이유). 시작해도 되는가."""
    if running_pid():
        return False, "이미 사이클 실행 중"
    pid, rel = peer_running()
    if pid:
        return False, f"다른 역할이 실행 중(pid={pid}, {rel}) — CPU 직렬화를 위해 대기"
    if not container_up():
        return False, f"{CONTAINER} 컨테이너가 떠 있지 않음"
    if market_hours() and not force:
        return False, f"{market_note()} — 시작하지 않음 (--force 로 무시)"
    l = load1()
    if l > LOAD_MAX:
        return False, f"부하 과다 load1={l:.2f} > {LOAD_MAX} — 다른 학습이 도는 중"
    return True, "ok"


def next_item(backlog):
    pend = [i for i in backlog["items"] if i.get("status") == "pending"]
    pend.sort(key=lambda i: (i.get("priority", 99), i["id"]))
    for i in pend:
        if not i.get("command"):
            log(f"경고: {i['id']} 는 pending 인데 command 가 없다 → 건너뜀"
                f"{' (setup: ' + str(i.get('setup_needed'))[:80] + ')' if i.get('setup_needed') else ''}")
            continue
        return i
    return None


# ── 지표 파싱 (자기신고 금지 — 요약 JSON 에서 직접 계산) ───────────────────────
def summary_path(kind):
    if kind == "wf_sweep_summary":
        return os.path.join(PROJ, "services/xgboost-ml/reports/overnight/wf_label_sweep_summary.json")
    raise ValueError(f"알 수 없는 metric: {kind}")


def parse_wf_sweep(path, mtime_floor) -> dict:
    """요약 JSON 에서 설정별 폴드 평균을 뽑아 평균·std·폴드승률을 계산한다."""
    if not os.path.exists(path):
        return {"error": "요약 파일 없음"}
    mt = os.path.getmtime(path)
    if mt < mtime_floor:
        return {"error": "요약 미갱신(실행 전보다 새롭지 않음) — 옛 결과를 새 결과로 오독 방지",
                "mtime": mt, "floor": mtime_floor}
    with open(path, encoding="utf-8") as f:
        d = json.load(f)
    per: dict = {}
    for r in d.get("results", []):
        folds = r.get("folds") or {}
        means = [float(v["mean"]) for v in folds.values()
                 if isinstance(v, dict) and v.get("mean") is not None]
        if not means:
            continue
        per[r.get("exp")] = {
            "desc": r.get("desc"),
            "kind": r.get("kind"), "horizon": r.get("horizon"),
            "folds": [round(m, 4) for m in means],
            "mean": round(statistics.mean(means), 4),
            "std": round(statistics.pstdev(means), 4),
            "min": round(min(means), 4),
            "max": round(max(means), 4),
            "fold_win_rate": round(sum(1 for m in means if m > 0.5) / len(means), 3),
        }
    return {"finished_at": d.get("finished_at"), "config": d.get("config"), "per_exp": per}


# ── 사이클 실행 ──────────────────────────────────────────────────────────────
def execute(item, force=False):
    os.makedirs(RUNTIME, exist_ok=True)
    ok, why = guards(force)
    if not ok:
        log(f"시작 보류: {why}")
        return 3

    os.makedirs(LOGDIR, exist_ok=True)
    stamp = now_kst().strftime("%Y%m%d-%H%M%S")
    run_log = os.path.join(LOGDIR, f"me_cycle_{item['id']}_{stamp}.log")
    started = now_kst()

    spath = summary_path(item["metric"])
    mtime_floor = os.path.getmtime(spath) if os.path.exists(spath) else 0.0

    log(f"실행: {item['id']} — {item['title']}")
    log(f"로그: {run_log}")
    with open(run_log, "w", encoding="utf-8") as lf:
        lf.write(f"# {item['id']} {item['title']}\n# started {started.isoformat()}\n"
                 f"# command: {item['command']}\n\n")
        lf.flush()
        proc = subprocess.run(item["command"], shell=True, stdout=lf,
                              stderr=subprocess.STDOUT, cwd=PROJ)
    rc = proc.returncode

    parsed = parse_wf_sweep(spath, mtime_floor) if item["metric"] == "wf_sweep_summary" \
        else {"error": "parser 없음"}

    # 판정: **가설군(item['arm'])** 을 **대조군(counterfactual)** 과 비교한다.
    # ⚠ 함정(실측 2026-09-25): '최고 점수(winner) vs 대조군' 으로 비교하면, 가설군이 **진** 경우
    # winner == 대조군 이 되어 Δ 0.0000 "노이즈" 로 잘못 기록된다. 실제로는 h8 0.5068 vs
    # h5 0.5406 = Δ−0.0338 인데 원장에 Δ+0.0000 으로 남았다. 반드시 arm 기준으로 계산하라.
    verdict, detail, delta = "판정불가", "", None
    per: dict = parsed.get("per_exp") or {}
    if per:
        arm = item.get("arm")
        cf_name = (item.get("counterfactual") or "").split(" ")[0]
        best = max(per.items(), key=lambda kv: kv[1]["mean"])
        if arm and arm in per and cf_name in per:
            delta = round(per[arm]["mean"] - per[cf_name]["mean"], 4)
            verdict = "신호있음" if delta >= 0.02 else ("악화" if delta <= -0.02 else "노이즈")
            detail = (f"가설 {arm} {per[arm]['mean']:.4f} vs 대조군 {cf_name} "
                      f"{per[cf_name]['mean']:.4f} → Δ{delta:+.4f} "
                      f"(최고: {best[0]} {best[1]['mean']:.4f})")
        elif cf_name in per:
            delta = round(best[1]["mean"] - per[cf_name]["mean"], 4)
            verdict = "신호있음" if delta >= 0.02 else "노이즈"
            detail = (f"[arm 미지정] 최고 {best[0]} {best[1]['mean']:.4f} vs {cf_name} "
                      f"{per[cf_name]['mean']:.4f} → Δ{delta:+.4f}")
        elif item.get("baseline") and arm and arm in per:
            # **다른 실행(다른 유니버스/패널)과의 비교**: 대조군이 같은 요약에 없을 때 쓴다.
            # baseline 은 백로그에 기록된 기준선 실측값이다(출처를 함께 적어 추적 가능하게).
            base = float(item["baseline"]["value"])
            delta = round(per[arm]["mean"] - base, 4)
            verdict = "신호있음" if delta >= 0.02 else ("악화" if delta <= -0.02 else "노이즈")
            detail = (f"가설 {arm} {per[arm]['mean']:.4f} vs 기록 기준선 {base:.4f} "
                      f"({item['baseline'].get('source', '출처미상')}) → Δ{delta:+.4f}")
        else:
            verdict, detail = "기준선없음", (f"최고 {best[0]} {best[1]['mean']:.4f} "
                                        f"(대조군 {cf_name} 미측정)")
    elif parsed.get("error"):
        detail = parsed["error"]

    rec = {
        "ts": now_kst().isoformat(timespec="seconds"),
        "id": item["id"], "title": item["title"],
        "rc": rc, "elapsed_min": round((now_kst() - started).total_seconds() / 60.0, 1),
        "log": os.path.relpath(run_log, PROJ),
        "metric": item["metric"], "parsed": parsed,
        "verdict": verdict, "detail": detail,
        "reported": False,
    }
    append_ledger(rec)

    b = load_backlog()
    for it in b["items"]:
        if it["id"] == item["id"]:
            it.setdefault("attempts", []).append({
                "ts": rec["ts"], "rc": rc, "verdict": verdict, "detail": detail,
                "log": rec["log"], "elapsed_min": rec["elapsed_min"],
            })
            it["status"] = "done" if rc == 0 else "failed"
            it["result"] = {"verdict": verdict, "detail": detail, "delta": delta,
                            "per_exp": per or None, "rc": rc}
    save_backlog(b)
    log(f"종료 rc={rc} 경과 {rec['elapsed_min']}분 → 판정: {verdict} {detail}")
    return 0


def start_background(item_id, force=False):
    """nohup 으로 자기 자신을 띄우고 즉시 반환(크론 틱을 붙잡지 않는다)."""
    os.makedirs(RUNTIME, exist_ok=True)
    cmd = [sys.executable, os.path.abspath(__file__), "--run", item_id]
    if force:
        cmd.append("--force")
    logfile = os.path.join(RUNTIME, f"bg_{item_id}.log")
    # "w" 로 truncate: append 로 두면 이전 실행의 크래시 스택트레이스가 남아
    # 틱이 "최근 출력"으로 옛 오류를 보여준다(실측 2026-09-25 혼동 발생).
    with open(logfile, "w", encoding="utf-8") as lf:
        p = subprocess.Popen(cmd, stdout=lf, stderr=subprocess.STDOUT,
                             start_new_session=True, cwd=PROJ)
    with open(PIDFILE, "w", encoding="utf-8") as f:
        f.write(str(p.pid))
    with open(STATE, "w", encoding="utf-8") as f:
        json.dump({"id": item_id, "pid": p.pid, "started": now_kst().isoformat(timespec="seconds")}, f)
    log(f"백그라운드 시작: {item_id} pid={p.pid} (로그 {os.path.relpath(logfile, PROJ)})")
    return 0


# ── 틱/상태 출력 (에이전트가 읽는 요약) ────────────────────────────────────────
def north_star(role):
    """목표 사슬 스코어보드에서 내 북극성 한 줄을 가져온다.

    WHY(2026-09-25 사용자 지시): 엔지니어의 목표는 "실험을 돌렸다"가 아니라 **로버스트 AUC 향상**이다.
    매 틱 현재 최고 로버스트 값과 기준선 대비 델타, 무개선 사이클 수를 보고 앞에 붙인다.
    실패해도 틱은 계속 돌아야 하므로 예외를 삼킨다.
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
    ns = north_star("engineer")
    if ns:
        print(ns)
    pid = running_pid()
    if pid:
        st = {}
        try:
            with open(STATE, encoding="utf-8") as f:
                st = json.load(f)
        except (OSError, json.JSONDecodeError):
            pass
        log(f"사이클 실행 중: {st.get('id', '?')} pid={pid} 시작 {st.get('started', '?')}")
        tail = ""
        try:
            with open(os.path.join(RUNTIME, f"bg_{st.get('id', '')}.log"), encoding="utf-8") as f:
                tail = "".join(f.readlines()[-4:]).strip()
        except OSError:
            pass
        if tail:
            print("  최근 출력:", tail.replace("\n", "\n  "))
        return 0

    # ── 조용한 실패 감지: pidfile 은 있는데 프로세스가 죽었고 원장 기록도 없다 ──
    # 이 검사가 없으면 크래시(권한·문법·컨테이너 재시작)로 죽은 사이클이 흔적 없이 사라져
    # "왜 오늘 실험이 하나도 안 돌았지?" 를 아무도 모른다.
    if os.path.exists(PIDFILE):
        orphan, started = None, None
        try:
            with open(STATE, encoding="utf-8") as f:
                st_old = json.load(f)
            orphan, started = st_old.get("id"), st_old.get("started")
        except (OSError, json.JSONDecodeError):
            pass
        try:
            os.remove(PIDFILE)
        except OSError:
            pass
        if orphan:
            # 이 사이클의 시작 시각 이후로 그 id 의 원장 기록이 있는가?
            done = any(r.get("id") == orphan and (not started or r.get("ts", "") >= started)
                       for r in load_ledger())
            if not done:
                print(f"=== ⚠ 사이클이 기록 없이 죽었다: {orphan} (시작 {started})")
                try:
                    with open(os.path.join(RUNTIME, f"bg_{orphan}.log"), encoding="utf-8") as f:
                        tail = "".join(f.readlines()[-6:]).strip()
                    print("  로그 꼬리:", tail.replace("\n", "\n  "))
                except OSError:
                    pass
                print("  → 원인을 고치고 다시 시작하라(백로그에 기록되지 않았다).")
                return 0

    led = load_ledger()
    unreported = [r for r in led if not r.get("reported")]
    if unreported:
        for r in unreported:
            print(f"=== 결과 도착: {r['id']} — {r['title']}")
            print(f"  rc={r['rc']} 경과 {r['elapsed_min']}분 판정={r['verdict']}")
            print(f"  근거: {r['detail']}")
            per: dict = (r.get("parsed") or {}).get("per_exp") or {}
            for name, v in per.items():
                # 표시용 통계는 없을 수 있다(정정·부분 기록) → .get 으로 읽어 절대 죽지 않게 한다.
                # 실측 2026-09-25: 정정 스크립트가 std 를 빼고 써서 tick 이 KeyError 로 죽었다.
                print(f"    {name}: 폴드 평균 {v.get('mean')} std {v.get('std', '-')} "
                      f"(min {v.get('min', '-')} max {v.get('max', '-')}) "
                      f"폴드승률 {v.get('fold_win_rate', '-')} {v.get('folds', [])}")
            print(f"  로그: {r['log']}")
            # 보고 처리 표시(같은 결과를 매 틱 반복 보고하지 않는다)
            r["reported"] = True
        _rewrite_ledger(led)
        b = load_backlog()
        p = [i for i in b["items"] if i.get("status") == "pending"]
        print(f"=== 남은 pending: {len(p)}건 "
              f"({', '.join(i['id'] for i in sorted(p, key=lambda x: x.get('priority', 99))[:4])})")
        print("→ 이 결과를 사용자에게 3부 형식으로 보고하고, 필요하면 다음 가설을 설계하라.")
        return 0

    ok, why = guards(force)
    if not ok:
        print(f"대기: {why}")
        return 0
    it = next_item(load_backlog())
    if not it:
        print("백로그에 실행 가능한 pending 항목 없음 → 새 가설을 설계해 backlog/needs_setup 로 추가하라.")
        return 0
    start_background(it["id"], force)
    print(f"시작: {it['id']} — {it['title']} (기대 {it.get('expected')}, 비용 {it.get('cost')})")
    return 0


def _rewrite_ledger(rows):
    tmp = LEDGER + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        for r in rows:
            f.write(json.dumps(r, ensure_ascii=False) + "\n")
    os.replace(tmp, LEDGER)


def status():
    b = load_backlog()
    print(f"백로그: {BACKLOG} (updated {b.get('updated_at')})")
    for i in sorted(b["items"], key=lambda x: x.get("priority", 99)):
        print(f"  [{i['status']:11s}] {i['id']:3s} {i['title']}")
        if i.get("result"):
            r = i["result"]
            print(f"                 → {r['verdict']} {r['detail']}")
    pid = running_pid()
    print(f"실행 중: {pid if pid else '없음'}")
    led = load_ledger(5)
    if led:
        print("최근 원장(5):")
        for r in led:
            print(f"  {r['ts']} {r['id']} rc={r['rc']} {r['verdict']} "
                  f"{r['elapsed_min']}분 reported={r.get('reported')}")
    print(f"가드: 장중={market_hours()} load1={load1():.2f} 컨테이너={container_up()}")
    return 0


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--status", action="store_true")
    ap.add_argument("--tick", action="store_true")
    ap.add_argument("--run")
    ap.add_argument("--start")
    ap.add_argument("--force", action="store_true")
    a = ap.parse_args()

    if a.status:
        return status()
    if a.tick:
        return tick(a.force)
    if a.start:
        return start_background(a.start, a.force)
    if a.run:
        b = load_backlog()
        it = next((i for i in b["items"] if i["id"] == a.run), None)
        if not it:
            log(f"백로그에 {a.run} 없음")
            return 2
        rc = execute(it, a.force)
        # 백그라운드 실행이 끝나면 pidfile 정리
        try:
            os.remove(PIDFILE)
        except OSError:
            pass
        return rc
    ap.print_help()
    return 0


if __name__ == "__main__":
    sys.exit(main())

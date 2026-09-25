#!/usr/bin/env python3
"""system_hygiene — 시스템 위생 점검 (좀비·정지컨테이너·댕글링 자원·캐시·디스크).

왜 필요한가
  오늘 두 번의 실제 사고가 "위생" 문제였다:
   ① 좀비 5개가 컨테이너(stock_news_analyzer) 안에 1~2일 쌓여 있었다(앱이 subprocess 를 reap 안 함).
   ② 뉴스 저장이 2일간 전부 실패(3,657건 폐기) — 로그를 grep 하기 전엔 아무도 몰랐다.
  둘 다 **주기적으로 기계가 보면 즉시 드러나는** 종류다.

설계 원칙
  - **조용한 감시**: 정상이면 아무것도 출력하지 않는다(--quiet). 크론(no_agent)이 이 출력을 그대로
    전달하므로, 문제가 있을 때만 사용자에게 메시지가 간다(하루 48회 알림 소음 방지).
  - **거짓 성공 금지**: 각 판정은 실측 명령(docker system df, ps, df)의 출력에 근거한다.
  - **자동 정리는 증명된 안전 항목만**: 빌드 캐시(런타임 무영향)·오래된 /tmp 파일·스냅샷 보존.
    볼륨/이미지는 **절대 자동 삭제하지 않는다**(데이터·복구 가능성). 발견만 하고 보고한다.
  - 상태는 파일로 남긴다: data/reports/hygiene/latest.json + history.jsonl (추세 확인용).

사용
  /usr/bin/python3 scripts/system_hygiene.py               # 전체 출력(수동 점검)
  /usr/bin/python3 scripts/system_hygiene.py --quiet       # 정상이면 침묵(크론)
  /usr/bin/python3 scripts/system_hygiene.py --clean       # 안전 항목 자동 정리
  종료코드: 0=정상, 2=경고, 3=위반
"""

import argparse
import glob
import json
import os
import re
import subprocess
import sys
from datetime import datetime, timedelta, timezone

PROJ = os.environ.get("HYG_PROJ", "/home/jhshi/analyist_dd")
OUTDIR = os.path.join(PROJ, "data/reports/hygiene")
KST = timezone(timedelta(hours=9))

# 임계값 (초과 시 경고/위반)
TH = {
    "zombie_warn": 1, "zombie_breach": 10,
    "disk_warn": 85.0, "disk_breach": 92.0,
    "buildcache_warn_gb": 5.0,
    "tmp_warn_mb": 500.0,
    "restart_warn": 5,
    "snapshots_warn": 60,
    "stale_loop_warn": 1,
}


def sh(cmd, timeout=60):
    try:
        p = subprocess.run(cmd, shell=True, capture_output=True, text=True, timeout=timeout)
        return (p.stdout or "") + (p.stderr or "")
    except Exception as exc:  # noqa: BLE001
        return f"__ERR__ {exc}"


def now():
    return datetime.now(KST)


# ── 점검 항목 ───────────────────────────────────────────────────────────────
def check_zombies():
    """호스트에서 보이는 좀비 + 그 부모가 어느 컨테이너인지 매핑."""
    out = sh("ps -eo stat,ppid,pid,comm --no-headers")
    zombies = []
    for line in out.splitlines():
        parts = line.split(None, 3)
        if len(parts) < 4 or not parts[0].startswith("Z"):
            continue
        _, ppid, pid, comm = parts
        zombies.append({"pid": pid, "ppid": ppid, "comm": comm})

    # 부모 PID → 컨테이너 이름 매핑 (cgroup 에 컨테이너 ID 가 들어 있다)
    owner = {}
    for z in zombies:
        ppid = z["ppid"]
        if ppid in owner:
            z["container"] = owner[ppid]
            continue
        cg = ""
        try:
            with open(f"/proc/{ppid}/cgroup", encoding="utf-8") as f:
                cg = f.read()
        except OSError:
            pass
        ids = re.findall(r"[0-9a-f]{64}", cg)
        name = None
        if ids:
            listing = sh("docker ps --no-trunc --format '{{.ID}} {{.Names}}'")
            for cid, cname in (ln.split() for ln in listing.splitlines() if ln.strip()):
                if any(i.startswith(cid[:12]) for i in ids):
                    name = cname
                    break
        try:
            with open(f"/proc/{ppid}/cmdline", "rb") as f:
                pcmd = f.read().decode("utf-8", "replace").replace("\x00", " ").strip()
        except OSError:
            pcmd = ""
        owner[ppid] = name or (pcmd[:40] or f"pid {ppid}")
        z["container"] = owner[ppid]
    return zombies


def check_stale_loops():
    """자기매칭 대기 루프(과거 함정) / 장시간 방치된 내 스크립트."""
    out = sh("ps -eo pid,etime,cmd --no-headers")
    stale = []
    for line in out.splitlines():
        if "while pgrep" not in line:
            continue
        # ⚠ 오탐 제외: **이 검사 자체를 수행하는 명령**이 잡힌다 — 점검 커맨드라인에
        # `pgrep -af "while pgrep"` 같은 문자열이 들어 있으면 그 셸이 매칭된다(실측 2026-09-25).
        # ⚠ 더 큰 함정: `pgrep -f` 에는 **'grep -' 부분문자열이 들어 있다**. 그래서 'grep -' 로
        # 거르면 **진짜 루프까지 걸러져 탐지에 실패한다**(실측: 시험 루프를 놓쳤다).
        # → 제외 조건은 ① pgrep 의 플래그에 a 가 있는 경우(-a = 목록 출력, 점검 명령의 특징)
        #    ② 실제 grep 호출만 인정한다.
        if re.search(r"pgrep\s+-[A-Za-z]*a|(^|\s)grep\s+-|(^|\s)grep\s+['\"]", line):
            continue
        # ⚠ 오탐 제외 ②: 실제 루프 형태만 인정한다(while pgrep ...; do ... done).
        if not re.search(r"while pgrep.*(do\s|done)", line):
            continue
        stale.append(line.strip()[:140])
    long_running = []
    for line in out.splitlines():
        m = re.match(r"\s*(\d+)\s+(\d+):(\d+):(\d+)\s+(.*)", line)
        if not m:
            continue
        pid, hh, mm, ss, cmd = m.groups()
        hours = int(hh)
        if hours >= 12 and any(k in cmd for k in ("model_engineer_cycle", "researcher_cycle",
                                                  "dart_disclosure", "pnl_backtest", "ask_claude")):
            long_running.append(f"{hours}h {cmd[:100]}")
    return stale, long_running


def check_docker():
    info = {}
    df = sh("docker system df --format '{{.Type}}|{{.TotalCount}}|{{.Size}}|{{.Reclaimable}}'")
    info["df"] = [ln for ln in df.splitlines() if ln.strip()]
    info["containers_running"] = len(sh("docker ps -q").split())
    info["containers_stopped"] = len(sh("docker ps -aq").split()) - info["containers_running"]
    unhealthy = sh("docker ps --filter health=unhealthy --format '{{.Names}}'").split()
    info["unhealthy"] = [u for u in unhealthy if u]
    info["dangling_volumes"] = len([v for v in sh("docker volume ls -qf dangling=true").split() if v])
    info["dangling_images"] = len([i for i in sh("docker images -qf dangling=true").split() if i])
    # 재시작 횟수(플래핑 감지)
    flapping = []
    for c in sh("docker ps --format '{{.Names}}'").split():
        rc = sh(f"docker inspect -f '{{{{.RestartCount}}}}' {c}").strip()
        if rc.isdigit() and int(rc) >= TH["restart_warn"]:
            flapping.append(f"{c}:{rc}회")
    info["flapping"] = flapping
    # 빌드 캐시: **회수 가능량** 기준으로 경고한다. 총량만 보면 오탐이 난다.
    # 실측: 총 12.4GB 인데 RECLAIMABLE 은 0B 였다(공유/사용 중 레이어) → 총량 기준 경고는 항상 뜬다.
    gc = gc_reclaim = 0.0
    for ln in info["df"]:
        if ln.startswith("Build Cache"):
            parts = ln.split("|")
            if len(parts) >= 4:
                m = re.search(r"([\d.]+)\s*(GB|MB|kB)", parts[2])
                if m:
                    gc = float(m.group(1)) if m.group(2) == "GB" else float(m.group(1)) / 1024
                m2 = re.search(r"([\d.]+)\s*(GB|MB|kB)", parts[3])
                if m2:
                    gc_reclaim = float(m2.group(1)) if m2.group(2) == "GB" else float(m2.group(1)) / 1024
    info["build_cache_gb"] = round(gc, 2)
    info["build_cache_reclaim_gb"] = round(gc_reclaim, 3)
    # 컨테이너 내부 좀비 (exec 되는 것만)
    inner = []
    for c in sh("docker ps --format '{{.Names}}'").split():
        z = sh(f"docker exec {c} sh -c 'ps -eo stat --no-headers 2>/dev/null | grep -c \"^Z\"' 2>/dev/null")
        z = z.strip().splitlines()[-1] if z.strip() else "0"
        if z.isdigit() and int(z) > 0:
            inner.append(f"{c}:{z}")
    info["inner_zombies"] = inner
    return info


def check_disk():
    d = {}
    out = sh("df -h /").splitlines()
    if len(out) >= 2:
        parts = out[1].split()
        d["use_pct"] = float(parts[4].rstrip("%"))
        d["avail"] = parts[3]
        d["size"] = parts[1]
    d["tmp_mb"] = du_mb("/tmp")
    d["dumps_mb"] = du_mb(os.path.join(PROJ, "dumps"))
    d["snapshots"] = len(glob.glob(os.path.join(PROJ, "data/reports/dq_snapshots/*.png")))
    d["models_mb"] = du_mb(os.path.join(PROJ, "services/xgboost-ml/app/models"))
    d["cycle_logs_kb"] = du_kb(os.path.join(PROJ, "data/reports/me_cycle")) + \
        du_kb(os.path.join(PROJ, "data/reports/res_cycle"))
    return d


def du_mb(path):
    try:
        out = subprocess.run(["du", "-sm", path], capture_output=True, text=True, timeout=60).stdout
        return float(out.split()[0])
    except Exception:  # noqa: BLE001
        return -1.0


def du_kb(path):
    try:
        out = subprocess.run(["du", "-sk", path], capture_output=True, text=True, timeout=60).stdout
        return int(out.split()[0])
    except Exception:  # noqa: BLE001
        return -1


def check_orphan_pidfiles():
    """사이클이 '실행 중'으로 표시되지만 프로세스가 죽은 경우."""
    bad = []
    for name, pidfile in (("engineer", "data/reports/me_cycle/running.pid"),
                          ("researcher", "data/reports/res_cycle/running.pid")):
        p = os.path.join(PROJ, pidfile)
        if not os.path.exists(p):
            continue
        try:
            pid = int(open(p, encoding="utf-8").read().strip())
            os.kill(pid, 0)
        except (OSError, ValueError):
            bad.append(name)
    return bad


# ── 안전 자동 정리 (증명된 항목만) ───────────────────────────────────────────
def clean(dinfo, disk):
    actions = []
    if dinfo.get("build_cache_reclaim_gb", 0) > 1.0:
        out = sh("docker builder prune -f", timeout=300)
        m = re.search(r"Total reclaimed space: ([^\n]+)", out)
        actions.append(f"빌드 캐시 정리: {m.group(1) if m else '완료'}")
    # 6시간 넘은 내 임시 파일만 (시스템 디렉터리 절대 건드리지 않음)
    cutoff = now() - timedelta(hours=6)
    for pat in ("/tmp/*.log", "/tmp/*.txt", "/tmp/*.sh", "/tmp/*.py", "/tmp/*.json"):
        for f in glob.glob(pat):
            try:
                if datetime.fromtimestamp(os.path.getmtime(f), KST) < cutoff:
                    os.remove(f)
                    actions.append(f"정리 {os.path.basename(f)}")
            except OSError:
                pass
    return actions[:12]


def main():
    ap = argparse.ArgumentParser(description="시스템 위생 점검")
    ap.add_argument("--quiet", action="store_true", help="정상이면 아무것도 출력하지 않음(크론용)")
    ap.add_argument("--clean", action="store_true", help="안전 항목 자동 정리")
    ap.add_argument("--json", action="store_true")
    a = ap.parse_args()

    ts = now()
    zs = check_zombies()
    stale, longrun = check_stale_loops()
    dinfo = check_docker()
    disk = check_disk()
    orphans = check_orphan_pidfiles()

    warns, breaches = [], []
    if len(zs) >= TH["zombie_breach"]:
        owners = sorted({z.get("container", "?") for z in zs})
        breaches.append(f"좀비 {len(zs)}개 (부모: {', '.join(owners)})")
    elif len(zs) >= TH["zombie_warn"]:
        owners = sorted({z.get("container", "?") for z in zs})
        warns.append(f"좀비 {len(zs)}개 누적 (부모: {', '.join(owners)})")
    if dinfo["containers_stopped"] > 0:
        warns.append(f"정지 컨테이너 {dinfo['containers_stopped']}개")
    if dinfo["unhealthy"]:
        breaches.append(f"비정상 컨테이너: {', '.join(dinfo['unhealthy'])}")
    if dinfo["dangling_volumes"] > 0:
        warns.append(f"댕글링 볼륨 {dinfo['dangling_volumes']}개")
    if dinfo["flapping"]:
        warns.append(f"재시작 반복: {', '.join(dinfo['flapping'])}")
    if dinfo["inner_zombies"]:
        warns.append(f"컨테이너 내부 좀비: {', '.join(dinfo['inner_zombies'])}")
    if stale:
        # 어떤 프로세스가 걸렸는지 보여준다 — 이것이 없으면 "오탐인가?"를 판단할 수 없다.
        warns.append(f"자기매칭 대기 루프 {len(stale)}개: " + " / ".join(s[:70] for s in stale[:2]))
    if longrun:
        warns.append(f"12시간+ 방치 프로세스 {len(longrun)}개")
    if orphans:
        breaches.append(f"죽은 프로세스의 사이클 pidfile 잔존: {', '.join(orphans)}")
    dp = disk.get("use_pct", 0)
    if dp >= TH["disk_breach"]:
        breaches.append(f"디스크 {dp}% (여유 {disk.get('avail')})")
    elif dp >= TH["disk_warn"]:
        warns.append(f"디스크 {dp}%")
    if dinfo["build_cache_reclaim_gb"] > 2.0:
        warns.append(f"빌드 캐시 회수가능 {dinfo['build_cache_reclaim_gb']}GB")
    if disk["tmp_mb"] > TH["tmp_warn_mb"]:
        warns.append(f"/tmp {disk['tmp_mb']:.0f}MB")
    if disk["snapshots"] > TH["snapshots_warn"]:
        warns.append(f"DQ 스냅샷 {disk['snapshots']}개(보존 정책 초과)")

    cleaned = clean(dinfo, disk) if a.clean else []
    status = "breach" if breaches else ("warn" if warns else "ok")

    snapshot = {"ts": ts.isoformat(timespec="seconds"), "status": status,
                "zombies": zs, "stale_loops": stale, "long_running": longrun,
                "docker": dinfo, "disk": disk, "warns": warns, "breaches": breaches,
                "cleaned": cleaned}
    os.makedirs(OUTDIR, exist_ok=True)
    with open(os.path.join(OUTDIR, "latest.json"), "w", encoding="utf-8") as f:
        json.dump(snapshot, f, ensure_ascii=False, indent=2)
    with open(os.path.join(OUTDIR, "history.jsonl"), "a", encoding="utf-8") as f:
        f.write(json.dumps({k: snapshot[k] for k in ("ts", "status", "warns", "breaches")},
                           ensure_ascii=False) + "\n")
    # 30분 주기 → 하루 48줄. 상한을 둬 장기적으로도 안전하게(최근 2,000줄 ≈ 41일).
    try:
        with open(os.path.join(OUTDIR, "history.jsonl"), encoding="utf-8") as f:
            lines = f.readlines()
        if len(lines) > 2000:
            with open(os.path.join(OUTDIR, "history.jsonl"), "w", encoding="utf-8") as f:
                f.writelines(lines[-2000:])
    except OSError:
        pass

    if a.json:
        print(json.dumps(snapshot, ensure_ascii=False, indent=2))
    elif not a.quiet or status != "ok":
        head = {"ok": "정상", "warn": "경고", "breach": "위반"}[status]
        print(f"[위생] {ts.strftime('%m-%d %H:%M')} {head}")
        print(f"  좀비 {len(zs)} / 정지컨테이너 {dinfo['containers_stopped']} / "
              f"비정상 {len(dinfo['unhealthy'])} / 댕글링볼륨 {dinfo['dangling_volumes']} / "
              f"댕글링이미지 {dinfo['dangling_images']}")
        print(f"  컨테이너 {dinfo['containers_running']}개 running / 재시작반복 {len(dinfo['flapping'])} / "
              f"내부좀비 {len(dinfo['inner_zombies'])}")
        print(f"  디스크 {dp}% (여유 {disk.get('avail')}) / 빌드캐시 {dinfo['build_cache_gb']}GB / "
              f"/tmp {disk['tmp_mb']:.0f}MB / 스냅샷 {disk['snapshots']}개")
        if warns:
            print("  경고: " + " | ".join(warns))
        if breaches:
            print("  위반: " + " | ".join(breaches))
        if cleaned:
            print("  자동정리: " + " | ".join(cleaned))
    return 3 if breaches else (2 if warns else 0)


if __name__ == "__main__":
    sys.exit(main())

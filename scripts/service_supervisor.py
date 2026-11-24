#!/usr/bin/env python3
"""서비스 감시·자동복구: 뉴스게이트(8082)·피드서버(8090)·브리지(8100)·도커·디스크.

자동 재시작은 우리가 띄운 서버(뉴스게이트·피드서버)만 한다.
브리지는 크레온 로그인이 필요하므로 재시작하지 않고 경보만 남긴다.
산출: reports/supervisor_latest.json, reports/supervisor.log
"""
from __future__ import annotations

import datetime
import json
import os
import shutil
import subprocess
import time
import urllib.error
import urllib.request

REPO = "/home/jhshi/analyist_dd"
OUT_JSON = os.path.join(REPO, "reports/supervisor_latest.json")
OUT_LOG = os.path.join(REPO, "reports/supervisor.log")


def kst() -> str:
    return datetime.datetime.now(datetime.timezone(datetime.timedelta(hours=9))).isoformat(timespec="seconds")


def probe(url: str, timeout: float = 5.0) -> dict:
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    try:
        with opener.open(url, timeout=timeout) as res:
            return {"ok": 200 <= res.status < 300, "status": res.status}
    except urllib.error.HTTPError as exc:
        return {"ok": False, "status": exc.code, "error": str(exc)[:100]}
    except Exception as exc:
        return {"ok": False, "status": None, "error": str(exc)[:100]}


def env_from_dotenv() -> dict:
    env = os.environ.copy()
    path = os.path.join(REPO, ".env")
    if os.path.exists(path):
        for line in open(path, encoding="utf-8", errors="replace"):
            if "=" in line and not line.strip().startswith("#"):
                key, val = line.rstrip("\n").split("=", 1)
                env.setdefault(key.strip(), val.strip())
    return env


def spawn(name: str, cmd: list[str], env: dict) -> dict:
    log_path = os.path.join(REPO, "reports/{0}.log".format(name))
    fh = open(log_path, "ab")
    try:
        proc = subprocess.Popen(cmd, cwd=REPO, env=env, stdout=fh, stderr=fh,
                                start_new_session=True)
        return {"restarted": True, "pid": proc.pid, "cmd": " ".join(cmd[:3])}
    except Exception as exc:
        return {"restarted": False, "error": str(exc)[:160]}
    finally:
        fh.close()


def restart_newsgate(env: dict) -> dict:
    env = dict(env)
    env["NEWS_GATE_HOST"] = "127.0.0.1"
    env["NEWS_GATE_PORT"] = "8082"
    env["POSTGRES_HOST"] = "127.0.0.1"
    env["POSTGRES_PORT"] = "5434"
    return spawn("newsgate", ["/usr/bin/python3", "scripts/news_gate_server.py"], env)


def restart_relay(env: dict) -> dict:
    return spawn("feed_relay", ["/usr/bin/python3", "services/feed_relay/server.py",
                                "--root", os.path.join(REPO, "data/feed_relay"),
                                "--host", "127.0.0.1", "--port", "8095"], env)


def restart_feedserver(env: dict) -> dict:
    return spawn("feed_server", ["/usr/bin/python3", "scripts/feed_server.py",
                                 "--host", "0.0.0.0", "--port", "8090",
                                 "--dir", os.path.join(REPO, "data/feed")], env)


def tailscale_state() -> dict:
    if not os.path.exists("/sys/class/net/tailscale0"):
        return {"ok": False, "reason": "tailscale0 인터페이스 없음"}
    try:
        out = subprocess.run(["tailscale", "funnel", "status"], capture_output=True,
                             text=True, timeout=25)
    except (OSError, subprocess.SubprocessError) as exc:
        return {"ok": False, "reason": "funnel status 실패: {0}".format(exc)}
    stdout = out.stdout or ""
    if "127.0.0.1:8095" not in stdout and "localhost:8095" not in stdout:
        return {"ok": False, "reason": "funnel 이 8095 를 프록시하지 않음"}
    return {"ok": True, "reason": ""}


def restore_tailscale_funnel() -> dict:
    started = []
    if not os.path.exists("/sys/class/net/tailscale0"):
        subprocess.run(["nohup", "tailscaled", "--state=/var/lib/tailscale/tailscaled.state",
                        "--socket=/var/run/tailscale/tailscaled.sock"],
                       stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=15)
        started.append("tailscaled")
        time.sleep(6)
    res = subprocess.run(["tailscale", "funnel", "--bg", "--https=443",
                          "http://127.0.0.1:8095"], capture_output=True, text=True, timeout=60)
    started.append("funnel rc={0}".format(res.returncode))
    return {"ok": res.returncode == 0, "did": started,
            "stderr": (res.stderr or res.stdout or "").strip()[:120]}


def docker_state(container: str) -> dict:
    try:
        out = subprocess.run(["docker", "inspect", "-f", "{{.State.Status}}", container],
                             capture_output=True, text=True, timeout=20).stdout.strip()
        if out == "running":
            return {"ok": True, "status": out}
        started = subprocess.run(["docker", "start", container], capture_output=True,
                                 text=True, timeout=90)
        return {"ok": started.returncode == 0, "status": out, "restarted": started.returncode == 0,
                "error": started.stderr.strip()[:120]}
    except Exception as exc:
        return {"ok": False, "error": str(exc)[:120]}


def disk() -> dict:
    du = shutil.disk_usage(REPO)
    pct = round(du.used / du.total * 100, 1)
    return {"used_pct": pct, "free_gb": round(du.free / 1e9, 1), "ok": pct < 90}


def main() -> int:
    env = env_from_dotenv()
    checks: dict[str, dict] = {}
    actions: list[str] = []

    checks["newsgate"] = probe("http://127.0.0.1:8082/newsgate?codes=005930")
    if not checks["newsgate"].get("ok"):
        result = restart_newsgate(env)
        actions.append("newsgate 재기동 {0}".format(result))
        time.sleep(4)
        checks["newsgate"] = probe("http://127.0.0.1:8082/newsgate?codes=005930")

    checks["feedserver"] = probe("http://127.0.0.1:8090/screener_latest.json")
    if not checks["feedserver"].get("ok"):
        result = restart_feedserver(env)
        actions.append("feedserver 재기동 {0}".format(result))
        time.sleep(3)
        checks["feedserver"] = probe("http://127.0.0.1:8090/screener_latest.json")

    checks["feed_relay"] = probe("http://127.0.0.1:8095/health")
    if not checks["feed_relay"].get("ok"):
        result = restart_relay(env)
        actions.append("feed_relay 재기동 {0}".format(result))
        time.sleep(3)
        checks["feed_relay"] = probe("http://127.0.0.1:8095/health")

    checks["tailscale_funnel"] = tailscale_state()
    if not checks["tailscale_funnel"].get("ok"):
        result = restore_tailscale_funnel()
        actions.append("tailscale funnel 복구 {0}".format(result))
        time.sleep(3)
        checks["tailscale_funnel"] = tailscale_state()

    checks["bridge"] = probe("http://127.0.0.1:8100/health")
    checks["postgres"] = docker_state("stock_postgres")
    checks["redis"] = docker_state("stock_redis")
    checks["disk"] = disk()

    critical = [name for name in ("newsgate", "feedserver", "feed_relay", "tailscale_funnel", "bridge", "postgres", "redis", "disk")
                if not checks[name].get("ok")]
    report = {"at": kst(), "checks": checks, "actions": actions,
              "critical": critical, "verdict": "OK" if not critical else "ALERT:" + ",".join(critical)}
    json.dump(report, open(OUT_JSON, "w", encoding="utf-8"), ensure_ascii=False, indent=2)
    line = "[{0}] {1} | {2}".format(
        report["at"], report["verdict"],
        " ".join("{0}={1}".format(k, "ok" if v.get("ok") else "FAIL") for k, v in checks.items()))
    if actions:
        line += " | " + "; ".join(actions)
    with open(OUT_LOG, "a", encoding="utf-8") as fh:
        fh.write(line + "\n")
    print(line)
    return 0 if not critical else 3


if __name__ == "__main__":
    raise SystemExit(main())

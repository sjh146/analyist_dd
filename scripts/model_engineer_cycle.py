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
     ⚠ floor 는 `max(파일 mtime, 실행 시작 시각)` 이고 비교는 `<=` 다. 파일 mtime 만 floor 로
     쓰고 `<` 로 비교하면 **갱신되지 않은 파일(mtime == floor)이 통과**한다(실측 2026-09-25:
     소실된 U1 이 L2 요약을 읽어 Δ+0.0000 '노이즈' 로 원장에 기록됨).
  5. **자기신고 신뢰 금지**: 판정은 요약 JSON 의 폴드 값에서 직접 계산한다. 로그의 문구를 믿지 않는다.
  6. **실패한 실행에는 성능 판정을 붙이지 않는다**: rc!=0 이면 판정은 "실행실패"(측정값 없음)다.
     옛 요약이 남아 있어도 Δ 를 계산하지 않는다 — 소실을 '노이즈(측정됨)'로 세면 무개선 카운터와
     '새 레버 필요' 승격 판단이 오염된다.
  7. **컨테이너 재생성 창 회피**: 평일 20:00 크론(evening_pipeline.sh → full_pipeline_dd.sh L78
     `docker compose up -d --no-build`)이 컨테이너를 갈아끼워 그 시각에 도는 `docker exec` 를
     SIGKILL(137) 한다. 항목의 `est_minutes` 로 ETA 를 계산해 이 창을 넘으면 시작하지 않는다.
  8. **--force 의 의미**: 장중 가드·ETA 가드는 force 로 무시되지만, **부하 가드는 장외에서만**
     무시된다. 실측(2026-09-25 21:00): 저녁 파이프라인 재학습 + yfinance 수집이 postgres 를
     291% CPU 로 태워 load1=7 인 동안 대기형 런처의 강제 시작이 rc=3 으로 죽었고, 그대로면
     11시간짜리 U1 패널 빌드가 밤새 시작되지 못한다. 크론 틱은 force 없이 돌므로
     평소 직렬화(우리 학습 두 개 동시 실행 금지)는 그대로 유지된다.

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
# ── 미전달 보고 복구용: Hermes 크론 실행 기록 ────────────────────────────────
# 실측(2026-09-30): 이 잡의 04:00·05:00·06:00·07:00 틱이 전부
# `RuntimeError: Hermes can't reach the model provider` 로 실패했다. 틱 스크립트 자체는 돌았으므로
# 04:51 에 끝난 U3 결과의 원장 `reported` 플래그는 켜졌지만 **사용자에게는 전달되지 않았다**
# (executions.db 의 delivery_outcome='failed'). 원장 플래그만 믿으면 결과가 영구 소실된다.
# → reported 로 표시할 때 `reported_at` 을 함께 남기고, 다음 틱이 그 시각에 대응하는 크론 실행의
#   전달 결과를 executions.db 에서 확인해 실패였으면 플래그를 되돌린다(재보고 경로).
HERMES_EXEC_DB = os.path.expanduser(
    os.environ.get("ME_EXEC_DB", "~/.hermes/cron/executions.db"))
JOBID_FILE = os.path.join(RUNTIME, "cron_job_id.txt")
DEFAULT_JOB_ID = "d4070d508732"        # quant-model-engineer-overnight
REPORT_EARLY_SEC = 300                  # 보고 처리 시각과 실행 시작 시각의 허용 선행 오차
REPORT_WINDOW_SEC = 3600                # 한 실행이 소비한 기록으로 보는 사후 창
KST = timezone(timedelta(hours=9))
CONTAINER = "stock_xgboost_ml"
LOAD_MAX = float(os.environ.get("ME_LOAD_MAX", "3.5"))
MARKET_OPEN, MARKET_CLOSE = (9, 0), (15, 30)
# KRX 휴장일 파일 — scripts/data_gap.py 가 데이터 공백을 probe 하며 자동 유지한다.
HOLIDAY_PATH = os.path.join(PROJ, "data/krx_holidays.json")
# 평일 20:00 컨테이너 재생성 창(위 설계원칙 7). 실측 2026-09-25: U1 패널 빌드가
# 30,000/41,893(71.6%)·4h14m 지점에서 docker compose 재생성으로 SIGKILL(137) → 전량 소실.
RECREATE_WEEKDAYS = (1, 2, 3, 4, 5)     # cron 의 1-5 = 월~금
RECREATE_HOUR = 20
RECREATE_SAFETY_MIN = 10                # 재생성 10분 전부터는 새로 시작하지 않는다
# 재생성 **직후** 창 — 20:00 정각에 뜬 틱이 '창을 넘지 않는' 짧은 실험(8~12분)을 시작하면
# 컨테이너 교체(SIGKILL 137)에 그대로 노출된다. eta_blocks 는 '예상 종료가 창을 넘는가'만
# 보므로 이 경로를 못 막는다. full_pipeline_dd.sh 는 running.pid 가 있으면 xgboost-ml 을
# 재생성에서 제외하지만, 파이프라인이 pidfile 을 읽는 시점(20:00:0x)과 우리가 쓰는 시점의
# 경쟁이라 보장이 아니다(실측 2026-09-25 20:00:16 U1 rc=137). 그래서 창 앞뒤로 시작을 막는다.
RECREATE_GRACE_MIN = 12                 # 20:00~20:12 은 시작 금지(다음 틱에서 재시도)
RETRY_MAX = 3                           # 인프라 사고(소실·타임아웃) 재시도 상한


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


# ── 미전달 보고 감지(크론 실행 DB) ──────────────────────────────────────────
def cron_job_id():
    """이 틱을 돌리는 크론 잡 id. 환경변수 → 파일 → 기본값 순으로 찾는다."""
    jid = (os.environ.get("ME_CRON_JOB_ID") or os.environ.get("HERMES_CRON_JOB_ID") or "").strip()
    if jid:
        return jid
    try:
        with open(JOBID_FILE, encoding="utf-8") as f:
            jid = f.read().strip()
        if jid:
            return jid
    except OSError:
        pass
    return DEFAULT_JOB_ID


def _parse_ts(s):
    try:
        return datetime.fromisoformat(str(s))
    except (TypeError, ValueError):
        return None


def _exec_history(job_id, limit=60):
    """executions.db(읽기 전용)에서 최근 실행의 (started_at, status, delivery_outcome)."""
    import sqlite3
    con = sqlite3.connect(f"file:{HERMES_EXEC_DB}?mode=ro", uri=True)
    try:
        cur = con.execute(
            "select started_at, status, delivery_outcome from executions "
            "where job_id=? order by started_at desc limit ?", (job_id, limit))
        return [tuple(r) for r in cur.fetchall()]
    finally:
        con.close()


def check_undelivered_reports():
    """직전 크론 실행이 **전달 실패**했으면 그 실행이 보고 처리한 기록을 미보고로 되돌린다.

    실측(2026-09-30): 모델 제공자 불통으로 04:00~07:00 틱이 실패하면서 U3 결과가 조용히
    사라졌다. `reported` 플래그는 "틱이 출력했다"는 뜻일 뿐 "사용자가 받았다"가 아니다.
    크론 실행 DB 를 못 읽으면 **아무것도 바꾸지 않고** NOTE 만 남긴다(잘못 되돌리면 중복 보고).
    """
    led = load_ledger()
    cand = [r for r in led if r.get("reported") and r.get("reported_at")]
    if not cand:
        return []
    try:
        hist = _exec_history(cron_job_id())
    except Exception as e:      # sqlite3 부재·DB 이동·스키마 변경 — 본업을 막지 않는다
        return [f"NOTE: 미전달 감지 불가({type(e).__name__}: {e}) — 원장 플래그는 유지"]
    bad, changed = [], False
    for started, _status, outcome in hist:
        st = _parse_ts(started)
        if st is None or outcome not in ("failed", "unknown"):
            continue
        hit = []
        for r in cand:
            rt = _parse_ts(r.get("reported_at"))
            if rt is None:
                continue
            dt = (rt - st).total_seconds()
            if -REPORT_EARLY_SEC <= dt <= REPORT_WINDOW_SEC:
                hit.append(r)
        if not hit:
            continue
        for r in hit:
            r["reported"] = False
            r.pop("reported_at", None)
        changed = True
        bad.append(f"{str(started)[:16]} 실행({outcome}) 이 소비한 기록: "
                   + ", ".join(r.get("id", "?") for r in hit))
    if changed:
        _rewrite_ledger(led)
        return ["=== ⚠ 이전 틱 결과가 사용자에게 전달되지 않았다(크론 실행 실패/미확정)"
                " → 이번 보고에 포함하라:"] + [f"    {b}" for b in bad]
    return []


# ── 가드 ────────────────────────────────────────────────────────────────────
def _pid_of(path, json_key=None):
    """pidfile 또는 state.json 에서 pid 를 읽는다. 없거나 깨졌으면 None."""
    try:
        with open(path, encoding="utf-8") as f:
            raw = f.read().strip()
        if json_key:
            raw = str(json.loads(raw).get(json_key) or "")
        return int(raw)
    except (OSError, ValueError, TypeError):
        return None


def _pid_alive(pid):
    """pid 가 살아 있는지 + **우리 사이클 스크립트**인지(pid 재사용 오탐 방지).

    /proc/<pid>/cmdline 을 못 읽으면 보수적으로 '살아있음'으로 본다(차단이 안전한 쪽).

    ⚠ EPERM 은 '죽음'이 아니라 '존재하지만 내 것이 아님'이다.
    실측(2026-09-25 22:00): 대기형 런처가 passwordless root 로 띄운 U1(--run) 은
    jhshi 가 `os.kill(pid, 0)` 하면 **EPERM** 이 온다(프로세스는 살아서 패널 빌드 중).
    이걸 OSError 전체로 묶어 '죽음'으로 처리하자 state.json fallback 까지 무력화되어
    ① 틱이 살아있는 사이클을 "기록 없이 죽었다"로 오보고하고
    ② pidfile 이 없으면 교차 락이 풀려 **두 번째 무거운 실험이 동시에 시작**될 수 있었다
    (4코어에서 학습 2개 = 과거 SIGKILL 사고의 재료).
    """
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False                 # ESRCH — 진짜로 없다
    except PermissionError:
        pass                         # EPERM/EACCES — 존재한다. 아래 cmdline 으로 재사용만 거른다
    except OSError:
        return False
    try:
        with open(f"/proc/{pid}/cmdline", "rb") as f:
            cmd = f.read()
    except OSError:
        return True
    return b"_cycle.py" in cmd      # 두 역할 모두 *_cycle.py 로 실행된다


def running_pid(exclude_self=True):
    """실행 중인 사이클의 pid 를 반환(없으면 None).

    exclude_self: 백그라운드로 뜬 **자식 프로세스 자신**은 부모가 써 둔 자기 pid 를 보고
    스스로를 '다른 사이클'로 오판해 즉시 종료한다(실측 2026-09-25: bg_L1.log =
    "시작 보류: 이미 사이클 실행 중"). 그래서 자기 pid 는 '없음'으로 취급한다.

    ⚠ state.json fallback: pidfile 이 사라져도 '실행 중' 신호를 잃지 않는다.
    실측(2026-09-25 16:00~17:00): U1 4~6시간 빌드가 도는 중 running.pid 만 없어져
    ① 교차 락(PEER_PIDFILES)이 풀려 리서처가 동시에 시작할 수 있게 되고
    ② 틱이 "사이클 실행 중" 대신 "부하 과다 — 다른 학습이 도는 중"으로 **잘못 보고**했다.
    pidfile 은 지워질 수 있는 캐시, state.json 은 사이클이 직접 쓰는 기록이라 둘 다 본다.
    """
    for path, key in ((PIDFILE, None), (STATE, "pid")):
        pid = _pid_of(path, key)
        if not pid or (exclude_self and pid == os.getpid()):
            continue
        if _pid_alive(pid):
            return pid
    return None


def _is_holiday(dt) -> bool:
    """휴장일인가(data/krx_holidays.json). 파일이 없거나 깨졌으면 '모름' -> False."""
    try:
        with open(HOLIDAY_PATH, encoding="utf-8") as f:
            return dt.strftime("%Y-%m-%d") in {str(d) for d in json.load(f)}
    except (OSError, json.JSONDecodeError):
        return False


def next_market_open(now=None):
    """다음 장 시작(평일 09:00 KST, 휴장일 제외) 시각. 8일 내에 없으면 None.

    왜 필요한가(실측 2026-09-29): guards() 의 장중 가드는 **'시작'만** 막는다. est_minutes 가
    큰 항목은 개장 직전(예: 07:00 시작 · est 420분 -> 종료 14:00)에 시작해도 통과해서 장 전체를
    잡아먹는다. eta_blocks 가 재생성 창(평일 20:00)만 봤기 때문이다. 실측 전례: 2026-09-28
    05:0x 에 U3(est 를 '남은 시간' 700 으로 잘못 채운 상태)가 05:0x 에 시작해 컨테이너 timeout 이
    16:41 에 걸렸다 - 개장~마감을 통째로 점유.
    """
    n = now or now_kst()
    for add in range(0, 8):
        d = n + timedelta(days=add)
        if d.weekday() >= 5:
            continue
        cand = d.replace(hour=MARKET_OPEN[0], minute=MARKET_OPEN[1],
                         second=0, microsecond=0)
        if cand <= n:
            continue
        if _is_holiday(cand):
            continue
        return cand
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


def in_recreate_window(dt=None) -> bool:
    """평일 20:00~20:12(컨테이너 재생성 창)인가 — 이 구간엔 **시작 자체**를 막는다.

    왜: eta_blocks 는 '예상 종료가 창을 넘는가'만 본다 → 20:00 정각 틱은 8~12분짜리 실험을
    통과시킨다(종료 20:08 < 다음 재생성 20:00+1일). 그런데 그 실험은 파이프라인의 docker
    compose 재생성과 정면으로 겹친다(실측 2026-09-25 20:00:16 U1 rc=137).
    full_pipeline_dd.sh 의 인플라이트 보호(running.pid 있으면 xgboost-ml 제외)는 pidfile 을
    읽는 시점 경쟁이 있어 보장이 아니므로, 시작을 창 밖으로 미룬다(손실 = 틱 1회 지연).
    --force 로도 뚫지 않는다: 이건 우리 부하가 아니라 **외부 이벤트**라서다(장중 가드와 같은 성격).
    """
    dt = dt or now_kst()
    if dt.isoweekday() not in RECREATE_WEEKDAYS:
        return False
    t = (dt.hour, dt.minute)
    return (RECREATE_HOUR, 0) <= t < (RECREATE_HOUR, RECREATE_GRACE_MIN)


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
        # 교차 락도 같은 EPERM 함정을 피해야 한다: 상대 역할이 root 로 떠 있으면
        # os.kill(pid,0) 이 EPERM 을 주므로 '없음'으로 넘기면 락이 조용히 풀린다.
        if _pid_alive(pid):
            return pid, rel
    return None, None


def next_recreate(now=None):
    """다음 컨테이너 재생성 시각(평일 20:00 KST). 오늘 20:00 이 이미 지났으면 다음 평일."""
    n = now or now_kst()
    for add in range(0, 8):
        cand = (n + timedelta(days=add)).replace(
            hour=RECREATE_HOUR, minute=0, second=0, microsecond=0)
        if cand.isoweekday() in RECREATE_WEEKDAYS and cand > n:
            return cand
    return None


def eta_blocks(item) -> tuple:
    """(막는가, 이유). est_minutes 로 예상한 **종료 시각**이 넘으면 시작하지 않는다.

    ① 다음 컨테이너 재생성 창(평일 20:00) ② 다음 장 시작(평일 09:00, 휴장일 제외).
    ②가 없으면 개장 직전(예: 07:00 · est 420분 → 종료 14:00)에 시작한 항목이 장 전체를
    점유한다 — 장중 가드는 '시작'만 막기 때문이다(실측 전례 2026-09-28 05:0x U3).
    est_minutes 가 없으면 판단하지 않는다(보수적으로 막지는 않되, 긴 항목엔 반드시 채워라).
    """
    est = (item or {}).get("est_minutes")
    if not est:
        return False, ""
    fin = now_kst() + timedelta(minutes=float(est))
    rec = next_recreate()
    if rec and fin > rec - timedelta(minutes=RECREATE_SAFETY_MIN):
        return True, (f"예상 종료 {fin.strftime('%m-%d %H:%M')} 이 컨테이너 재생성 창"
                      f"({rec.strftime('%m-%d %H:%M')} 평일 저녁 파이프라인, docker compose up -d)"
                      f"을 넘음 — est_minutes={est} · 재생성 직후 틱에서 재시도")
    mo = next_market_open()
    if mo and fin > mo:
        return True, (f"예상 종료 {fin.strftime('%m-%d %H:%M')} 이 다음 장 시작"
                      f"({mo.strftime('%m-%d %H:%M')}) 을 넘음 — 장중엔 트레이더·피드가 CPU 우선"
                      f" · est_minutes={est} · 마감(15:30) 후 틱에서 재시도")
    return False, ""


def guards(force=False, item=None) -> tuple:
    """(ok, 이유). 시작해도 되는가."""
    if running_pid():
        return False, "이미 사이클 실행 중"
    pid, rel = peer_running()
    if pid:
        return False, f"다른 역할이 실행 중(pid={pid}, {rel}) — CPU 직렬화를 위해 대기"
    if not container_up():
        return False, f"{CONTAINER} 컨테이너가 떠 있지 않음"
    if in_recreate_window():
        return False, (f"컨테이너 재생성 창({RECREATE_HOUR:02d}:00~{RECREATE_HOUR:02d}:"
                       f"{RECREATE_GRACE_MIN:02d}, 평일 저녁 파이프라인 docker compose up -d)"
                       f" — 시작하지 않음 · 다음 틱에서 재시도")
    blocked, why = eta_blocks(item)
    if blocked and not force:
        return False, why
    # --force 로도 뚫지 않는다(2026-09-29 수리): 예전 술어는 `market_hours() and not force`
    # 라서 --force 가 장중 가드를 그대로 통과했다 — 아래 load 가드 주석의 "장중은 force 로도
    # 뚫지 않는다"와 코드가 어긋나 있었다. 장중엔 트레이더·피드가 CPU 우선이고 그건 사람이
    # 즉흥적으로 넘길 판단이 아니다(런처도 20:35~21:00 창만 쓴다).
    if market_hours():
        return False, f"{market_note()} — 시작하지 않음 (--force 로도 차단)"
    l = load1()
    if l > LOAD_MAX:
        # 표현 주의: 예전 문구는 "다른 학습이 도는 중"이라고 단정했다. 실측(2026-09-25 17:00)
        # 정작 CPU 를 쓰는 것은 **우리 U1 패널 빌드**였고(진행 중 사이클), 그 문구 때문에
        # "남의 작업이 돌아 대기 중"으로 잘못 보고됐다.
        #
        # --force 는 **장외에서만** 부하 가드를 무시한다. 실측(2026-09-25 21:00): 저녁
        # 파이프라인의 챔피언 재학습 + yfinance 수집이 postgres 를 291% CPU 로 태워 load1=7 인
        # 동안, 대기형 런처(u1_launcher.sh)의 90분 강제 시작이 이 가드를 뚫지 못하고 rc=3 으로
        # 끝났다 → 아무도 다시 띄우지 않으면 밤이 통째로 날아간다(패널 빌드 11시간).
        # 진짜 직렬화 대상은 '우리 학습 두 개'이고 그건 위에서 이미 확인했다(사이클·타 역할 없음).
        # 장중(트레이더/피드 우선)은 force 로도 뚫지 않는다 — 위 장중 가드가 먼저 막는다.
        if force and not market_hours():
            log(f"주의: load1={l:.2f} > {LOAD_MAX} 이지만 --force·장외·경쟁 사이클 없음 → 강행")
        else:
            return False, f"부하 과다 load1={l:.2f} > {LOAD_MAX} — CPU 사용 중(우리 실험 포함)이라 시작 안 함"
    return True, "ok"


def next_item(backlog, force=False):
    """다음 실행 후보. **ETA 가드에 걸리는 항목은 건너뛴다.**

    왜(실측 2026-09-28): U3(est_minutes=1410 = 23.5h)가 priority=1 인데 평일엔 예상 종료가
    다음 컨테이너 재생성(평일 20:00)을 항상 넘어 execute() 가 rc=3 으로 거부된다 →
    next_item 이 매 틱 U3 만 돌려주므로 **큐 전체가 굶는다**(U3b·L5b·L5c·신규 라벨 실험
    전부 영구 대기). ETA 가드의 목적은 SIGKILL 로 결과를 잃지 않는 것이지 큐를 멈추는 게 아니다.
    건너뛴 이유는 로그에 남겨 '왜 이 항목이 안 도는지'를 추적 가능하게 한다.
    """
    pend = [i for i in backlog["items"] if i.get("status") == "pending"]
    pend.sort(key=lambda i: (i.get("priority", 99), i["id"]))
    for i in pend:
        if not i.get("command"):
            log(f"경고: {i['id']} 는 pending 인데 command 가 없다 → 건너뜀"
                f"{' (setup: ' + str(i.get('setup_needed'))[:80] + ')' if i.get('setup_needed') else ''}")
            continue
        if not force:
            blocked, why = eta_blocks(i)
            if blocked:
                log(f"{i['id']}: ETA 가드로 건너뜀 — {why}")
                continue
        return i
    return None


# ── 지표 파싱 (자기신고 금지 — 요약 JSON 에서 직접 계산) ───────────────────────
def summary_path(kind, command=None):
    if kind == "wf_sweep_summary":
        return os.path.join(PROJ, "services/xgboost-ml/reports/overnight/wf_label_sweep_summary.json")
    if kind == "champion_robust_eval":
        # champion_robust_eval.py 는 컨테이너 cwd=/app 에서 --out /app/reports/... 로 쓴다
        # (/app = services/xgboost-ml). CG31 이 이 metric 으로 돌아간다.
        #
        # 실측 함정(2026-09-29): 경로를 **고정**해 두면 여러 arm 을 서로 다른 --out 으로 돌릴 때
        # 구동기가 항상 기본 경로만 보게 되어 '요약 미갱신 → 실행실패'로 오판한다(같은 파일을
        # 덮어써 이전 기준선 산출물을 잃는 위험도 있다). 커맨드에 `--out <path>` 가 있으면
        # 그 파일을 요약으로 쓴다.
        out = _out_arg(command or "")
        if out:
            return _container_path_to_host(out)
        return os.path.join(PROJ, "services/xgboost-ml/reports/champion_robust_eval.json")
    if kind == "champion_promote_dryrun":
        # 승격 게이트 dry-run(champion_promote --summary-out) 요약. CG43 이 이 metric 으로 돈다.
        # 실측(2026-09-30 22:34): 이 metric 이 등록돼 있지 않아 구동기가 "parser 없음 →
        # 판정불가" 로 기록할 참이었다 — rc=0 이면 백로그가 done 으로 닫히면서 **게이트 판정
        # (status·사유·후보 AUC)이 원장에서 통째로 사라진다**(항목의 유일한 산출물인데도).
        out = _arg(command or "", "--summary-out")
        if out:
            return _container_path_to_host(out.strip("'\""))
        return os.path.join(PROJ, "services/xgboost-ml/reports/ml_result.json")
    # 알 수 없는 metric(또는 metric 없음)은 **예외를 내지 않고 빈 경로**로 돌려준다.
    # 왜(2026-09-30): 백로그에는 metric 이 없는 항목이 8개 있다(진단·준비 항목). 종전
    # `raise ValueError` 는 그 항목을 `--start` 하는 순간 guards 통과 직후 크래시를 내
    # ① 원장 기록 없이 사라지고(설계원칙 4 위반) ② 틱이 그 항목을 영원히 집지 못하게 했다.
    # 빈 경로면 "요약 없음 → 판정불가" 로 정직하게 끝난다(성능 주장 없음).
    if kind:
        log(f"경고: 알 수 없는 metric {kind!r} — 요약 경로 없음(판정불가로 기록)")
    return ""


def _out_arg(command: str) -> str:
    """커맨드에서 `--out <path>` 값을 뽑는다(없으면 빈 문자열)."""
    return _arg(command, "--out")


def _arg(command: str, flag: str) -> str:
    """커맨드 문자열에서 `--flag <값>` 또는 `--flag=<값>` 을 뽑는다(없으면 빈 문자열).

    `--out`·`--summary-out` 처럼 산출물 경로를 지정하는 플래그를 요약 경로로 쓰기 위한 것.
    """
    parts = (command or "").split()
    for i, p in enumerate(parts):
        if p == flag and i + 1 < len(parts):
            return parts[i + 1].strip("'\"")
        if p.startswith(flag + "="):
            return p.split("=", 1)[1].strip("'\"")
    return ""


def _container_path_to_host(path: str) -> str:
    """컨테이너 안 경로를 호스트 경로로 옮긴다(/app = services/xgboost-ml)."""
    if path.startswith("/app/"):
        return os.path.join(PROJ, "services/xgboost-ml", path[len("/app/"):])
    if os.path.isabs(path):
        return path
    return os.path.join(PROJ, "services/xgboost-ml", path)


def parse_wf_sweep(path, mtime_floor) -> dict:
    """요약 JSON 에서 설정별 폴드 평균을 뽑아 평균·std·폴드승률을 계산한다.

    mtime_floor 는 `max(파일 mtime, 실행 시작 시각)` 이다. 비교는 **`<=`** —
    `mt < mtime_floor` 로 쓰면 '전혀 갱신되지 않은 파일(mt == floor)' 이 통과해
    소실된 실행이 옛 결과로 판정된다(실측 2026-09-25 U1).
    """
    if not os.path.exists(path):
        return {"error": "요약 파일 없음"}
    mt = os.path.getmtime(path)
    if mt <= mtime_floor:
        return {"error": "요약 미갱신(mtime <= 실행 시작 시각) — 옛 결과를 새 결과로 오독 방지",
                "summary_mtime": mt, "floor": mtime_floor}
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


def parse_champion_robust(path, mtime_floor) -> dict:
    """배포 챔피언 견고 AUC(champion_robust_eval.py) 요약을 파싱한다.

    왜(실측 2026-09-29 16:19): CG31 을 `--start` 하면 summary_path 가 ValueError 로 rc=3 즉시
    종료됐다(metric 파서 부재). 이 항목의 값은 **승격 게이트 기준선**이라 자동 경로로 돌아야 한다.

    ⚠ **per_exp 를 만들지 않는다**(실측 2026-09-29 17:27): scoreboard 의 best_robust 와 무개선
    카운터는 원장의 `parsed.per_exp` 전체를 'arm 의 폴드 평균'으로 읽는다. 기준선 실측 기록에
    창별 AUC 를 per_exp 로 넣었더니 창 4(0.6013)가 '최고 arm'으로 잡혀 **거짓 돌파**
    ("로버스트 0.6013 · 직전 개선 CG31 · 무개선 0사이클")가 났다. 창별 값은 `windows` 로 따로
    싣는다 — 표시에는 그대로 쓰이고 카운터·best 에는 관여하지 않는다.
    """
    if not os.path.exists(path):
        return {"error": "요약 파일 없음"}
    mt = os.path.getmtime(path)
    if mtime_floor and mt <= mtime_floor:
        return {"error": "요약 미갱신(mtime <= 실행 시작 시각) — 옛 결과를 새 결과로 오독 방지",
                "summary_mtime": mt, "floor": mtime_floor}
    with open(path, encoding="utf-8") as f:
        try:
            d = json.load(f)
        except json.JSONDecodeError as e:
            # 쓰는 중인 파일(부분 기록)을 읽으면 여기로 온다 → 틱이 크래시하지 않게 오류로 반환한다.
            return {"error": f"요약 JSON 파싱 실패(쓰는 중일 수 있음): {e}", "summary_mtime": mt}
    folds = [f for f in (d.get("folds") or []) if isinstance(f, dict)
             and isinstance(f.get("auc_mean"), (int, float))]
    if not folds:
        return {"error": "folds 비어 있음(유효 창 없음)", "measured_at": d.get("measured_at")}
    return {
        "measured_at": d.get("measured_at"), "model_dir": d.get("model_dir"),
        "protocol": d.get("protocol"), "metric_name": d.get("metric"),
        "robust_auc": d.get("robust_auc"),
        "auc_std_across_folds": d.get("auc_std_across_folds"),
        "fold_means": [float(f["auc_mean"]) for f in folds],
        "windows": [{"fold": f.get("fold"), "window": f.get("window"),
                     "n_dates": f.get("n_dates"), "auc_mean": f.get("auc_mean"),
                     "auc_std": f.get("auc_std")} for f in folds],
        "auc_pooled": d.get("auc_pooled"), "auc_per_date_mean": d.get("auc_per_date_mean"),
        "rows_scored": d.get("rows_scored"), "dates_scored": d.get("dates_scored"),
        "errors": (d.get("errors") or [])[:5],
        "summary_mtime": mt,
    }


def parse_champion_promote_dryrun(path, mtime_floor) -> dict:
    """승격 게이트 dry-run 요약(champion_promote --summary-out)을 파싱한다.

    왜(실측 2026-09-30 22:34 CG43): 이 스택의 생산 경로는 저녁 파이프라인에서
    `retrain_champion --days 90 --stock-limit 200` 뒤 `champion_promote --dry-run` 을 돌리는데,
    게이트가 내린 **판정 자체**(status·사유·후보 AUC·기준선)가 이 JSON 의 유일한 산출물이다.
    metric 파서가 없으면 rc=0 일 때 "parser 없음"으로 기록되고 항목이 done 으로 닫혀
    판정이 영구 소실된다.

    ⚠ `per_exp` 를 만들지 않는다 — scoreboard 는 원장의 per_exp 전체를 'arm 의 폴드 평균'으로
    읽어 best_robust·무개선 카운터를 만든다(2026-09-29 CG31 사고). 후보 AUC 는 단일 학습의
    인샘플 값이라 arm 폴드 평균이 아니므로 최상위 키로만 싣는다.
    """
    if not os.path.exists(path):
        return {"error": "요약 파일 없음"}
    mt = os.path.getmtime(path)
    if mtime_floor and mt <= mtime_floor:
        return {"error": "요약 미갱신(mtime <= 실행 시작 시각) — 옛 결과를 새 결과로 오독 방지",
                "summary_mtime": mt, "floor": mtime_floor}
    with open(path, encoding="utf-8") as f:
        try:
            d = json.load(f)
        except json.JSONDecodeError as e:
            return {"error": f"요약 JSON 파싱 실패(쓰는 중일 수 있음): {e}", "summary_mtime": mt}
    if not d.get("status"):
        return {"error": "status 키가 없음(게이트 판정 아님)", "summary_mtime": mt}
    return {
        "status": d.get("status"),
        "promoted": d.get("promoted"),
        "reason": d.get("reason"),
        "candidate_auc": d.get("auc"),
        "candidate_metric": d.get("candidate_metric"),
        "champion_auc_before": d.get("champion_auc_before"),
        "champion_baseline": d.get("champion_baseline"),
        "champion_baseline_source": d.get("champion_baseline_source"),
        "model_aucs": d.get("model_aucs"),
        "n_rows": d.get("n_rows"), "n_features": d.get("n_features"),
        "up_rate": d.get("up_rate"), "retrained_at": d.get("retrained_at"),
        "decided_at": d.get("decided_at"),
        "summary_mtime": mt,
    }


def judge_promote_dryrun(item, parsed) -> tuple:
    """승격 게이트 dry-run 의 판정 — **성능 판정이 아니라 생산 경로 검증**이다.

    왜 judge_per 를 쓰지 않는가: 이 항목(CG43)의 성공 조건은 "예산 안에서 후보가 만들어지고
    게이트 판정이 기록되는가"다. per_exp 가 없어 judge_per 는 '판정불가'만 돌려준다.
    AUC 는 후보의 **인샘플** 값이라 승격·기준선 판단에 쓰지 않는다(하드룰 #1).
    """
    if parsed.get("error"):
        return "판정불가", parsed["error"], None
    st = parsed.get("status")
    parts = [f"dry-run status={st}"]
    for k, label, fmt in (("candidate_auc", "후보 AUC", "{:.4f}"),
                          ("champion_auc_before", "챔피언", "{:.4f}"),
                          ("champion_baseline", "기준선", "{}")):
        v = parsed.get(k)
        if isinstance(v, (int, float)):
            parts.append(f"{label} {fmt.format(v) if fmt == '{:.4f}' else v}")
    if parsed.get("champion_baseline_source"):
        parts.append(f"기준선 출처 {parsed['champion_baseline_source']}")
    if parsed.get("n_rows") is not None:
        parts.append(f"학습행 {parsed['n_rows']}")
    if parsed.get("up_rate") is not None:
        parts.append(f"양성률 {parsed['up_rate']}")
    if parsed.get("reason"):
        parts.append(f"사유: {parsed['reason']}")
    verdict = {"would_promote": "게이트 통과(승격후보 생성)",
               "kept_incumbent": "후보 생성·게이트 거부",
               "promoted": "승격됨(비-dry-run)",
               "invalid_candidate": "후보 무효"}.get(st, "판정불가")
    return verdict, " · ".join(parts), None


def parse_by_metric(item, spath, mtime_floor=0.0) -> dict:
    """metric 이름으로 파서를 고른다(모르는 metric 은 예외 없이 오류 dict)."""
    kind = item.get("metric")
    if kind == "wf_sweep_summary":
        return parse_wf_sweep(spath, mtime_floor)
    if kind == "champion_robust_eval":
        return parse_champion_robust(spath, mtime_floor)
    if kind == "champion_promote_dryrun":
        return parse_champion_promote_dryrun(spath, mtime_floor)
    return {"error": f"parser 없음 (metric={kind!r})"}


def judge_by_metric(item, parsed, per=None) -> tuple:
    """metric 이름으로 판정기를 고른다(arm 실험은 judge_per, 기준선·게이트는 전용 판정)."""
    kind = item.get("metric")
    if kind == "champion_robust_eval":
        return judge_champion_baseline(item, parsed)
    if kind == "champion_promote_dryrun":
        return judge_promote_dryrun(item, parsed)
    p = per if per is not None else (parsed.get("per_exp") or {})
    return judge_per(item, p)


def rejudge_parser_gap() -> list:
    """'parser 없음'으로 기록된 원장 항목을 **사후 재판정**한다(요약 JSON 재파싱).

    왜(실측 2026-09-30 CG43): 백로그에 새 metric(champion_promote_dryrun)을 등록해 실행하면,
    파서를 나중에 배선했을 때 **이미 돌고 있던 프로세스는 옛 모듈을 들고 있어** "parser 없음"으로
    기록된다(rc=0 → 항목이 done 으로 닫힘) → 그 항목의 유일한 산출물인 게이트 판정이 사라진다.
    같은 CPU 비용(수십 분~수 시간)을 재실행하는 대신, 요약 JSON 을 새 파서로 다시 읽어
    기록만 교정한다(자기신고 금지 유지 — 여전히 요약 파일에서 계산한다).

    대상은 `parsed.error` 에 'parser 없음' 이 있는 기록뿐이다(실행실패·측정값 있는 기록은
    건드리지 않는다).
    """
    rows = load_ledger()
    b = load_backlog()
    fixed, changed = [], False
    for r in rows:
        pe = r.get("parsed")
        if not isinstance(pe, dict) or "parser 없음" not in str(pe.get("error", "")):
            continue
        it = next((i for i in b["items"] if i.get("id") == r.get("id")), None)
        if not it or not it.get("command"):
            continue
        parsed = parse_by_metric(it, summary_path(it.get("metric") or "", it.get("command")), 0.0)
        if parsed.get("error"):
            continue
        verdict, detail, delta = judge_by_metric(it, parsed)
        r["parsed"] = parsed
        r["verdict"], r["detail"] = verdict, detail
        r["rejudged"] = {"ts": now_kst().isoformat(timespec="seconds"),
                         "note": "파서 사후 배선 — 요약 JSON 재파싱으로 판정 교정(재실행 아님)"}
        changed = True
        fixed.append((r.get("id"), verdict, detail))
    if changed:
        _rewrite_ledger(rows)
        for it in b["items"]:
            for iid, v, d in fixed:
                if it.get("id") == iid and isinstance(it.get("result"), dict):
                    it["result"].update({"verdict": v, "detail": d})
        save_backlog(b)
        for iid, v, d in fixed:
            log(f"재판정(파서 사후 배선) {iid}: {v} — {d}")
    return fixed


def failure_cause(rc, started=None, log_path=None):
    """실패 원인 추정. 137 이면 컨테이너가 **실행 중에** 재생성됐는지 실제로 확인해 적는다.

    실측(2026-09-27 TR3): StartedAt 을 실행 시작 시각과 비교하지 않고 무조건 "컨테이너 재생성
    (평일 20:00 evening_pipeline)"으로 단정해, **호스트 부팅(14:43) 이후 시작한 실행(14:54)** 의
    137 을 엉뚱한 원인으로 원장에 남겼다(실제는 수동 kill). StartedAt 이 실행 시작보다 이전이면
    재생성은 원인이 아니다 — 후보를 좁혀 적어야 다음 사람이 잘못된 복구를 하지 않는다.

    rc=1 은 로그 꼬리에서 실제 예외 줄을 찾아 적는다(실측 2026-09-29 U3): 종전엔 "종료코드 1"
    만 남아, 354분 빌드가 '빌드 중 피처 코드 변경 감지'로 조기 중단된 사실이 원장에서 보이지
    않았다 → 원인 진단에 로그를 다시 열어야 했다.
    """
    if rc == 1:
        pat = ("빌드 중 피처 코드 변경", "체크포인트 무시", "RuntimeError", "Error", "Traceback")
        tail = ""
        try:
            if log_path:
                with open(log_path, encoding="utf-8", errors="replace") as f:
                    tail = "".join(f.readlines()[-60:])
        except OSError:
            tail = ""
        fatal = ""
        for line in reversed([ln.strip() for ln in tail.splitlines() if ln.strip()]):
            if any(p in line for p in pat):
                fatal = line[:300]
                break
        return (f"종료코드 1 — {fatal}" if fatal else "종료코드 1(로그 꼬리에서 예외 줄 미발견)")
    if rc == 137:
        st = ""
        try:
            st = subprocess.run(
                ["docker", "inspect", CONTAINER, "--format", "{{.State.StartedAt}}"],
                capture_output=True, text=True, timeout=20).stdout.strip()
        except Exception:
            st = ""
        st_dt = None
        try:
            st_dt = datetime.strptime(st[:19], "%Y-%m-%dT%H:%M:%S").replace(tzinfo=timezone.utc)
        except Exception:
            st_dt = None
        if st_dt is not None and started is not None:
            if st_dt > started.astimezone(timezone.utc):
                return (f"SIGKILL(137) — 컨테이너가 **실행 중** 재생성됨(StartedAt={st[:19]}Z > 실행 시작)"
                        " → 평일 20:00 evening_pipeline 의 docker compose up -d 가 원인")
            return (f"SIGKILL(137) — 컨테이너 재생성 아님(StartedAt={st[:19]}Z 는 실행 시작보다 이전)"
                    " → 외부/수동 종료 후보: 호스트 세션 종료, 사용자 종료 준비, 수동 kill."
                    " 호스트 uptime·auth.log ROOT LOGIN·다른 세션 흔적으로 확인하라")
        return ("SIGKILL(137) — 컨테이너 StartedAt 조회/파싱 실패로 원인 미확정"
                " (재생성 여부를 단정하지 말 것)")
    if rc == 124:
        return "timeout(124) — 작업이 타임아웃을 초과(진행률 대비 타임아웃이 짧았는지 확인하라)"
    return f"종료코드 {rc}"


# ── 판정 (요약 JSON 에서 직접 계산 — 로그 문구·winner 기준 금지) ───────────────
def judge_per(item, per_exp) -> tuple:
    """per_exp(config → {mean,std,folds,...}) 에서 (verdict, detail, delta) 를 계산한다.

    함정 ①(실측 2026-09-25): '최고 점수(winner) vs 대조군' 으로 비교하면 가설군이 **진** 경우
      winner == 대조군 이 되어 Δ 0.0000 '노이즈' 로 잘못 기록된다(실제 h8 0.5068 vs h5 0.5406
      = Δ−0.0338). → 반드시 arm 기준으로 계산한다.
    함정 ②(실측 2026-09-26, U1): 대조군이 **다른 패널/다른 런**에 있어서 `counterfactual` 의
      첫 토큰이 arm 과 **같은 설정명**이면(예: 둘 다 LS_quant_q30_h5) 같은 런 안에서 자기 자신과
      비교해 Δ+0.0000 '노이즈' 가 된다. 150종목 실측 0.5140 vs 기록 기준선 0.5406 = Δ−0.0266
      인데 자기대조로 '노이즈' 로 남았다. → 이름이 같으면 같은 런 비교를 금지하고 **기록
      기준선(item['baseline'])** 과 비교한다(다른 데이터 스냅샷 대조라 증거 강도는 약함을 명시).
    """
    verdict, detail, delta = "판정불가", "", None
    per = per_exp if isinstance(per_exp, dict) else {}
    if not per:
        return verdict, detail, delta
    # ── 구간 짝(paired) 프로토콜 (2026-09-28, CG13 후속) ─────────────────────────
    # 왜: CG13 실측(5.7분)에서 **같은 크기(30종목) 서로소 5구간**의 게이트 ON 대조군 폴드 평균이
    # 0.5204([0:30)) ~ 0.4917([120:150)) = Δ0.0287 로 사전문턱 +0.02 를 유니버스 교체만으로 넘겼다.
    # 그러면 '단일 arm vs 단일 대조군' 비교는 유니버스 교체 잡음과 뒤섞여 해석할 수 없다 →
    # 같은 구간 **안에서** arm−대조군 짝 Δ 를 구해(구간 간 교체 효과가 상쇄된다) 평균·부호로 판정한다.
    # 사전등록: 짝 Δ 평균 ≥ +0.02 **이고** 양(+) 구간이 n-1 개 이상이면 신호, 평균 ≤ −0.02 면 악화,
    # 그 사이는 노이즈(구간 수가 5라 '1개 예외'까지 허용한다).
    pairs = item.get("pairs")
    if pairs:
        ds, wins, miss = [], 0, []
        for a, c in pairs:
            if a in per and c in per:
                dseg = per[a]["mean"] - per[c]["mean"]
                ds.append(dseg)
                wins += 1 if dseg > 0 else 0
            else:
                miss.append(f"{a}/{c}")
        if ds:
            mean_d = sum(ds) / len(ds)
            delta = round(mean_d, 4)
            verdict = "신호있음" if (mean_d >= 0.02 and wins >= len(ds) - 1) else \
                ("악화" if mean_d <= -0.02 else "노이즈")
            detail = (f"구간 짝 Δ 평균 {mean_d:+.4f} (n={len(ds)}구간 · 양(+) {wins}/{len(ds)}) "
                      f"[{', '.join(f'{d:+.4f}' for d in ds)}]"
                      + (f" · 미측정 {', '.join(miss)}" if miss else ""))
            return verdict, detail, delta
    arm = item.get("arm")
    cf_name = (item.get("counterfactual") or "").split(" ")[0]
    base_rec = item.get("baseline")
    best = max(per.items(), key=lambda kv: kv[1]["mean"])
    if arm and arm in per and cf_name in per and cf_name != arm:
        delta = round(per[arm]["mean"] - per[cf_name]["mean"], 4)
        verdict = "신호있음" if delta >= 0.02 else ("악화" if delta <= -0.02 else "노이즈")
        detail = (f"가설 {arm} {per[arm]['mean']:.4f} vs 대조군 {cf_name} "
                  f"{per[cf_name]['mean']:.4f} → Δ{delta:+.4f} "
                  f"(최고: {best[0]} {best[1]['mean']:.4f})")
    elif arm and arm in per and base_rec:
        # 대조군이 이 런에 없거나(다른 패널) arm 과 동명(자기대조) → 기록 기준선과 비교한다.
        base = float(base_rec["value"])
        delta = round(per[arm]["mean"] - base, 4)
        verdict = "신호있음" if delta >= 0.02 else ("악화" if delta <= -0.02 else "노이즈")
        why = "자기대조(대조군이 다른 패널·다른 런)" if cf_name == arm \
            else f"대조군 {cf_name} 이 이 런에 없음"
        # 증거 강도 문구는 **스냅샷 동일 여부**로 갈린다(실측 2026-09-30 U3b): 같은 패널 원본에서
        # 행 구간만 잘라 비교하면(창 A/B) 교차패널 잡음이 없으므로 '타 패널 대조라 증거 약함'은
        # 거짓이다. 백로그 baseline.same_panel=true 로 표시한 항목만 in-snapshot 문구를 쓴다.
        strength = ("같은 패널 원본·행 구간만 다름(in-snapshot)" if base_rec.get("same_panel")
                    else "타 패널 대조라 증거 약함")
        detail = (f"가설 {arm} {per[arm]['mean']:.4f} vs 기록 기준선 {base:.4f} "
                  f"({base_rec.get('source', '출처미상')}) → Δ{delta:+.4f} [{why} · {strength}]")
    elif cf_name in per and cf_name != arm:
        # arm 미지정: 대조군 **을 제외한** 최고 config 와 비교한다. 대조군이 이미 최고면
        # 비교 자체가 Δ0 이 되어 의미가 없다(함정 ① 과 같은 종류).
        others = [(k, v) for k, v in per.items() if k != cf_name]
        if others:
            w = max(others, key=lambda kv: kv[1]["mean"])
            delta = round(w[1]["mean"] - per[cf_name]["mean"], 4)
            verdict = "신호있음" if delta >= 0.02 else ("악화" if delta <= -0.02 else "노이즈")
            detail = (f"[arm 미지정] {cf_name} 제외 최고 {w[0]} {w[1]['mean']:.4f} vs {cf_name} "
                      f"{per[cf_name]['mean']:.4f} → Δ{delta:+.4f}")
        else:
            verdict = "기준선없음"
            detail = (f"대조군 {cf_name} {per[cf_name]['mean']:.4f} 만 측정됨(비교할 가설군 없음)"
                      + (f" · 기록 기준선 {float(base_rec['value']):.4f} 존재" if base_rec else ""))
    else:
        verdict, detail = "기준선없음", (f"최고 {best[0]} {best[1]['mean']:.4f} "
                                    f"(대조군 {cf_name} 미측정 · 기록 기준선 없음)")
    return verdict, detail, delta


def judge_champion_baseline(item, parsed) -> tuple:
    """배포 챔피언 견고 AUC 실측의 판정 — arm 실험이 아니라 **기준선 실측**이다.

    왜 judge_per 를 쓰지 않는가: 판정 대상이 '가설군 vs 대조군'이 아니라 **승격 게이트가
    비교해야 할 정직한 기준선 숫자**다(하드룰 #1: 단일 분할 AUC 는 승격 기준선으로 쓰지 않는다).
    arm/counterfactual 이 없으므로 judge_per 는 '기준선없음'이라는 무의미한 줄을 남긴다.
    """
    robust = parsed.get("robust_auc")
    if not isinstance(robust, (int, float)):
        return "판정불가", (parsed.get("error") or "robust_auc 없음 — 요약 확인 필요"), None
    std = parsed.get("auc_std_across_folds")
    means = parsed.get("fold_means") or []
    detail = (f"워크포워드 견고 AUC {robust:.4f}"
              + (f"±{std:.4f}" if isinstance(std, (int, float)) else "±?")
              + f" (시간창 {len(means)}개 [{', '.join(f'{m:.4f}' for m in means)}])"
              + f" · 풀링 {parsed.get('auc_pooled')} · 날짜별평균 {parsed.get('auc_per_date_mean')}"
              + f" · 행 {parsed.get('rows_scored')}")
    ref = ((item.get("baseline") or {}) if isinstance(item.get("baseline"), dict) else {}).get("value")
    if isinstance(ref, (int, float)):
        detail += (f" · 기존 승격 기준선 {float(ref):.4f}(단일분할) — 프로토콜이 달라 직접비교 금지"
                   f" · 교체는 승인 대상")
    return "기준선 실측", detail, None


# ── 사이클 실행 ──────────────────────────────────────────────────────────────
def execute(item, force=False):
    os.makedirs(RUNTIME, exist_ok=True)
    ok, why = guards(force, item)
    if not ok:
        log(f"시작 보류: {why}")
        # 거부는 '사이클 실행'이 아니다 — pidfile/state 를 남기면 다음 틱이 "기록 없이 죽었다"로
        # 오보하고 **원장에 가짜 실행실패 기록 + 재시도 카운터**를 남긴다. 실측(2026-09-27 15:10):
        # `--start U3` 가 부하 가드로 거부됐는데 state.json 에 pid 27092 가 남아, 그대로 뒀으면
        # 16:00 틱이 "사이클이 기록 없이 죽었다: U3" 로 orphan 을 기록할 참이었다.
        # 단, **내 pid 일 때만** 지운다(뒤늦게 끝난 옛 자식이 지금 도는 사이클의 락을 지우면 안 된다).
        try:
            if os.path.exists(PIDFILE) and \
                    open(PIDFILE, encoding="utf-8").read().strip() == str(os.getpid()):
                os.remove(PIDFILE)
            with open(STATE, encoding="utf-8") as fh:
                if json.load(fh).get("pid") == os.getpid():
                    os.remove(STATE)
        except (OSError, json.JSONDecodeError):
            pass
        return 3

    os.makedirs(LOGDIR, exist_ok=True)
    stamp = now_kst().strftime("%Y%m%d-%H%M%S")
    run_log = os.path.join(LOGDIR, f"me_cycle_{item['id']}_{stamp}.log")
    started = now_kst()

    # metric 이 없는 항목(예: 산출물 존재를 보는 항목)도 크래시 없이 '판정불가' 로 끝나야 한다.
    # 실측 2026-09-26: L4 는 metric 키가 없어 사후 처리에서 KeyError 로 죽을 수 있었다.
    spath = summary_path(item.get("metric") or "", item.get("command"))
    pre_mtime = os.path.getmtime(spath) if os.path.exists(spath) else 0.0
    # floor 에 **실행 시작 시각**을 포함한다: 파일 mtime 만 쓰면 갱신되지 않은 옛 요약이
    # 통과한다(설계원칙 4 함정, 실측 2026-09-25).
    mtime_floor = max(pre_mtime, time.time())

    log(f"실행: {item['id']} — {item['title']}")
    log(f"로그: {run_log}")
    with open(run_log, "w", encoding="utf-8") as lf:
        lf.write(f"# {item['id']} {item['title']}\n# started {started.isoformat()}\n"
                 f"# command: {item['command']}\n\n")
        lf.flush()
        proc = subprocess.run(item["command"], shell=True, stdout=lf,
                              stderr=subprocess.STDOUT, cwd=PROJ)
    rc = proc.returncode
    cause = ""          # rc==0 이면 미설정 — 아래 재시도 분기가 참조하므로 초기화한다

    # ── rc=5 예외(champion_promote) ─────────────────────────────────────────────
    # champion_promote.py 는 후보가 무효(invalid_candidate)일 때 **5** 를 돌려준다. 이건 인프라
    # 실패가 아니라 **게이트가 내린 판정**이다(실측 2026-09-30 CG43: 항목의 유일한 산출물이
    # 그 판정인데 rc!=0 이면 '실행실패·측정값 없음'으로 지워진다). 요약이 실제로 갱신됐을 때만
    # 완료로 다루고, 요약이 없으면 종전대로 실패로 기록한다.
    gate_rc5 = False
    if rc == 5 and item.get("metric") == "champion_promote_dryrun":
        _p5 = parse_champion_promote_dryrun(spath, mtime_floor)
        gate_rc5 = not _p5.get("error")

    if rc != 0 and not gate_rc5:
        # 실패한 실행에 성능 판정을 붙이지 않는다(설계원칙 6). 옛 요약을 읽어 Δ 를 만들면
        # 소실이 '노이즈(측정됨)'로 세어져 무개선 카운터·승격 판단이 오염된다.
        cause = failure_cause(rc, started, run_log)
        parsed = {"error": "실행 실패 — 측정값 없음", "rc": rc, "cause": cause,
                  "summary_mtime": os.path.getmtime(spath) if os.path.exists(spath) else None}
        verdict, detail, delta = "실행실패", f"측정값 없음 — {cause}", None
        per: dict = {}
    else:
        parsed = parse_by_metric(item, spath, mtime_floor)
        # 판정: **가설군(item['arm'])** 을 **대조군(counterfactual)** 과 비교한다.
        # ⚠ 함정(실측 2026-09-25): '최고 점수(winner) vs 대조군' 으로 비교하면, 가설군이 **진** 경우
        # winner == 대조군 이 되어 Δ 0.0000 "노이즈" 로 잘못 기록된다. 실제로는 h8 0.5068 vs
        # h5 0.5406 = Δ−0.0338 인데 원장에 Δ+0.0000 으로 남았다. 반드시 arm 기준으로 계산하라.
        _pe = parsed.get("per_exp")
        per = _pe if isinstance(_pe, dict) else {}
        verdict, detail, delta = judge_by_metric(item, parsed, per)
        if parsed.get("error") and not per:
            detail = parsed["error"]

    rec = {
        "ts": now_kst().isoformat(timespec="seconds"),
        "id": item["id"], "title": item["title"],
        "rc": rc, "elapsed_min": round((now_kst() - started).total_seconds() / 60.0, 1),
        "log": os.path.relpath(run_log, PROJ),
        "metric": item.get("metric"), "parsed": parsed,
        "verdict": verdict, "detail": detail,
        "reported": False,
    }
    if gate_rc5:
        rec["rc_note"] = "champion_promote rc=5 = 후보 무효(invalid_candidate) — 게이트 판정이므로 실행실패 아님"
    append_ledger(rec)

    b = load_backlog()
    for it in b["items"]:
        if it["id"] == item["id"]:
            it.setdefault("attempts", []).append({
                "ts": rec["ts"], "rc": rc, "verdict": verdict, "detail": detail,
                "log": rec["log"], "elapsed_min": rec["elapsed_min"],
            })
            code_churn = rc == 1 and "피처 코드 변경" in cause
            if rc == 0 or gate_rc5:
                it["status"] = "done"
            elif (rc in (137, 124) or code_churn) and len(it["attempts"]) < RETRY_MAX:
                # 인프라 사고(컨테이너 재생성·타임아웃·빌드 중 피처 코드 변경)는 가설의 결과가 아니다
                # → pending 으로 되돌려 다시 돌린다(최대 RETRY_MAX 회). 코드 변경 건은 이제
                # feature_pipeline 이 조기 중단하므로 소실이 몇 분으로 줄고, 착수는 u3_launcher 의
                # 프리플라이트(피처 코드 120분 안정)가 담당한다.
                it["status"] = "pending"
                resume = ("코드 프리즈 후 재빌드 — u3_launcher 프리플라이트가 피처 코드 120분 무편집 시 착수"
                          if code_churn else "체크포인트 재개")
                it["retry_note"] = (f"{rec['ts']} rc={rc} 소실 → 재시도 "
                                    f"{len(it['attempts'])}/{RETRY_MAX} ({resume})")
            else:
                it["status"] = "failed"
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
    # ── 기동 확인: 가드 거부로 즉시 죽은 자식을 '실행 중'으로 세우지 않는다 ────────────
    # 실측(2026-09-27 15:10): `--start U3` 가 부하 가드 거부로 rc=3 즉시 종료했는데 pidfile·state
    # 가 남아 다음 틱이 "기록 없이 죽었다"로 오보하고 원장에 가짜 실행실패를 남길 참이었다.
    time.sleep(3)
    refused = False
    try:
        with open(logfile, encoding="utf-8") as fh:
            refused = "시작 보류" in fh.read()
    except OSError:
        pass
    if p.poll() is not None or refused:
        for f in (PIDFILE, STATE):
            try:
                os.remove(f)
            except OSError:
                pass
        log(f"기동 실패: {item_id} — 가드 거부 또는 즉시 종료"
            f"(로그 {os.path.relpath(logfile, PROJ)}). '실행 중'으로 기록하지 않는다")
        return 3
    log(f"백그라운드 시작: {item_id} pid={p.pid} (로그 {os.path.relpath(logfile, PROJ)})")
    return 0


def ingest(item_id, log_rel=None):
    """구동기 **밖**에서 돌린 실행의 결과를 원장·백로그에 반영한다(자기신고 없이 요약 JSON 에서).

    왜 필요한가(실측 2026-09-29): 세션과 함께 죽지 않게 `setsid` 로 분리해 띄운 장시간 실행
    (예: 70분짜리 champion_robust_eval)은 execute() 를 거치지 않아 **원장에 기록이 남지 않는다**.
    그렇다고 같은 CPU 비용을 다시 쓰는 재실행은 낭비이므로, 결과 파일을 파싱해 편입하는
    정식 경로를 둔다. 판정 규칙은 execute() 와 동일하다(요약 JSON 직접 계산 · 로그 문구 금지).
    mtime floor 는 0 — 실행 시작 시각을 모르는 외부 실행이라 '낡은 요약'과 구분할 수 없다.
    대신 요약의 measured_at 을 기록에 남겨 사람이 시각을 검증할 수 있게 한다.
    """
    b = load_backlog()
    it = next((i for i in b["items"] if i["id"] == item_id), None)
    if not it:
        log(f"백로그에 {item_id} 없음 — ingest 불가")
        return 2
    try:
        spath = summary_path(it.get("metric") or "", it.get("command"))
    except ValueError as e:
        log(f"ingest 불가: {e}")
        return 2
    parsed = parse_by_metric(it, spath, 0.0)
    per = parsed.get("per_exp") if isinstance(parsed.get("per_exp"), dict) else {}
    if parsed.get("error") and not per:
        log(f"ingest 실패: {parsed['error']} (파일 {os.path.relpath(spath, PROJ)})")
        return 3
    verdict, detail, delta = judge_by_metric(it, parsed, per)
    parsed["out_of_band"] = True
    parsed["source"] = os.path.relpath(spath, PROJ)
    parsed["note"] = "구동기 밖(setsid)에서 돌린 실행의 결과를 ingest 로 편입(요약 JSON 직접 파싱)"
    rec = {
        "ts": now_kst().isoformat(timespec="seconds"),
        "id": it["id"], "title": it["title"],
        "rc": 0, "elapsed_min": None,
        "log": log_rel or "",
        "metric": it.get("metric"), "parsed": parsed,
        "verdict": verdict, "detail": detail, "reported": False,
    }
    append_ledger(rec)
    for x in b["items"]:
        if x["id"] == item_id:
            x.setdefault("attempts", []).append({
                "ts": rec["ts"], "rc": 0, "verdict": verdict, "detail": detail,
                "log": log_rel or "", "elapsed_min": None, "ingested": True,
            })
            x["status"] = "done"
            x["result"] = {"verdict": verdict, "detail": detail, "delta": delta,
                           "per_exp": per or None, "rc": 0}
    save_backlog(b)
    log(f"ingest {item_id}: {verdict} — {detail}")
    # 창별 값을 표시한다(per_exp 가 아니다 — 카운터·best_robust 오염 방지 위해 파서가 windows 로 싣는다).
    for w in (parsed.get("windows") or []):
        if not isinstance(w, dict):
            continue
        log(f"  창{w.get('fold')}: {w.get('auc_mean')} (std {w.get('auc_std')}) [{w.get('window')}]")
    for name, v in per.items():
        log(f"  {name}: {v.get('mean')} [{v.get('desc')}]")
    log(f"원장 기록 완료(ts={rec['ts']}, reported=False → 다음 틱이 보고한다). "
        f"측정 시각(요약) {parsed.get('measured_at')}")
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


def _elapsed_note(started):
    """시작 시각 → '경과 2h16m'. 파싱 실패하면 빈 문자열(틱은 절대 죽지 않는다)."""
    try:
        mins = int((now_kst() - datetime.fromisoformat(started)).total_seconds() // 60)
        return f"경과 {mins // 60}h{mins % 60:02d}m"
    except (TypeError, ValueError):
        return ""


def _progress_note(item_id):
    """장시간 빌드의 진행률 한 줄 — 틱이 4~6시간 동안 눈이 멀지 않게.

    실측(2026-09-25): U1 패널 빌드(41,893 페어)는 4~5시간 걸리는데, 그 동안 틱은
    "부하 과다"만 반복해 진행 중인지·소실됐는지 사람이 알 수 없었다. 로그의
    `Build progress: N/M` 을 읽어 진행률로 바꾼다(진행 로그가 없으면 로그 줄 수만).
    """
    if not item_id:
        return ""
    try:
        cands = [os.path.join(LOGDIR, n) for n in os.listdir(LOGDIR)
                 if n.startswith(f"me_cycle_{item_id}_") and n.endswith(".log")]
    except OSError:
        return ""
    if not cands:
        return ""
    newest = max(cands, key=os.path.getmtime)
    last, lines = "", 0
    try:
        with open(newest, encoding="utf-8", errors="replace") as f:
            for line in f:
                lines += 1
                if "Build progress" in line:
                    last = line.strip()
    except OSError:
        return ""
    if not last:
        return f" · 로그 {lines:,}줄 (진행 로그 없음)"
    pair = last.rsplit("Build progress:", 1)[-1].strip().split()[0]     # "15600/41893"
    done, _, total = pair.partition("/")
    if not done.isdigit() or not total.isdigit() or int(total) == 0:
        return f" · {pair}"
    d, t = int(done), int(total)
    return f" · 빌드 진행 {d:,}/{t:,} ({d / t * 100:.1f}%)"


def _run_log_of(item_id):
    """그 사이클의 실행 로그 상대경로(가장 최근 것). 없으면 빈 문자열."""
    try:
        cands = [os.path.join(LOGDIR, n) for n in os.listdir(LOGDIR)
                 if n.startswith(f"me_cycle_{item_id}_") and n.endswith(".log")]
    except OSError:
        return ""
    if not cands:
        return ""
    return os.path.relpath(max(cands, key=os.path.getmtime), PROJ)


def _record_orphan(orphan, started):
    """기록 없이 사라진 사이클을 **원장에 남긴다**(측정값 없음 → 무효, 무개선 카운터 비오염).

    왜(실측 2026-09-26 U3): 사용자가 기차 탑승 전 컴퓨터를 끄면서 종료 준비 스크립트가
    사이클(pid 14876)과 wf_label_sweep 자식들을 **의도적으로 정지**시켰는데, 구동기 프로세스가
    함께 SIGKILL 되어 원장에 아무 기록이 남지 않았다. 틱은 "기록 없이 죽었다" 한 줄만 출력하고
    끝나서 ① 그 실행이 무엇이었는지·얼마나 진척됐는지가 증거로 남지 않고 ② scoreboard 의
    무효(invalid) 카운터에도 잡히지 않는다. 외부 종료도 rc=137 '실행실패(측정값 없음)' 로
    명시해 남긴다 — **성능 판정은 절대 붙이지 않는다**(rc!=0 규칙: 소실을 '측정된 노이즈'로
    세면 무개선 카운터와 '새 레버 필요' 판단이 오염된다).

    반환: (원장 레코드, 백로그 상태 문자열)
    """
    detail = ("외부/비정상 종료 — 원장 기록 전에 프로세스가 사라짐(추정: 호스트 종료·수동 정지·"
              "컨테이너 재생성 SIGKILL). 측정값 없음")
    note = _progress_note(orphan)
    try:
        mins = round((now_kst() - datetime.fromisoformat(started)).total_seconds() / 60.0, 1)
    except (TypeError, ValueError):
        mins = None
    rec = {
        "ts": now_kst().isoformat(timespec="seconds"),
        "id": orphan,
        "title": f"(기록 없이 종료된 사이클 — {orphan})",
        "rc": 137,
        "elapsed_min": mins,
        "log": _run_log_of(orphan),
        "metric": None,
        "parsed": {"error": "실행 실패 — 측정값 없음", "rc": 137, "cause": detail,
                   "progress": (note.strip(" ·") or None)},
        "verdict": "실행실패",
        "detail": detail + note,
        "reported": True,      # 이 줄에서 이미 사람에게 보고했다(중복 보고 방지)
        # 미전달 감지용: 이 시각에 대응하는 크론 실행이 전달 실패면 다음 틱이 되돌려 재보고한다.
        "reported_at": now_kst().isoformat(timespec="seconds"),
    }
    append_ledger(rec)

    # 백로그도 execute() 와 같은 규칙으로 갱신한다(인프라 사고 = 가설의 결과가 아니다).
    b = load_backlog()
    status = "?"
    for it in b["items"]:
        if it["id"] == orphan:
            it.setdefault("attempts", []).append({
                "ts": rec["ts"], "rc": 137, "verdict": "실행실패", "detail": rec["detail"],
                "log": rec["log"], "elapsed_min": mins,
            })
            if len(it["attempts"]) < RETRY_MAX:
                it["status"] = "pending"
                it["retry_note"] = (f"{rec['ts']} 기록 없이 종료 → 재시도 "
                                    f"{len(it['attempts'])}/{RETRY_MAX} (체크포인트 재개)")
            else:
                it["status"] = "failed"
            it["result"] = {"verdict": "실행실패", "detail": rec["detail"],
                            "delta": None, "per_exp": None, "rc": 137}
            status = it["status"]
            break
    save_backlog(b)
    return rec, status


# ── U3 런처 상시 유지 ─────────────────────────────────────────────────────────
# 왜 틱이 런처를 관리하는가(실측 2026-09-28 05:0x): U3(995일 창 패널 빌드)는 est_minutes 가
# 컨테이너 재생성 창을 넘는 장시간 항목이라 **틱은 시작할 수 없다**(--force 필요) → 런처가
# 20:35~21:00 창에 대신 착수시킨다. 그런데 런처는 Hermes 세션에서 nohup 으로 뜨므로
# **세션이 끝나면 함께 사라진다**(실측: 04:31:45 에 뜬 pid 84267 이 05:0x 에 이미 없음 —
# 로그에 종료 흔적조차 없이). 런처가 없으면 그날 밤 U3 는 시작조차 못 하고 큐가 조용히 멈춘다.
# 틱은 크론이라 세션과 무관하게 매시간 돌므로, 틱이 생존을 확인해 없으면 setsid 로 다시 띄운다
# (자식의 프로세스 그룹을 세션에서 분리해야 세션 정리 SIGKILL 을 피한다).
LAUNCHER = os.path.join(PROJ, "scripts/u3_launcher.sh")
PANEL995 = os.path.join(PROJ, "services/xgboost-ml/app/models/wf/panel_995.npz")


def launcher_alive():
    """u3_launcher.sh 가 살아 있는가. pgrep 대신 /proc 스캔(pgrep 부재·플래그 차이 회피)."""
    for pid in os.listdir("/proc"):
        if not pid.isdigit():
            continue
        try:
            with open(f"/proc/{pid}/cmdline", "rb") as f:
                cl = f.read().replace(b"\x00", b" ").decode("utf-8", "ignore")
        except OSError:
            continue
        if "u3_launcher.sh" in cl:
            return True
    return False


def u3_results_done() -> bool:
    """U3 **스윕 결과**까지 끝났는가 — 패널 npz 존재만으로 '완료'라 하지 않는다.

    패널 npz 는 빌드 끝에 저장되고 스윕(wf_label_sweep)은 그 **뒤**에 같은 프로세스에서 돈다.
    그래서 npz 존재는 '빌드는 끝났다'일 뿐이고, 개장 전 컨테이너 timeout 에 스윕이 잘린 밤에는
    결과가 없다(실측 2026-09-29: 빌드 ETA 08:34 vs timeout 08:35:55 = 여유 0). 이때 완료로
    오독하면 런처를 폐기하고, 틱은 est 1410분 때문에 ETA 가드로 U3 를 계속 건너뛰므로
    **스윕을 아무도 시작하지 않는 교착**이 된다. 완료 판정은 원장의 U3 rc=0 기록으로만 한다.
    """
    try:
        sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
        from u3_done_check import u3_done
        return bool(u3_done(LEDGER, "U3"))
    except Exception:
        return False


def ensure_launcher(dry=False) -> str:
    """U3 런처를 살아 있게 유지한다. 반환: 사람이 읽을 상태 문자열."""
    if os.path.exists(PANEL995) and u3_results_done():
        return "U3 패널 완성 + 스윕 rc=0 — 런처 불필요"
    if launcher_alive():
        return "U3 런처 실행 중"
    if not os.path.exists(LAUNCHER):
        return "U3 런처 스크립트 없음(경로 확인 필요)"
    if dry:
        return "U3 런처 시작 필요(dry-run)"
    try:
        with open(os.path.join(RUNTIME, "u3_launcher.log"), "a", encoding="utf-8") as lg:
            lg.write(f"[{now_kst().strftime('%F %T')}] 틱이 런처를 (재)기동"
                     f" — 세션 종료로 죽었던 것으로 보임\n")
    except OSError:
        pass
    subprocess.Popen(["setsid", "bash", LAUNCHER], cwd=PROJ, start_new_session=True,
                     stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                     stderr=subprocess.DEVNULL)
    return "U3 런처 (재)기동 — 20:35~21:00 창에 U3 착수"


def _print_results(rows):
    """원장 기록을 틱 stdout 으로 출력한다(자기신고 금지 — 요약 JSON 에서 계산된 값만)."""
    for r in rows:
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


def tick(force=False):
    ns = north_star("engineer")
    if ns:
        print(ns)
    print(f"  런처: {ensure_launcher()}")
    # 이전 틱이 '보고 처리'했지만 크론 실행이 전달 실패한 기록을 되돌린다 → 아래 unreported 블록이
    # 같은 결과를 다시 출력한다(영구 소실 방지). 실측 2026-09-30: 제공자 불통 4시간 동안 U3 소실.
    for line in check_undelivered_reports():
        print(line)
    # 파서가 없어 '판정불가(parser 없음)'로 남은 기록을 요약 JSON 재파싱으로 교정한다
    # (실측 2026-09-30 CG43 — 실행 중 프로세스는 옛 모듈을 들고 돌므로 착수 후 배선한 파서가
    #  반영되지 않는다. 재실행 없이 결과를 살린다).
    for iid, verdict, detail in rejudge_parser_gap():
        print(f"  재판정(파서 사후 배선): {iid} → {verdict} — {detail[:160]}")
    pid = running_pid()
    if pid:
        st = {}
        try:
            with open(STATE, encoding="utf-8") as f:
                st = json.load(f)
        except (OSError, json.JSONDecodeError):
            pass
        log(f"사이클 실행 중: {st.get('id', '?')} pid={pid} 시작 {st.get('started', '?')}")
        print(f"  {_elapsed_note(st.get('started'))}{_progress_note(st.get('id', ''))}")
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
                # 원장·백로그에 남긴다: 안 남기면 "왜 그 실험 기록이 없지"를 아무도 모른다.
                rec, st_new = _record_orphan(orphan, started)
                print(f"  기록: {rec['verdict']} · {rec['detail']} · 백로그 상태 → {st_new}")
                print("  → 원인을 고치고 다시 시작하라(재시도는 구동기가 체크포인트에서 재개한다).")
                return 0

    led = load_ledger()
    unreported = [r for r in led if not r.get("reported")]
    if unreported:
        _print_results(unreported)
        # 보고 처리 표시(같은 결과를 매 틱 반복 보고하지 않는다). reported_at 은 **미전달 감지용**이다:
        # 이 시각에 대응하는 크론 실행이 전달 실패면 다음 틱이 플래그를 되돌려 재보고한다.
        stamp = now_kst().isoformat(timespec="seconds")
        for r in unreported:
            r["reported"] = True
            r["reported_at"] = stamp
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
    it = next_item(load_backlog(), force)
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
            # 원장/백로그의 result 는 dict 일 수도, 자유서술 문자열일 수도 있다(실측 L4).
            # dict 만 가정하면 --status 가 TypeError 로 죽어 현황 조회가 불가능해진다.
            if isinstance(r, dict):
                print(f"                 → {r.get('verdict')} {r.get('detail')}")
            else:
                print(f"                 → {r}")
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
    ap.add_argument("--ingest", metavar="ID",
                    help="구동기 밖(setsid)에서 돌린 실행의 요약 JSON 을 원장·백로그에 편입")
    ap.add_argument("--log", help="--ingest 와 함께 쓸 실행 로그 경로(상대경로, 선택)")
    ap.add_argument("--force", action="store_true")
    a = ap.parse_args()

    if a.status:
        return status()
    if a.tick:
        return tick(a.force)
    if a.ingest:
        return ingest(a.ingest, a.log)
    if a.start:
        return start_background(a.start, a.force)
    if a.run:
        b = load_backlog()
        it = next((i for i in b["items"] if i["id"] == a.run), None)
        if not it:
            log(f"백로그에 {a.run} 없음")
            return 2
        rc = execute(it, a.force)
        # 백그라운드 실행이 끝나면 pidfile 정리 — 단, **그 pidfile 이 내 것일 때만**.
        # 실측(2026-09-25): 소유권 검사 없이 지우면 뒤늦게 끝난 옛 자식이 지금 도는
        # 사이클의 pidfile 을 지워 교차 락까지 무력화한다(16:00엔 있었고 17:00엔 없었다).
        try:
            if _pid_of(PIDFILE) == os.getpid():
                os.remove(PIDFILE)
            # state.json 도 같은 소유권 규칙으로 정리한다. 실측(2026-09-27 15:10): 가드 거부로
            # 즉시 끝난 `--run U3` 이 pidfile 만 지워 state.json 에 죽은 pid 가 남았고,
            # running_pid() 의 fallback 이 그걸 읽어 "실행 중"으로 오인할 수 있는 상태였다.
            with open(STATE, encoding="utf-8") as fh:
                if json.load(fh).get("pid") == os.getpid():
                    os.remove(STATE)
        except (OSError, json.JSONDecodeError):
            pass
        return rc
    ap.print_help()
    return 0


if __name__ == "__main__":
    sys.exit(main())

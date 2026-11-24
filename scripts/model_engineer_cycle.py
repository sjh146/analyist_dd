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
import hashlib
import json
import math
import os
import re
import statistics
import subprocess
import sys
import time
from datetime import datetime, timedelta, timezone
from _paths import proj, proj_dir

PROJ = os.environ.get("ME_PROJ", proj_dir())
BACKLOG = os.path.join(PROJ, "docs/QUANT_MODEL_BACKLOG.json")
LEDGER = os.path.join(PROJ, "data/reports/model_engineer_ledger.jsonl")
RUNTIME = os.path.join(PROJ, "data/reports/me_cycle")
LOGDIR = os.path.join(PROJ, "data/reports/me_cycle/logs")



PIDFILE = os.path.join(RUNTIME, "running.pid")
LOCKFILE = os.path.join(RUNTIME, "cycle.lock")
STATE = os.path.join(RUNTIME, "state.json")







HERMES_EXEC_DB = os.path.expanduser(
    os.environ.get("ME_EXEC_DB", "~/.hermes/cron/executions.db"))
JOBID_FILE = os.path.join(RUNTIME, "cron_job_id.txt")
DEFAULT_JOB_ID = "d4070d508732"
REPORT_EARLY_SEC = 300
REPORT_WINDOW_SEC = 3600


UNDELIVERED_LOOKBACK_HOURS = 6
KST = timezone(timedelta(hours=9))
CONTAINER = "stock_xgboost_ml"
LOAD_MAX = float(os.environ.get("ME_LOAD_MAX", "3.5"))




LOAD_MAX_NIGHT = float(os.environ.get("ME_LOAD_MAX_NIGHT", "4.5"))
CPU_IDLE_MIN = float(os.environ.get("ME_CPU_IDLE_MIN", "20"))
MARKET_OPEN, MARKET_CLOSE = (9, 0), (15, 30)


MIN_PAIRED_SESSIONS = 20

HOLIDAY_PATH = os.path.join(PROJ, "data/krx_holidays.json")


RECREATE_WEEKDAYS = (1, 2, 3, 4, 5)
RECREATE_HOUR = 20
RECREATE_SAFETY_MIN = 10





RECREATE_GRACE_MIN = 12
RETRY_MAX = 3


def now_kst():
    return datetime.now(KST)


def log(msg):
    print(f"[{now_kst().strftime('%H:%M:%S')}] {msg}", flush=True)



def load_backlog():
    with open(BACKLOG, encoding="utf-8") as f:
        return json.load(f)


def backlog_indent():
    """커밋된 백로그의 들여쓰기 폭을 파일에서 직접 읽는다(기본 1).

    왜 파일에서 읽는가: 이 파일의 포맷은 세션마다 갈렸다(indent=1 로 커밋된 시기도,
    indent=2 로 커밋된 시기도 있다). 고정값을 쓰면 **한쪽 관례에서 틱마다 2만 줄 전체가
    '변경'으로 잡혀** 진짜 변경(수 줄)이 화이트스페이스 노이즈에 묻힌다 — 실측 2026-11-19
    16:0x 틱: HEAD 는 indent=2 인데 구동기는 indent=1 로 덮어써 diff 21,552줄(실변경 4줄).
    현재 파일의 폭을 그대로 따르면 어느 관례로 커밋돼 있어도 diff 가 실변경만 남는다.
    """
    try:
        with open(BACKLOG, encoding="utf-8") as f:
            f.readline()
            nxt = f.readline()
        if nxt.startswith(" "):
            return max(1, len(nxt) - len(nxt.lstrip(" ")))
    except OSError:
        pass
    return 1


def save_backlog(b):
    b["updated_at"] = now_kst().isoformat(timespec="seconds")
    tmp = BACKLOG + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:

        json.dump(b, f, ensure_ascii=False, indent=backlog_indent())
    os.replace(tmp, BACKLOG)


def load_ledger(limit=None):
    if not os.path.exists(LEDGER):
        return []
    out = []
    with open(LEDGER, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
                try:
                    rec = json.loads(line)
                except json.JSONDecodeError:
                    continue


                if not rec.get("detail"):
                    rec["detail"] = rec.get("note") or "(근거 미기록)"
                out.append(rec)
    return out[-limit:] if limit else out


SCORECARD = os.path.join(PROJ, "data", "reports", "model_engineer_scorecard.json")

FORWARD_SCORECARD = os.path.join(PROJ, "services", "xgboost-ml", "reports", "overnight",
                                 "forward_scorecard.json")


def validation_block():
    """원장 기록마다 싣는 검증 블록 — 트레이더 계약 2번(폴드 통계·purge·승격 dry-run).

    WHY (2026-10-05, MT49 실측): 트레이더 사이클의 `verify_model_handoff` 는 **원장 최근 5행**을
    문자열로 훑어 folds / mean±std / fold_win_rate / purge / promote_dryrun 이 있는지 본다.
    그런데 2026-10-04 밤의 최근 5행이 전부 돈 지표(fillable_topk_expectancy) 기록이라
    gaps 4개가 계속 남아 트레이더가 매 사이클 같은 핸드오프를 재발행했다(계약2 미비).
    → 배포 챔피언 성적표(scripts/champion_scorecard.py 산출물)를 **모든 기록에 압축해 실어**
    최근 5행 어디서나 계약 필드가 보이게 한다. 값은 파일에서 읽고 출처·측정시각을 함께 남긴다
    (자기신고 금지 — 값을 만들어 내지 않고, 낡았으면 낡은 사실이 보이게 둔다).
    """
    try:
        import champion_scorecard as _cs
        return _cs.validation_block(SCORECARD)
    except Exception as e:
        log(f"검증 블록 생략({type(e).__name__}: {e})")
        return None


def append_ledger(rec):
    os.makedirs(os.path.dirname(LEDGER), exist_ok=True)
    try:
        vb = validation_block()
        if vb and "validation" not in rec:
            rec["validation"] = vb
    except Exception as e:
        rec.setdefault("validation_error", f"{type(e).__name__}: {e}")
    with open(LEDGER, "a", encoding="utf-8") as f:
        f.write(json.dumps(rec, ensure_ascii=False) + "\n")



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
    """ISO 시각 → aware datetime. **tz 없는 값은 KST 로 간주**한다(원장 규약).

    WHY(실측 2026-10-07 22:1x, CG133): 세션이 원장 `reported_at` 을 tz 없이(`2026-10-07T22:01:52`)
    적어 두자 `check_undelivered_reports()` 의 `rt - st` 가
    `TypeError: can't subtract offset-naive and offset-aware datetimes` 로 죽었다. 이 함수는
    **틱(tick())이 try 없이 호출**하므로 예외가 그대로 올라가 틱 전체(보고·다음 항목 착수·런처 유지)를
    중단시킨다 — 데이터 표기 하나가 자율 루프를 세우는 구조였다. 원장·실행 DB 의 시각 규약은 KST 이므로
    naive 는 KST 로 붙여 aware 로 통일한다(값을 바꾸지 않고 해석만 맞춘다).
    """
    try:
        dt = datetime.fromisoformat(str(s))
    except (TypeError, ValueError):
        return None
    if dt.tzinfo is None:
        return dt.replace(tzinfo=KST)
    return dt


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


    cand = [r for r in led if r.get("reported") and (r.get("reported_at") or r.get("ts"))]
    if not cand:
        return []
    try:
        hist = _exec_history(cron_job_id())
    except Exception as e:
        return [f"NOTE: 미전달 감지 불가({type(e).__name__}: {e}) — 원장 플래그는 유지"]
    floor = now_kst() - timedelta(hours=UNDELIVERED_LOOKBACK_HOURS)




    hist_norm = []
    for started, status, outcome in hist:
        st = _parse_ts(started)
        if st is None or st < floor:
            continue
        undelivered = (outcome in ("failed", "unknown")
                       or (status in ("failed", "unknown") and outcome != "delivered"))
        hist_norm.append((st, undelivered, started, status, outcome))




    flagged = {}
    for r in cand:
        rt = _parse_ts(r.get("reported_at") or r.get("ts"))
        if rt is None:
            continue
        for idx, (st, undelivered, _started, _status, _outcome) in enumerate(hist_norm):
            dt = (rt - st).total_seconds()
            if -REPORT_EARLY_SEC <= dt <= REPORT_WINDOW_SEC:
                if undelivered:
                    flagged.setdefault(idx, []).append(r)
                break
    bad, changed = [], False
    for idx in sorted(flagged):
        _st, _und, started, status, outcome = hist_norm[idx]
        hit = flagged[idx]
        for r in hit:
            r["reported"] = False
            r.pop("reported_at", None)
        changed = True
        bad.append(f"{str(started)[:16]} 실행({outcome or status}) 이 소비한 기록: "
                   + ", ".join(r.get("id", "?") for r in hit))
    if changed:
        _rewrite_ledger(led)
        return ["=== ⚠ 이전 틱 결과가 사용자에게 전달되지 않았다(크론 실행 실패/미확정)"
                " → 이번 보고에 포함하라:"] + [f"    {b}" for b in bad]
    return []



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
        return False
    except PermissionError:
        pass
    except OSError:
        return False
    try:
        with open(f"/proc/{pid}/cmdline", "rb") as f:
            cmd = f.read()
    except OSError:
        return True
    return b"_cycle.py" in cmd


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










PIPELINE_SCRIPTS = ("full_pipeline_dd.sh", "evening_pipeline.sh")
PIPELINE_LONG_MIN = 60


_PIPELINE_RUNNERS = {"bash", "sh", "dash", "zsh", "nohup", "setsid", "timeout", "env",
                     "python", "python3"}


def _iter_cmdlines():
    """(pid, cmdline '\0' 구분 원문) — 이 이미지엔 쓸 만한 `ps` 플래그가 없다(스킬 교훈)."""
    for name in os.listdir("/proc"):
        if not name.isdigit():
            continue
        try:
            with open(f"/proc/{name}/cmdline", "rb") as fh:
                raw = fh.read().decode("utf-8", "replace")
        except OSError:
            continue
        yield int(name), raw


def pipeline_pid_from_cmdlines(cmdlines, self_pids=()):
    """실행 중인 저녁 파이프라인 (pid, name). 없으면 (None, None).  [순수 함수 — 테스트 대상]

    자기매칭 방지(`pkill -f`·/proc 스캔 함정의 재판): ①self_pids 제외 ②argv 경계는 '\\0' 로
    (셸 `-c '<긴 문자열>'` 은 argv 원소 1개이므로 공백 split 으로 쪼개면 오탐한다)
    ③공백 포함 토큰 제외 ④첫 토큰이 인터프리터/래퍼일 때만 '실행 중'으로 인정.
    """
    for pid, raw in cmdlines:
        if pid in self_pids:
            continue
        argv = [a for a in raw.split("\0") if a]
        if not argv:
            continue
        head = argv[0].rsplit("/", 1)[-1]
        for k, tok in enumerate(argv):
            if any(c.isspace() for c in tok):


                first = tok.split()[0] if tok.split() else ""
                if first and first.rsplit("/", 1)[-1] in PIPELINE_SCRIPTS and \
                        (k == 0 or head in _PIPELINE_RUNNERS):
                    return pid, first.rsplit("/", 1)[-1]
                continue
            if tok.rsplit("/", 1)[-1] in PIPELINE_SCRIPTS and (k == 0 or head in _PIPELINE_RUNNERS):
                return pid, tok.rsplit("/", 1)[-1]
    return None, None


def pipeline_in_flight():
    """(pid, name) — 저녁 파이프라인이 **실행 중**이면 그 정보. 자기·부모 pid 는 제외."""
    return pipeline_pid_from_cmdlines(_iter_cmdlines(), self_pids={os.getpid(), os.getppid()})


def load1():
    try:
        return os.getloadavg()[0]
    except OSError:
        return 0.0


def cpu_idle_pct(interval=1.0):
    """즉시 CPU 유휴율(%). load1 은 '대기 중 작업'까지 세어 장외 잡음을 과대평가하므로
    시작 판단에는 실측 유휴율을 함께 본다. 읽기 실패 시 None(판단은 load 로만)."""
    def _read():
        with open("/proc/stat", encoding="utf-8") as f:
            v = [int(x) for x in f.readline().split()[1:]]
        return sum(v), v[3] + (v[4] if len(v) > 4 else 0)

    try:
        t0, i0 = _read()
        time.sleep(interval)
        t1, i1 = _read()
    except (OSError, ValueError):
        return None
    if t1 - t0 <= 0:
        return None
    return 100.0 * (i1 - i0) / (t1 - t0)


def container_up():
    r = subprocess.run(["docker", "inspect", "-f", "{{.State.Running}}", CONTAINER],
                       capture_output=True, text=True, timeout=30)
    return r.stdout.strip() == "true"





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









_CMD_TIMEOUT_RE = re.compile(r"\btimeout\s+(\d+)\b")

_ONLY_RE = re.compile(r"--only[=\s]+([^\s'\"]+)")


def command_timeout_seconds(command):
    """명령에 박힌 컨테이너 `timeout N`(초) 중 **최대값**. 없으면 None.

    따옴표·중첩 셸(`sh -c '... timeout 4500 python ...'`) 안이어도 텍스트 스캔으로 찾는다 —
    파싱 실패를 조용히 None 으로 넘기면 이 가드가 있으나 마나다(못 잡은 항목만 조용히 잘린다).
    """
    if not command:
        return None
    vals = [int(m) for m in _CMD_TIMEOUT_RE.findall(str(command))]
    return max(vals) if vals else None


def required_timeout_seconds(est_minutes):
    """est_minutes 로 본 필요 벽시계(초) = est×60×1.5 + 300.

    1.5배인 이유(스킬 교훈): 같은 패널·같은 config 도 유휴 7s/cell, 경쟁 155s/cell 로 20배
    차이가 난다 — 유휴 실측으로 타임아웃을 정하면 경쟁 구간에서 반드시 잘린다.
    """
    try:
        est = float(est_minutes)
    except (TypeError, ValueError):
        return None
    if est <= 0:
        return None
    return int(est * 60 * 1.5) + 300


def widen_command_timeouts(command, seconds):
    """`timeout N` 을 N < seconds 인 것만 seconds 로 올린다(내리지 않는다).

    반환: (새 명령, [(이전, 이후), ...]) — 바뀐 값을 로그로 남기기 위해서다.
    """
    changed = []

    def _sub(m):
        cur = int(m.group(1))
        if cur < seconds:
            changed.append((cur, seconds))
            return "timeout {0}".format(seconds)
        return m.group(0)

    return _CMD_TIMEOUT_RE.sub(_sub, command), changed


def effective_command_for(item):
    """(실행 명령, 상향내역, 원래 timeout, 필요 timeout) — execute() 가 쓰는 순수 함수.

    로직을 순수 함수로 빼 두면 자체점검(_cmd_timeout_guard_test)이 컨테이너·부작용 없이
    "명령의 timeout 이 실제로 올라가는가"를 검증할 수 있다(가드가 장식이 되는 것을 막는다).
    """
    cmd = (item or {}).get("command") or ""
    t = command_timeout_seconds(cmd)
    need = required_timeout_seconds((item or {}).get("est_minutes"))
    if need and t is not None and t < need:
        new_cmd, chg = widen_command_timeouts(cmd, need)
        return new_cmd, chg, t, need
    return cmd, [], t, need


def sweep_only_tags(command):
    """`wf_label_sweep` 류 명령의 `--only` 태그 목록. 없으면 None(= 선별 없이 전 config)."""
    if not command:
        return None
    m = _ONLY_RE.search(str(command))
    if not m:
        return None
    return [t.strip() for t in m.group(1).split(",") if t.strip()]


def prereg_control_gap(item):
    """같은 런에 대조군이 없어 **판정이 원천 불가**한 등록이면 경고 문자열, 아니면 None.

    실측(2026-11-18): 최근 40판정 중 8건이 '기준선없음' 으로 닫혔다 — 전부 `--only` 에
    대조군 태그가 없고 기록 `baseline.value` 도 없는 스윕이었다(10~60분 CPU 소모 후 판정 불가,
    원장 verdict='기준선없음'). 판정식은 (arm − 같은런 대조군) 또는 (arm − 기록 기준선) 둘 중
    하나만 인정하므로, 둘 다 없으면 결과가 나와도 무효다. **차단하지 않고 경고만** 한다
    (차단하면 ETA 가드처럼 큐를 굶긴다 — next_item 교훈).
    """
    if not item:
        return None
    tags = sweep_only_tags(item.get("command"))
    if not tags:
        return None
    arm = item.get("arm")
    cf = ((item.get("counterfactual") or "").split(" ") or [""])[0]
    have = set(tags)


    if cf and cf in have and cf != arm:
        return None

    if "--force-paired" in (item.get("command") or ""):


        return None
    if arm and (f"{arm}__ff" in have or (arm.endswith("__ff") and arm[:-4] in have)):
        return None
    if any(t.endswith("__ff") for t in have):
        return None
    base = item.get("baseline") or {}
    if base.get("value") is not None:
        return None
    if arm and arm in have and len(have) == 1:
        return (f"{item.get('id')}: `--only {','.join(tags)}` 단일 arm — 같은 런 대조군도 "
                f"기록 기준선도 없어 결과가 나와도 '기준선없음'(무효)으로 닫힌다. "
                f"대조군 태그를 `--only` 에 함께 넣거나 baseline.value 를 등록하라")
    return (f"{item.get('id')}: `--only {','.join(tags)}` 에 대조군({cf or '미지정'}) 태그가 없고 "
            f"기록 기준선도 없다 — 판정 불가 위험(같은 런 짝 Δ 를 못 만든다)")


def prereg_selfcheck_warnings(items):
    """pending 항목의 '판정 불가' 등록을 경고 문자열 목록으로 돌려준다(읽기 전용)."""
    out = []
    for it in items or []:
        if it.get("status") != "pending":
            continue
        w = prereg_control_gap(it)
        if w:
            out.append(w)
    return out


def timeout_selfcheck_warnings(items):
    """pending 항목의 등록 자기모순을 경고 문자열 목록으로 돌려준다(읽기 전용)."""
    out = []
    for it in items or []:
        if it.get("status") != "pending":
            continue
        est = it.get("est_minutes")
        t = command_timeout_seconds(it.get("command"))
        need = required_timeout_seconds(est)
        if est and t is None:
            out.append(f"{it.get('id')}: est_minutes={est:g} 인데 명령에 컨테이너 `timeout` 이 없다"
                       f" — 벽시계 상한 미설정")
        elif need and t is not None and t < need:
            out.append(f"{it.get('id')}: 명령 timeout {t}s < est {est:g}분 기준 필요 {need}s"
                       f" — 실행은 {need}s 로 올려서 돌리지만 **등록값도 고쳐라**")
    return out


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




    if not force:
        _p, _pname = pipeline_in_flight()
        _est = (item or {}).get("est_minutes") if isinstance(item, dict) else None
        if _p and isinstance(_est, (int, float)) and _est >= PIPELINE_LONG_MIN:
            return False, (f"저녁 파이프라인 실행 중(pid={_p}, {_pname}) — est {_est:g}분 ≥ "
                           f"{PIPELINE_LONG_MIN}분 항목은 시작하지 않음(4코어 직렬화: 수집·챔피언 "
                           f"재학습과 동시 실행 시 서로 3~20배 느려져 컨테이너 timeout 에 잘린다)"
                           f" · 다음 틱에서 재시도")




    if market_hours():
        return False, f"{market_note()} — 시작하지 않음 (--force 로도 차단)"
    l = load1()
    _idle = cpu_idle_pct()



    _limit = LOAD_MAX_NIGHT
    if l > _limit or (_idle is not None and _idle < CPU_IDLE_MIN):










        if force and not market_hours():
            log(f"주의: load1={l:.2f} > {_limit} 또는 idle={_idle if _idle is None else round(_idle,1)}% "
                f"< {CPU_IDLE_MIN}% 이지만 --force·장외·경쟁 사이클 없음 → 강행")
        else:
            _why = (f"load1={l:.2f} > {_limit}" if l > _limit
                    else f"CPU idle={_idle:.1f}% < {CPU_IDLE_MIN}%")
            return False, (f"부하 과다 {_why} — CPU 사용 중(우리 실험 포함)이라 시작 안 함 "
                           f"(다음 틱 재시도 · 장외 문턱 {_limit}/idle {CPU_IDLE_MIN}%)")
    return True, "ok"


def _dep_done(dep_id, backlog, ledger=None) -> bool:
    """선행 항목이 실제로 **끝났는가** — 백로그 status 뿐 아니라 원장 rc=0 도 본다.

    왜(실측 2026-10-05): 백로그 갱신이 원장 기록 **뒤**에 있으므로, 갱신에서 죽으면
    '원장엔 rc=0 인데 백로그는 pending'(반쪽 상태)이 된다(2026-10-01 CG38 실측). 선행 판정을
    status 만으로 하면 그때 후속이 영원히 대기한다 → 원장도 함께 본다.
    """
    for it in backlog.get("items", []):
        if it.get("id") == dep_id and it.get("status") in ("done",):
            return True
    try:
        if ledger is None:
            ledger = load_ledger()
    except Exception:
        return False
    return any(r.get("id") == dep_id and r.get("rc") == 0 for r in ledger)


def _prio(it):
    """정렬용 priority 강건 키.

    왜(실측 2026-11-12): 백로그 262건 중 1건(CG207)의 priority 가 문자열 '1' 로 들어와
    `sorted(..., key=lambda x: x.get("priority", 99))` 가 int/str 비교 TypeError 로 죽었다
    → `--status` 현황 조회가 통째로 불가능. 데이터를 고치는 것만으로는 재발하므로(수기 편집·외부 생성)
    정렬 지점은 전부 이 키를 쓴다.
    """
    p = it.get("priority", 99)
    try:
        return int(p)
    except (TypeError, ValueError):
        return 99


def next_item(backlog, force=False):
    """다음 실행 후보. **ETA 가드에 걸리는 항목은 건너뛴다.**

    왜(실측 2026-09-28): U3(est_minutes=1410 = 23.5h)가 priority=1 인데 평일엔 예상 종료가
    다음 컨테이너 재생성(평일 20:00)을 항상 넘어 execute() 가 rc=3 으로 거부된다 →
    next_item 이 매 틱 U3 만 돌려주므로 **큐 전체가 굶는다**(U3b·L5b·L5c·신규 라벨 실험
    전부 영구 대기). ETA 가드의 목적은 SIGKILL 로 결과를 잃지 않는 것이지 큐를 멈추는 게 아니다.
    건너뛴 이유는 로그에 남겨 '왜 이 항목이 안 도는지'를 추적 가능하게 한다.
    """
    pend = [i for i in backlog["items"] if i.get("status") == "pending"]
    pend.sort(key=lambda i: (_prio(i), i["id"]))
    _led = None
    for i in pend:
        deps = i.get("depends_on") or []
        if deps:
            if _led is None:
                try:
                    _led = load_ledger()
                except Exception:
                    _led = []
            missing = [d for d in deps if not _dep_done(d, backlog, _led)]
            if missing:



                log(f"{i['id']}: 선행 미완({', '.join(missing)}) → 건너뜀")
                continue






        nb = i.get("not_before")
        if nb and not force:
            try:
                _nb = datetime.fromisoformat(str(nb))
                if _nb.tzinfo is None:
                    _nb = _nb.replace(tzinfo=KST)
                if now_kst() < _nb:
                    log(f"{i['id']}: not_before({nb}) 이전 → 건너뜀")
                    continue
            except ValueError:
                log(f"{i['id']}: not_before 형식 오류({nb!r}) — 무시하고 진행")






        _cmd = str(i.get("command") or "").strip()
        if not _cmd or _cmd.startswith("#"):
            log(f"경고: {i['id']} 는 pending 인데 command 가 없다" if not _cmd else
                f"경고: {i['id']} 의 command 가 셸 주석('#')으로 시작한다 → 실행해도 no-op → 건너뜀",
                )
            continue
        if not force:
            blocked, why = eta_blocks(i)
            if blocked:
                log(f"{i['id']}: ETA 가드로 건너뜀 — {why}")
                continue
        return i
    return None



def summary_path(kind, command=None, summary_out=None, quiet=False):











    if summary_out:







        p = str(summary_out).strip("'\"")
        if p.startswith("/app/"):
            return _container_path_to_host(p)
        if os.path.isabs(p):
            return p
        host_p = os.path.join(PROJ, p)
        if os.path.exists(host_p):
            return host_p
        return _container_path_to_host(p)
    if kind == "score_deciles":

        out = _arg(command or "", "--out") or _arg(command or "", "--summary-out")
        if out:
            return _container_path_to_host(out.strip("'\""))
        return os.path.join(PROJ, "services/xgboost-ml/reports/overnight/cg190_score_deciles.json")
    if kind == "wf_sweep_summary":





        out = _arg(command or "", "--summary-out")
        if out:
            return _container_path_to_host(out)
        return os.path.join(PROJ, "services/xgboost-ml/reports/overnight/wf_label_sweep_summary.json")
    if kind == "wf_wave_summary":






        out = _arg(command or "", "--summary-out")
        if out:
            return _container_path_to_host(out.strip("'\""))
        return os.path.join(PROJ, "services/xgboost-ml/reports/overnight/wf_wave_summary.json")
    if kind in ("baseline_protocol_gap", "wf_fold_auc"):







        out = _arg(command or "", "--summary-out") or _arg(command or "", "--out")
        if out:
            return _container_path_to_host(out.strip("'\""))
        raise ValueError(f"{kind}: --summary-out/--out 경로가 커맨드에 없다")
    if kind == "champion_robust_eval":







        out = _out_arg(command or "")
        if out:
            return _container_path_to_host(out)
        return os.path.join(PROJ, "services/xgboost-ml/reports/champion_robust_eval.json")
    if kind == "robust_auc_se":





        out = _arg(command or "", "--out") or _arg(command or "", "--summary-out")
        if out:
            return _container_path_to_host(out.strip("'\""))
        import glob as _glob
        cand = _glob.glob(os.path.join(PROJ, "services/xgboost-ml/reports/overnight/oos_accrual_*.json"))
        return max(cand, key=os.path.getmtime) if cand else ""
    if kind == "oos_accrual":




        import glob as _glob




        _out = _arg(command or "", "--out") or _arg(command or "", "--summary-out")
        if _out:
            _p = _container_path_to_host(_out.strip("'\""))
            if os.path.exists(_p):
                return _p
        cand = _glob.glob(os.path.join(PROJ, "services/xgboost-ml/reports/overnight/oos_accrual_*.json"))
        if cand:
            return max(cand, key=os.path.getmtime)
        return os.path.join(PROJ, "services/xgboost-ml/reports/overnight/oos_accrual_<앵커>.json")
    if kind == "champion_promote_dryrun":




        out = _arg(command or "", "--summary-out")
        if out:
            return _container_path_to_host(out.strip("'\""))
        return os.path.join(PROJ, "services/xgboost-ml/reports/ml_result.json")
    if kind == "champion_scorecard":





        out = (_arg(command or "", "--out") or "").strip("'\"")
        if not out:
            return SCORECARD
        if out.startswith("/app/"):
            return _container_path_to_host(out)
        return out if os.path.isabs(out) else os.path.join(PROJ, out)
    if kind == "intraday_screen":



        out = _arg(command or "", "--json-out")
        if out:
            return _container_path_to_host(out.strip("'\""))
        return os.path.join(PROJ, "services/xgboost-ml/reports/overnight/cg128_intraday_screen.json")
    if kind == "champion_seed_family":





        out = _arg(command or "", "--agg-out")
        if out:
            return _container_path_to_host(out.strip("'\""))
        log("경고: champion_seed_family 인데 커맨드에 --agg-out 이 없다 → 요약 없음(판정불가)")
        return ""
    if kind == "topk_precision":





        out = _arg(command or "", "--json-out")
        if out:
            return _container_path_to_host(out.strip("'\""))
        log("경고: topk_precision 인데 커맨드에 --json-out 이 없다 → 요약 없음(판정불가)")
        return ""
    if kind == "money_anchor_repeat":




        out = _arg(command or "", "--json-out")
        if out:
            return _container_path_to_host(out.strip("'\""))
        log("경고: money_anchor_repeat 인데 커맨드에 --json-out 이 없다 → 요약 없음(판정불가)")
        return ""
    if kind in ("fillable_topk_expectancy", "fillable_topk_vs_pool", "fillable_gate_bands"):








        out = _arg(command or "", "--json-out")
        if out:
            p = out.strip("'\"").rstrip(";,)")






            if "$" in p:
                import glob as _glob, re as _re
                pat = _re.sub(r"\$\{?\w+\}?", "*", _container_path_to_host(p))
                cand = [x for x in _glob.glob(pat) if os.path.isfile(x)]
                if cand:
                    return max(cand, key=os.path.getmtime)
            return _container_path_to_host(p)
        log("경고: fillable_topk_expectancy 인데 커맨드에 --json-out 이 없다 → 요약 없음(판정불가)")
        return ""
    if kind == "fillable_topk_spread":



        out = _arg(command or "", "--out")
        if out:
            return _container_path_to_host(out.strip("'\""))
        log("경고: fillable_topk_spread 인데 커맨드에 --out 이 없다 → 요약 없음(판정불가)")
        return ""
    if kind == "rank_ic_money":






        out = _arg(command or "", "--json-out")
        if out:
            return _container_path_to_host(out.strip("'\""))
        log("경고: rank_ic_money 인데 커맨드에 --json-out 이 없다 → 요약 없음(판정불가)")
        return ""
    if kind == "factor_money_screen":





        out = _out_arg(command or "")
        if out:
            return _container_path_to_host(out)
        return os.path.join(PROJ, "services/xgboost-ml/reports/overnight/factor_money_screen.json")
    if kind == "factor_money_fillable":

        out = _out_arg(command or "")
        if out:
            return _container_path_to_host(out)
        return os.path.join(PROJ, "services/xgboost-ml/reports/overnight/factor_money_screen_fillable.json")
    if kind in ("screen", "db_feature_screen"):




        out = _arg(command or "", "--out") or _redirect_arg(command or "")
        if out:
            return _container_path_to_host(out.strip("'\""))
        return os.path.join(PROJ, "services/xgboost-ml/reports/overnight/db_feature_screen.json")
    if kind == "blend_eval":






        out = _out_arg(command or "")
        if out:
            return _container_path_to_host(out)
        log("경고: blend_eval 인데 커맨드에 --out 이 없다 → 요약 없음(판정불가)")
        return ""
    if kind == "forward_scorecard":







        out = _out_arg(command or "")
        if out:
            return _container_path_to_host(out.strip("'\""))
        return os.path.join(PROJ, "services/xgboost-ml/reports/overnight/forward_scorecard.json")
    if kind == "calibration_probe":





        out = _arg(command or "", "--json-out")
        if out:
            return _container_path_to_host(out.strip("'\""))
        log("경고: calibration_probe 인데 커맨드에 --json-out 이 없다 → 요약 없음(판정불가)")
        return ""
    if kind == "policy_compare":






        out = _arg(command or "", "--json-out")
        if out:
            return _container_path_to_host(out.strip("'\""))
        log("경고: policy_compare 인데 커맨드에 --json-out 이 없다 → 요약 없음(판정불가)")
        return ""
    if kind == "panel_leak_gate":






        out = _arg(command or "", "--json-out")
        if out:
            return _container_path_to_host(out.strip("'\""))
        log("경고: panel_leak_gate 인데 커맨드에 --json-out 이 없다 → 요약 없음(판정불가)")
        return ""
    if kind == "regime_ic_screen":




        out = _arg(command or "", "--json-out")
        if out:
            return _container_path_to_host(out.strip("'\""))
        log("경고: regime_ic_screen 인데 커맨드에 --json-out 이 없다 → 요약 없음(판정불가)")
        return ""
    if kind == "protocol_noise_floor":








        return os.path.join(PROJ, "services/xgboost-ml/reports/overnight/cg143_noise_floor.json")
    if kind == "protocol_noise_curve":




        return os.path.join(PROJ, "services/xgboost-ml/reports/overnight/cg144_noise_curve.json")
    if kind == "protocol_pair_anchor":






        pfx = _env_arg(command or "", "TAGPFX") or "cg145"
        return os.path.join(PROJ, f"services/xgboost-ml/reports/overnight/{pfx}_pair_anchor.json")
    if kind == "scale_regime_audit":







        out = _arg(command or "", "--json-out")
        if out:
            return _container_path_to_host(out.strip("'\""))
        return os.path.join(PROJ, "services/xgboost-ml/reports/overnight/scale_regime_audit.json")
    if kind == "scale_bisect_probe":








        out = _arg(command or "", "--out")
        if out:
            return _container_path_to_host(out.strip("'\""))
        return os.path.join(PROJ, "services/xgboost-ml/app/reports/cg175_scale_bisect.json")
    if kind == "robust_oos_audit":







        out = _out_arg(command or "")
        if out:
            return _container_path_to_host(out.strip("'\""))
        return os.path.join(PROJ, "services/xgboost-ml/reports/model_metric_protocol_audit.json")
    if kind == "metric_audit_pair":







        out = _out_arg(command or "")
        if out:
            return _container_path_to_host(out.strip("'\""))
        return os.path.join(PROJ, "services/xgboost-ml/reports/model_metric_protocol_audit.json")
    if kind == "source_freshness":






        out = _arg(command or "", "--json-out")
        if out:
            return _container_path_to_host(out.strip("'\""))
        return os.path.join(PROJ, "data/reports/source_freshness.json")
    if kind == "reproducibility_overlap":





        out = _arg(command or "", "--json")
        if out:
            return _container_path_to_host(out.strip("'\""))
        return os.path.join(PROJ, "services/xgboost-ml/reports/overnight/cg369_repro_frozen.json")
    if kind == "sns_money_control":



        out = _arg(command or "", "--json-out")
        if out:
            return _container_path_to_host(out.strip("'\""))
        return os.path.join(PROJ, "services/xgboost-ml/reports/overnight/cg250_sns_control.json")





    if kind == "anchor_level_auc":
        return os.path.join(PROJ, "data/reports/overnight/cg455_anchor_level.json")
    if kind == "anchor_paired_delta":
        return os.path.join(PROJ, "data/reports/overnight/cg456_paired_anchors.json")
    if kind == "feature_coverage":
        out = _arg(command or "", "--json-out")
        if out:
            return _container_path_to_host(out.strip("'\""))
        return os.path.join(PROJ, "data/reports/feature_coverage.json")

    if kind and not quiet:
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


def _redirect_arg(command: str) -> str:
    """커맨드의 셸 리다이렉트 대상을 뽑는다(`... > path`, `... >> path`). 없으면 빈 문자열.

    왜(실측 2026-11-14 CG270): 산출물 경로를 `--out` 플래그가 아니라 `>` 로 쓰는 항목이 있다
    (`_db_feature_screen.py > reports/overnight/cg270_db_screen.json`). `_arg` 는 '--flag 값' 만
    보므로 그런 항목은 요약 경로를 영영 못 찾아 rc=0 완주가 '판정불가'로 닫힌다.
    """
    parts = (command or "").split()
    for i, p in enumerate(parts):
        if p in (">", ">>") and i + 1 < len(parts):
            return parts[i + 1].strip("'\"")
        for mark in (">", ">>"):
            if p.startswith(mark) and len(p) > len(mark) and p not in (mark,):
                return p[len(mark):].strip("'\"")
    return ""


def _env_arg(command: str, name: str) -> str:
    """커맨드 접두의 `NAME=값` 환경변수 할당을 뽑는다(없으면 빈 문자열).

    예: `ANCHORS="..." TAGPFX=cg146 bash scripts/cg145_pair_anchor.sh` → "cg146".
    산출물 접두가 env 로 바뀌는 래퍼 스크립트의 요약 경로를 정하기 위한 것 — 경로를 고정하면
    실행마다 같은 파일을 덮어써 이전 실측을 잃는다(champion_robust_eval 에서 실측한 함정).
    """
    for tok in (command or "").split():
        if tok.startswith(name + "=") and not tok.startswith("--"):
            return tok.split("=", 1)[1].strip("'\"")
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
    if not isinstance(d, dict) or "results" not in d:




        return {"error": (f"요약 스키마 불일치: results[] 없음(키={sorted(list(d))[:8]}) — "
                          f"metric 등록이 산출물 형식과 다르다")}
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


def parse_baseline_protocol_gap(path, mtime_floor, item=None) -> dict:
    """같은 런·같은 패널에서 '기준선 arm + 후보 arm' 을 나란히 잰 요약 → arm 통계 + 기준선 짝 Δ.

    왜 별도 파서인가(실측 2026-11-14 00:19, CG260): metric='baseline_protocol_gap' 으로 등록된
    항목이 rc=0(16.9분)으로 완주해 요약 JSON 에 4 arm 폴드 평균을 남겼는데 parse_by_metric 에
    파서가 없어 '판정불가'로 닫혔다 — 측정값은 있는데 판정이 원장에서 사라졌다(CG43·CG10 과 같은
    'metric 등록 누락' 함정).

    ⚠ **per_exp 를 만들지 않는다**: 이 런의 arm 은 다른 패널(panel_prod200)에서 측정됐고,
      스코어보드는 원장 per_exp 전체를 'arm 의 폴드 AUC' 로 읽어 등록 기준선(0.5406)과 직접
      비교한다 → 패널 교체만으로 움직이는 폭(실측 Δ0.025)이 '돌파'로 둔갑한다(CG104 와 동형).
      판정 입력은 `arms`(arm 별 폴드 통계) · `pairs`(같은 런 짝 Δ) 두 필드로만 싣는다.

    item 에서 읽는 필드: `paired_reference`(짝 기준 arm) · `arm_list`(후보 arm 목록) ·
    `registered_baseline_value`(등록 기준선 AUC — 격차 보고용).
    """
    if not os.path.exists(path):
        return {"error": "요약 파일 없음"}
    mt = os.path.getmtime(path)
    if mt <= mtime_floor:
        return {"error": "요약 미갱신(mtime <= 실행 시작 시각) — 옛 결과를 새 결과로 오독 방지",
                "summary_mtime": mt, "floor": mtime_floor}
    with open(path, encoding="utf-8") as f:
        d = json.load(f)
    item = item or {}
    arms: dict = {}
    fold_names = None
    for r in d.get("results", []):
        folds = r.get("folds") or {}
        names = list(folds.keys())
        means = [float(v["mean"]) for v in folds.values()
                 if isinstance(v, dict) and v.get("mean") is not None]
        if not means:
            continue
        if fold_names is None:
            fold_names = names
        if names != fold_names:

            fold_names = [] if fold_names else fold_names
        arms[r.get("exp")] = {
            "desc": r.get("desc"),
            "folds": [round(m, 4) for m in means],
            "mean": round(statistics.mean(means), 4),
            "std": round(statistics.pstdev(means), 4),
            "min": round(min(means), 4),
            "max": round(max(means), 4),
            "fold_win_rate": round(sum(1 for m in means if m > 0.5) / len(means), 3),
            "rows": r.get("n_rows_used"),
            "n_features": r.get("n_effective_features"),
        }
    ref = item.get("paired_reference") or ""
    if not ref:
        _cf = (item.get("counterfactual") or "").split(" ")[0]
        if _cf and _cf in arms and _cf != (item.get("arm") or ""):
            ref = _cf
    pairs: dict = {}
    if ref in arms and len(fold_names or []) > 1:
        for name, a in arms.items():
            if name == ref:
                continue
            ds = [round(x - y, 4) for x, y in zip(a["folds"], arms[ref]["folds"])]
            pairs[name] = {
                "vs": ref,
                "deltas": ds,
                "mean": round(statistics.mean(ds), 4),
                "std": round(statistics.pstdev(ds), 4),
                "pos": sum(1 for x in ds if x > 0),
                "n": len(ds),
            }
    reg = item.get("registered_baseline_value")
    gap = None
    if reg is not None and ref in arms:
        gap = round(arms[ref]["mean"] - float(reg), 4)
    return {
        "finished_at": d.get("finished_at"),
        "panel": item.get("panel") or "",
        "arms": arms,
        "pairs": pairs,
        "paired_reference": ref,
        "registered_baseline_value": reg,
        "baseline_gap_vs_registered": gap,
        "source_rows": [a.get("rows") for a in arms.values()],
        "note": "per_exp 미생성(패널 교체 잡음의 스코어보드 오독 방지)",
    }


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


PER_DATE_AUC_SD = 0.0587


def parse_oos_accrual(path, mtime_floor) -> dict:
    """OOS 캘린더 적립(CG354/CG356) — 견고평가 요약 + 누적 원장을 함께 읽는다.

    왜 전용 파서인가(실측 2026-11-19 CG354): 요약 스키마는 champion_robust_eval 과 같지만,
    판정 대상은 '이 런의 AUC'가 아니라 **누적 표본이 목표에 도달했는가**다. 파서 미등록으로
    rc=0 완주가 '판정불가(parser 없음)'로 닫혀 4.4분 측정이 원장에서 사라졌다.

    ⚠ per_exp 를 만들지 않는다(parse_champion_robust 와 같은 이유) — 창별 AUC 를 arm 처럼 넣으면
    scoreboard 가 최고 창을 '돌파'로 오독한다. 창별 값은 windows 로만 싣는다.
    """
    if not os.path.exists(path):
        return {"error": "요약 파일 없음"}
    mt = os.path.getmtime(path)
    if mtime_floor and mt <= mtime_floor:
        return {"error": "요약 미갱신(mtime <= 실행 시작 시각) — 옛 결과를 새 결과로 오독 방지",
                "summary_mtime": mt, "floor": mtime_floor}
    try:
        with open(path, encoding="utf-8") as f:
            d = json.load(f)
    except json.JSONDecodeError as e:
        return {"error": f"요약 JSON 파싱 실패(쓰는 중일 수 있음): {e}", "summary_mtime": mt}
    folds = [x for x in (d.get("folds") or [])
             if isinstance(x, dict) and isinstance(x.get("auc_mean"), (int, float))]
    if not folds:
        return {"error": "folds 비어 있음(유효 창 없음)", "measured_at": d.get("measured_at")}


    ledger = os.path.join(PROJ, "data/reports/oos_accrual.jsonl")
    rows = []
    if os.path.exists(ledger):
        with open(ledger, encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if line:
                    try:
                        rows.append(json.loads(line))
                    except json.JSONDecodeError:
                        continue
    real, legacy = set(), {}
    for r in rows:
        for fl in (r.get("folds") or []):
            ds = fl.get("dates") or []
            if ds:
                real.update(str(x) for x in ds)
            else:
                k = (str(fl.get("fold")), tuple(fl.get("window") or []))
                legacy[k] = max(legacy.get(k, 0), int(fl.get("n_dates") or 0))
    n_ledger = len(real) if real else sum(legacy.values())
    run_dates = {str(x) for fl in folds for x in (fl.get("dates") or [])}
    n_union = len(real | run_dates) if run_dates else n_ledger
    se = PER_DATE_AUC_SD / (n_union ** 0.5) if n_union else None




    _sds = [(float(x["auc_std"]), int(x.get("n_dates") or 0)) for x in folds
            if isinstance(x.get("auc_std"), (int, float)) and int(x.get("n_dates") or 0) > 0]
    _tot = sum(n for _, n in _sds)
    sd_meas = (sum(sd * n for sd, n in _sds) / _tot) if _tot else None
    se_meas = (sd_meas / (n_union ** 0.5)) if (sd_meas is not None and n_union) else None
    return {
        "measured_at": d.get("measured_at"), "model_dir": d.get("model_dir"),
        "protocol": d.get("protocol"), "window_anchor": d.get("window_anchor"),
        "metric_name": d.get("metric"),
        "robust_auc": d.get("robust_auc"),
        "auc_std_across_folds": d.get("auc_std_across_folds"),
        "fold_means": [float(x["auc_mean"]) for x in folds],
        "windows": [{"fold": x.get("fold"), "window": x.get("window"),
                     "n_dates": x.get("n_dates"), "auc_mean": x.get("auc_mean"),
                     "auc_std": x.get("auc_std"), "dates": x.get("dates") or []} for x in folds],
        "dates_scored": d.get("dates_scored"), "rows_scored": d.get("rows_scored"),
        "ledger_runs": len(rows),
        "ledger_dates": n_ledger,
        "union_dates": n_union,
        "date_level": bool(run_dates),
        "se_per_date": (round(se, 4) if se else None),
        "sd_measured": (round(sd_meas, 4) if sd_meas is not None else None),
        "se_measured": (round(se_meas, 4) if se_meas is not None else None),
        "summary_mtime": mt,
    }


SE_PREDICTED_30D = 0.0114


def parse_robust_auc_se(path, mtime_floor) -> dict:
    """적립 '판정 가능성' 실측(CG370) — 동결 유니버스 런의 날짜 SD·SE 를 그대로 읽는다.

    왜 전용 metric 인가(실측 2026-11-20 06:08): CG370 을 metric=`robust_auc_se` 로 등록했는데
    구동기에 경로·파서가 없어 시작 로그에 "알 수 없는 metric 'robust_auc_se' — 요약 경로 없음
    (판정불가로 기록)" 이 찍혔다. 그대로면 40분짜리 실행이 끝나도 원장에 판정이 남지 않는다
    (CG354·CG261 과 같은 'metric 등록 누락' 함정). 스키마는 champion_robust_eval 과 같으므로
    parse_oos_accrual 을 재사용하고, 이 항목의 질문('SE 가 사전 예측과 맞는가')만 덧붙인다.
    ⚠ per_exp 를 만들지 않는다 — 측정 계측기 항목이라 무개선 카운터에 관여하면 안 된다.
    """
    p = parse_oos_accrual(path, mtime_floor)
    if p.get("error"):
        return p
    sd, se = p.get("sd_measured"), p.get("se_measured")
    n = p.get("union_dates")
    p["metric_name"] = "robust_auc_se"
    if sd is None or se is None or not n:
        p["error"] = "날짜 SD/SE 계산 불가(창 auc_std 없음)"
        return p
    dev = (se - SE_PREDICTED_30D) / SE_PREDICTED_30D
    band = "예측범위(±15%)" if abs(dev) <= 0.15 else ("예측 상회" if dev > 0 else "예측 하회")
    need = int(round((3 * sd / 0.02) ** 2))
    p["se_predicted"] = SE_PREDICTED_30D
    p["se_dev_pct"] = round(dev * 100, 1)
    p["dates_needed_3sigma"] = need
    p["sd_vs_cg366"] = round(sd - 0.0623, 4)
    p["verdict"] = (f"측정완료 — 날짜 {n} · 견고 AUC {p.get('robust_auc')} · SD {sd} · "
                    f"SE {se} vs 예측 {SE_PREDICTED_30D}({band} {p['se_dev_pct']:+.1f}%)")
    p["detail"] = (f"동결 유니버스 날짜 SD {sd} (CG366 0.0623 대비 {p['sd_vs_cg366']:+.4f}) · "
                   f"SE {se} · 판정 3σ(+0.02) 필요 날짜 {need}개 · "
                   + ("SD 가 줄지 않았다 → 날짜 적립만으로는 판정 불가, 종목수 축이 다음 레버"
                      if sd >= 0.05 else
                      f"SD 가 0.05 이하로 내려감 → 남은 적립 필요 날짜 {max(0, need - n)}개"))
    return p


def parse_protocol_noise_floor(path, mtime_floor) -> dict:
    """프로토콜 재현 잡음바닥 요약(scripts/cg143_noise_floor.sh)을 파싱한다 (CG143, 2026-10-09).

    스키마: {determinism_same_anchor, auc{a1,a2,b1}, anchor_shift_delta, folds{a1,b1},
             windows{a1,b1}, pre_registered_threshold, verdict}
    ⚠ **per_exp 를 만들지 않는다** — scoreboard 는 원장의 parsed.per_exp 전체를 'arm 폴드 평균'으로
    읽어 best_robust·무개선 카운터를 만든다. 여기 값은 AUC 자체가 아니라 **잡음 추정치**라
    per_exp 로 넣으면 가짜 돌파/가짜 개선이 난다(CG31 사고와 동형).
    """
    if not path or not os.path.exists(path):
        return {"error": "요약 파일 없음", "path": path}
    mt = os.path.getmtime(path)
    if mtime_floor and mt <= mtime_floor:
        return {"error": "요약 미갱신(mtime <= 실행 시작 시각) — 옛 결과를 새 결과로 오독 방지",
                "summary_mtime": mt, "floor": mtime_floor}
    with open(path, encoding="utf-8") as f:
        try:
            d = json.load(f)
        except json.JSONDecodeError as e:
            return {"error": f"요약 JSON 파싱 실패(쓰는 중일 수 있음): {e}", "summary_mtime": mt}
    errs = d.get("errors") or {}
    if errs or d.get("auc", {}).get("a1") is None:
        return {"error": f"런 실패(앵커 {d.get('anchors')})", "errors": errs, "summary_mtime": mt}
    shift = d.get("anchor_shift_delta")
    thr = float(d.get("pre_registered_threshold", 0.02))
    det = bool(d.get("determinism_same_anchor"))
    if not det:
        verdict = "결정성 위반 — 프로토콜 비결정성 결함"
    elif shift is not None and float(shift) >= thr:
        verdict = "잡음바닥 >= 문턱 — 사전문턱 +0.02 는 잡음과 구분 불가"
    else:
        verdict = "잡음바닥 < 문턱"
    return {
        "metric_name": d.get("metric"), "protocol": d.get("config"),
        "anchors": d.get("anchors"),
        "determinism_same_anchor": det,
        "auc": d.get("auc"),
        "anchor_shift_delta": shift,
        "threshold": thr,
        "noise_over_threshold": (shift is not None and float(shift) >= thr),
        "folds": d.get("folds"), "windows": d.get("windows"),
        "verdict": verdict, "summary_mtime": mt,
    }


def parse_protocol_noise_curve(path, mtime_floor) -> dict:
    """프로토콜 잡음바닥 **스케일링** 요약(scripts/cg144_noise_curve.sh)을 파싱한다 (CG144).

    스키마: {base_anchor, anchors{d0..d7}, auc{tag}, deltas{tag vs d0}, folds{tag},
             max_abs_delta, pre_registered_threshold, verdict}
    ⚠ **per_exp 를 만들지 않는다** — 값이 AUC 자체가 아니라 앵커 이동 잡음이라 스코어보드에
    넣으면 가짜 개선이 난다(parse_protocol_noise_floor 와 동형).
    """
    if not path or not os.path.exists(path):
        return {"error": "요약 파일 없음", "path": path}
    mt = os.path.getmtime(path)
    if mtime_floor and mt <= mtime_floor:
        return {"error": "요약 미갱신(mtime <= 실행 시작 시각) — 옛 결과를 새 결과로 오독 방지",
                "summary_mtime": mt, "floor": mtime_floor}
    with open(path, encoding="utf-8") as f:
        try:
            d = json.load(f)
        except json.JSONDecodeError as e:
            return {"error": f"요약 JSON 파싱 실패(쓰는 중일 수 있음): {e}", "summary_mtime": mt}
    errs = d.get("errors") or {}
    if errs or (d.get("auc") or {}).get("d0") is None:
        return {"error": f"런 실패(앵커 {d.get('anchors')})", "errors": errs, "summary_mtime": mt}
    return {"metric_name": d.get("metric"), "protocol": d.get("config"),
            "base_anchor": d.get("base_anchor"), "anchors": d.get("anchors"),
            "auc": d.get("auc"), "deltas": d.get("deltas"), "folds": d.get("folds"),
            "max_abs_delta": d.get("max_abs_delta"),
            "threshold": d.get("pre_registered_threshold"),
            "verdict": d.get("verdict"), "summary_mtime": mt}


def parse_protocol_pair_anchor(path, mtime_floor) -> dict:
    """앵커 **평균** 짝 Δ 요약(scripts/cg145_pair_anchor.sh)을 파싱한다 (CG145/CG146, 2026-10-09).

    스키마: {metric, anchors{tag:date}, arms{name:dir}, config, auc{tag/arm}, folds{tag/arm},
             level_mean/level_std{arm}, pair_delta{tag}, pair_delta_mean, pair_delta_std,
             anchor_threshold_pass[tag:x|O], anchors_over_threshold, sign_consistent,
             pre_registered_threshold, verdict, errors}
    ⚠ **per_exp 를 만들지 않는다** — 값이 AUC 자체가 아니라 '앵커를 옮겼을 때의 짝 Δ'라
    스코어보드에 넣으면 가짜 개선이 난다(parse_protocol_noise_floor 와 동형).
    """
    if not path or not os.path.exists(path):
        return {"error": "요약 파일 없음", "path": path}
    mt = os.path.getmtime(path)
    if mtime_floor and mt <= mtime_floor:
        return {"error": "요약 미갱신(mtime <= 실행 시작 시각) — 옛 결과를 새 결과로 오독 방지",
                "summary_mtime": mt, "floor": mtime_floor}
    with open(path, encoding="utf-8") as f:
        try:
            d = json.load(f)
        except json.JSONDecodeError as e:
            return {"error": f"요약 JSON 파싱 실패(쓰는 중일 수 있음): {e}", "summary_mtime": mt}
    errs = d.get("errors") or {}
    if errs or not d.get("pair_delta"):
        return {"error": f"런 실패(앵커 {d.get('anchors')})", "errors": errs, "summary_mtime": mt}
    return {
        "metric_name": d.get("metric"), "protocol": d.get("config"),
        "anchors": d.get("anchors"), "arms": d.get("arms"), "pair": d.get("pair"),
        "auc": d.get("auc"), "folds": d.get("folds"),
        "level_mean": d.get("level_mean"), "level_std": d.get("level_std"),
        "pair_delta": d.get("pair_delta"),
        "pair_delta_mean": d.get("pair_delta_mean"), "pair_delta_std": d.get("pair_delta_std"),
        "anchor_threshold_pass": d.get("anchor_threshold_pass"),
        "anchors_over_threshold": d.get("anchors_over_threshold"),
        "sign_consistent": d.get("sign_consistent"),
        "threshold": d.get("pre_registered_threshold"),
        "verdict": d.get("verdict"), "summary_mtime": mt,
    }


def parse_robust_paired_seeds(path, mtime_floor) -> dict:
    """같은 유니버스 시드 짝 Δ(scripts/robust_paired_seeds.py)를 champion_seed_family 스키마로 옮긴다.

    왜(실측 2026-11-19 CG347): 절대 AUC 는 시드 표집만으로 0.0227 흔들린다 → arm 비교는 같은
    시드 짝 Δ 로만 유효하다. 스키마를 seed_family 와 똑같이 맞춰 판정기(judge_seed_family)를
    재사용한다 — 시드 ≥3·Δ평균 ≥ +0.02·양(+) 시드 전부라는 사전 등록 규칙이 동일하기 때문이다.
    """
    if not path:
        return {"error": "요약 경로 없음(summary_out 미지정)"}
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
    if d.get("error"):
        return {"error": d["error"]}
    a, b = d.get("auc_a") or {}, d.get("auc_b") or {}
    paired = {
        "n": d.get("n_seeds"),
        "delta_mean": d.get("delta_mean"),
        "se": d.get("delta_se"),
        "t": d.get("t"),
        "pos_seeds": d.get("pos_seeds"),
        "pos_frac": d.get("pos_frac"),
        "threshold": d.get("threshold", 0.02),
        "seeds": d.get("seeds"),
        "deltas": d.get("deltas"),
        "challenger_arm": d.get("label_a"),
        "base_arm": d.get("label_b"),

        "window_mismatch": d.get("window_mismatch") or None,
        "fold_mismatch": d.get("fold_mismatch") or None,

        "window_cluster": d.get("window_cluster"),
        "folds": d.get("folds"),
    }
    return {
        "measured_at": d.get("measured_at"),
        "protocol": "같은 유니버스 시드 짝 Δ (champion_robust_eval 다중 시드)",
        "arms": {d.get("label_a"): {"mean": sum(a.values()) / len(a) if a else None},
                 d.get("label_b"): {"mean": sum(b.values()) / len(b) if b else None}},
        "robust_auc": sum(a.values()) / len(a) if a else None,
        "baseline_auc": sum(b.values()) / len(b) if b else None,
        "seeds": d.get("seeds"),
        "paired": paired,
        "threshold": d.get("threshold", 0.02),
        "summary_mtime": mt,
    }


def parse_robust_seed_range(path, mtime_floor) -> dict:
    """유니버스 시드 표집 잡음 범위(scripts/robust_seed_range.py)를 파싱한다.

    왜 이 metric 이 필요한가(실측 2026-11-19 CG347): champion_robust_eval 은 실행 하나가
    유니버스 표집 하나다. 같은 모델·같은 10날짜에서 시드 0/1/2 = 0.4887/0.5114/0.5011 →
    범위 0.0227 로 **사전문턱 +0.02 를 넘었다.** 종전에는 이 실험의 metric 이 등록되지 않아
    rc=0 완주가 '판정불가(parser 없음)'로 닫혔다(측정은 있는데 결론이 사라짐).
    per_exp 를 만들지 않는다 — 모델 팔 실험이 아니라 계측기이므로 스코어보드 무개선 카운터와
    무관해야 한다(원장에 arm 값이 실리면 기준선 비교에 섞인다).
    """
    if not path:
        return {"error": "요약 경로 없음(summary_out 미지정)"}
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
    if d.get("error"):
        return {"error": d["error"], "seed_aucs": d.get("seed_aucs")}
    return {
        "metric": "robust_seed_range",
        "n_seeds": d.get("n_seeds"),
        "seed_aucs": d.get("seed_aucs"),
        "range": d.get("range"),
        "std": d.get("std"),
        "se": d.get("se"),
        "mean": d.get("mean"),
        "threshold": d.get("threshold", 0.02),
        "resolvable": d.get("resolvable"),
        "dates_scored": d.get("dates_scored"),
        "rows_scored": d.get("rows_scored"),
        "summary_mtime": mt,
    }


def judge_robust_seed_range(item, parsed) -> tuple:
    """시드 표집 잡음 판정 — 사전 등록 규칙: 범위 ≥ +0.02 면 단일 시드 절대 비교는 해상 불가."""
    if parsed.get("error"):
        return "판정불가", f"요약 없음/미갱신 — {parsed['error']}", None
    n = int(parsed.get("n_seeds") or 0)
    rng = parsed.get("range")
    thr = float(parsed.get("threshold") or 0.02)
    seeds = parsed.get("seed_aucs") or {}
    detail = (f"시드 {n}개 절대 AUC {seeds} · 평균 {parsed.get('mean')} · std {parsed.get('std')} · "
              f"**범위 {rng}** (문턱 {thr:+.2f} 의 {round((rng or 0)/thr, 2)}배) · "
              f"같은 런 날짜별 std 평균 {parsed.get('dates_scored')}날짜/{parsed.get('rows_scored')}행")
    if n < 3:
        return "판정불가", f"시드 수 부족({n}<3) — 범위 추정 불가. {detail}", None
    if rng is None:
        return "판정불가", f"범위 없음. {detail}", None
    if rng >= thr:
        return ("문턱 미해상 — 시드 표집 잡음이 사전문턱과 동급",
                f"{detail}. 함의: 단일 시드·단일 창의 '기록 기준선 대비 Δ'는 부호조차 신뢰 불가 "
                f"(반폭 ±{round(rng/2, 4)}). 유효한 비교는 **같은 유니버스 시드의 짝 Δ**(시드 잡음 상쇄)뿐. "
                f"프로토콜 변경은 리뷰보드 안건(②).", rng)
    return ("문턱 해상 가능", f"{detail} → 시드 잡음이 문턱 미만. 남은 분산원은 날짜 표집.", rng)


def parse_anchor_level_auc(path, mtime_floor) -> dict:
    """앵커(창 상한) 레벨 AUC 격자(`data/reports/overnight/cg455_anchor_level.json`)를 파싱한다.

    왜 이 metric 이 필요한가(실측 2026-11-24 CG455): champion_robust_eval 은 한 번의 실행이
    '창 하나'다. 같은 모델·같은 학습구간에서 창 상한만 옮겨도 post-train AUC 가 0.4790~0.5889
    (**범위 0.1099**)로 흔들렸는데, 종전에는 metric 이 등록되지 않아 17.8분 실행이
    '판정불가(parser 없음)'로 원장에서 사라졌다. 이 값은 arm 실험이 아니라 **계측기**이므로
    per_exp 를 만들지 않는다(스코어보드 무개선 카운터에 섞이면 안 된다).
    """
    if not path:
        return {"error": "요약 경로 없음"}
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
    if d.get("error"):
        return {"error": d["error"], "anchors": d.get("anchors")}
    return {
        "metric": "anchor_level_auc",
        "n_anchors": d.get("n_anchors"),
        "anchors": d.get("anchors"),
        "mean": d.get("mean"),
        "std": d.get("std"),
        "min": d.get("min"),
        "max": d.get("max"),
        "range": d.get("range"),
        "nested_windows": d.get("nested_windows"),
        "window_date_counts": d.get("window_date_counts"),
        "verdict_raw": d.get("verdict"),
        "threshold": d.get("threshold", 0.02),
        "summary_mtime": mt,
    }


def judge_anchor_level_auc(item, parsed) -> tuple:
    """앵커 레벨 잡음 판정 — 사전 등록 규칙(CG455): std≤0.010 기준선 후보 / std>0.020 단일앵커 금지."""
    if parsed.get("error"):
        return "판정불가", f"요약 없음/미갱신 — {parsed['error']}", None
    n = int(parsed.get("n_anchors") or 0)
    sd = parsed.get("std")
    rows = parsed.get("anchors") or []
    grid = " · ".join(f"{r.get('anchor')}:{r.get('auc')}({r.get('n_dates')}d)" for r in rows)
    detail = (f"앵커 {n}개 AUC [{grid}] · 평균 {parsed.get('mean')} · std {parsed.get('std')} · "
              f"범위 {parsed.get('range')} ({parsed.get('min')}~{parsed.get('max')})")
    if parsed.get("nested_windows"):
        detail += (" · ⚠중첩 창(앵커를 옮기면 표본 날짜가 누적: "
                   f"{parsed.get('window_date_counts')}) — 이 std 는 앵커 잡음 + 창 길이 효과")
    if n < 2 or sd is None:
        return "판정불가", f"유효 앵커 {n}개(<2) — std 추정 불가. {detail}", None
    if float(sd) > 0.020:
        return ("앵커 불안정 — 단일 앵커 AUC 는 기준선이 될 수 없다",
                f"{detail}. 함의: **단일 창·단일 앵커의 '기록 기준선 대비 Δ'는 앵커 잡음과 구분 불가**"
                f"(범위 {parsed.get('range')} = 사전문턱 +0.02 의 "
                f"{round((parsed.get('range') or 0)/0.02, 1)}배). 유효 비교는 같은 창·같은 앵커의 짝 Δ"
                f"뿐이며, post-train 절대 AUC 를 기준선으로 등록하는 안은 기각한다.", sd)
    if float(sd) <= 0.010:
        return ("앵커 안정 — post-train 기준선 후보(등록은 승인 항목)",
                f"{detail}. std {sd} ≤ 0.010 이므로 앵커 평균±std 를 기준선 후보로 등록 제안(② 승인).", sd)
    return ("중간 — 평균±std 병기 없이는 판정 불가",
            f"{detail}. std {sd} 가 0.010~0.020 → 단일 값 인용 금지, 앵커 평균±std 병기 요구.", sd)


def parse_anchor_paired_delta(path, mtime_floor) -> dict:
    """앵커별 짝 Δ(`data/reports/overnight/cg456_paired_anchors.json`)를 파싱한다.

    CG455 실측(앵커 레벨 std 0.0382 · 범위 0.1099)으로 **절대 AUC 의 단일 앵커 비교는 폐기**됐고,
    남은 유효 비교는 '같은 앵커·같은 창에서 두 모델을 함께 재는 짝 Δ' 뿐이다. 그 짝 Δ 를
    창(앵커) 5개에 대해 평균±std 로 내는 것이 이 metric 이다. arm 실험이 아니므로 per_exp 없음.
    """
    if not path:
        return {"error": "요약 경로 없음"}
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
    return {
        "metric": "anchor_paired_delta",
        "arm": d.get("arm"), "control": d.get("control"),
        "n_paired_anchors": d.get("n_paired_anchors"),
        "paired": d.get("paired"), "unpaired": d.get("unpaired"),
        "delta_mean": d.get("delta_mean"), "delta_std": d.get("delta_std"),
        "n_positive": d.get("n_positive"),
        "min": d.get("min"), "max": d.get("max"),
        "abs_champion_mean": d.get("abs_champion_mean"),
        "abs_challenger_mean": d.get("abs_challenger_mean"),
        "threshold": d.get("threshold", 0.02),
        "summary_mtime": mt,
    }


def judge_anchor_paired_delta(item, parsed) -> tuple:
    """앵커 짝 Δ 판정 — 사전 등록: 유효 짝 ≥3 · 평균 +0.02 이상 · 4/5 앵커 양(+) 이면 신호."""
    if parsed.get("error"):
        return "판정불가", f"요약 없음/미갱신 — {parsed['error']}", None
    n = int(parsed.get("n_paired_anchors") or 0)
    dm, ds = parsed.get("delta_mean"), parsed.get("delta_std")
    rows = parsed.get("paired") or []
    grid = " · ".join(f"{r.get('anchor')}:{r.get('champion')}→{r.get('retrain_cg346', r.get('arm_value'))}"
                      f"({r.get('delta'):+})" for r in rows) if rows else ""
    detail = (f"짝 앵커 {n}개 [{grid}] · Δ평균 {dm} · 앵커 std {ds} · 양(+) {parsed.get('n_positive')}/{n} · "
              f"절대 챔피언 {parsed.get('abs_champion_mean')} vs 후보 {parsed.get('abs_challenger_mean')}"
              f"(패널·창 동일)")
    if n < 3 or dm is None:
        return "판정불가", f"유효 짝 {n}개(<3) — 앵커 std 추정 불가. {detail}", None
    if dm >= 0.02 and int(parsed.get("n_positive") or 0) >= max(3, round(n * 0.8)):
        return ("신호 — 같은 창 짝 Δ 평균이 사전문턱 +0.02 이상(승격은 돈 게이트 별도)",
                f"{detail}. AUC 축 신호로 기록하되 **승격 아님** — champion_promote --require-expectancy 통과가 "
                f"별도로 필요하다.", dm)
    if dm <= -0.02:
        return ("악화 — 후보가 같은 창에서 챔피언보다 낮다",
                f"{detail}. 후보 교체 축은 이 창에서 닫는다(돈 지표도 함께 확인).", dm)
    return ("노이즈 — 같은 창 짝 Δ 가 사전문턱 +0.02 미만",
            f"{detail}. post-train 창에서 두 모델은 AUC 로 구분되지 않는다 — 이 창의 판단은 돈 지표"
            f"(체결성·수수료 반영)로만 하라.", dm)


def parse_feature_coverage(path, mtime_floor) -> dict:
    """피처 커버리지 재측정 요약(`feature_coverage_report.py --json-out`)을 파싱한다.

    왜(실측 2026-11-24, XR49): 커버리지 지표 210행 중 131행이 computed_at 2026-11-18 에 멈춰
    있었고, 재측정 경로는 ① 호스트에서 `ModuleNotFoundError: app` ② 신선 재빌드에도 '캐시 낡음'
    가드가 걸려 exit 3 으로 **항상 막혀** 있었다. 수리 후 재측정이 완주하는지, 그리고
    '낡은 패널로 computed_at 만 갱신'(신선도 위조)이 아닌지를 요약 JSON 으로 판정한다.
    """
    if not path:
        return {"error": "요약 경로 없음(--json-out 미지정)"}
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
    return {
        "metric": "feature_coverage",
        "source": d.get("source"), "from_cache": d.get("from_cache"),
        "window_days": d.get("window_days"),
        "panel_last_trade_date": d.get("panel_last_trade_date"),
        "n_features": d.get("n_features"), "n_dead": d.get("n_dead"),
        "n_alive": d.get("n_alive"),
        "n_stock_constant": d.get("n_stock_constant"),
        "n_market_level": d.get("n_market_level"),
        "illusion_max": d.get("illusion_max"),
        "worst_null_ratio": d.get("worst_null_ratio"),
        "dead_features": d.get("dead_features"),
        "summary_mtime": mt,
    }


def judge_feature_coverage(item, parsed) -> tuple:
    """커버리지 재측정 판정 — 신선 빌드(from_cache=False)면 갱신 성공, 캐시면 위조 위험으로 무효."""
    if parsed.get("error"):
        return "판정불가", f"요약 없음/미갱신 — {parsed['error']}", None
    n = int(parsed.get("n_features") or 0)
    detail = (f"source={parsed.get('source')} · 피처 {n}개(죽은 {parsed.get('n_dead')} · "
              f"종목상수 {parsed.get('n_stock_constant')} · 시장레벨 {parsed.get('n_market_level')}) · "
              f"패널 마지막 거래일 {parsed.get('panel_last_trade_date')} · "
              f"창 {parsed.get('window_days')}일 · 착시 최대 {parsed.get('illusion_max')}")
    if n <= 0:
        return "판정불가", f"피처 0개 — 측정 자체가 비었다. {detail}", None
    if parsed.get("from_cache"):
        return ("측정 무효 — 낡은 캐시 패널 재사용(computed_at 위조 위험)",
                f"{detail}. `--ignore-cache` 로 신선 재빌드해야 커버리지 신선도가 의미를 갖는다.", None)
    return ("재측정 완료 — 신선 빌드 기준 커버리지 갱신",
            f"{detail}. 다음: 이 값으로 dq_feature_oldest_age_days 리셋 여부를 확인(위조 아님).", None)


def parse_champion_seed_family(path, mtime_floor) -> dict:
    """다중 시드(유니버스) 짝 판정 집계(champion_seed_family_agg.py)를 파싱한다.

    ⚠ per_exp 를 만들지 않는다 — scoreboard 는 원장의 per_exp 전체를 'arm 폴드 평균'으로 읽어
    best_robust·무개선 카운터를 만든다(2026-09-29 CG31 사고). 시드별 값은 `seeds` 로 싣는다.

    판정에 쓰는 값은 `paired`(짝 Δ 평균·SE·t·양(+) 시드 수) 하나뿐이다 — 이게 이 metric 의
    존재 이유다: 단일 유니버스 Δ 는 유니버스 교체 잡음(σ 0.0133)과 구분되지 않는다(CG48/49).
    """
    if not path:
        return {"error": "요약 경로 없음(--agg-out 미지정)"}
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
    paired = d.get("paired") or {}
    if not paired:
        return {"error": "paired 없음(집계 실패)", "measured_at": d.get("measured_at")}
    arms = d.get("arms") or {}
    return {
        "measured_at": d.get("measured_at"),
        "protocol": d.get("protocol"),
        "family": d.get("family"),
        "arms": arms,
        "robust_auc": (arms.get(paired.get("challenger_arm")) or {}).get("mean"),
        "baseline_auc": (arms.get(paired.get("base_arm")) or {}).get("mean"),
        "seeds": d.get("seeds"),
        "paired": paired,
        "threshold": paired.get("threshold", 0.02),
        "summary_mtime": mt,
    }


def judge_seed_family(item, parsed) -> tuple:
    """다중 시드 짝 판정 — 사전 등록 규칙: 시드 ≥3, 짝 Δ평균 ≥ +0.02, 양(+) 시드 = 전부.

    왜 judge_champion_baseline 이 아닌가: 그 판정기는 창 하나(또는 한 arm)의 AUC 를
    `counterfactual_value` 와 비교한다. 여기서는 **시드별 짝 Δ 분포**(평균·SE·부호)가 판정
    대상이라 SE·t·양(+) 비율을 함께 싣는다(CG38 교훈: 문턱 초과만으로 신호라 쓰지 말고 t·SE 를
    함께 보라 — 창 3개면 SE 0.08 대라 어떤 Δ 도 구분 불가).
    """
    if parsed.get("error"):
        return "판정불가", f"요약 없음/미갱신 — {parsed['error']}", None
    p = parsed.get("paired") or {}
    n = int(p.get("n") or 0)
    thr = float(p.get("threshold", 0.02))
    dm = p.get("delta_mean")
    ties = int(p.get("n_ties") or 0)
    tie_note = f" · 동점 {ties}" if ties else ""
    dm_s = f"{dm:+.4f}" if isinstance(dm, (int, float)) else "None"
    detail = (f"시드 {n}개 짝 Δ 평균 {dm_s} (SE {p.get('se')} · t {p.get('t')} · "
              f"양(+) {p.get('pos_seeds')}{tie_note}) · 대조군 {parsed.get('baseline_auc')} vs "
              f"챌린저 {parsed.get('robust_auc')} · 문턱 {thr:+.2f}")


    if p.get("window_mismatch"):
        return "판정불가", f"두 arm 의 채점 창이 다름(Δ 무의미) — {p['window_mismatch'][0]}", dm
    if p.get("fold_mismatch"):
        return "판정불가", f"두 arm 의 폴드 수가 다름(Δ 무의미) — {p['fold_mismatch'][0]}", dm
    if n < 3:
        return "판정불가", f"시드 수 부족({n}<3) — SE 과대. {detail}", dm


    exp = _arg((item or {}).get("command") or "", "--expect-seeds")
    if exp.strip().isdigit() and n < int(exp):
        return "판정불가", f"기대 시드 수 미달({n}<{exp.strip()}) — arm 완주 후 재집계. {detail}", dm
    if dm is None:
        return "판정불가", f"짝 Δ 없음. {detail}", None
    if dm >= thr and float(p.get("pos_frac") or 0) == 1.0:
        return "신호있음", f"{detail} → 양(+) 시드 전부 · 문턱 초과", dm
    if dm >= thr:
        return "노이즈", f"{detail} → 문턱 명목 초과이나 부호 불일치", dm
    return "노이즈", f"{detail} → 문턱 미달", dm


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





        "expectancy_gate": d.get("expectancy_gate"),
        "live_score_gate": d.get("live_score_gate"),
        "candidate_robust_oos": d.get("candidate_robust_oos"),
        "summary_mtime": mt,
    }


def parse_champion_scorecard(path, mtime_floor) -> dict:
    """배포 챔피언 검증 성적표(scripts/champion_scorecard.py --out)를 파싱한다(MT49).

    ⚠ `per_exp` 를 만들지 않는다 — scoreboard 는 원장 `parsed.per_exp` 전체를 'arm 의 폴드 평균'으로
    읽어 best_robust·무개선 카운터를 만든다(2026-09-29 CG31 사고). 여기 폴드 통계는 **챔피언 자기
    과제**의 값이지 어떤 arm 도 아니므로 `fold_stats` 키로만 싣는다.
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
    if not isinstance(d.get("fold_stats"), dict):
        return {"error": "fold_stats 키가 없음(성적표 아님)", "summary_mtime": mt}
    return {
        "generated_at": d.get("generated_at"), "metric_name": d.get("metric_name"),
        "champion_dir": d.get("champion_dir"), "champion_auc_txt": d.get("champion_auc_txt"),
        "fold_stats": d.get("fold_stats"), "purge": d.get("purge"),
        "promote_dryrun": d.get("promote_dryrun"), "money": d.get("money"),
        "insample": d.get("insample"),
        "summary_mtime": mt,
    }


def judge_champion_scorecard(item, parsed) -> tuple:
    """성적표 판정 — **성능 판정이 아니라 계약(자료 완비) 판정**이다.

    이 항목의 성공 조건은 "트레이더가 판정에 쓸 폴드 통계·purge·승격 dry-run 이 원장에 실렸는가"다.
    AUC 개선 여부는 여기서 판정하지 않는다(하드룰 #1 — 승격·기준선은 다중창 짝 Δ 로만).
    """
    if parsed.get("error"):
        return "판정불가", parsed["error"], None
    fs = parsed.get("fold_stats") or {}
    pd = parsed.get("promote_dryrun") or {}
    pv = (parsed.get("purge") or {}).get("observed") or {}
    mn = parsed.get("money") or {}
    parts = []
    if fs.get("mean") is not None:
        parts.append(f"챔피언 폴드 통계 {fs.get('folds')} · 평균 {fs.get('mean')}±{fs.get('std')}"
                     f"(min {fs.get('min')} max {fs.get('max')}) · 폴드승률 {fs.get('fold_win_rate')}"
                     f" · n_folds {fs.get('n_folds')} · 출처 {fs.get('source')} @{fs.get('measured_at')}")
    else:
        parts.append(f"폴드 통계 없음({fs.get('error')})")
    parts.append(f"purge 정책 h={(parsed.get('purge') or {}).get('horizon')}거래일(라벨 참조일 기준)"
                 + (f" · 관측 {pv.get('n_label_ref_purged')}행" if pv else " · 관측값 없음"))
    parts.append(f"승격 dry-run status={pd.get('status')} · 기록 {pd.get('record_id')} @{pd.get('record_ts')}")
    parts.append(f"돈 지표 순기대 {mn.get('expectancy_pct')}%p(t {mn.get('expectancy_t')}) · 세션 {mn.get('n_sessions')}")
    complete = bool(fs.get("mean") is not None and pd.get("status") and (parsed.get("purge") or {}).get("policy"))
    return ("계약 충족" if complete else "계약 미비"), " · ".join(parts), None


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
               "blocked_live_score": "게이트 통과·라이브 스코어 차단(미승격)",
               "invalid_candidate": "후보 무효"}.get(st, "판정불가")
    return verdict, " · ".join(parts), None


def parse_date_paired_auc(path, mtime_floor) -> dict:
    """날짜 단위 짝 Δ(scripts/auc_date_pair.py)를 판정 가능한 스키마로 옮긴다.

    왜(실측 2026-11-19 CG359): 폴드 2개짜리 짝 Δ 는 SE 0.0187 로 사전문턱 +0.02 를 해상하지
    못한다. 폴드는 날짜 묶음일 뿐이므로 **날짜 하나를 한 단위**로 짝지으면 단위 수가 22 로 늘어
    SE 가 측정 가능해진다. 이 metric 이 없으면 date-paired 실행이 전부 'parser 없음 → 판정불가'로
    원장에서 사라진다(CG359 가 실제로 그렇게 닫혔다).
    """
    if not path:
        return {"error": "요약 경로 없음(summary_out 미지정)"}
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
    if d.get("error"):
        return {"error": d["error"]}
    arms = d.get("arms") or {}
    pair = ((d.get("pair") or {}).get("date") or {})
    delta = pair.get("delta") or {}
    if not arms or not delta:
        return {"error": "arms/pair.date.delta 없음 — auc_date_pair 요약이 아니다"}
    boot = pair.get("bootstrap95") or [None, None]
    arm_stats = {}
    for lbl, a in arms.items():
        da = a.get("date_auc") or {}
        arm_stats[lbl] = {"mean": da.get("mean"), "sd": da.get("sd"), "se": da.get("se"),
                          "dates": a.get("dates"), "pooled": a.get("pooled_auc"),
                          "min": da.get("min"), "max": da.get("max")}
    return {
        "protocol": d.get("protocol"),
        "arms": arm_stats,


        "robust_auc": None,
        "paired": {
            "n": pair.get("n_pairs"),
            "delta_mean": delta.get("mean"),
            "delta_sd": delta.get("sd"),
            "se": delta.get("se"),
            "t": delta.get("t"),
            "pos": delta.get("pos"),
            "neg": delta.get("neg"),
            "pos_frac": delta.get("pos_frac"),
            "sign_test_p": delta.get("sign_test_p"),
            "boot_lo": boot[0],
            "boot_hi": boot[1],
            "first": pair.get("first"),
            "last": pair.get("last"),
        },
        "threshold": 0.02,
        "summary_mtime": mt,
    }


def judge_date_paired_auc(item, parsed) -> tuple:
    """날짜 단위 짝 Δ 판정 — 해상 가능성(SE) → 신호/노이즈 순서로 본다."""
    if parsed.get("error"):
        return "판정불가", parsed["error"], None
    p = parsed.get("paired") or {}
    n, delta, se = p.get("n"), p.get("delta_mean"), p.get("se")
    thr = parsed.get("threshold", 0.02)
    if n is None or delta is None:
        return "판정불가", "짝 Δ 없음", None
    head = f"{n}날짜 짝 Δ {delta:+.4f}"


    sd, tv = p.get("delta_sd"), p.get("t")
    if se is not None and sd is not None and tv is not None:
        head += f" (sd {sd:.4f} · SE {se:.4f} · t {tv:+.2f}"
    elif se is not None:
        head += f" (SE {se:.4f}"
    else:
        head += " ("
    head += (f" · 양/음 {p.get('pos')}/{p.get('neg')} · 부호검정 p {p.get('sign_test_p')}"
             if p.get("pos") is not None or p.get("neg") is not None else "")
    head += (f" · 부트스트랩95 [{p.get('boot_lo'):+.4f}, {p.get('boot_hi'):+.4f}]"
             if p.get("boot_lo") is not None and p.get("boot_hi") is not None else "")
    head += f" · 창 {p.get('first')}~{p.get('last')})"
    if n < 10:
        return "판정불가", f"{head} → 날짜 표본 {n} < 10 (판정 최소)", delta

    if se is None or se > thr / 3:
        return ("미해상 — 짝 SE 과대",
                f"{head} → 짝 SE {se} > {thr/3:.4f}: 이 프로토콜로 +0.02 를 해상할 수 없다"
                if se is not None else head, delta)
    if abs(delta) >= thr:
        return ("신호" if delta > 0 else "역신호",
                f"{head} → |Δ| ≥ 사전문턱 {thr} · 짝 SE {se:.4f} (3σ 해상)", delta)
    return "노이즈", f"{head} → |Δ| < 사전문턱 {thr} · 짝 SE {se:.4f} (해상된 무신호)", delta


def parse_repro_overlap(path, mtime_floor) -> dict:
    """CG369 — 같은 플래그·다른 시각 두 런이 **같은 모집단**을 채점했는지 파싱한다.

    왜(실측 2026-11-20 05:07): metric `reproducibility_overlap` 이 구동기에 등록되지 않아
    시작 로그에 "알 수 없는 metric — 요약 경로 없음(판정불가로 기록)" 이 찍혔다 → 200종목 런 2회
    (각 timeout 2400s)가 끝나도 교집합·재현 판정이 원장에서 사라진다. 계측기(모델 팔 아님)이므로
    per_exp 를 만들지 않는다 — 스코어보드 무개선 카운터에 섞이면 안 된다.
    """
    if not path:
        return {"error": "요약 경로 없음(--json 미지정)"}
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
    return {
        "metric": "reproducibility_overlap",
        "n_dates_common": d.get("n_dates_common"),
        "n_dates_a": d.get("n_dates_a"),
        "n_dates_b": d.get("n_dates_b"),
        "overlap_frac_mean": d.get("overlap_frac_mean"),
        "pred_max_abs_delta": d.get("pred_max_abs_delta"),
        "auc_common_delta_mean": d.get("auc_common_delta_mean"),
        "auc_common_delta_sd": d.get("auc_common_delta_sd"),
        "auc_only_a_mean": d.get("auc_only_a_mean"),
        "auc_only_b_mean": d.get("auc_only_b_mean"),
        "verdict": d.get("verdict"),
        "summary_mtime": mt,
    }


def judge_repro_overlap(item, parsed) -> tuple:
    """CG369 사전 등록 성공조건: 교집합 200/200 · max|Δpred| < 1e-9 · |ΔAUC| ≤ 0.005."""
    if parsed.get("error"):
        return "판정불가", f"요약 없음/미갱신 — {parsed['error']}", None
    ov = parsed.get("overlap_frac_mean")
    mx = parsed.get("pred_max_abs_delta")
    da = parsed.get("auc_common_delta_mean")
    nd = parsed.get("n_dates_common")
    detail = (f"날짜 공통 {nd}개 · 종목 교집합 {ov} · max|Δpred| {mx} · "
              f"공통집단 ΔAUC {da} (sd {parsed.get('auc_common_delta_sd')}) · "
              f"전용집단 A {parsed.get('auc_only_a_mean')} / B {parsed.get('auc_only_b_mean')}")
    if ov is None:
        return "판정불가", f"overlap_frac_mean 없음. {detail}", None
    ok = (float(ov) >= 0.999 and mx is not None and float(mx) < 1e-9
          and da is not None and abs(float(da)) <= 0.005)
    if ok:
        return ("재현됨 — 적립 날짜 비교 가능",
                f"{detail}. 유니버스 동결(UNIVERSE_ASOF_DATE)이 같은 모집단·같은 예측을 재현했다 "
                f"→ 이후 날짜 적립의 Δ 는 '모델 vs 모집단 교체' 혼합이 아니다.", None)
    why = []
    if float(ov) < 0.999:
        why.append(f"교집합 {ov}<1.0(모집단 재추첨)")
    if mx is None or float(mx) >= 1e-9:
        why.append(f"예측 불일치 max|Δpred| {mx}")
    if da is None or abs(float(da)) > 0.005:
        why.append(f"공통집단 ΔAUC {da} > 0.005")
    return ("재현 안 됨 — 동결로도 모집단 불일치", f"{detail}. 원인 후보: {', '.join(why)}", None)


METRIC_ALIASES = {



    "fillable_expectancy": "fillable_topk_expectancy",



    "forward_scorecard_version_mix": "forward_scorecard",
    "robust_auc_multifold": "baseline_protocol_gap",
}


def _metric_alias(kind):
    """등록 metric 이름의 별칭을 표준 이름으로 정규화한다(없으면 그대로)."""
    return METRIC_ALIASES.get(kind, kind)


def parse_by_metric(item, spath, mtime_floor=0.0) -> dict:
    """metric 이름으로 파서를 고른다(모르는 metric 은 예외 없이 오류 dict)."""
    kind = _metric_alias(item.get("metric"))
    if kind == "reproducibility_overlap":
        return parse_repro_overlap(spath, mtime_floor)
    if kind in ("wf_sweep_summary", "wf_wave_summary"):

        return parse_wf_sweep(spath, mtime_floor)
    if kind in ("baseline_protocol_gap", "wf_fold_auc", "fold_auc_mean"):






        return parse_baseline_protocol_gap(spath, mtime_floor, item=item)
    if kind == "champion_robust_eval":
        return parse_champion_robust(spath, mtime_floor)
    if kind == "oos_accrual":

        return parse_oos_accrual(spath, mtime_floor)
    if kind == "robust_auc_se":

        return parse_robust_auc_se(spath, mtime_floor)
    if kind == "date_paired_auc":

        return parse_date_paired_auc(spath, mtime_floor)
    if kind == "money_seed_family":

        return parse_money_seed_family(spath, mtime_floor)
    if kind == "insample_gap":

        return parse_insample_gap(spath, mtime_floor)
    if kind == "horizon_ladder":

        return parse_insample_gap(spath, mtime_floor)
    if kind == "champion_promote_dryrun":
        return parse_champion_promote_dryrun(spath, mtime_floor)
    if kind == "champion_seed_family":
        return parse_champion_seed_family(spath, mtime_floor)
    if kind == "anchor_level_auc":
        return parse_anchor_level_auc(spath, mtime_floor)
    if kind == "anchor_paired_delta":
        return parse_anchor_paired_delta(spath, mtime_floor)
    if kind == "feature_coverage":
        return parse_feature_coverage(spath, mtime_floor)
    if kind == "robust_seed_range":

        return parse_robust_seed_range(spath, mtime_floor)
    if kind == "robust_paired_seeds":

        return parse_robust_paired_seeds(spath, mtime_floor)
    if kind == "topk_precision":
        return parse_topk_precision(spath, mtime_floor)
    if kind in ("fillable_topk_expectancy", "fillable_topk_vs_pool", "fillable_topk_spread",
                "fillable_gate_bands"):

        return parse_fillable_topk_expectancy(spath, mtime_floor)
    if kind == "money_anchor_repeat":
        return parse_money_anchor_repeat(spath, mtime_floor)
    if kind == "rank_ic_money":
        return parse_rank_ic_money(spath, mtime_floor)
    if kind == "factor_money_screen":
        return parse_factor_money_screen(spath, mtime_floor)
    if kind == "factor_money_fillable":
        return parse_factor_money_fillable(spath, mtime_floor)
    if kind == "hold_horizon_curve":
        return parse_hold_horizon_curve(
            spath, mtime_floor,
            null_path=summary_path(kind, item.get("command"), item.get("null_out"), quiet=True)
            if item.get("null_out") else None)
    if kind == "vol_beta_decomp":
        return parse_vol_beta_decomp(spath, mtime_floor)
    if kind == "h5_time_profile":
        return parse_h5_time_profile(spath, mtime_floor)
    if kind == "capital_path_compare":
        return parse_capital_path_compare(spath, mtime_floor)
    if kind in ("h5_regime_exit", "hold_horizon_capital"):
        return parse_h5_regime_exit(spath, mtime_floor)
    if kind == "panel_feature_screen":
        return parse_panel_feature_screen(spath, mtime_floor)
    if kind in ("screen", "db_feature_screen"):

        return parse_db_feature_screen(spath, mtime_floor, item=item)
    if kind == "blend_eval":
        return parse_blend_eval(spath, mtime_floor)
    if kind == "forward_scorecard":
        return parse_forward_scorecard(spath, mtime_floor)
    if kind == "calibration_probe":
        return parse_calibration_probe(spath, mtime_floor)
    if kind == "policy_compare":
        return parse_policy_compare(spath, mtime_floor)
    if kind == "panel_leak_gate":
        return parse_panel_leak_gate(spath, mtime_floor)
    if kind == "regime_ic_screen":
        return parse_regime_ic_screen(spath, mtime_floor)
    if kind == "champion_scorecard":
        return parse_champion_scorecard(spath, mtime_floor)
    if kind == "intraday_screen":
        return parse_intraday_screen(spath, mtime_floor)
    if kind == "sns_money_control":
        return parse_sns_money_control(spath, mtime_floor)
    if kind == "source_freshness":
        return parse_source_freshness(spath, mtime_floor)
    if kind == "protocol_noise_floor":
        return parse_protocol_noise_floor(spath, mtime_floor)
    if kind == "protocol_noise_curve":
        return parse_protocol_noise_curve(spath, mtime_floor)
    if kind == "protocol_pair_anchor":
        return parse_protocol_pair_anchor(spath, mtime_floor)
    if kind == "scale_regime_audit":
        return parse_scale_regime_audit(spath, mtime_floor)
    if kind == "scale_bisect_probe":
        return parse_scale_bisect_probe(spath, mtime_floor)
    if kind == "robust_oos_audit":
        return parse_robust_oos_audit(spath, mtime_floor, label=item.get("model_label"))
    if kind == "score_deciles":
        return parse_score_deciles(spath, mtime_floor, label=item.get("model_label"))
    if kind == "metric_audit_pair":
        return parse_metric_audit_pair(spath, mtime_floor,
                                       arm_label=item.get("arm_label"),
                                       control_label=item.get("control_label"))
    return {"error": f"parser 없음 (metric={kind!r})"}


def parse_robust_oos_audit(path, mtime_floor, label=None) -> dict:
    """감사 도구의 단일 모델 robust AUC(재기준선)를 판정 필드로 옮긴다(CG180).

    스키마: {"meta": {...}, "b_robust_auc": {label: {robust_auc, folds[], fold_detail[],
            auc_pooled, train_start, train_end, windows_excluded[]}}, "c_net_expectancy": {label: {...}}}
    `--robust-oos-out`(게이트 증거)에는 폴드 목록이 없으므로 **--out 쪽을 읽는다**(폴드 평균·std·
    폴드별 값을 함께 남겨야 '단일 분할 금지' 하드룰 #1 을 지킨 보고가 된다).
    """
    if not path:
        return {"error": "요약 경로 없음(--out 미지정)"}
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
    b_all = d.get("b_robust_auc") or {}
    if not b_all:
        return {"error": "b_robust_auc 없음(감사 런 실패 또는 --skip b)", "summary_mtime": mt}
    lab = label if label in b_all else (next(iter(b_all)) if len(b_all) == 1 else None)
    if lab is None:
        return {"error": f"모델이 여러 개인데 model_label 미지정: {sorted(b_all)}", "summary_mtime": mt}
    b = b_all.get(lab) or {}
    folds = [x for x in (b.get("folds") or []) if isinstance(x, (int, float))]
    robust = b.get("robust_auc")
    if not isinstance(robust, (int, float)):
        return {"error": f"{lab} robust_auc 없음(폴드 표본 부족 — windows_excluded={b.get('windows_excluded')})",
                "summary_mtime": mt}
    std = statistics.pstdev(folds) if len(folds) >= 2 else None
    da = [x for x in (b.get("date_aucs") or []) if isinstance(x, (int, float))]
    c = (d.get("c_net_expectancy") or {}).get(lab) or {}
    return {
        "metric_name": "robust_oos_audit", "model": lab,
        "robust_auc": float(robust),
        "auc_std_across_folds": round(std, 4) if std is not None else None,
        "fold_means": folds,
        "fold_detail": b.get("fold_detail"),
        "auc_pooled": b.get("auc_pooled"),
        "auc_per_date_mean": round(statistics.mean(da), 4) if da else None,
        "n_dates": b.get("n_dates") or (len(da) or None),
        "train_start": b.get("train_start"), "train_end": b.get("train_end"),
        "windows_excluded": b.get("windows_excluded"),
        "money": {"expectancy_pct": c.get("avg_pct"), "expectancy_t": c.get("t_stat"),
                  "n_sessions": c.get("n_sessions"), "n_trades": c.get("n_trades"),
                  "halves": c.get("halves")},
        "generated_at": (d.get("meta") or {}).get("generated_at") or d.get("generated_at"),


        "other_models": {k: {"robust_auc": v.get("robust_auc"), "folds": v.get("folds"),
                             "train_end": v.get("train_end")}
                         for k, v in b_all.items() if k != lab},
        "summary_mtime": mt,
    }


def judge_robust_oos_audit(item, parsed) -> tuple:
    """재기준선 판정 = judge_champion_baseline + **돈 지표 병기**(최우선 규칙 2)."""
    verdict, detail, delta = judge_champion_baseline(item, parsed)
    m = parsed.get("money") or {}
    if isinstance(m.get("expectancy_pct"), (int, float)):
        detail += (f" · 순기대 {m['expectancy_pct']:+.3f}%p/세션(t {m.get('expectancy_t')})"
                   f" · {m.get('n_sessions')}세션/{m.get('n_trades')}거래 · halves {m.get('halves')}")
    else:
        detail += " · 순기대 없음(c 파트 미실행)"
    return verdict, detail, delta


def parse_score_deciles(path, mtime_floor, label=None) -> dict:
    """점수 분위 순기대(CG190) 요약을 판정 필드로 옮긴다.

    스키마(scripts/cg190_score_deciles.py --out):
      {"config": {...}, "models": {label: {n_sessions, deciles[{decile,mean_pct,t_stat,...}],
        spearman_rho, d10_minus_d1_pct, paired_d10_d1{mean_pct,t_stat,pos,neg,front_pct,back_pct},
        pool_mean_pct, monotonic}}}
    ⚠ per_exp 를 만들지 않는다 — 분위별 값은 'arm 폴드 AUC' 가 아니므로 스코어보드가 읽으면 오독한다.
    """
    if not path:
        return {"error": "요약 경로 없음(--out 미지정)"}
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
    models = d.get("models") or {}
    if not models:
        return {"error": "models 없음(분위 계산 실패)", "summary_mtime": mt}
    lab = label if label in models else (next(iter(models)) if len(models) == 1 else
                                         ("cand" if "cand" in models else next(iter(models))))
    m = models.get(lab) or {}
    if m.get("error"):
        return {"error": m["error"], "summary_mtime": mt}
    dec = [x for x in (m.get("deciles") or []) if isinstance(x.get("mean_pct"), (int, float))]
    if len(dec) < 3:
        return {"error": f"{lab} 분위 평균 부족({len(dec)}개) — 표본 결함", "summary_mtime": mt}
    return {
        "metric_name": "score_deciles", "model": lab,
        "n_sessions": m.get("n_sessions"), "n_rows_after_filter": m.get("n_rows_after_filter"),
        "n_rows_scored": m.get("n_rows_scored"), "n_skipped_sessions": m.get("n_skipped"),
        "deciles": [{"decile": x["decile"], "mean_pct": x["mean_pct"], "t_stat": x.get("t_stat"),
                     "pos_sessions_pct": x.get("pos_sessions_pct"), "n_rows": x.get("n_rows")}
                    for x in dec],
        "spearman_rho": m.get("spearman_rho"),
        "d10_minus_d1_pct": m.get("d10_minus_d1_pct"),
        "paired_d10_d1": m.get("paired_d10_d1"),
        "pool_mean_pct": m.get("pool_mean_pct"), "pool_t": m.get("pool_t"),
        "monotonic": m.get("monotonic"),
        "config": d.get("config"),
        "other_models": {k: {"spearman_rho": v.get("spearman_rho"),
                             "d10_minus_d1_pct": v.get("d10_minus_d1_pct")}
                         for k, v in models.items() if k != lab},
        "generated_at": d.get("generated_at"), "summary_mtime": mt,
    }


def judge_score_deciles(item, parsed) -> tuple:
    """사전 등록 문턱(백로그 success): ρ ≥ +0.5 이고 D10−D1 ≥ +0.3%p 면 '순서 정보 있음'.

    둘 중 하나라도 미달이면 '노이즈'(=랭킹 무효 → 라벨·목적함수 축으로 이동). 판정 불가는 실행실패.
    """
    if parsed.get("error"):
        return "판정불가", f"측정값 없음 — {parsed['error']}", None
    rho = parsed.get("spearman_rho")
    delta = parsed.get("d10_minus_d1_pct")
    p = parsed.get("paired_d10_d1") or {}
    dec = parsed.get("deciles") or []
    d1 = dec[0]["mean_pct"] if dec else None
    d10 = dec[-1]["mean_pct"] if dec else None
    detail = (f"분위 ρ {rho} · D10−D1 {delta}%p (D1 {d1:+.4f} / D10 {d10:+.4f}) · "
              f"세션별 짝Δ {p.get('mean_pct')}%p t {p.get('t_stat')} "
              f"(양 {p.get('pos')}/음 {p.get('neg')}, 앞 {p.get('front_pct')}/뒤 {p.get('back_pct')}) · "
              f"풀평균 {parsed.get('pool_mean_pct')}%p · {parsed.get('n_sessions')}세션/"
              f"{parsed.get('n_rows_after_filter')}행") if isinstance(delta, (int, float)) else "측정값 없음"
    ok = (rho is not None and rho >= 0.5 and delta is not None and delta >= 0.3)
    if ok:
        return "신호", detail + " → 사전문턱(ρ≥0.5·Δ≥0.3%p) 통과: 순서 정보 있음", delta
    return "노이즈", detail + " → 사전문턱(ρ≥0.5·Δ≥0.3%p) 미달: 랭킹 무효", delta


def parse_forward_scorecard(path, mtime_floor) -> dict:
    """배포 경로 전방 성적표(scripts/forward_scorecard.py --out)를 파싱한다.

    스키마: {"generated_at", "predictions_rows", "model_versions", "result": {"h1": {...}, "h5": {...}}}
    각 창 = {n_pairs, n_dates, pooled_auc, daily_auc_mean, daily_auc_list, top10_ret_mean, all_ret_mean}
    + (CG137) money / money_filter — 체결성 필터·수수료·널 기준선 순기대 블록(창별 그대로 통과시킨다).

    ⚠ `per_exp` 를 만들지 않는다 — scoreboard 는 원장 per_exp 전체를 'arm 폴드 평균(AUC)'으로
    읽어 best_robust·무개선 카운터를 만든다(2026-09-29 CG31 사고). 여기 값은 AUC·수익 혼합이라
    키 이름을 `windows` 로 분리해 스코어보드가 AUC 로 오독하지 않게 한다.
    """
    if not path:
        return {"error": "요약 경로 없음(--out 미지정)"}
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
    res = d.get("result") if isinstance(d.get("result"), dict) else {}
    windows = {}
    for k, v in res.items():
        if not isinstance(v, dict):
            continue
        windows[str(k)] = {f: v.get(f) for f in
                           ("n_pairs", "n_dates", "skipped", "pooled_auc", "daily_auc_mean",
                            "daily_auc_list", "base_rate_up", "top10_ret_mean", "all_ret_mean",
                            "money", "money_filter",

                            "dates", "date_versions", "by_version")}
    out = {"summary_mtime": mt, "generated_at": d.get("generated_at"),
           "predictions_rows": d.get("predictions_rows"),
           "model_versions": d.get("model_versions"), "windows": windows}
    if not windows:
        out["error"] = "요약에 result 창(h1/h5)이 없음 — 계측기 출력 형식을 확인하라"
    return out


def parse_calibration_probe(path, mtime_floor) -> dict:
    """확률 보정 계측기(scripts/calibration_probe.py --json-out)를 파싱한다.

    스키마: {n_rows, n_dates, base_rate, best_method, brier_gain, ece_gain, auc_delta,
             raw:{auc,brier,logloss,ece,max_overconf,...}, calibrated:{platt:{...}, isotonic:{...}}}

    ⚠ `per_exp` 를 만들지 않는다 — scoreboard 는 원장의 per_exp 전체를 'arm 폴드 평균(AUC)'으로
    읽어 best_robust·무개선 카운터를 만든다(2026-09-29 CG31 사고). 여기 값은 Brier/ECE 라
    `calibration` 키로 분리해 스코어보드가 AUC 로 오독하지 않게 한다.
    """
    if not path:
        return {"error": "요약 경로 없음(--json-out 미지정)"}
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
    mkeys = ("auc", "brier", "logloss", "ece", "max_overconf", "p50", "p90", "max")
    raw = d.get("raw") or {}
    cal = d.get("calibrated") or {}
    out = {
        "summary_mtime": mt, "generated_at": d.get("generated_at"),
        "n_rows": d.get("n_rows"), "n_dates": d.get("n_dates"),
        "base_rate": d.get("base_rate"), "best_method": d.get("best_method"),
        "brier_gain": d.get("brier_gain"), "ece_gain": d.get("ece_gain"),
        "auc_delta": d.get("auc_delta"),
        "raw": {k: raw.get(k) for k in mkeys},
        "calibration": {m: {k: (v or {}).get(k) for k in mkeys} for m, v in cal.items()},
        "raw_thresholds": raw.get("thresholds"), "raw_topk": raw.get("topk"),
        "cal_thresholds": {m: (v or {}).get("thresholds") for m, v in cal.items()},
        "cal_topk": {m: (v or {}).get("topk") for m, v in cal.items()},
    }
    if out["brier_gain"] is None:
        out["error"] = "요약에 brier_gain 이 없음 — 계측기 출력 형식을 확인하라"
    return out


def judge_forward_scorecard(item, parsed) -> tuple:
    """전방 성적표 판정 — **단일 런 비교가 아니라 사전등록 문턱**으로 판정한다.

    사전등록(항목 필드로 override 가능): forward_horizon(기본 5) 창에서
    ① n_dates ≥ min_dates(기본 10) ② pooled AUC ≥ min_auc(기본 0.52)
    ③ top10 실현수익 평균 ≥ 전체평균(all_ret_mean) — 세 조건 **동시** 충족이면 신호.
    n_dates 미달이면 '표본부족'(판정 보류) — 배포 경로 표본이 아직 쌓이지 않은 상태를
    '노이즈'로 오독해 축을 조기에 닫는 사고를 막는다(CG75 전제: 표본 누적 후 판정).
    """
    if parsed.get("error"):
        return "판정불가", str(parsed["error"]), None
    h = str(item.get("forward_horizon") or 5)
    w = (parsed.get("windows") or {}).get(f"h{h}")
    if not w:
        return "판정불가", f"요약에 h{h} 창이 없음(창={sorted((parsed.get('windows') or {}).keys())})", None

    def _f(x):
        return float(x) if isinstance(x, (int, float)) else None

    nd = int(w.get("n_dates") or 0)
    auc, dmean = _f(w.get("pooled_auc")), _f(w.get("daily_auc_mean"))
    top10, allr = _f(w.get("top10_ret_mean")), _f(w.get("all_ret_mean"))
    min_dates = int(item.get("min_dates") or 10)
    min_auc = float(item.get("min_auc") or 0.52)
    detail = (f"h{h} 전방 pooled AUC {auc if auc is None else round(auc, 4)}"
              f"(날짜별 {dmean if dmean is None else round(dmean, 4)}) · n_dates {nd} · "
              f"n_pairs {w.get('n_pairs')} · top10 실현수익 "
              f"{'n/a' if top10 is None else format(top10, '+.4%')} vs 전체평균 "
              f"{'n/a' if allr is None else format(allr, '+.4%')}")
    if nd < min_dates:
        return "표본부족", detail + f" — n_dates {nd} < {min_dates} → 판정 보류(표본 누적 대기)", None
    ok_auc = auc is not None and auc >= min_auc
    ok_top = top10 is not None and allr is not None and top10 >= allr
    verdict = "신호있음" if (ok_auc and ok_top) else "노이즈"
    delta = round(auc - min_auc, 4) if auc is not None else None
    return verdict, detail + f" (AUC≥{min_auc} {ok_auc} · top10≥전체평균 {ok_top})", delta


def judge_calibration_probe(item, parsed) -> tuple:
    """확률 보정 계측기 판정 — **AUC 판정이 아니다**(단조 보정은 순위를 바꾸지 않는다).

    사전등록(항목 필드로 override 가능): cross-fitted Brier 이득 ≥ min_brier_gain(기본 +0.002)
    **그리고** ECE 이득 > 0 이면 '보정 유효', 아니면 '보정 무효'. delta 는 brier_gain.

    ⚠ 이 판정을 성능(승격) 근거로 쓰지 말라 — 목적은 '절대문턱이 확률로서 의미를 갖는가'이고,
    결과는 소비 정책(절대 vs top-k) 판단의 입력이다(소비자 경로 수정은 승인 대상, CG82).
    """
    if parsed.get("error"):
        return "판정불가", str(parsed["error"]), None
    bg = parsed.get("brier_gain")
    eg = parsed.get("ece_gain")
    if bg is None:
        return "판정불가", "brier_gain 없음", None
    min_bg = float(item.get("min_brier_gain") or 0.002)
    raw, cal = parsed.get("raw") or {}, parsed.get("calibration") or {}
    best = parsed.get("best_method")
    bc = (cal.get(best) or {}) if best else {}
    detail = (f"cross-fitted {best}: Brier {parsed.get('raw', {}).get('brier')}→{bc.get('brier')}"
              f"(이득 {bg:+.4f}) · ECE {raw.get('ece')}→{bc.get('ece')}(이득 {eg:+.4f})"
              f" · 최대과신 {raw.get('max_overconf')}→{bc.get('max_overconf')}"
              f" · AUC 변화 {parsed.get('auc_delta')}(단조면 0) · n={parsed.get('n_rows')}"
              f" {parsed.get('n_dates')}일")
    if bg >= min_bg and eg is not None and eg > 0:
        return "보정 유효", detail + f" → 사전등록 충족(이득 ≥ {min_bg}). 승격 아님 — 소비 정책 입력", float(bg)
    return "보정 무효", detail + f" → 사전등록 미충족(Brier 이득 ≥ {min_bg} 미달 또는 ECE 미개선)", float(bg)


def parse_policy_compare(path, mtime_floor) -> dict:
    """소비 문턱 정책 비교 계측기(scripts/policy_compare_probe.py --json-out)를 파싱한다.

    스키마: {n_rows, n_groups, threshold, ks, primary_scale, k_passed, criterion,
             scales: {"raw": {"k3": {...}, "k5": {...}}, "platt": {...}}}
    각 k 통계 = {n_pairs, mean_delta, std, se, pos, neg, ties, pos_rate, p_value,
                 abs_mean_ret, topk_mean_ret, abs_n, topk_n}.

    ⚠ `per_exp` 를 만들지 않는다 — scoreboard 는 원장의 per_exp 전체를 'arm 폴드 평균(AUC)'으로
    읽어 best_robust·무개선 카운터를 만든다(2026-09-29 CG31 사고). 여기 값은 실현수익 Δ 라
    `policy` 키로 분리해 스코어보드가 AUC 로 오독하지 않게 한다.
    """
    if not path:
        return {"error": "요약 경로 없음(--json-out 미지정)"}
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
    scales = d.get("scales") or {}
    if not scales:
        return {"error": "요약에 scales 가 없음 — 계측기 출력 형식을 확인하라"}
    keys = ("n_pairs", "n_skipped_dates", "mean_delta", "std", "se", "pos", "neg", "ties",
            "pos_rate", "p_value", "abs_mean_ret", "topk_mean_ret", "abs_n", "topk_n")
    out = {
        "summary_mtime": mt, "generated_at": d.get("generated_at"),
        "n_rows": d.get("n_rows"), "n_groups": d.get("n_groups"),
        "threshold": d.get("threshold"), "ks": d.get("ks"),
        "primary_scale": d.get("primary_scale"), "k_passed": d.get("k_passed"),
        "criterion": d.get("criterion"),
        "policy": {sc: {str(k): {kk: (v or {}).get(kk) for kk in keys}
                        for k, v in (st or {}).items()}
                   for sc, st in scales.items()},
    }
    return out


def judge_policy_compare(item, parsed) -> tuple:
    """소비 문턱 정책 비교 판정 — **AUC 판정이 아니다**(실현수익 짝 Δ + 부호검정).

    사전등록(CG84 success, 항목 필드로 override 가능): k ∈ ks(기본 3·5·10) 중
    min_k_pass(기본 2)개 이상에서 ① mean_delta > 0 ② pos_rate ≥ min_pos_rate(기본 0.6)
    ③ p_value < max_p(기본 0.05) 를 **동시에** 만족하면 '정책 교체 근거 있음',
    아니면 '정책 교체 근거 없음(노이즈)' 으로 종결한다.

    판정 스케일은 primary_scale(기본 raw = 현 배포 스케일 — 보정 계층은 아직 미배선, CG82 ②)이다.
    보조 스케일(platt) 값은 detail 에 함께 적어 방향이 스케일 의존인지 드러나게 한다.

    ⚠ 승격 근거가 아니다 — 결과는 소비 정책(절대→분위 top-k) 승인 요청의 입력이다(CG82 ①).
    """
    if parsed.get("error"):
        return "판정불가", str(parsed["error"]), None
    crit = parsed.get("criterion") or {}
    min_pos_rate = float(item.get("min_pos_rate") or crit.get("min_pos_rate") or 0.6)
    max_p = float(item.get("max_p") or crit.get("max_p") or 0.05)
    min_k_pass = int(item.get("min_k_pass") or crit.get("min_k_pass") or 2)
    pol = parsed.get("policy") or {}
    scale = item.get("policy_scale") or parsed.get("primary_scale") or "raw"
    st = pol.get(scale) or {}
    ks = item.get("ks") or parsed.get("ks") or [int(str(k)[1:]) for k in st if str(k).startswith("k")]
    if not st:
        return "판정불가", f"policy[{scale}] 가 비어 있음(스케일={sorted(pol.keys())})", None

    passed, parts = [], []
    for k in ks:
        v = st.get(f"k{k}") or st.get(str(k)) or {}
        md, pr, pv = v.get("mean_delta"), v.get("pos_rate"), v.get("p_value")
        if md is None:
            parts.append(f"k{k}: 미측정")
            continue
        ok = md > 0 and (pr or 0) >= min_pos_rate and pv is not None and pv < max_p
        if ok:
            passed.append(k)
        parts.append(f"k{k}: Δ{md:+.4f}({md:+.2%}) 양(+) {v.get('pos')}/{v.get('n_pairs')}"
                     f"(rate {pr}) p {pv}{' ✓' if ok else ''}")
    detail = (f"[{scale}] " + " · ".join(parts)
              + f" → 사전등록 통과 {len(passed)}/{len(ks)}개"
                f"(필요 {min_k_pass} · Δ>0 · pos_rate≥{min_pos_rate} · p<{max_p})")
    other = [s for s in pol if s != scale]
    for s in other:
        ov = (pol.get(s) or {}).get(f"k{ks[0] if ks else 3}") or {}
        if ov.get("mean_delta") is not None:
            detail += f" | 보조[{s}] k{ks[0]}: Δ{ov['mean_delta']:+.4f} p {ov.get('p_value')}"

    prim = st.get("k5") or st.get(f"k{ks[0]}") if ks else None
    delta = prim.get("mean_delta") if prim else None
    if len(passed) >= min_k_pass:
        return "정책 교체 근거 있음", detail, round(delta, 6) if delta is not None else None
    return "정책 교체 근거 없음", detail, round(delta, 6) if delta is not None else None


def parse_panel_leak_gate(path, mtime_floor) -> dict:
    """패널 누수 지문 사전 게이트(scripts/panel_leak_gate.py --json-out)를 파싱한다.

    스키마: {n_panels, n_clean, n_leaky, clean: [...], leaky: [...], missing: [...],
             panels: {파일명: {verdict, rows, cols, n_stocks, date_min, date_max,
                              leaky_columns, offenders}}}

    ⚠ `per_exp` 를 만들지 않는다 — scoreboard 는 원장 per_exp 전체를 'arm 폴드 평균(AUC)'으로
    읽어 best_robust·무개선 카운터를 만든다(2026-09-29 CG31 사고). 패널별 판정은 `panels` 키다.
    """
    if not path:
        return {"error": "요약 경로 없음(--json-out 미지정)"}
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
    if "panels" not in d:
        return {"error": "요약에 panels 가 없음 — 계측기 출력 형식을 확인하라"}
    return {
        "summary_mtime": mt, "generated_at": d.get("generated_at"),
        "n_panels": d.get("n_panels"), "n_clean": d.get("n_clean"), "n_leaky": d.get("n_leaky"),
        "clean": d.get("clean") or [], "leaky": d.get("leaky") or [],
        "missing": d.get("missing") or [],
        "panels": {k: {kk: (v or {}).get(kk) for kk in
                       ("verdict", "rows", "cols", "n_stocks", "date_min", "date_max",
                        "leaky_columns")}
                   for k, v in (d.get("panels") or {}).items()},
    }


def judge_panel_leak_gate(item, parsed) -> tuple:
    """패널 누수 지문 게이트 판정 — **AUC 판정이 아니다**(스냅샷 청정성 판정).

    사전등록(항목 필드로 override 가능):
      실험에 쓰는 패널(`--panels` 로 지정하거나 `require_clean` 목록) 중 하나라도 LEAKY 면
      '누수 발견' — 하드 판정규칙 ③에 따라 그 패널로 측정한 절대값은 인용 금지, 승격 후보 제외.
      전부 CLEAN 이면 '누수 없음'.

    ⚠ 이미 실행된 실험을 소급 무효화하지 않는다 — 같은 런·같은 패널의 **짝 Δ**(arm vs 대조군)는
    공통 오염이 상쇄되어 귀속에 유효하다. 문제는 ①누수 컬럼이 선별(top-k)에 들어가 슬롯을 먹는 것
    ②수리 전후 절대값을 비교하는 것이다.
    """
    if parsed.get("error"):
        return "판정불가", str(parsed["error"]), None
    leaky = parsed.get("leaky") or []
    clean = parsed.get("clean") or []
    req = item.get("require_clean") or []
    if req:
        bad = [p for p in req if p in leaky]
        ok = not bad
    else:
        bad, ok = leaky, not leaky
    detail = (f"패널 {parsed.get('n_panels')}개 스캔 — 청정 {len(clean)}개 {clean} · "
              f"누수 {len(leaky)}개 {leaky}")
    if req:
        detail += f" · 검사요청 {req} → 위반 {bad}"
    panels = parsed.get("panels") or {}
    leaky_detail = "; ".join(
        f"{p}: {'/'.join((panels.get(p) or {}).get('leaky_columns') or [])}"
        for p in bad[:6])
    if not ok:
        return ("누수 발견",
                detail + f" → 실험 금지(수리 후 재빌드 필요). 누수 컬럼 {leaky_detail}",
                float(len(leaky)))
    return "누수 없음", detail + " → 실험 가능", float(len(leaky))


def parse_topk_precision(path, mtime_floor) -> dict:
    """상위 k 정밀도·실현수익 짝 집계(scripts/topk_precision.py --json-out)를 파싱한다.

    ⚠ `per_exp` 를 만들지 않는다 — scoreboard 는 원장의 per_exp 전체를 'arm 폴드 평균(AUC)'으로
    읽어 best_robust·무개선 카운터를 만든다(2026-09-29 CG31 사고). 여기 값은 정밀도/수익이라
    키 이름을 `kstats` 로 분리해 스코어보드가 AUC 로 오독하지 않게 한다.

    판정에 쓰는 것은 `paired`(k별 Δ정밀도·부호검정 p) + `control` 존재 여부다.
    """
    if not path:
        return {"error": "요약 경로 없음(--json-out 미지정)"}
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
    paired = d.get("paired") or {}
    if not paired:
        return {"error": "paired 없음(집계 실패)", "summary_mtime": mt}
    return {
        "arm": d.get("arm"), "control": d.get("control"),
        "restrict_q": d.get("restrict_q"), "min_pool": d.get("min_pool"),
        "ks": d.get("ks"), "rows": d.get("rows"), "exps": d.get("exps"),
        "skip_stats": d.get("skip_stats") or {},
        "saturated_cells": d.get("saturated_cells") or {},
        "paired": paired,
        "kstats": d.get("per_exp") or {},
        "summary_mtime": mt,
    }


def judge_topk_precision(item, parsed) -> tuple:
    """상위 k 정밀도 짝 판정 — 사전 등록: k=3 **과** k=5 둘 다 Δ정밀도 ≥ +0.05 **이고** 부호검정 p < 0.05.

    왜 두 k 인가: k 하나만 보면 다중비교로 우연한 유의가 나온다(CG21 실측: k=3 Δ+0.0800 p=2.7e-5
    인데 k=5 는 Δ+0.0178 p=0.228 였다). k=10 은 공통 후보집합(≈15행/일)에서 포화하므로 해석 금지
    — 포화 셀(saturated_cells)이 있으면 그 k 는 판정에서 제외하고 detail 에 남긴다.

    ⚠ 등록 문구 vs 구현 조건의 **의도적 갭**(2026-10-04 확인, CG93/CG94): 백로그 `success` 에는
    "Δfwd_ret>0" 이 함께 적혀 있으나 이 판정기는 **정밀도·부호검정만** 본다(CG93·CG94 의 등록 문구가
    그랬고, 그 실행 시점의 판정기도 같은 조건이었다). CG94 실측에서 k=3 Δfwd_ret = −0.0396(음수)라
    엄격 해석이면 미충족이 된다 → 그 verdict 를 뒤집으려면 문턱 변경(리뷰보드 승인)이 필요하다.
    그래서 여기서는 바꾸지 않고, 라벨 꼬리 축의 최종 판정은 **돈 지표**(CG95 ·
    `judge_fillable_topk_expectancy`: 순기대·짝 Δ·t·분할표본)로 넘긴다. 새 항목을 등록할 때는
    `success` 문구와 이 함수의 조건을 **글자 단위로 대조**하라 — 갭이 있으면 판정이 자기신고가 된다.
    """
    if parsed.get("error"):
        return "판정불가", f"요약 없음/미갱신 — {parsed['error']}", None
    paired = parsed.get("paired") or {}
    arm, ctl = parsed.get("arm"), parsed.get("control")
    if not ctl:
        return "판정불가", (f"대조군 없음(control=None) — 짝 비교가 성립하지 않는다(arm={arm}). "
                           f"두 모델을 한 덤프에 넣고 --arm/--control 로 지정하라"), None
    sat = parsed.get("saturated_cells") or {}
    parts, ok_all, first = [], True, None
    for k in ("3", "5"):
        p = paired.get(k)
        if not isinstance(p, dict):
            return "판정불가", f"k={k} 통계 없음 (paired keys={list(paired)})", None
        dm, pv = p.get("prec_delta_mean"), p.get("prec_sign_p")
        dret = p.get("ret_delta_mean")
        if first is None:
            first = dm
        good = (isinstance(dm, (int, float)) and isinstance(pv, (int, float))
                and dm >= 0.05 and pv < 0.05 and not sat.get(k))
        ok_all = ok_all and good
        parts.append(
            f"k={k} Δprec {dm:+.4f} (p {pv:.3g} · 양수 {p.get('prec_delta_pos')}/"
            f"{p.get('n_eff_sign')} 동점제외 · 동점 {p.get('ties')}) · Δfwd_ret "
            f"{dret:+.4f}" if isinstance(dm, (int, float)) and isinstance(pv, (int, float))
            and isinstance(dret, (int, float)) else f"k={k} 통계 결측")
        if sat.get(k):
            parts[-1] += f" · ⚠포화({sat[k]}셀) — 판정 제외"
    detail = (f"arm {arm} vs 대조군 {ctl} · " + " · ".join(parts)
              + f" · 공통 후보집합 restrict_q={parsed.get('restrict_q')}"
              + f" · Δ정밀도 사전문턱 +0.05 · 부호검정 p<0.05")
    if ok_all:
        return "신호있음", detail + " → k=3·5 둘 다 충족(실질성 있음)", first
    return "노이즈", detail + " → 사전등록 미충족", first


def parse_fillable_topk_expectancy(path, mtime_floor) -> dict:
    """돈 지표(체결성·수수료 반영 top-k 순기대) 요약을 파싱한다 — scripts/fillable_topk_expectancy.py --json-out.

    스키마: {exit, horizon, ks, fee_roundtrip_pct, rows, n_sessions_fillable, pool_median_fillable,
             conditions: {fillable|unfiltered: {filtered_out, k: {k: {arm, control, arm_halves,
             control_halves, paired}}}}}

    ⚠ `per_exp` 를 만들지 않는다 — scoreboard 는 원장 per_exp 전체를 'arm 폴드 평균(AUC)' 으로
    읽어 best_robust·무개선 카운터를 만든다(실측 2026-09-29 CG31 사고). k별 통계는 `conditions`
    에 그대로 싣는다.
    """
    if not path:
        return {"error": "요약 경로 없음(--json-out 미지정)"}
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
    conds = d.get("conditions") or {}
    fill = (conds.get("fillable") or {}).get("k") or {}
    if not fill:
        return {"error": "conditions.fillable.k 없음(집계 실패)", "summary_mtime": mt}
    return {
        "metric_name": d.get("metric_name"),
        "exit": d.get("exit"), "horizon": d.get("horizon"), "ks": d.get("ks"),
        "arm_tag": d.get("arm_tag"), "control_tag": d.get("control_tag"),
        "fee_roundtrip_pct": d.get("fee_roundtrip_pct"),



        "stride": d.get("stride", 1),
        "rows": d.get("rows"), "n_sessions_fillable": d.get("n_sessions_fillable"),
        "pool_median_fillable": d.get("pool_median_fillable"),
        "conditions": conds, "summary_mtime": mt,

        "row_tag": d.get("row_tag"), "quintile5": d.get("quintile5"),
    }


def parse_money_anchor_repeat(path, mtime_floor) -> dict:
    """scripts/money_anchor_repeat.py --json-out 요약(앵커별 Δ(top-k − 풀평균)).

    스키마: {config, panel, anchors[], folds, seeds, exit, horizon, ks[], fee_roundtrip_pct,
             per_anchor: {anchor: {n_sessions, pool{mean,t,pos_pct}, k: {k: {arm, delta_vs_pool,
                        halves}}, bottom_k, pool_ex_bottom}}, errors{}, aggregate{}, verdict}
    앵커가 2개 미만이면 '실행실패'로 닫는다(조용한 판정불가 금지 — CG245 실측).
    """
    if not path:
        return {"error": "요약 경로 없음(--json-out 미지정)"}
    if not os.path.exists(path):
        return {"error": "요약 파일 없음"}
    mt = os.path.getmtime(path)
    if mtime_floor and mt <= mtime_floor:
        return {"error": "요약 미갱신(mtime <= 실행 시작 시각) — 옛 결과 오독 방지",
                "summary_mtime": mt, "floor": mtime_floor}
    with open(path, encoding="utf-8") as f:
        try:
            d = json.load(f)
        except json.JSONDecodeError as e:
            return {"error": f"요약 JSON 파싱 실패: {e}", "summary_mtime": mt}
    per = {a: v for a, v in (d.get("per_anchor") or {}).items() if isinstance(v, dict) and v.get("k")}
    if len(per) < 2:
        return {"error": f"성공 앵커 {len(per)}개 < 2 — 앵커 반복성 판정 불가(errors={d.get('errors')})",
                "summary_mtime": mt}
    ks = sorted({k for v in per.values() for k in v["k"]}, key=lambda x: int(x))
    return {
        "metric_name": "money_anchor_repeat", "config": d.get("config"), "panel": d.get("panel"),
        "anchors": list(per), "ks": ks, "folds": d.get("folds"), "seeds": d.get("seeds"),
        "exit": d.get("exit"), "horizon": d.get("horizon"),
        "fee_roundtrip_pct": d.get("fee_roundtrip_pct"),
        "aggregate": d.get("aggregate"), "verdict_text": d.get("verdict"),
        "per_anchor": {a: {"n_sessions": v.get("n_sessions"), "pool": v.get("pool"),
                           "k": {k: {"arm_mean": b.get("arm", {}).get("mean"),
                                     "delta": b.get("delta_vs_pool", {}).get("delta_mean"),
                                     "t": b.get("delta_vs_pool", {}).get("t"),
                                     "halves": b.get("halves")}
                                 for k, b in (v.get("k") or {}).items()}}
                       for a, v in per.items()},
        "errors": d.get("errors"), "summary_mtime": mt, "verdict": "판정대기",
    }


def judge_money_anchor_repeat(item, parsed) -> tuple:
    """앵커 반복성 판정 — 사전 등록(CG244): k=3 **과** k=5 둘 다

      (a) Δ(top-k − 풀평균) > 0 인 앵커 ≥ 2  (b) 앵커평균 Δ ≥ +0.10%p
      (c) 앵커별 t 중앙 ≥ 1.5
    를 만족하면 '신호있음'(= 벡터 베타를 넘는 선택 엔진). 아니면 '노이즈'.
    '노이즈' 의 의미가 중요하다: 단일 앵커에서 양(+)이던 돈 지표는 반복되지 않으며,
    그 수치를 승격 근거로 쓰면 안 된다는 뜻이다.
    """
    if parsed.get("error"):
        return "판정불가", f"요약 없음/미갱신 — {parsed['error']}", None
    agg = parsed.get("aggregate") or {}
    if not agg:
        return "판정불가", "aggregate 없음(집계 실패)", None
    parts, ok_all, first = [], True, None
    for k in (parsed.get("ks") or ["3"]):
        if k not in ("3", "5"):
            continue
        g = agg.get(k) or {}
        pos, n = g.get("positive_anchors"), g.get("n_anchor")
        dm, mt_ = g.get("delta_mean"), g.get("median_t")
        if not all(isinstance(x, (int, float)) for x in (pos, n, dm, mt_)):
            return "판정불가", f"k={k} 집계 결측({g})", None
        good = (pos >= 2 and dm >= 0.1 and mt_ >= 1.5)
        ok_all = ok_all and good
        if first is None:
            first = dm
        by = g.get("delta_by_anchor") or {}
        parts.append(f"k={k} Δ평균 {dm:+.3f}%p · 양(+)앵커 {pos}/{n} · t중앙 {mt_:+.2f} · "
                     f"앵커별 {json.dumps(by, ensure_ascii=False)}" + ("" if good else " ✗"))
    detail = (f"config {parsed.get('config')} vs 풀평균(무작위 k 기대, 수수료 왕복 "
              f"{parsed.get('fee_roundtrip_pct')}%p) · 폴드 {parsed.get('folds')}×시드 "
              f"{parsed.get('seeds')} · 앵커 {len(parsed.get('anchors') or [])}개 · "
              + " · ".join(parts)
              + " · 사전문턱: 양(+)앵커 ≥2 & Δ평균 ≥+0.10%p & t중앙 ≥1.5")
    if ok_all:
        return "신호있음", detail + " → 선택 엣지가 앵커를 넘어 반복된다(승격 아님 — 별도 절차)", first
    return "노이즈", detail + " → 사전등록 미충족(단일 앵커 돈 지표는 승격 근거로 쓰지 않는다)", first


def judge_fillable_topk_expectancy(item, parsed) -> tuple:
    """돈 지표 짝 판정 — 사전 등록(CG95): k=3 **과** k=5 둘 다

      (a) arm(q0.05) 순기대 > 0  (b) 짝 Δ(arm − 대조군) ≥ +0.1%p/세션
      (c) 짝 t ≥ 2              (d) 분할표본 앞/뒤 모두 양(+)
    를 만족하면 '신호있음'. 하나라도 미달이면 '노이즈'(축 종결 근거).

    왜 이 기준인가: (a)(b)(d)는 이 회사의 승격 표준(`champion_promote --require-expectancy`
    = 순기대>0 · 분할표본 both_positive · 챔피언 대비 +0.1%p)과 동일하고, (c)는 표본(세션 80)에서
    +0.1%p 가 잡음과 구분되는지(≈2σ) 보는 장치다. AUC 처럼 폴드 std ±0.03 을 감안한 +0.02 문턱이
    아니라 **돈 단위(%p/세션)** 문턱이라는 점이 다르다 — 이 역할의 최우선 규칙.

    시드 루프 차단(2026-11-24 CG440): 커맨드가 `for s in 0 1 2 3 4` 로 5시드를 돌려 요약 파일도
    5개인데 summary_out 이 `..._s0.json` 이면 판정이 **한 시드**로 내려간다(CG440: s0 Δ+1.588 '노이즈',
    같은 런 5시드 집계 Δ+1.524·4/5 양수·LOO 최소 +1.256·arm 절대 −2.8503·양(+) 0/5 → 이유부터 달랐다).
    단일 시드 판정은 하드룰 위반이므로 값을 쓰지 않고 '판정불가'로 닫아 money_seed_family 집계를 지목한다.
    """
    if parsed.get("error"):
        return "판정불가", f"요약 없음/미갱신 — {parsed['error']}", None

    _cmd = str(item.get("command") or "")
    _tag = str(parsed.get("arm_tag") or "")
    if _cmd.count("for s in") >= 1 and re.search(r"_s\d+$", _tag):
        return "판정불가", (
            f"시드 루프 커맨드인데 요약은 단일 시드만 읽었다(arm_tag={_tag}) — 시드 부호 유지가 판정의 "
            "핵심이므로 한 시드 값으로 신호/노이즈를 쓰지 않는다. metric=money_seed_family 로 집계하라: "
            "python3 scripts/money_seed_family.py --glob '<seed files>' --null <control|pool> --ks 3,5 --out <family.json>"), None

    conds0 = parsed.get("conditions") or {}
    _fl = ((conds0.get("fillable") or {}).get("k") or {})
    _ties = sum(((_fl.get(str(k)) or {}).get("paired") or {}).get("ties") or 0 for k in _fl)
    _nd = sum(((_fl.get(str(k)) or {}).get("paired") or {}).get("n_dates") or 0 for k in _fl)
    if parsed.get("arm_tag") and parsed.get("arm_tag") == parsed.get("control_tag"):
        v, det, first = judge_fillable_topk_vs_pool(item, parsed)
        return v, ("[대조군 태그=arm → arm-vs-arm 비교 무효(짝 ties "
                   f"{_ties}/{_nd}) → 널(세션 풀평균) 비교로 대체] " + det), first
    conds = parsed.get("conditions") or {}
    fill = (conds.get("fillable") or {}).get("k") or {}
    unf = (conds.get("unfiltered") or {}).get("k") or {}




    ks = [k for k in ("3", "5") if k in fill]




    declared = [str(x) for x in (parsed.get("ks") or [])]
    missing = [k for k in ("3", "5") if (k in declared) and (k not in fill)]
    if missing:
        return "판정불가", f"k={missing} 통계 없음(선언 ks={declared} · keys={list(fill)})", None
    if not ks:
        try:
            ks = sorted(fill, key=lambda x: int(x))[:2]
        except (TypeError, ValueError):
            ks = list(fill)[:2]
    parts, ok_all, first = [], True, None
    for k in ks:
        b = fill.get(k)
        if not isinstance(b, dict):
            return "판정불가", f"k={k} 통계 없음 (keys={list(fill)})", None
        arm = b.get("arm") or {}
        ctl = b.get("control") or {}
        pa = b.get("paired") or {}
        hf = b.get("arm_halves") or {}
        net, d, t = arm.get("mean"), pa.get("delta_mean"), pa.get("t")
        st = hf.get("stable")
        good = (isinstance(net, (int, float)) and net > 0
                and isinstance(d, (int, float)) and d >= 0.1
                and isinstance(t, (int, float)) and t >= 2
                and st == "both_positive")
        ok_all = ok_all and good
        if first is None:
            first = d
        fmt = (f"k={k} arm {net:+.3f}%p/세션 · 대조 {ctl.get('mean', float('nan')):+.3f} · "
               f"Δ{d:+.3f}(t {t}) · 양세션 {arm.get('pos_pct')}% · 분할 {st} "
               f"[{hf.get('front', {}).get('mean', float('nan')):+.3f}/"
               f"{hf.get('back', {}).get('mean', float('nan')):+.3f}]"
               if all(isinstance(x, (int, float)) for x in (net, d, t))
               else f"k={k} 통계 결측")
        parts.append(fmt + ("" if good else " ✗"))
    ref = ""



    _bp = ((conds.get("fillable") or {}).get("baseline_pool") or {})
    _pbk = _bp.get("paired_by_k") or {}
    pool_rel = ""
    if _pbk and ks:
        _bb = _pbk.get(str(ks[0])) or next(iter(_pbk.values()))
        if isinstance(_bb, dict) and isinstance(_bb.get("delta_mean"), (int, float)):
            _ah = (fill.get(ks[0]) or {}).get("arm_halves") or {}
            _ph = _bp.get("halves") or {}
            _hv = []
            for _h in ("front", "back"):
                _a = ((_ah.get(_h) or {}).get("mean"))
                _pv = ((_ph.get(_h) or {}).get("mean"))
                if isinstance(_a, (int, float)) and isinstance(_pv, (int, float)):
                    _hv.append(f"{_a - _pv:+.2f}")
            pool_rel = (f" · 널(풀) 대비 k={ks[0]} Δ{_bb['delta_mean']:+.3f}%p(t {_bb.get('t')}) "
                        f"pos/neg {_bb.get('pos')}/{_bb.get('neg')}")
            if len(_hv) == 2:
                pool_rel += f" · 널 대비 절반 [{_hv[0]}/{_hv[1]}]"
    uk = ks[0] if ks[0] in unf else (next(iter(unf)) if unf else None)
    if uk is not None and isinstance(unf.get(uk), dict):
        ua = (unf[uk].get("arm") or {}).get("mean")
        ud = (unf[uk].get("paired") or {}).get("delta_mean")
        ref = (f" · 참고(체결성 필터 OFF): k={uk} arm {ua:+.3f}%p · Δ{ud:+.3f}"
               if isinstance(ua, (int, float)) and isinstance(ud, (int, float)) else "")



    _stride = parsed.get("stride") or 1
    _overlap = ""
    try:
        if int(_stride) < int(parsed.get("horizon") or 1):
            _overlap = (f" · ⚠중첩표본(stride {int(_stride)} < h{int(parsed.get('horizon') or 1)}) "
                        f"— t·Δ 는 겹친 창의 재사용으로 부풀려짐")
    except (TypeError, ValueError):
        _overlap = ""
    detail = (f"arm {parsed.get('arm_tag')} vs 대조군 {parsed.get('control_tag')} · "
              f"exit {parsed.get('exit')} h{parsed.get('horizon')} · stride {_stride} · 수수료 왕복 "
              f"{parsed.get('fee_roundtrip_pct')}%p · 세션 {parsed.get('n_sessions_fillable')} · "
              f"풀 중앙값 {parsed.get('pool_median_fillable')} · " + " · ".join(parts)
              + ref + pool_rel + _overlap
              + " · 사전문턱: 순기대>0 & Δ≥+0.1%p & t≥2 & 분할 both_positive")
    if ok_all:
        return "신호있음", detail + " → 돈으로도 실질성 있음(승격 아님 — 라벨 정의 변경은 리뷰보드 승인)", first
    return "노이즈", detail + " → 사전등록 미충족", first


def judge_fillable_topk_vs_pool(item, parsed) -> tuple:
    """모델 top-k 가 **널 기준선(세션 풀 평균 = 무작위 k 기대)** 을 넘는가 — 돈 축의 널 판정.

    WHY(2026-10-04 CG95): 같은 프로토콜에서 arm(q0.05) 과 대조군(q0.30) **둘 다** +2%p/세션 대로
    양(+)이었다 — arm vs arm 비교만으로는 그 양수가 '모델 엣지'인지 '시장 베타'인지 가릴 수
    없다. 무작위 k 바스켓의 기대값은 정확히 풀 평균이므로 `top-k − 풀평균` = **베타 제거 초과**다.

    사전문턱(승격 표준 `champion_promote --require-expectancy` 와 동일 단위): k=3 **과** k=5 둘 다
      (a) arm 순기대 > 0  (b) 짝 Δ(arm − 풀평균) ≥ +0.1%p/세션
      (c) 짝 t ≥ 2        (d) 분할표본(arm) 앞/뒤 모두 양(+)
    를 만족하면 '신호있음'(모델이 베타를 넘는다 — 승격은 별도 절차). 하나라도 미달이면
    '노이즈' = '돈 축에서 베타 초과 미검출' 로 기록한다(축 종결 근거).
    """
    if parsed.get("error"):
        return "판정불가", f"요약 없음/미갱신 — {parsed['error']}", None
    fill = ((parsed.get("conditions") or {}).get("fillable") or {})
    ks = fill.get("k") or {}
    bpool = fill.get("baseline_pool") or {}
    bp = bpool.get("stat") or {}
    paired_by_k = bpool.get("paired_by_k") or {}
    if not ks or not paired_by_k:
        return "판정불가", "baseline_pool 없음(구 스키마 — 널 기준선 미계산)", None
    parts, ok_all, first = [], True, None




    declared = [str(x) for x in (parsed.get("ks") or [])]
    missing = [k for k in ("3", "5") if (k in declared) and (k not in ks)]
    if missing:
        return "판정불가", f"k={missing} 통계 없음(선언 ks={declared} · keys={list(fill)})", None
    ks_list = [k for k in ("3", "5") if k in ks]
    if not ks_list:
        try:
            ks_list = sorted(ks, key=lambda x: int(x))[:2]
        except (TypeError, ValueError):
            ks_list = list(ks)[:2]





    try:
        n_paired_max = max(int((paired_by_k.get(k) or {}).get("n_dates") or 0) for k in ks_list)
    except (TypeError, ValueError):
        n_paired_max = 0
    if n_paired_max < MIN_PAIRED_SESSIONS:
        return ("판정불가",
                f"표본부족 — 짝 세션 {n_paired_max} < {MIN_PAIRED_SESSIONS} "
                f"(stride {parsed.get('stride')} · 필터 후 풀 중앙값 "
                f"{parsed.get('pool_median_fillable')}종목 · 채점 날짜 {parsed.get('n_sessions_fillable')}) "
                f"→ 노이즈로 닫지 말고 창(--window-mode post_train)·stride 를 키워 재측정하라", None)
    for k in ks_list:
        b = ks.get(k)
        if not isinstance(b, dict):
            return "판정불가", f"k={k} 통계 없음 (keys={list(ks)})", None
        arm = b.get("arm") or {}
        pk = paired_by_k.get(k) or {}
        hf = b.get("arm_halves") or {}
        net, d, t = arm.get("mean"), pk.get("delta_mean"), pk.get("t")
        st = hf.get("stable")
        good = (isinstance(net, (int, float)) and net > 0
                and isinstance(d, (int, float)) and d >= 0.1
                and isinstance(t, (int, float)) and t >= 2
                and st == "both_positive")
        ok_all = ok_all and good
        if first is None:
            first = d
        fmt = (f"k={k} arm {net:+.3f}%p/세션 · 풀평균 {bp.get('mean', float('nan')):+.3f} · "
               f"Δ(arm−풀) {d:+.3f}(t {t}) · 양세션 {arm.get('pos_pct')}% · 분할 {st} "
               f"[{hf.get('front', {}).get('mean', float('nan')):+.3f}/"
               f"{hf.get('back', {}).get('mean', float('nan')):+.3f}]"
               if all(isinstance(x, (int, float)) for x in (net, d, t))
               else f"k={k} 통계 결측")
        parts.append(fmt + ("" if good else " ✗"))



    _bk = fill.get("bottom_k") or {}
    _bparts = []
    for k in sorted(_bk, key=lambda x: int(x) if str(x).isdigit() else 99):
        _bb = _bk.get(k) or {}
        _ba = _bb.get("arm") or {}
        _pv = _bb.get("paired_vs_pool") or {}
        if isinstance(_ba.get("mean"), (int, float)) and isinstance(_pv.get("delta_mean"), (int, float)):
            _bparts.append(f"k={k} 하위 {_ba['mean']:+.3f}%p · Δ(풀대비) {_pv['delta_mean']:+.3f}"
                           f"(t {_pv.get('t')} · pos/neg {_pv.get('pos')}/{_pv.get('neg')})")
    _bot = (" · 하위k: " + " · ".join(_bparts)) if _bparts else ""
    detail = (f"arm {parsed.get('arm_tag')} · 널=세션 풀 평균(무작위 k 기대) · exit "
              f"{parsed.get('exit')} h{parsed.get('horizon')} · 수수료 왕복 "
              f"{parsed.get('fee_roundtrip_pct')}%p · 세션 {parsed.get('n_sessions_fillable')} · "
              + " · ".join(parts)
              + f" · 풀평균 t {bp.get('t')} · 사전문턱: arm>0 & Δ≥+0.1%p & t≥2 & 분할 both_positive"
              + _bot)
    if ok_all:
        return "신호있음", detail + " → 모델이 시장 베타를 넘는 돈 엣지(승격은 별도 절차)", first
    return "노이즈", detail + " → 사전등록 미충족(베타 초과 미검출)", first


def judge_fillable_gate_bands(item, parsed) -> tuple:
    """국면 타이밍 게이트(진입 시점 정보만)의 돈 효용 — CG304/CG316.

    사전등록(CG316): 트레일링 풀 모멘텀 게이트(days, min_pct)로 세션을 ON/OFF 로 갈랐을 때
      ① ON 대역 풀 순기대 > 0 & t ≥ 2 & ON 세션 ≥ 40  ② OFF 대역 풀 순기대 < 0
      ③ Δ(ON − OFF) ≥ +1.0%p/세션
    를 모두 만족하면 '신호있음' = '언제 살까'가 진입 시점 정보만으로 갈린다(트레이더 경로 승인 대상).
    하나라도 미달이면 '노이즈' = 타이밍 축 종결 근거.

    ⚠ 판정 대상은 **풀(무작위 k) 기대**다. top-k(모델 선택)는 CG300·CG314 에서 베타 초과가
    미검출로 축이 닫혔다 — 이 게이트는 '무엇을 살까'가 아니라 '언제 살까'의 레버다.
    """
    if parsed.get("error"):
        return "판정불가", f"요약 없음/미갱신 — {parsed['error']}", None
    g = (((parsed.get("conditions") or {}).get("fillable") or {}).get("gate") or {})
    bands = g.get("bands") or {}
    onp = (bands.get("on") or {}).get("pool") or {}
    offp = (bands.get("off") or {}).get("pool") or {}
    on_mean, off_mean = onp.get("mean"), offp.get("mean")
    n_on = onp.get("n")
    if not isinstance(on_mean, (int, float)):
        return ("판정불가",
                f"gate.bands.on 없음(게이트 미적용 — gate-rule={g.get('rule')} · "
                f"days={g.get('days')} · n_on={g.get('n_on')} · n_scored={g.get('n_scored')})", None)
    d = (on_mean - off_mean) if isinstance(off_mean, (int, float)) else None
    crit = [
        ("① ON 풀>0 & t≥2 & 세션≥40",
         bool(on_mean > 0 and (onp.get("t") or 0) >= 2 and (n_on or 0) >= 40)),
        ("② OFF 풀<0", bool(isinstance(off_mean, (int, float)) and off_mean < 0)),
        ("③ Δ(ON−OFF)≥+1.0%p", bool(isinstance(d, (int, float)) and d >= 1.0)),
    ]
    off_txt = (f"{off_mean:+.3f}%p(t {offp.get('t')})" if isinstance(off_mean, (int, float))
               else "결측")
    detail = (f"게이트 {g.get('rule')} d{g.get('days')} min{g.get('min_pct')}%p · "
              f"ON {n_on}세션 풀 {on_mean:+.3f}%p(t {onp.get('t')}) · "
              f"OFF {offp.get('n')}세션 풀 {off_txt} · "
              + (f"Δ(ON−OFF) {d:+.3f}%p · " if isinstance(d, (int, float)) else "")
              + " · ".join(f"{n} {'✓' if v else '✗'}" for n, v in crit))


    _ab = []
    for _nm in ("on", "off"):
        _kk = (bands.get(_nm) or {}).get("k") or {}
        _key = "3" if "3" in _kk else (next(iter(_kk)) if _kk else None)
        _pk = _kk.get(_key) if _key else None
        if isinstance(_pk, dict) and isinstance((_pk.get("arm") or {}).get("mean"), (int, float)):
            _pv = _pk.get("paired_vs_pool") or {}
            _ah = _pk.get("arm_halves") or {}
            _dm = _pv.get("delta_mean")
            _ab.append(f"{_nm} k={_key} arm {_pk['arm']['mean']:+.2f}%p"
                       + (f" vs 풀 Δ{_dm:+.2f}(t {_pv.get('t')})" if isinstance(_dm, (int, float)) else "")
                       + f" n{_pk['arm'].get('n')}" + f" 분할[{_ah.get('stable')}]")
    if _ab:
        detail += " · [모델 top-k] " + " / ".join(_ab)
    if all(v for _, v in crit):
        return ("신호있음",
                detail + " → 진입 시점 정보만으로 하락국면 회피 가능(트레이더 경로 승인 대상)", d)
    return ("노이즈",
            detail + " → 진입 시점 정보만으로는 국면을 가를 수 없다(타이밍 축 종결 근거)", d)


def judge_fillable_topk_spread(item, parsed) -> tuple:
    """점수가 수익을 **단조로 정렬**하는가 — 상위 k vs 하위 k 짝 스프레드 (CG213).

    WHY(2026-11-13): 모든 돈 지표가 'top-k − 풀평균' 형태였는데, 상위가 하위를 이기지 못하면
    어떤 순위 소비자(top-k·절대문턱·백분위)도 살릴 수 없다. 그 판정에는 **양 끝의 짝 스프레드**와
    **분위 단조성**이 필요하다(한쪽 k 에서만 t 가 넘는 것은 다중비교 운값).

    사전문턱: k=3 **과** k=5 둘 다 (a) spread(top−bottom) ≥ +0.1%p  (b) t ≥ 2
              (c) 분할표본(상위 k) 앞/뒤 both_positive  (d) 5분위 Q5>Q1 세션이 과반(>50%)
    미달이면 '노이즈' = '순위 정보 미검출' 로 기록한다(→ 남은 레버는 데이터 축).
    """
    if parsed.get("error"):
        return "판정불가", f"요약 없음/미갱신 — {parsed['error']}", None
    fill = ((parsed.get("conditions") or {}).get("fillable") or {})
    ks = fill.get("k") or {}
    if not ks:
        return "판정불가", "conditions.fillable.k 없음(구 스키마)", None
    q = parsed.get("quintile5") or {}
    q_ok = isinstance(q.get("q5_gt_q1_pct"), (int, float)) and q["q5_gt_q1_pct"] > 50.0
    parts, ok_all, first = [], True, None
    for k in ("3", "5"):
        b = ks.get(k)
        if not isinstance(b, dict):
            return "판정불가", f"k={k} 통계 없음 (keys={list(ks)})", None
        sp = b.get("spread_top_minus_bottom") or {}
        hf = b.get("top_halves") or {}
        d, t = sp.get("delta_mean"), sp.get("t")
        st = hf.get("stable")
        good = (isinstance(d, (int, float)) and d >= 0.1
                and isinstance(t, (int, float)) and t >= 2
                and st == "both_positive" and q_ok)
        ok_all = ok_all and good
        if first is None:
            first = d
        if all(isinstance(x, (int, float)) for x in (d, t)):
            parts.append(
                f"k={k} top {b['top'].get('mean', float('nan')):+.3f} · "
                f"bottom {b['bottom'].get('mean', float('nan')):+.3f} · "
                f"spread {d:+.3f}(t {t}) · 양/음/동 {sp.get('pos')}/{sp.get('neg')}/"
                f"{sp.get('ties')} · 분할 {st} "
                f"[{hf.get('front', {}).get('mean', float('nan')):+.3f}/"
                f"{hf.get('back', {}).get('mean', float('nan')):+.3f}]"
                + (" " if good else " ✗"))
        else:
            parts.append(f"k={k} 통계 결측 ✗")
    detail = (f"발행 아티팩트 {parsed.get('row_tag') or 'live_all'} · exit {parsed.get('exit')} "
              f"h{parsed.get('horizon')} · 수수료 왕복 {parsed.get('fee_roundtrip_pct')}%p · "
              f"세션 {parsed.get('n_sessions_fillable')} · " + " · ".join(parts)
              + f" · 5분위 Q5>Q1 {q.get('sessions_q5_gt_q1')}/{q.get('n_sessions')} "
                f"({q.get('q5_gt_q1_pct')}%) " + ("" if q_ok else "✗ ")
              + "· 사전문턱: spread≥+0.1%p & t≥2 & 분할 both_positive & Q5>Q1 과반")
    if ok_all:
        return "신호있음", detail + " → 순위가 수익을 정렬(승격은 별도 절차)", first
    return "노이즈", detail + " → 사전등록 미충족(순위 정보 미검출)", first


def parse_hold_horizon_curve(path, mtime_floor=0.0, null_path=None) -> dict:
    """h 지평 곡선 계측(CG220) — 호라이즌×k 비중첩 순기대 + (선택) 무작위 널 대조.

    스키마: {"metric_name","panel","tag","fee_roundtrip_pct",
             "horizons": {"<h>": {"<k>": {"overlap": {..}, "nonoverlap": {..},
                                        "fold_means_nonoverlap": {..}, "fold_signs_positive": "5/5"}}}}

    판정은 **비중첩(nonoverlap)** 만 쓴다 — 중첩 구간은 같은 세션을 재사용해 t 가 부풀려진다
    (CG219 실측: h5 k=5 중첩 n=240 vs 비중첩 n=48). 중첩 값은 참고로만 남긴다.
    `null_out`(무작위 치환 널)을 주면 관측−널 차이를 함께 남긴다 — 시장 베타·드리프트를 걷어낸
    '같은 세션·같은 수수료' 기준선이다.
    """
    if not path:
        return {"error": "요약 경로 없음(--out 미지정)"}
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
    hor = d.get("horizons") or {}
    if not hor:
        return {"error": "horizons 없음(계측 실패)", "summary_mtime": mt}
    rows = []
    for hk, kv in hor.items():
        if not isinstance(kv, dict):
            continue
        for kk, cell in kv.items():
            if not isinstance(cell, dict):
                continue
            try:
                h, k = int(hk), int(kk)
            except (TypeError, ValueError):
                continue
            fm = cell.get("fold_means_nonoverlap") or {}
            signs = str(cell.get("fold_signs_positive") or "")
            try:
                npos, nf = (int(x) for x in signs.split("/"))
            except (ValueError, TypeError):
                npos = nf = None
            rows.append({
                "h": h, "k": k,
                "overlap": cell.get("overlap"),
                "nonoverlap": cell.get("nonoverlap"),
                "fold_means_nonoverlap": fm,
                "folds_positive": npos, "folds_total": nf,
                "fold_win_rate": round(npos / nf, 3) if npos is not None and nf else None,
            })
    if not rows:
        return {"error": "horizons 셀 없음(계측 실패)", "summary_mtime": mt}
    key = next((r for r in rows if r["h"] == 5 and r["k"] == 5), None)
    null = None
    if null_path and os.path.exists(null_path):
        try:
            with open(null_path, encoding="utf-8") as f:
                nd = json.load(f)
            tag = d.get("tag")
            tags = nd.get("tags") or {}
            nt = tags.get(tag) or (next(iter(tags.values())) if len(tags) == 1 else None)
            if isinstance(nt, dict):
                null = {"metric_name": nd.get("metric_name"), "draws": nd.get("draws"),
                        "by_k": nt}
        except (json.JSONDecodeError, OSError):
            null = None
    return {"metric_name": "hold_horizon_curve", "tag": d.get("tag"),
            "panel": d.get("panel"), "fee_roundtrip_pct": d.get("fee_roundtrip_pct"),
            "rows": sorted(rows, key=lambda r: (r["h"], r["k"])),
            "key_h5k5": key, "null": null,
            "summary_mtime": mt}


def judge_hold_horizon_curve(item, parsed) -> tuple:
    """h5 축 검증 판정 — 돈 기준(비중첩 Δ≥+0.5%p · t≥2 · 폴드 부호 6/8 이상).

    AUC 로 판정하지 않는다(같은 런의 AUC Δ 는 +0.003 수준 = 노이즈). 사전 등록 성공 조건은
    백로그 항목의 `success` 문구이고, 여기서는 그 문턱을 코드로 옮겨 기계 판정한다.
    """
    if parsed.get("error"):
        return "판정불가", str(parsed["error"]), None
    key = parsed.get("key_h5k5") or {}
    no = (key.get("nonoverlap") or {})
    mean = no.get("mean")
    t = no.get("t")
    n = no.get("n")
    wr = key.get("fold_win_rate")
    if not isinstance(mean, (int, float)) or not isinstance(t, (int, float)):
        return "판정불가", "h5 k=5 비중첩 통계 없음", None
    parts = []
    for r in parsed.get("rows") or []:
        ov, nv = r.get("overlap") or {}, r.get("nonoverlap") or {}
        parts.append(f"h{r['h']} k{r['k']} 중첩 Δ{ov.get('mean')}(t {ov.get('t')}, n{ov.get('n')})"
                     f" · 비중첩 Δ{nv.get('mean')}(t {nv.get('t')}, n{nv.get('n')})"
                     f" · 폴드+ {r.get('folds_positive')}/{r.get('folds_total')}")
    nl = parsed.get("null") or {}
    nb = (nl.get("by_k") or {}).get("5")
    nl_txt = ""
    if isinstance(nb, dict):
        nl_txt = (f" · 무작위 널 Δ(obs−null) {nb.get('obs_minus_null')}"
                  f" (obs {nb.get('obs_mean')} vs null {nb.get('null_mean')},"
                  f" t {nb.get('t_vs_uniform_half')}, draws {nl.get('draws')})")
    cond_mean = mean >= 0.5
    cond_t = t >= 2.0
    cond_fold = (wr is not None and wr >= 0.6)
    ok = cond_mean and cond_t and cond_fold
    verdict = "h5 축 검증통과 — 승격 검토(돈 기준)" if ok else "노이즈"
    detail = (" · ".join(parts) + nl_txt +
              f" · 사전문턱(비중첩 h5k5 Δ≥+0.5%p & t≥2 & 폴드+ ≥6/8): "
              f"Δ{'O' if cond_mean else 'X'} t{'O' if cond_t else 'X'} 폴드{'O' if cond_fold else 'X'}"
              f" → {'통과' if ok else '미충족'}")
    return verdict, detail, round(float(mean), 4)


def parse_vol_beta_decomp(path, mtime_floor=0.0) -> dict:
    """변동성·베타 교란 분해(CG221) — 원 Δ vs 변동성 매칭 널 vs 위험조정 Δ.

    스키마: {"metric_name":"vol_beta_decomp","tag","k","horizon","fee_roundtrip_pct","n_sessions",
             "raw_delta":{n,mean,sd,t,pos_pct}, "vol_matched_delta":{..},
             "risk_adjusted_delta":{..}, "top_vol_mean", "pool_vol_mean"}

    판정 규칙(사전 등록, CG221 success): 매칭 Δ 가 원 Δ 의 1/2 이하로 붕괴하면 이득은
    '변동성 베팅', 70% 이상 유지 + t≥2 면 '선택 실력'. 즉 비율(ratio)이 판정 축이다.
    """
    if not path:
        return {"error": "요약 경로 없음(--out 미지정)"}
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
    raw = d.get("raw_delta") or {}
    mat = d.get("vol_matched_delta") or {}
    adj = d.get("risk_adjusted_delta") or {}
    rm, mm = raw.get("mean"), mat.get("mean")
    ratio = (mm / rm) if isinstance(rm, (int, float)) and isinstance(mm, (int, float)) and rm else None
    return {"metric_name": "vol_beta_decomp", "tag": d.get("tag"), "k": d.get("k"),
            "horizon": d.get("horizon"), "fee_roundtrip_pct": d.get("fee_roundtrip_pct"),
            "n_sessions": d.get("n_sessions"), "raw": raw, "matched": mat, "risk_adj": adj,
            "matched_over_raw": round(ratio, 3) if ratio is not None else None,
            "top_vol_mean": d.get("top_vol_mean"), "pool_vol_mean": d.get("pool_vol_mean"),
            "summary_mtime": mt}


def judge_vol_beta_decomp(item, parsed) -> tuple:
    """CG221 판정 — 사전 등록: 매칭/원 Δ 비율 ≤0.5 = 변동성 노출(축 종결), ≥0.7 & t≥2 = 선택 실력."""
    if parsed.get("error"):
        return "판정불가", str(parsed["error"]), None
    raw, mat = parsed.get("raw") or {}, parsed.get("matched") or {}
    adj = parsed.get("risk_adj") or {}
    ratio = parsed.get("matched_over_raw")
    rm, rt = raw.get("mean"), raw.get("t")
    mm, mt_ = mat.get("mean"), mat.get("t")
    if not isinstance(rm, (int, float)) or not isinstance(mm, (int, float)):
        return "판정불가", "raw/vol-matched Δ 없음", None
    detail = (f"tag {parsed.get('tag')} h{parsed.get('horizon')} k{parsed.get('k')} · 세션 "
              f"{parsed.get('n_sessions')} · 원 Δ{rm:+.3f}%p(t {rt}) · 변동성매칭 Δ{mm:+.3f}"
              f"(t {mt_}, n{(mat.get('n'))}) · 비율 {ratio} · 위험조정 Δ"
              f"{(adj.get('mean')):+.4f}(t {adj.get('t')}) · top vol {parsed.get('top_vol_mean')}"
              f" vs 풀 {parsed.get('pool_vol_mean')}")
    if ratio is not None and ratio <= 0.5:
        return ("변동성 노출 — 축 종결", detail + " · 사전문턱: 매칭 Δ ≤ 원 Δ의 1/2 → 선택 실력 아님", round(mm, 4))
    if ratio is not None and ratio >= 0.7 and isinstance(mt_, (int, float)) and mt_ >= 2:
        return ("신호있음 — 변동성 아님(선택 실력)", detail +
                " · 사전문턱: 매칭 Δ ≥ 원 Δ의 70% & t≥2 → 변동성 교란 기각", round(mm, 4))
    return "노이즈", detail + " · 사전문턱(≤1/2 붕괴 or ≥70% 유지+t≥2) 미충족", round(mm, 4)


def parse_h5_time_profile(path, mtime_floor=0.0) -> dict:
    """h5 초과수익의 시간 분해(CG223) — 지금 배포하면 돈이 되는가.

    스키마: {"metric_name":"h5_time_profile","tag","k","horizon","n_sessions","window",
             "overall":{n,mean,sd,t,pos_pct}, "halves":{"front":{..},"back":{..}},
             "quarters":{...}, "recent":{sessions,from,mean,t,..}, "nonoverlap":{..}}
    """
    if not path:
        return {"error": "요약 경로 없음(--out 미지정)"}
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
    if not isinstance(d.get("overall"), dict):
        return {"error": "overall 통계 없음(계측 실패)", "summary_mtime": mt}
    d["summary_mtime"] = mt
    return d


def judge_h5_time_profile(item, parsed) -> tuple:
    """CG223 판정 — 전체 t·비중첩 Δ·앞뒤 절반·최근 구간이 모두 양(+)이면 '시간 강건'.

    왜 이 조합인가: 단일 구간(최근 60세션)은 t 1.7 수준이라 그 자체로는 판정력이 없다
    (MDE 실측 CG214: 11세션 표본 MDE ±1.276%p). 전체 240세션의 t·비중첩 표본·절반 부호를
    주 기준으로 두고, 최근 구간은 **부호 붕괴 감지**에만 쓴다.
    """
    if parsed.get("error"):
        return "판정불가", str(parsed["error"]), None
    ov, hl, rc = parsed.get("overall") or {}, parsed.get("halves") or {}, parsed.get("recent") or {}
    nov = parsed.get("nonoverlap") or {}
    m, t = ov.get("mean"), ov.get("t")
    f, b = (hl.get("front") or {}).get("mean"), (hl.get("back") or {}).get("mean")
    rcm, rct = rc.get("mean"), rc.get("t")
    nm, nt = nov.get("mean"), nov.get("t")
    if not all(isinstance(x, (int, float)) for x in (m, t, nm)):
        return "판정불가", "overall/nonoverlap 통계 결측", None
    detail = (f"tag {parsed.get('tag')} k{parsed.get('k')} h{parsed.get('horizon')} · 체결성 필터 ON · "
              f"세션 {parsed.get('n_sessions')} ({(parsed.get('window') or {}).get('first')}~"
              f"{(parsed.get('window') or {}).get('last')}) · 전체 Δ{m:+.3f}%p/5세션(t {t}) · "
              f"앞/뒤 {f:+.3f}/{b:+.3f} · 비중첩(n{nov.get('n')}) Δ{nm:+.3f}(t {nt}) · "
              f"최근{rc.get('sessions')}세션 Δ{rcm:+.3f}(t {rct})")
    robust = (t >= 3 and nm > 0 and isinstance(f, (int, float)) and f > 0
              and isinstance(b, (int, float)) and b > 0
              and isinstance(rcm, (int, float)) and rcm > 0)
    if robust:
        return ("시간 강건 — 운용경로(청산규칙) 검토 근거", detail +
                " · 사전문턱: 전체 t≥3 & 비중첩 Δ>0 & 앞/뒤 둘 다 + & 최근 Δ>0 → 충족"
                " (최근 60세션 t는 1.7 수준 = 부호 감지용일 뿐 판정력 없음)", round(m, 4))
    if isinstance(rcm, (int, float)) and rcm <= 0:
        return ("시간 감쇠 — 배포 근거 아님", detail + " · 최근 구간 Δ ≤ 0 → 과거 이득이 현재 없다", round(m, 4))
    return "노이즈", detail + " · 사전문턱 미충족", round(m, 4)


def parse_capital_path_compare(path, mtime_floor=0.0) -> dict:
    """운용 경로 비교(CG224) — 자본-세션 기대와 낙폭으로 h1 vs h5 경로를 판정한다.

    스키마: {"metric_name":"capital_path_compare","tag","k","h5",
             "path_a_h1":{sessions,mean,t,capital_session,mdd_pct},
             "path_b_h5":{...}, "nonoverlap_compare":{n_cohorts,a_mdd_pct,b_mdd_pct,...}}
    """
    if not path:
        return {"error": "요약 경로 없음(--out 미지정)"}
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
    if not isinstance(d.get("path_a_h1"), dict) or not isinstance(d.get("path_b_h5"), dict):
        return {"error": "path_a/path_b 통계 없음(계측 실패)", "summary_mtime": mt}
    d["summary_mtime"] = mt
    return d


def judge_capital_path_compare(item, parsed) -> tuple:
    """CG224 판정 — 자본효율 이득(+0.05%p/자본세션 이상)과 낙폭(비중첩 MDD ≤1.5배)을 함께 본다.

    왜 낙폭을 같이 보나: h5 는 5세션 보유로 자본 회전이 느려 세션당 Δ 를 그대로 비교하면 5배
    과대평가된다(자본-세션 단위로 정규화해야 공정). 그리고 수익이 커도 낙폭이 3~4배가 되면
    실계좌 한도(총 100%)·손실 한도 아래에서 운용 불가다.
    """
    if parsed.get("error"):
        return "판정불가", str(parsed["error"]), None
    A, B = parsed.get("path_a_h1") or {}, parsed.get("path_b_h5") or {}
    nov = parsed.get("nonoverlap_compare") or {}
    ac, bc = A.get("capital_session"), B.get("capital_session")
    am, bm = A.get("mean"), B.get("mean")
    if not all(isinstance(x, (int, float)) for x in (ac, bc, am, bm)):
        return "판정불가", "capital_session 통계 결측", None
    a_mdd = nov.get("a_mdd_pct", A.get("mdd_pct"))
    b_mdd = nov.get("b_mdd_pct", B.get("mdd_pct"))
    ratio = (abs(b_mdd) / abs(a_mdd)) if isinstance(a_mdd, (int, float)) and a_mdd else None
    detail = (f"tag {parsed.get('tag')} k{parsed.get('k')} · 체결성 필터 ON · "
              f"A(h1) {A.get('sessions')}세션 {am:+.4f}%p/세션·자본세션 {ac:+.4f}·"
              f"MDD {a_mdd}% · B(h5) {B.get('sessions')}코호트 {bm:+.4f}%p/코호트·자본세션 "
              f"{bc:+.4f}·MDD {b_mdd}% · 비중첩 코호트 {nov.get('n_cohorts')} · "
              f"낙폭 배수 {ratio:.2f}x" if ratio is not None else
              f"tag {parsed.get('tag')} A {am:+.4f}%p B {bm:+.4f}%p")
    eff = bc >= ac + 0.05
    noise_arms = [str(x) for x in (parsed.get("noise_arms") or [])]
    sel_k = str(parsed.get("k"))
    if sel_k in noise_arms:
        return ("노이즈 — 문턱 통과했으나 짝 Δ t<2", detail +
                " · 자본세션 점추정은 문턱을 넘지만 같은 세션 짝 Δ 의 t 가 2 미만이면 "
                "표본 잡음이다(점추정 승격 금지)", bc)
    if "pass" in parsed and not parsed.get("pass"):
        return ("A 유지 — 사전문턱 미충족", detail +
                " · 스크립트가 계산한 통과 변형 없음(pass=[])", bc)
    if eff and ratio is not None and ratio <= 1.5:
        return ("경로 변경 검토 근거", detail +
                " · 사전문턱: 자본세션 +0.05%p 이상 & 낙폭 1.5배 이내 → 충족(트레이더 경로는 ②승인)", bc)
    if eff:
        return ("A 유지 — 낙폭 악화", detail +
                f" · 자본효율은 우위지만 낙폭이 {ratio:.1f}x 로 사전문턱(1.5x) 초과 → "
                "실계좌 한도에서 운용 불가" if ratio is not None else
                " · 낙폭 통계 없음", bc)
    return "A 유지 — 자본효율 우위 없음", detail + " · 사전문턱(+0.05%p/자본세션) 미충족", bc


def parse_h5_regime_exit(path, mtime_floor=0.0) -> dict:
    """시장 레짐 조기청산(CG228, scripts/_cg228_regime.py --out) 요약을 파싱한다.

    스키마: {"metric_name":"h5_regime_exit","tag","k","h5","path_a_h1","path_b_h5",
             "nonoverlap_compare","variants":{"none"|"reg_1p5"|...},"diagnostics","pass"}
    판정에 쓰는 값은 `pass`(사전문턱 통과 변형 목록)와 nonoverlap_compare.mdd_ratio 뿐이다.
    """
    if not path:
        return {"error": "요약 경로 없음(--out 미지정)"}
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
    if not isinstance(d.get("path_a_h1"), dict) or not isinstance(d.get("path_b_h5"), dict):
        return {"error": "path_a/path_b 통계 없음(계측 실패)", "summary_mtime": mt}
    if not isinstance(d.get("variants"), dict):
        return {"error": "variants 없음(레짐 변형 미계측)", "summary_mtime": mt}
    d["summary_mtime"] = mt
    return d


def judge_h5_regime_exit(item, parsed) -> tuple:
    """CG228 판정 — 레짐 청산으로 자본효율을 지키며 낙폭 배수를 1.5x 이내로 낮췄는가.

    왜 사전문턱이 '자본세션 ≥ A+0.05%p & 비중첩 MDD ≤1.5x' 인가: h5 는 자본 회전이 1/5 이라
    세션당 Δ 를 그대로 비교하면 5배 과대평가된다. 그리고 수익이 커도 낙폭이 3~4배면 실계좌 한도
    (총 100%·일 손실 3%) 아래에서 운용 불가다. 진단(시장하락 코호트 vs 상승 코호트 수익)은
    판정에 쓰지 않고, '레짐 규칙이 어느 방향으로 틀렸는지'를 다음 가설에 남기는 용도다.
    """
    if parsed.get("error"):
        return "판정불가", str(parsed["error"]), None
    A = parsed.get("path_a_h1") or {}
    B = parsed.get("path_b_h5") or {}
    nov = parsed.get("nonoverlap_compare") or {}
    diag = parsed.get("diagnostics") or {}
    sel = parsed.get("selected_variant")
    ratio = nov.get("mdd_ratio")
    ac, bc = A.get("capital_session"), B.get("capital_session")
    if not all(isinstance(x, (int, float)) for x in (ac, bc, ratio)):
        return "판정불가", "capital_session/mdd_ratio 결측", None
    a_mdd = nov.get("a_mdd_pct")

    def _vr(v):
        m = (v.get("nonoverlap") or {}).get("mdd_pct")
        if not isinstance(m, (int, float)):
            return "?"
        return f"{abs(m) / abs(a_mdd):.2f}x" if isinstance(a_mdd, (int, float)) and a_mdd else f"{abs(m):.2f}%"

    var_txt = " / ".join(
        f"{k} cap {v.get('capital_session'):+.4f}·{_vr(v)}"
        for k, v in (parsed.get("variants") or {}).items()
        if isinstance(v, dict) and isinstance(v.get("capital_session"), (int, float)))
    detail = (f"tag {parsed.get('tag')} k{parsed.get('k')} h{parsed.get('h5')} · 체결성 필터 ON · "
              f"A(h1) {A.get('sessions')}세션 자본세션 {ac:+.4f}·MDD {nov.get('a_mdd_pct')}% · "
              f"최선 레짐변형 {sel} 자본세션 {bc:+.4f}·비중첩 MDD {nov.get('b_mdd_pct')}%·"
              f"낙폭 배수 {ratio:.2f}x · 변형별 [{var_txt}] · "
              f"진단: 시장하락 코호트 {diag.get('cohorts_market_down')}개 "
              f"{diag.get('mean_pct_market_down')}%p vs 상승 {diag.get('cohorts_market_up')}개 "
              f"{diag.get('mean_pct_market_up')}%p")
    if parsed.get("pass"):
        return ("경로 변경 검토 근거", detail +
                " · 사전문턱: 자본세션 +0.05%p 이상 & 낙폭 1.5배 이내 → 충족(트레이더 경로는 ②승인)", bc)
    if ratio <= 1.5:
        return "A 유지 — 자본효율 우위 없음", detail + " · 낙폭은 통과하나 자본세션 문턱 미충족", bc
    return ("A 유지 — 낙폭 악화(레짐 규칙도 미달)", detail +
            f" · 레짐 청산으로 낙폭 배수를 1.5x 이내로 못 내림({ratio:.2f}x) → h5 축 종결 근거", bc)


def parse_panel_feature_screen(path, mtime_floor=0.0) -> dict:
    """패널 피처 스크린(scripts/panel_feature_screen.py --out) 요약 파싱(CG233)."""
    if not path:
        return {"error": "요약 경로 없음(--out 미지정)"}
    if not os.path.exists(path):
        return {"error": "요약 파일 없음"}
    mt = os.path.getmtime(path)
    if mtime_floor and mt <= mtime_floor:
        return {"error": "요약 미갱신(mtime <= 실행 시작 시각)", "summary_mtime": mt, "floor": mtime_floor}
    with open(path, encoding="utf-8") as f:
        try:
            d = json.load(f)
        except json.JSONDecodeError as e:
            return {"error": f"요약 JSON 파싱 실패: {e}", "summary_mtime": mt}
    if not isinstance(d.get("rows"), list) or not d["rows"]:
        return {"error": "rows 없음(스크린 실패)", "summary_mtime": mt}
    d["summary_mtime"] = mt
    return d


def judge_panel_feature_screen(item, parsed) -> tuple:
    """CG233 판정 — 누수 의심 0 인지, 시간가변 상위 후보(IC t≥2)가 있는지.

    판정 기준은 '학습 가능한가'가 아니라 '데이터 축 후보가 있는가'다. 누수 의심이 1개라도
    나오면 스크린 자체를 반려하고(리서처), 아니면 후보 목록을 다음 실험 입력으로 쓴다.
    """
    if parsed.get("error"):
        return "판정불가", str(parsed["error"]), None
    rows, leak = parsed.get("rows") or [], parsed.get("leak") or []
    const, mkt, dead = parsed.get("const") or [], parsed.get("market") or [], parsed.get("dead") or []
    n = len(rows)
    cand = [r for r in rows
            if not r.get("stock_constant") and not r.get("market_level")
            and isinstance(r.get("ic_t"), (int, float)) and abs(r["ic_t"]) >= 2.0]
    cand.sort(key=lambda r: -abs(r.get("ic_t") or 0))
    txt = " · ".join(f"{r['feature']} IC {r.get('ic_mean'):+.3f}(t {r.get('ic_t')}, fill {r.get('fill')})"
                     for r in cand[:4])
    detail = (f"패널 {os.path.basename(str(parsed.get('panel')))} {n}피처 · "
              f"누수 의심 {len(leak)}{leak[:3]} · 종목상수 {len(const)} · 시장레벨 {len(mkt)} · "
              f"무정보 {len(dead)}/{n} · IC |t|≥2 시간가변 후보 {len(cand)}개: {txt or '없음'}")
    if leak:
        return "반려 — 누수 의심 피처", detail + " · |AUC−0.5|>0.25 는 리서처 반려 대상", None
    if cand:
        return "스크린 완료 — 데이터 축 후보 있음", detail, None
    return "스크린 완료 — 후보 없음", detail + " · IC t≥2 시간가변 피처 없음", None


def parse_db_feature_screen(path, mtime_floor=0.0, item=None) -> dict:
    """DB 원천 컬럼 스크린(scripts/_db_feature_screen.py) 요약 파싱(CG270).

    왜 전용 파서인가(실측 2026-11-14 07:00 CG270): 항목이 metric='screen' 으로 등록됐는데
    parse_by_metric 에 분기가 없어 rc=0 완주(0.3분)가 원장에 '판정불가(parser 없음)'로 남았다
    → 스크린이 실제로 만든 후보 목록(44피처 · 32채점 · 최고 |AUC−0.5|=0.0404)이 통째로 사라지고
    '데이터 축 종결'이라는 결론도 근거 없이 남는다. 스키마는 {"panel":…, "features":{이름:{…}}} 이고
    기존 panel_feature_screen 파서는 rows[] 스키마를 기대해 재사용할 수 없다.

    판정 입력은 **단일변량 |AUC−0.5|** 다(CG268 감도: |AUC−0.5|≥0.065 면 모델 +0.02 도달).
    커버리지·종목상수·시장레벨 조건을 통과한 '살아 있는' 후보만 문턱에 세운다 —
    그 조건 없이 세면 short_selling_ratio(커버리지 1.8%·종목상수 0.94)가 최고 후보로 올라온다.
    ⚠ 스크린은 판정 arm 이 아니다 — per_exp 를 만들지 않는다(스코어보드 오독 방지).
    """
    if not path:
        return {"error": "요약 경로 없음(리다이렉트·--out 미지정)"}
    if not os.path.exists(path):
        return {"error": "요약 파일 없음"}
    mt = os.path.getmtime(path)
    if mtime_floor and mt <= mtime_floor:
        return {"error": "요약 미갱신(mtime <= 실행 시작 시각)", "summary_mtime": mt, "floor": mtime_floor}
    with open(path, encoding="utf-8") as f:
        try:
            d = json.load(f)
        except json.JSONDecodeError as e:
            return {"error": f"요약 JSON 파싱 실패: {e}", "summary_mtime": mt}
    feats = d.get("features") or {}
    if not isinstance(feats, dict) or not feats:
        return {"error": "features 없음(스크린 실패)", "summary_mtime": mt}
    it = item or {}
    thr = float(it.get("screen_threshold") or 0.065)
    cov_min = float(it.get("screen_cov_min") or 0.05)
    rows = []
    for name, v in feats.items():
        if not isinstance(v, dict):
            continue
        auc = v.get("single_feature_auc")
        rows.append({
            "feature": name,
            "auc": auc,
            "abs_delta": abs(auc - 0.5) if isinstance(auc, (int, float)) else None,
            "coverage": v.get("coverage"),
            "stock_const": v.get("stock_const_ratio"),
            "market_level": v.get("market_level_rate"),
            "ic": v.get("xs_ic_mean"),
            "ic_t": v.get("xs_ic_t"),
            "n_dates": v.get("n_ic_dates"),
        })
    live = [r for r in rows
            if r["abs_delta"] is not None
            and (r["coverage"] or 0) >= cov_min
            and (r["stock_const"] or 0) < 0.5
            and (r["market_level"] or 0) < 0.9]
    live.sort(key=lambda r: -r["abs_delta"])
    dead = [r["feature"] for r in rows if (r["coverage"] or 0) < cov_min]
    d["rows"] = rows
    d["live"] = live
    d["dead_coverage"] = dead
    d["threshold"] = thr
    d["cov_min"] = cov_min
    d["summary_mtime"] = mt
    return d


def judge_db_feature_screen(item, parsed) -> tuple:
    """CG270 판정 — 문턱(|AUC−0.5|≥thr)을 넘는 **살아 있는** 후보가 있는가.

    후보 0개는 실패가 아니라 '모델 축 종결의 근거'다(항목의 success ③). 그래서 판정 이름을
    '노이즈'가 아니라 '후보 없음'으로 쓴다 — 스크린은 arm 실험이 아니므로 Δ 도 없다.
    """
    if parsed.get("error"):
        return "판정불가", str(parsed["error"]), None
    rows, live = parsed.get("rows") or [], parsed.get("live") or []
    thr = parsed.get("threshold") or 0.065
    scored = [r for r in rows if r["abs_delta"] is not None]
    if not scored:
        return "판정불가", "채점된 피처 0개(전부 결측/상수)", None
    cand = [r for r in live if r["abs_delta"] >= thr]
    top = live[:3] or sorted(scored, key=lambda r: -r["abs_delta"])[:3]
    txt = " · ".join(
        f"{r['feature']} {r['auc']:.4f}(|Δ|{r['abs_delta']:.4f} cov {r['coverage']:.3f}"
        f"{' · 종목상수' if (r['stock_const'] or 0) >= 0.5 else ''})" for r in top)
    block = [r for r in scored if r["abs_delta"] >= thr and r not in cand]
    pn = parsed.get("panel")
    if isinstance(pn, dict):
        pn = pn.get("file") or pn.get("name") or "?"
    detail = (f"패널 {os.path.basename(str(pn or '?'))} · 피처 {len(rows)}개 중 {len(scored)}채점"
              f"(무정보/커버리지<{parsed.get('cov_min')} {len(parsed.get('dead_coverage') or [])}) · "
              f"문턱 |AUC−0.5|≥{thr} · 살아있는 상위: {txt}")
    if cand:
        return ("데이터 축 후보 있음 — 다음 실험 입력", detail + f" · 문턱 통과 {len(cand)}개: "
                + ", ".join(r["feature"] for r in cand), max(r["abs_delta"] for r in cand))
    if block:
        detail += " · 문턱은 넘었으나 커버리지/종목상수 조건에서 탈락: " + ", ".join(r["feature"] for r in block)



    return (f"후보 없음 — 데이터 축 종결({len(rows)}컬럼 중 {len(scored)}채점)", detail, None)


def parse_insample_gap(path, mtime_floor=0.0) -> dict:
    """학습셋(in-sample) vs OOS AUC 갭 리포트(scripts/_insample_gap_report.py --out) 파싱(CG275).

    왜 전용 metric 인가(2026-11-14): 이 리포트의 판정 입력은 폴드 AUC 평균이 아니라 **갭**
    (train − OOS)이고 스키마도 `arms{exp:{gap, gap_by_fold, train_mean, oos_mean}}` 로 다르다.
    wf_sweep 파서로 읽히면 폴드 AUC 만 남고 갭이 사라져 항목의 유일한 산출물이 원장에서 없어진다
    (CG43/CG10 함정). ⚠ per_exp 를 만들지 않는다 — 스코어보드는 per_exp 를 arm 폴드 AUC 로 읽어
    best_robust·무개선 카운터를 만들므로, 갭 값을 넣으면 '돌파'로 오독된다.
    """
    if not path:
        return {"error": "요약 경로 없음(--out/summary_out 미지정)"}
    if not os.path.exists(path):
        return {"error": "요약 파일 없음"}
    mt = os.path.getmtime(path)
    if mtime_floor and mt <= mtime_floor:
        return {"error": "요약 미갱신(mtime <= 실행 시작 시각)", "summary_mtime": mt, "floor": mtime_floor}
    with open(path, encoding="utf-8") as f:
        try:
            d = json.load(f)
        except json.JSONDecodeError as e:
            return {"error": f"요약 JSON 파싱 실패: {e}", "summary_mtime": mt}
    arms = d.get("arms") or {}
    if not isinstance(arms, dict) or not arms:
        return {"error": "arms 없음(리포트 실패)", "summary_mtime": mt}
    return {"summary_mtime": mt, "arms": arms, "panel": d.get("panel"),
            "thresholds": d.get("thresholds") or {}, "finished_at": d.get("finished_at"),
            "report_verdict": d.get("verdict"), "source": os.path.relpath(path, PROJ)}


def judge_insample_gap(item, parsed) -> tuple:
    """CG275 판정 — 갭(train−OOS) 분포로 '정보 상한' 과 '모델 용량' 을 가른다.

    문턱·처방은 **사전등록 그대로**(항목 success — 사후에 뒤집지 않는다):
      갭 ≥ gap_ceiling(기본 0.30) → 학습셋을 외우고 일반화분이 없다 = **정보 상한** → 데이터 투자
      갭 ≤ gap_floor(기본 0.10)   → 학습셋조차 못 맞힌다 = **모델 용량·표현력** → 용량/정규화 투자
    ⚠ 두 해석이 갈릴 수 있으므로 판정 문구에 train_mean·OOS 를 반드시 함께 남긴다
      (갭이 작은데 train AUC 도 낮으면 '정보 없음'이 아니라 '언더핏' 후보다 — 그 구분은
      train_mean 을 보고 사람이/다음 실험이 확정한다).
    판정은 리포트의 자기신고 문구가 아니라 **숫자에서 다시 계산**한다.
    """
    if parsed.get("error"):
        return "판정불가", str(parsed["error"]), None
    arms = {k: v for k, v in (parsed.get("arms") or {}).items() if isinstance(v, dict) and v.get("n_folds")}
    if not arms:
        return "판정불가", "폴드별 train_auc 없음(--report-train-auc 미지정 또는 학습 실패)", None
    ceil_ = float(item.get("gap_ceiling") or 0.30)
    floor_ = float(item.get("gap_floor") or 0.10)
    ref = item.get("paired_reference") or "CO_core30_h5"
    name = ref if ref in arms else max(arms, key=lambda k: arms[k]["n_folds"])
    a = arms[name]
    gap = a.get("gap")
    if not isinstance(gap, (int, float)):
        return "판정불가", f"{name}: gap 값 없음", None
    parts = [f"{n}: train {v.get('train_mean')} vs OOS {v.get('oos_mean')} → 갭 {v.get('gap')}"
             f"(폴드별 {v.get('gap_by_fold')})" for n, v in arms.items()]
    detail = (f"{name} 기준 · 폴드 {a['n_folds']}개·시드 {a.get('n_seeds')} · "
              f"train {a.get('train_mean')} vs OOS {a.get('oos_mean')} · 갭 {gap}±{a.get('gap_std')} "
              f"(min {a.get('gap_min')} max {a.get('gap_max')}) · OOS−0.5 {a.get('oos_edge_vs_chance')} · "
              f"문턱 갭≥{ceil_} 정보상한 / 갭≤{floor_} 용량 | " + " | ".join(parts))
    if gap >= ceil_:
        return ("정보 상한 — 학습셋 암기, 일반화분 없음(데이터 축)", detail, gap)
    if gap <= floor_:
        return ("모델 용량·표현력 축(언더핏) — 정규화·용량 투자", detail, gap)
    return ("혼합 — 갭 중간대(단일 원인 아님)", detail, gap)


def judge_horizon_ladder(item, parsed) -> tuple:
    """CG276 판정 — 같은 피처·같은 저용량에서 라벨 **지평**만 바꿨을 때 OOS AUC 가 움직이는가.

    CG275 실측: 저용량(depth1+스무딩) 갭 0.052 (train 0.587 vs OOS 0.5352) — 모델이 학습셋조차
    못 맞히므로 '표현력' 문제가 아니라 **피처에 담긴 일반화 신호가 작다**는 쪽이다. 그렇다면
    남은 질문은 "그 작음이 이 지평(h5)의 성질인가"다. 지평 사다리의 OOS AUC 를 기준 arm(h5)과
    비교해 Δ ≥ +0.02 면 신호, 미달이면 지평 축 종결로 닫는다.
    ⚠ 지평이 다르면 라벨의 기저율·거래비용이 달라진다 → AUC 비교는 '정보량' 비교일 뿐
      돈 지표가 아니다. 판정 문구에 그 한계를 함께 남긴다(돈 판정은 순기대 별도 측정).
    """
    if parsed.get("error"):
        return "판정불가", str(parsed["error"]), None
    arms = {k: v for k, v in (parsed.get("arms") or {}).items() if isinstance(v, dict) and v.get("n_folds")}
    if not arms:
        return "판정불가", "폴드 기록 없음(학습 실패 또는 --report-train-auc 미지정)", None
    ref = item.get("paired_reference") or "CO_smooth_d1_h5"
    if ref not in arms:
        return "판정불가", f"기준 arm {ref} 이 결과에 없다(대조 없는 비교는 무효)", None
    r = arms[ref]
    others = {k: v for k, v in arms.items() if k != ref}
    if not others:
        return "판정불가", "비교 arm 이 없다", None
    best = max(others, key=lambda k: others[k].get("oos_mean") or -1)
    b = others[best]
    delta = round((b.get("oos_mean") or 0) - (r.get("oos_mean") or 0), 4)
    ladder = " · ".join(f"{k} OOS {v.get('oos_mean')}(갭 {v.get('gap')})" for k, v in arms.items())
    detail = (f"기준 {ref} OOS {r.get('oos_mean')}(갭 {r.get('gap')}) vs 최고 {best} OOS "
              f"{b.get('oos_mean')}(갭 {b.get('gap')}) → Δ{delta:+} · 폴드 {b['n_folds']}개·시드 "
              f"{b.get('n_seeds')} · 사다리: {ladder} · ⚠ AUC 비교는 정보량 비교다(지평별 라벨 "
              f"기저율·거래비용이 달라 돈 지표 대체 아님)")
    if delta >= 0.02:
        return (f"신호 — 지평 {best} 가 h5 대비 Δ{delta:+.4f}", detail, delta)
    return (f"노이즈 — 지평 축 종결(최고 Δ{delta:+.4f} < +0.02)", detail, delta)


def parse_blend_eval(path, mtime_floor) -> dict:
    """라벨 다양성 앙상블(scripts/blend_eval.py --out) 짝 집계를 파싱한다.

    ⚠ per_exp 를 만들지 않는다 — scoreboard 는 원장 per_exp 전체를 'arm 폴드 평균(AUC)' 로 읽어
    best_robust·무개선 카운터를 만든다(실측 2026-09-29 CG31 사고). 시드별 값은 `seeds` 로 싣는다.

    판정에 쓰는 값은 `paired`(blend−champ 짝 Δ 평균·SE·t·양(+) 시드 수) 하나뿐이다.
    """
    if not path:
        return {"error": "요약 경로 없음(--out 미지정)"}
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
    paired = d.get("paired") or {}
    if not paired:
        return {"error": "paired 없음(집계 실패)", "measured_at": d.get("measured_at")}
    arms = d.get("arms") or {}
    return {
        "measured_at": d.get("measured_at"), "protocol": d.get("protocol"),
        "metric_name": d.get("metric_name"),
        "robust_auc": d.get("robust_auc"),
        "auc_std_across_folds": d.get("auc_std_across_folds"),
        "fold_means": d.get("fold_means"),
        "blend_mean": (arms.get("blend") or {}).get("mean"),
        "champ_mean": d.get("champ_mean"), "cand_mean": d.get("cand_mean"),
        "seeds": d.get("windows"), "n_seeds": d.get("n_seeds"),
        "n_windows": d.get("n_windows"), "rows_scored": d.get("rows_scored"),
        "paired": paired, "threshold": paired.get("threshold", 0.02),
        "errors": (d.get("errors") or [])[:5],
        "summary_mtime": mt,
    }


def judge_blend_eval(item, parsed) -> tuple:
    """모델 결합(rank-avg) 판정 — 사전 등록: in-run 짝 Δ(blend−champ) ≥ +0.02 **그리고** 양(+) 시드 ≥ 80%.

    왜 in-run 짝인가(실측 2026-10-01 CG55): 같은 모델·같은 시드는 비트 동일(Δ 0)이지만 **창 구성만**
    바꿔도 폭이 +0.0261 로 사전문턱을 넘는다 → 단일 런 절대값 비교는 판정이 아니다. 같은 런에서 두 arm
    을 짝지은 Δ 만이 검출력(10시드 SE≈0.005 → +0.02 = 3.8σ)을 갖는다.
    """
    d = parsed.get("paired") or {}
    delta = d.get("delta_blend_minus_champ_mean")
    if not isinstance(delta, (int, float)):
        return "판정불가", (parsed.get("error") or "paired.delta 없음 — 요약 확인 필요"), None
    pos = str(d.get("pos_seeds") or "0/0")
    try:
        a, b = pos.split("/")
        pos_ratio = (float(a) / float(b)) if float(b) else 0.0
    except Exception:
        pos_ratio = 0.0
    thr = float(parsed.get("threshold", 0.02) or 0.02)
    detail = (f"결합(rank-avg) 시드평균 {parsed.get('robust_auc')}±{parsed.get('auc_std_across_folds')}"
              f" · 챔피언(같은 런) {parsed.get('champ_mean')} · 후보 {parsed.get('cand_mean')}"
              f" · 짝 Δ(blend−champ) {float(delta):+.4f}(SE {d.get('se')} · t {d.get('t')} · 양(+) {pos})"
              f" · Δ(blend−cand) {d.get('delta_blend_minus_cand_mean')}(양(+) {d.get('pos_seeds_vs_cand')})"
              f" · 창 {parsed.get('n_windows')} · 시드 {parsed.get('n_seeds')} · 문턱 +{thr}")
    if float(delta) >= thr and pos_ratio >= 0.8:
        return "짝 신호", detail + " → 사전등록 충족(승격 아님 — 두 모델 추론 계약 변경은 승인 대상)", float(delta)
    return "짝 노이즈", detail + f" → 사전등록 미충족(양(+) 비율 {pos_ratio:.2f}, 기준 0.80)", float(delta)


def parse_rank_ic_money(path, mtime_floor) -> dict:
    """세션별 횡단면 랭크 IC 요약을 파싱한다 — scripts/rank_ic_money.py --json-out.

    스키마: {arm, arm_jsonl, fillable, fillable_filter, n_rows, n_rows_skipped,
             mean_cs_sd_ret, ic:{n_sessions,mean_ic,sd_ic,t,pos_session_share,
             first_half_ic,second_half_ic,implied_edge_pct}, verdict, detail,
             control:{...}, paired:{...}}

    ⚠ `per_exp` 를 만들지 않는다 — scoreboard 가 그것을 'arm 폴드 평균(AUC)' 으로 오독한다.
    """
    if not path:
        return {"error": "요약 경로 없음(--json-out 미지정)"}
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
    ic = d.get("ic") or {}
    if not ic or ic.get("mean_ic") is None:
        return {"error": "ic.mean_ic 없음(집계 실패)", "summary_mtime": mt}
    return {
        "metric_name": d.get("metric_name") or "rank_ic_money",
        "arm": d.get("arm"), "arm_jsonl": d.get("arm_jsonl"),
        "fillable": d.get("fillable"), "fillable_filter": d.get("fillable_filter"),
        "n_rows": d.get("n_rows"), "n_rows_skipped": d.get("n_rows_skipped"),
        "mean_cs_sd_ret": d.get("mean_cs_sd_ret"),
        "ic": ic,
        "control": d.get("control"),
        "paired": d.get("paired"),
        "profile": d.get("profile"),
        "tool_verdict": d.get("verdict"),
        "summary_mtime": mt,
    }


def judge_rank_ic_money(item, parsed) -> tuple:
    """랭크 IC 판정 — 사전등록(CG113, 도구 기본값과 동일):

      mean_IC > 0 · t ≥ min_t(기본 2.0) · 양(+) 세션 ≥ min_pos(기본 0.55) · 앞/뒤 절반 모두 양(+)

    를 만족하면 '신호있음' = **랭킹에 돈 방향 정보가 있다**(순기대의 필요조건 충족).
    하나라도 미달이면 '노이즈' = 미검출. ⚠ 어느 쪽이든 이 판정만으로 승격하지 않는다 —
    IC 는 순기대가 아니고, 소비 정책(top-k)의 기대값은 별도 측정이다.
    """
    if parsed.get("error"):
        return "판정불가", f"요약 없음/미갱신 — {parsed['error']}", None
    ic = parsed.get("ic") or {}
    m, t = ic.get("mean_ic"), ic.get("t")
    pos, h1, h2 = ic.get("pos_session_share"), ic.get("first_half_ic"), ic.get("second_half_ic")
    if m is None:
        return "판정불가", "ic.mean_ic 없음", None
    min_t = float(item.get("min_t") or 2.0)
    min_pos = float(item.get("min_pos") or 0.55)
    mode = "체결성 필터 ON" if parsed.get("fillable") else "무필터"
    detail = ("%s · 세션 %s · mean IC %+.4f · t %s · 양세션 %s%% · 앞/뒤 %s/%s · "
              "상위10%% 스프레드 근사 %s%%p"
              % (mode, ic.get("n_sessions"),
                 m, None if t is None else round(t, 2),
                 None if pos is None else round(pos * 100, 1),
                 None if h1 is None else round(h1, 4),
                 None if h2 is None else round(h2, 4),
                 None if ic.get("implied_edge_pct") is None
                 else round(ic["implied_edge_pct"], 3)))
    if parsed.get("paired"):
        p = parsed["paired"]
        detail += (" · 대조 짝 ΔIC %+.4f(t %s, n %s)"
                   % (p.get("mean_delta_ic") or 0.0,
                      None if p.get("t") is None else round(p["t"], 2),
                      p.get("n_sessions")))
    ok = (m > 0 and (t or 0) >= min_t and (pos or 0) >= min_pos
          and (h1 or 0) > 0 and (h2 or 0) > 0)
    if ok:
        return "신호있음", detail + " — 랭킹에 돈 방향 정보(순기대의 필요조건 충족)", round(m, 4)
    return "노이즈", detail + " — 미검출(검출 바닥 이하 또는 부호 불일치)", round(m, 4)


def parse_regime_ic_screen(path, mtime_floor) -> dict:
    """시장 국면별 랭크 IC 분해(scripts/regime_ic_screen.py --json-out) 파서. CG126/CG127.

    스키마: {metric_name, arm, arm_jsonl, fillable, fillable_filter, n_rows, n_sessions_total,
             all, trend:{up,down}, volatility:{low,mid,high},
             delta_ic_up_minus_down, delta_t, delta_ic_vol_low_minus_rest, delta_t_vol,
             thresholds:{min_delta_ic,min_t,min_sessions}, verdict, detail}
    버킷 = rank_ic_money._agg 산출: {n_sessions, mean_ic, sd_ic, t, pos_session_share,
             first_half_ic, second_half_ic, pool_mean_fwd_pct}
    ⚠ per_exp 를 만들지 않는다 — scoreboard 가 'arm 폴드 평균(AUC)' 로 오독한다.
    """
    if not path:
        return {"error": "요약 경로 없음(--json-out 미지정)"}
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
    if d.get("error"):
        return {"error": d["error"], "summary_mtime": mt}
    if not d.get("all") or d["all"].get("n_sessions") is None:
        return {"error": "all.n_sessions 없음(집계 실패)", "summary_mtime": mt}
    return {
        "metric_name": d.get("metric_name") or "regime_ic_screen",
        "arm": d.get("arm"), "arm_jsonl": d.get("arm_jsonl"),
        "fillable": d.get("fillable"), "fillable_filter": d.get("fillable_filter"),
        "n_rows": d.get("n_rows"), "n_sessions_total": d.get("n_sessions_total"),
        "n_sessions_no_regime": d.get("n_sessions_no_regime"),
        "all": d.get("all"), "trend": d.get("trend"), "volatility": d.get("volatility"),
        "delta_ic_up_minus_down": d.get("delta_ic_up_minus_down"),
        "delta_t": d.get("delta_t"),
        "delta_ic_vol_low_minus_rest": d.get("delta_ic_vol_low_minus_rest"),
        "delta_t_vol": d.get("delta_t_vol"),
        "thresholds": d.get("thresholds"),
        "tool_verdict": d.get("verdict"),
        "summary_mtime": mt,
    }


def judge_regime_ic_screen(item, parsed) -> tuple:
    """국면 조건화 판정 — **항목 플래그 `regime_test` 로 분기**(없으면 판정불가).

      regime_test="trend_up_vs_down" : |ΔIC(up−down)| >= min_delta_ic AND |t| >= min_t  (양측)
      regime_test="vol_low_vs_rest"  : ΔIC(vol low−rest) >= +min_delta_ic AND t >= min_t  (단측 양)

    공통: 두 버킷 세션수 >= min_sessions. 미달이면 '노이즈' = 국면 조건화 정보 없음.
    ⚠ '요약 블록 존재'로 분기하면 미등록 실행이 조용히 다른 규칙으로 폴백해 verdict 가 뒤집힌다
    (CG119 교훈) → 플래그가 없으면 판정불가로 세운다.
    """
    if parsed.get("error"):
        return "판정불가", f"요약 없음/미갱신 — {parsed['error']}", None
    test = item.get("regime_test")
    if not test:
        return "판정불가", "regime_test 플래그 없음(등록 누락) — 판정 규칙 미지정", None
    th = parsed.get("thresholds") or {}
    min_d = float(item.get("min_delta_ic") or th.get("min_delta_ic") or 0.03)
    min_t = float(item.get("min_t") or th.get("min_t") or 2.0)
    min_n = int(item.get("min_sessions") or th.get("min_sessions") or 15)
    allb = parsed.get("all") or {}

    def _g(d, k):
        v = d.get(k)
        return None if v is None else round(v, 4)

    def _gt(d, k):
        v = d.get(k)
        return None if v is None else round(v, 2)

    base = ("all IC %s(t %s, n %s)" % (_g(allb, "mean_ic"), _gt(allb, "t"), allb.get("n_sessions")))
    if test == "vol_low_vs_rest":
        dl, tt = parsed.get("delta_ic_vol_low_minus_rest"), parsed.get("delta_t_vol")
        vb = parsed.get("volatility") or {}
        n_low = (vb.get("low") or {}).get("n_sessions") or 0
        n_rest = ((vb.get("mid") or {}).get("n_sessions") or 0) + ((vb.get("high") or {}).get("n_sessions") or 0)
        detail = ("ΔIC(vol low−rest) %s · t %s · n low/rest %s/%s · %s"
                  % (None if dl is None else round(dl, 4), None if tt is None else round(tt, 2),
                     n_low, n_rest, base))
        ok = (dl is not None and tt is not None and dl >= min_d and tt >= min_t
              and n_low >= min_n and n_rest >= min_n)
        if ok:
            return "신호있음", detail + f" — 저변동 국면 IC 우위(단측 Δ≥+{min_d}, t≥{min_t})", round(dl, 4)
        return "노이즈", detail + f" — 저변동 국면 우위 미재현(단측 Δ≥+{min_d}·t≥{min_t} 미달)", (
            None if dl is None else round(dl, 4))
    if test != "trend_up_vs_down":
        return "판정불가", f"알 수 없는 regime_test={test!r}", None
    dd, tt = parsed.get("delta_ic_up_minus_down"), parsed.get("delta_t")
    tb = parsed.get("trend") or {}
    n_up = (tb.get("up") or {}).get("n_sessions") or 0
    n_dn = (tb.get("down") or {}).get("n_sessions") or 0
    detail = ("ΔIC(up−down) %s · t %s · n up/down %s/%s · %s"
              % (None if dd is None else round(dd, 4), None if tt is None else round(tt, 2),
                 n_up, n_dn, base))
    ok = (dd is not None and tt is not None and abs(dd) >= min_d and abs(tt) >= min_t
          and n_up >= min_n and n_dn >= min_n)
    if ok:
        return "신호있음", detail + f" — 국면 의존 IC(|Δ|≥{min_d}, |t|≥{min_t})", round(dd, 4)
    return "노이즈", detail + f" — 국면 의존 IC 미검출(|Δ|≥{min_d}·|t|≥{min_t} 미달)", (
        None if dd is None else round(dd, 4))


def parse_intraday_screen(path, mtime_floor) -> dict:
    """인트라데이(분봉) 피처 사전 스크린(scripts/intraday_feature_screen.py --json-out) 파서. CG128.

    스키마: {full_days:[...], n_rows:int,
             horizons:{"h1":{n_dates, dates, features:{name:{daily_auc_mean, daily_auc_std,
                      daily_auc_list, ic_mean, ic_t}}}, "h5":{...}}}
    ⚠ per_exp 를 만들지 않는다 — scoreboard 가 'arm 폴드 AUC' 로 오독한다(2026-09-29 교훈).
    판정 입력은 **날짜별 IC(|평균|)·t·표본 날짜수(n_dates)** 이고 AUC 는 참고값이다.
    """
    if not path:
        return {"error": "요약 경로 없음(--json-out 미지정)"}
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
    if d.get("error"):
        return {"error": d["error"], "summary_mtime": mt}
    hz = d.get("horizons") or {}
    if not hz:
        return {"error": "horizons 없음(집계 실패)", "summary_mtime": mt}
    out = {"metric_name": "intraday_screen", "full_days": d.get("full_days"),
           "n_rows": d.get("n_rows"), "horizons": {}, "summary_mtime": mt}
    for h, blk in hz.items():
        feats = (blk or {}).get("features") or {}
        best = None
        for name, v in feats.items():
            if not isinstance(v, dict):
                continue
            ic = v.get("ic_mean")
            key = abs(ic) if isinstance(ic, (int, float)) else -1.0
            if best is None or key > best[0]:
                best = (key, name, v)
        out["horizons"][h] = {
            "n_dates": (blk or {}).get("n_dates") or 0,
            "dates": (blk or {}).get("dates") or [],
            "n_features": len(feats),
            "best_feature": (best[1] if best else None),
            "best_ic": (None if not best else best[2].get("ic_mean")),
            "best_ic_t": (None if not best else best[2].get("ic_t")),
            "best_auc": (None if not best else best[2].get("daily_auc_mean")),
            "aucs": {n: v.get("daily_auc_mean") for n, v in feats.items() if isinstance(v, dict)},
            "ics": {n: v.get("ic_mean") for n, v in feats.items() if isinstance(v, dict)},
        }
    return out


def judge_intraday_screen(item, parsed) -> tuple:
    """인트라데이 피처 스크린 판정 — 사전등록 문턱을 실행 전에 고정한 그대로 적용한다.

      정보있음 : 어떤 호라이즌에 |IC 평균| >= min_delta_ic AND |t| >= min_t 인 피처가 있고
                 그 호라이즌의 n_dates >= min_dates
      판정불가 : 위를 만족할 수 있는 호라이즌의 n_dates 가 min_dates 미만(표본 부족)
                 → **결과를 보고 문턱을 낮추지 않는다**(관찰-후-재현 규율)
      노이즈   : 표본은 충분한데 통과 피처가 없음
    ⚠ 분기는 항목 플래그 `intraday_test` 로 한다(다른 규칙으로 조용히 폴백하지 않게).
    """
    if parsed.get("error"):
        return "판정불가", f"요약 없음/미갱신 — {parsed['error']}", None
    test = item.get("intraday_test") or "feature_ic"
    if test != "feature_ic":
        return "판정불가", f"알 수 없는 intraday_test={test!r}", None
    min_d = float(item.get("min_delta_ic") or 0.03)
    min_t = float(item.get("min_t") or 2.0)
    min_n = int(item.get("min_dates") or 20)
    hz = parsed.get("horizons") or {}
    lines, best_abs, max_n = [], None, 0
    for h in sorted(hz):
        b = hz[h] or {}
        nd = int(b.get("n_dates") or 0)
        max_n = max(max_n, nd)
        ic, tt = b.get("best_ic"), b.get("best_ic_t")
        ba = b.get("best_auc")
        lines.append("%s: n_dates %d · 최강 %s (IC %s · t %s · AUC %s)"
                     % (h, nd, b.get("best_feature"),
                        None if ic is None else round(ic, 4),
                        None if tt is None else round(tt, 2),
                        None if ba is None else round(ba, 4)))
        if (isinstance(ic, (int, float)) and nd >= min_n and abs(ic) >= min_d
                and isinstance(tt, (int, float)) and abs(tt) >= min_t):
            best_abs = abs(ic) if best_abs is None else max(best_abs, abs(ic))
    detail = " | ".join(lines) + f" · 사전문턱 |IC|>={min_d} & |t|>={min_t} & n_dates>={min_n}"
    if best_abs is not None:
        return "정보있음", detail + " → 문턱 통과 피처 있음", round(best_abs, 4)
    if max_n < min_n:
        return "판정불가", detail + f" → 표본 부족(n_dates {max_n} < {min_n}) — 문턱을 낮추지 않는다", None
    return "노이즈", detail + " → 문턱 통과 피처 없음", None


def parse_factor_money_screen(path, mtime_floor) -> dict:
    """고전 팩터 랭킹 정보 스크린(scripts/factor_money_screen.py --out) 파서.

    스키마: {panel:{file,rows,codes,from,to,horizon}, asof:{...},
             factors:{<name>:{coverage, ic:{n,mean,sd,t,pos_share,first_half,second_half},
                              decile_spread_pct:{...}, top_decile_mean_pct:{...},
                              bottom_decile_mean_pct:{...}}}, note}
    ⚠ per_exp 를 만들지 않는다 — scoreboard 가 'arm 폴드 평균(AUC)' 로 오독한다.
    """
    if not path:
        return {"error": "요약 경로 없음(--out 미지정)"}
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
    fac = d.get("factors") or {}
    mf = (fac.get("multifactor") or {}).get("ic") or {}
    if not fac or mf.get("mean") is None:
        return {"error": "factors.multifactor.ic.mean 없음(집계 실패)", "summary_mtime": mt}
    return {"metric_name": "factor_money_screen",
            "panel": d.get("panel"), "asof": d.get("asof"),
            "factors": fac, "fillable": d.get("fillable"),
            "paired_vs_factor": d.get("paired_vs_factor"),
            "note": d.get("note"), "summary_mtime": mt}


def parse_factor_money_fillable(path, mtime_floor) -> dict:
    """체결성 필터 ON 팩터 top-k vs 풀평균(CG116) — 같은 파일의 `fillable` 블록을 요구한다."""
    d = parse_factor_money_screen(path, mtime_floor)
    if d.get("error"):
        return d
    if not (d.get("fillable") or {}).get("factors"):
        return {"error": "fillable 블록 없음(--fillable 미지정으로 실행됨)", "summary_mtime": d.get("summary_mtime")}
    return d


def judge_factor_money_fillable(item, parsed) -> tuple:
    """체결성 필터 ON 팩터 top-k vs 세션 풀평균 판정 — 사전등록(CG116):

      **멀티팩터 top-k(k=3·5 둘 다) Δ(바스켓 − 풀평균) ≥ +0.1 %p/세션 AND t ≥ 2**
      → '신호있음'(팩터 랭킹이 무작위 k 를 이긴다 = 베타 초과). 미달이면 '노이즈'.

    널 기준선은 세션 풀평균이다(AUC 의 '동전 0.5' 에 해당) — arm vs arm 만 보면 베타와
    구분되지 않는다(실측 CG95: arm·대조군 둘 다 +2%p/세션 = 시장 베타).
    ⚠ 승격 근거가 아니다(패널 유니버스 스크린 — 소비 정책·체결 경로 검증은 별도).
    """
    if parsed.get("error"):
        return "판정불가", f"요약 없음/미갱신 — {parsed['error']}", None
    fl = parsed.get("fillable") or {}
    mf = ((fl.get("factors") or {}).get("multifactor") or {}).get("top", {}).get("k", {})
    if not mf:
        return "판정불가", "fillable.factors.multifactor.top.k 없음", None
    min_d = float(item.get("min_delta_pct") or 0.1)
    min_t = float(item.get("min_t") or 2.0)
    ks = item.get("ks") or [3, 5]
    desc, ok_all = [], True
    for k in ks:
        v = mf.get(str(k)) or {}
        d, t = v.get("mean_delta_pct"), v.get("t")
        desc.append("k=%s Δ%+.3f%%p(t %s, 양세션 %s%%)"
                    % (k, d if d is not None else float("nan"), t,
                       None if v.get("pos_share") is None else round(v["pos_share"] * 100, 1)))
        if d is None or d < min_d or (t or 0) < min_t:
            ok_all = False
    pool = ((((fl.get("factors") or {}).get("multifactor") or {}).get("top") or {})
            .get("pool_mean_pct") or {})
    detail = ("체결성 필터 ON · 유지 %s/%s행 · 널=세션 풀평균 %+.3f%%p(t %s) · %s"
              % ((fl.get("filter") or {}).get("rows_kept"), (fl.get("filter") or {}).get("rows_total"),
                 pool.get("mean_delta_pct") or 0.0, pool.get("t"),
                 " · ".join(desc)))
    if ok_all:
        return "신호있음", detail + " — 팩터 랭킹이 무작위 k(풀평균)를 이긴다(베타 초과)", \
            round((mf.get(str(ks[0])) or {}).get("mean_delta_pct") or 0.0, 4)
    return "노이즈", detail + " — 사전문턱 미달(베타 초과 미검출)", \
        round((mf.get(str(ks[0])) or {}).get("mean_delta_pct") or 0.0, 4)


def judge_factor_money_screen(item, parsed) -> tuple:
    """팩터 스크린 판정 — 사전등록(CG115): **멀티팩터 횡단면 IC > 0 이고 t ≥ 2** 이면
    '정보있음' = 고전 팩터 랭킹에 횡단면 정보가 있다(모델 대조군). 미달이면 '노이즈'.

    ⚠ 이 판정은 **순기대가 아니다**(체결성 필터·수수료 미적용) → 승격 근거가 될 수 없다.
    분위 스프레드 부호는 detail 에 함께 싣는다(비단조 진단용): IC 가 양(+)인데
    top−bottom 스프레드가 음(−)이면 랭킹이 단조가 아니라는 신호다.
    """
    if parsed.get("error"):
        return "판정불가", f"요약 없음/미갱신 — {parsed['error']}", None
    pv = parsed.get("paired_vs_factor")
    if pv and not item.get("paired_vs_factor"):



        return ("판정불가",
                "항목에 paired_vs_factor 플래그가 없는데 요약에 짝 블록이 있다 — 등록 누락(수리 필요)",
                None)
    if item.get("paired_vs_factor"):





        if not pv or pv.get("error") or not pv.get("paired"):
            why = ((pv or {}).get("error") if isinstance(pv, dict) else None) \
                or "요약에 paired_vs_factor 없음(--arm-jsonl 없이 실행됨)"
            return "판정불가", f"paired 블록 없음 — {why}", None
        pa = pv["paired"]
        if pa.get("error"):
            return "판정불가", f"paired 집계 불가 — {pa['error']}", None
        d, t = pa.get("mean_delta_ic"), pa.get("t")
        min_d = float(item.get("min_delta_ic") or 0.01)
        min_t = float(item.get("min_t") or 2.0)
        im = pv.get("ic_model") or {}
        iff = pv.get("ic_factor") or {}
        u, r = pv.get("universe") or {}, pv.get("rows") or {}
        detail = ("같은 (code,date) 행 짝 · 덤프 %s · 종목 %s(panel 교집합 %s) · 세션 %s · "
                  "모델 IC %+.4f(t %s) vs 팩터 IC %+.4f(t %s) · ΔIC %+.4f(t %s, 양세션 %s%%, "
                  "동점 %s, 부호검정 p %s) · 채점행 %s"
                  % (pv.get("arm"), u.get("dump_codes"), u.get("panel_code_overlap"),
                     pa.get("n_sessions"),
                     im.get("mean") or 0.0, im.get("t"),
                     iff.get("mean") or 0.0, iff.get("t"),
                     d or 0.0, t,
                     None if pa.get("pos_session_share") is None
                     else round(pa["pos_session_share"] * 100, 1),
                     pa.get("n_tied"), pa.get("sign_p"), r.get("scored")))
        ok = d is not None and d >= min_d and (t or -9) >= min_t
        dnum = float(d) if d is not None else 0.0
        if ok:
            return ("모델 우위 있음", detail + " — 사전등록 충족(IC 기준·순기대 아님·승격 근거 아님)",
                    round(dnum, 4))
        return ("모델 우위 없음", detail + " — 사전등록 미달(IC 기준·순기대 아님)", round(dnum, 4))
    fac = parsed.get("factors") or {}
    mf = fac.get("multifactor") or {}
    ic, sp = (mf.get("ic") or {}), (mf.get("decile_spread_pct") or {})
    m, t = ic.get("mean"), ic.get("t")
    if m is None:
        return "판정불가", "multifactor.ic.mean 없음", None
    min_t = float(item.get("min_t") or 2.0)
    parts = []
    for name in ("value_score", "quality_score", "momentum_score", "lowvol_score"):
        v = fac.get(name) or {}
        vi, vs = (v.get("ic") or {}), (v.get("decile_spread_pct") or {})
        if vi.get("mean") is None:
            continue
        parts.append("%s IC %+.4f(t %s) 스프레드 %+.2f%%p(t %s)"
                     % (name, vi["mean"], vi.get("t"),
                        vs.get("mean") or 0.0, vs.get("t")))
    detail = ("멀티팩터 IC %+.4f(t %s, n %s, 양세션 %s%%) · 스프레드 %+.2f%%p(t %s) · %s"
              % (m, t, ic.get("n"),
                 None if ic.get("pos_share") is None else round(ic["pos_share"] * 100, 1),
                 sp.get("mean") or 0.0, sp.get("t"), " | ".join(parts)))
    ok = m > 0 and (t or 0) >= min_t
    if ok:
        return ("정보있음",
                detail + " — 고전 팩터 랭킹에 횡단면 정보(순기대 아님·승격 근거 아님)", round(m, 4))
    return ("노이즈", detail + " — 미검출(문턱 이하)", round(m, 4))


def judge_protocol_noise_floor(item, parsed) -> tuple:
    """CG143 — 프로토콜 재현 잡음바닥(계측기 검정). 모델 팔 실험이 **아니다**.

    ⚠ delta 는 항상 None 을 돌려준다: 여기 숫자는 '성능 개선폭'이 아니라 '같은 모델을 하루 옮겼을 때의
    흔들림'이라 무개선 카운터·스코어보드 개선 판정에 넣으면 가짜 신호가 난다(parsed 에 per_exp 를
    일부러 안 싣는 이유와 동형). 판정 문구는 파서가 만든 결정성/문턱 비교 결과를 그대로 쓴다.

    왜 필요(실측 2026-10-09 06:05 CG143): 파서(`parse_protocol_noise_floor`)는 배선돼 있었지만
    `judge_by_metric` 에 분기가 없어 `judge_per({})` 로 흘러 **원장 헤드라인 verdict 가 '판정불가',
    detail ''** 로 기록됐다 — 유효한 실측(rc=0·요약 정상)이 판정 없이 남았다.
    """
    if parsed.get("error"):
        return "판정불가", str(parsed["error"]), None
    auc = parsed.get("auc") or {}
    folds = parsed.get("folds") or {}
    anchors = parsed.get("anchors") or {}
    shift, thr = parsed.get("anchor_shift_delta"), parsed.get("threshold")
    det = bool(parsed.get("determinism_same_anchor"))
    verdict = parsed.get("verdict") or "판정불가"
    detail = (f"결정성 {'OK' if det else '위반'}"
              f" · 앵커 {anchors.get('A')}→{anchors.get('B')} Δ{shift} (사전문턱 {thr})"
              f" · AUC a1/a2/b1 {auc.get('a1')}/{auc.get('a2')}/{auc.get('b1')}"
              f" · 폴드 a1 {folds.get('a1')} b1 {folds.get('b1')}"
              f" · 계측기 검정(모델 팔 아님) — 무개선 카운터 무관")
    return verdict, detail, None


def judge_protocol_noise_curve(item, parsed) -> tuple:
    """CG144 — 앵커 이동폭별 잡음 곡선. 계측기 검정이라 delta=None(개선 카운터·스코어보드 무관)."""
    if parsed.get("error"):
        return "판정불가", str(parsed["error"]), None
    verdict = parsed.get("verdict") or "판정불가"
    auc = parsed.get("auc") or {}
    deltas = parsed.get("deltas") or {}
    auc_s = " ".join(f"{k}:{auc[k]}" for k in sorted(auc))
    d_s = " ".join(f"{k}:{deltas.get(k)}" for k in sorted(deltas))
    detail = (f"기준 앵커 {parsed.get('base_anchor')} · AUC {auc_s} · Δ(vs d0) {d_s}"
              f" · 최대 |Δ| {parsed.get('max_abs_delta')} (사전문턱 {parsed.get('threshold')})"
              f" · 계측기 검정(모델 팔 아님) — 무개선 카운터 무관")
    return verdict, detail, None


def judge_protocol_pair_anchor(item, parsed) -> tuple:
    """CG145/CG146 — 앵커 **평균** 짝 Δ(계측기 검정). 모델 팔 실험이 **아니다**.

    ⚠ delta 는 항상 None 을 돌려준다: 숫자가 '성능 개선폭'이 아니라 '앵커를 옮겼을 때의 짝 Δ
    안정성'이라 무개선 카운터·스코어보드에 넣으면 가짜 신호가 난다(parsed 에 per_exp 를 일부러
    안 싣는 이유와 동형). 판정 문구는 스크립트가 사전등록 규칙으로 만든 verdict 를 그대로 쓴다.
    """
    if parsed.get("error"):
        return "판정불가", str(parsed["error"]), None
    verdict = parsed.get("verdict") or "판정불가"
    pd_ = parsed.get("pair_delta") or {}
    dm, ds = parsed.get("pair_delta_mean"), parsed.get("pair_delta_std")

    def _f(v):
        return f"{v:+.4f}" if isinstance(v, (int, float)) else str(v)

    d_s = " ".join(f"{k}:{_f(pd_[k])}" for k in sorted(pd_))
    detail = (f"앵커 {len(pd_)}개 짝 Δ {d_s} · 평균 {_f(dm)}±{ds}"
              f" · 문턱통과 {parsed.get('anchors_over_threshold')}/{len(pd_)}"
              f" ({parsed.get('anchor_threshold_pass')}) · 부호일관 {parsed.get('sign_consistent')}"
              f" · 레벨 std {parsed.get('level_std')}"
              f" · 계측기 검정(모델 팔 아님) — 무개선 카운터 무관")
    return verdict, detail, None


def parse_scale_regime_audit(path, mtime_floor) -> dict:
    """배포 스코어 스케일 체제 감사(scripts/scale_regime_audit.py --json-out) 파서. CG162.

    스키마: {generated_at_kst, summary:{threshold,n_dates,span,blocked_dates,degenerate_dates,
             trailing_blocked_streak,verdict}, per_date:[{date,n,n_distinct,min,p50,p90,max,
             n_ge_threshold,frac_ge_threshold,state,model_version}, ...], model_inventory:[...]}
    ⚠ per_exp 를 만들지 않는다 — scoreboard 가 'arm 폴드 평균(AUC)' 로 오독한다.
    판정 입력은 summary.trailing_blocked_streak(최근 연속 구조적 무진입 일수)이다.
    """
    if not path:
        return {"error": "요약 경로 없음(--json-out 미지정)"}
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
    summ = d.get("summary") or {}
    if not summ or summ.get("n_dates") is None:
        return {"error": "summary 없음(집계 실패)", "summary_mtime": mt}
    per_date = d.get("per_date") or []
    return {"metric_name": "scale_regime_audit", "summary": summ,
            "blocked_dates": summ.get("blocked_dates") or [],
            "degenerate_dates": summ.get("degenerate_dates") or [],
            "trailing_blocked_streak": summ.get("trailing_blocked_streak"),
            "last_state": (per_date[-1].get("state") if per_date else None),
            "last_date": (per_date[-1].get("date") if per_date else None),
            "per_date": per_date, "generated_at_kst": d.get("generated_at_kst"),
            "summary_mtime": mt}


def judge_scale_regime_audit(item, parsed) -> tuple:
    """배포 스코어 스케일 체제 감사 판정 — 소비 관점 결함(문턱 도달 0행)의 현재 진행 여부.

    사전등록(항목 success): ① 감사가 매 발행일 실행되고 날짜별 상태를 남긴다
    ② 신규 'blocked' 날짜 0 ③ 재시작 직후 첫 발행이 정상 스케일이다.
    판정: 최근 연속 blocked/degenerate(trailing_blocked_streak) > 0 → '경보'(진행 중),
    0 이면 '정상'(직전 스트릭이 있었다면 복구로 기록). 요약 없음/미갱신 → '판정불가'.

    ⚠ 승격 근거가 아니다(성능 레버 아님 · affects_model=False) — 측정 정합성 감사다.
    """
    if parsed.get("error"):
        return "판정불가", f"요약 없음/미갱신 — {parsed['error']}", None
    s = parsed.get("summary") or {}
    streak = parsed.get("trailing_blocked_streak")
    streak = int(streak) if isinstance(streak, (int, float)) else 0
    nb, nd = len(parsed.get("blocked_dates") or []), len(parsed.get("degenerate_dates") or [])
    span = s.get("span") or [None, None]
    base = (f"감사 {s.get('n_dates')}일({span[0]}~{span[1]}) · blocked {nb}일 · degenerate {nd}일 · "
            f"최근연속 blocked {streak} · 스크립트 판정: {s.get('verdict')} · 문턱 {s.get('threshold')}")
    if streak > 0:
        return "경보", base + " → 구조적 무진입 진행 중(문턱 도달 0행)", None
    return "정상", base + " → 현재 문턱 도달 정상(신규 blocked 없음)", None


def parse_scale_bisect_probe(path, mtime_floor) -> dict:
    """배포 스코어 압축 원인 이분 프로브(scripts/_cg163_scale_bisect.py --out) 파서. CG175.

    스키마: {probe, dates[], max_per_day, conf_ts, published_dist{date:{n,mean,max,p90,p95,
             ge_055,ge_055_pct}}, sample{date:[{date,code,published,champion,cg166}, ...]},
             summary{published_sample, rescored{label}, paired_delta{label}},
             threshold_recovery, verdict, n_sample_rows, n_failures, elapsed_s}
    ⚠ per_exp 를 만들지 않는다 — 값이 AUC 가 아니라 확률 스케일이라 scoreboard 가 오독한다.
    판정 입력은 summary.paired_delta.champion.mean_rescored_minus_published 다.
    """
    if not path:
        return {"error": "요약 경로 없음(--out 미지정)"}
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
    summ = d.get("summary") or {}
    if not summ or not d.get("verdict"):
        return {"error": "summary/verdict 없음(집계 실패)", "summary_mtime": mt}
    pd_ = summ.get("paired_delta") or {}
    return {"metric_name": "scale_bisect_probe",
            "verdict_text": d.get("verdict"),
            "summary": summ,
            "published_dist": d.get("published_dist") or {},
            "champion_delta": (pd_.get("champion") or {}).get("mean_rescored_minus_published"),
            "cg166_delta": (pd_.get("cg166") or {}).get("mean_rescored_minus_published"),
            "n_sample_rows": d.get("n_sample_rows"), "n_failures": d.get("n_failures"),
            "threshold_recovery": d.get("threshold_recovery"),
            "dates": d.get("dates"), "summary_mtime": mt}


def judge_scale_bisect_probe(item, parsed) -> tuple:
    """배포 스코어 압축 원인 이분 프로브 판정 — 발행 눌림이 현재 (모델·피처)로 재현되는가.

    사전등록(항목 success): ① 같은 프로토콜로 재채점 ② 발행본과 짝 비교로 원인 이분.
    판정: |짝 Δ평균| < 0.02 → '재현됨'(눌림은 발행 시점 입력/모델 신원의 성질),
    |Δ| ≥ 0.02 → '미재현'(후보 원인 = 모델 신원 또는 당시 입력 열화 · 스냅샷 부재로 분리 불가).
    ⚠ 승격 근거가 아니다(성능 레버 아님 · affects_model=False) — 배포 정합 진단이다.
    """
    if parsed.get("error"):
        return "판정불가", f"요약 없음/미갱신 — {parsed['error']}", None
    delta = parsed.get("champion_delta")
    if delta is None:
        return "판정불가", "짝 표본 없음(champion 짝 Δ 없음)", None
    delta = float(delta)
    summ = parsed.get("summary") or {}
    pub = summ.get("published_sample") or {}
    row = (summ.get("rescored") or {}).get("champion") or {}
    base = (f"짝 Δ(champion−발행) {delta:+.4f} · 표본 {parsed.get('n_sample_rows')}행 · "
            f"발행 ≥{item.get('conf_ts', 0.55)} {pub.get('ge_055_pct')}% → 재채점 "
            f"{row.get('ge_055_pct')}% · 실패 {parsed.get('n_failures')}")
    if abs(delta) < 0.02:
        return "재현됨", base + " → 발행 눌림이 현재 (모델·피처)로 재현됨", None
    return "미재현", base + f" → {parsed.get('verdict_text')}", None


def parse_metric_audit_pair(path, mtime_floor, arm_label=None, control_label=None) -> dict:
    """감사 프로토콜(승격 표준)의 **같은 런 짝** 파서 (CG171, 2026-10-11 자율).

    대상: `scripts/model_metric_protocol_audit.py --out <path>` 산출물.
    스키마: {config{topk,max_day_chg,folds,dates_per_fold,stocks,horizon,label_kind,
                   models[{label,dir}]}, single_split_auc{label}, train_windows{label},
             a_live_signal{label}, b_robust_auc{label:{robust_auc,folds[],fold_detail[],
                   auc_pooled,train_start,train_end,windows_excluded[]}},
             c_net_expectancy{label:{n_rows_scored,n_sessions,avg_pct,median_pct,
                   pos_sessions_pct,t_stat,n_trades,halves{front_pct,back_pct,stable,split_date}}}}

    arm/control 은 item 의 `arm_label`/`control_label`(없으면 config.models 순서의 처음/마지막)로 정한다.
    ⚠ **per_exp 를 만들지 않는다** — scoreboard 는 원장 `parsed.per_exp` 전체를 'arm 의 폴드 평균
    AUC' 로 읽어 best_robust·무개선 카운터를 만든다. 감사 산출물은 한 파일에 arm 과 **기준선**이
    함께 있어 그대로 실으면 기준선이 최고 arm 으로 잡힌다(CG31·CG143 사고와 동형). 짝 Δ 는
    `pair` 로 싣는다.
    """
    if not path:
        return {"error": "요약 경로 없음(--out 미지정)"}
    if not os.path.exists(path):
        return {"error": "요약 파일 없음", "path": path}
    mt = os.path.getmtime(path)
    if mtime_floor and mt <= mtime_floor:
        return {"error": "요약 미갱신(mtime <= 실행 시작 시각) — 옛 결과를 새 결과로 오독 방지",
                "summary_mtime": mt, "floor": mtime_floor}
    with open(path, encoding="utf-8") as f:
        try:
            d = json.load(f)
        except json.JSONDecodeError as e:
            return {"error": f"요약 JSON 파싱 실패(쓰는 중일 수 있음): {e}", "summary_mtime": mt}
    cfg = d.get("config") or {}
    labels = [m.get("label") for m in (cfg.get("models") or []) if m.get("label")]
    if not labels:
        return {"error": "config.models 없음(감사 산출물 형식 아님)", "summary_mtime": mt}
    b = d.get("b_robust_auc") or {}
    c = d.get("c_net_expectancy") or {}
    if not b:
        return {"error": "b_robust_auc 없음(감사 런 실패 — a/c 파트만 있는 부분 산출물)",
                "labels": labels, "summary_mtime": mt}

    def _f(v):
        return float(v) if isinstance(v, (int, float)) else None

    def _mean(xs):
        xs = [x for x in xs if x is not None]
        return round(statistics.fmean(xs), 6) if xs else None

    def _std(xs):
        xs = [x for x in xs if x is not None]
        return round(statistics.pstdev(xs), 6) if len(xs) > 1 else (0.0 if xs else None)

    arms = {}
    for lb in labels:
        bb = b.get(lb) or {}
        cc = c.get(lb) or {}
        folds = [f for f in (bb.get("folds") or []) if isinstance(f, (int, float))]
        arms[lb] = {
            "dir": next((m.get("dir") for m in (cfg.get("models") or []) if m.get("label") == lb), None),
            "robust_auc": _f(bb.get("robust_auc")),
            "auc_pooled": _f(bb.get("auc_pooled")),
            "folds": folds, "n_folds": len(folds),
            "fold_mean": _mean(folds), "fold_std": _std(folds),
            "fold_min": (min(folds) if folds else None), "fold_max": (max(folds) if folds else None),
            "train_window": (d.get("train_windows") or {}).get(lb),
            "windows_excluded": bb.get("windows_excluded"),
            "single_split_auc": _f((d.get("single_split_auc") or {}).get(lb)),
            "expectancy_pct": _f(cc.get("avg_pct")), "expectancy_t": _f(cc.get("t_stat")),
            "n_sessions": cc.get("n_sessions"), "n_trades": cc.get("n_trades"),
            "pos_sessions_pct": _f(cc.get("pos_sessions_pct")), "halves": cc.get("halves"),
            "live_gt_055": ((d.get("a_live_signal") or {}).get(lb) or {}).get("deployed_gt_0.55"),
        }
    out = {
        "metric_name": "metric_audit_pair", "protocol": cfg, "labels": labels, "arms": arms,
        "summary_mtime": mt, "threshold": 0.02,
        "single_arm": len(labels) < 2,
    }
    if len(labels) >= 2:
        al = arm_label if arm_label in labels else labels[0]
        cl = control_label if control_label in labels else labels[-1]
        assert al != cl
        a, cc_ = arms[al], arms[cl]
        fa, fc = a["folds"], cc_["folds"]
        delta_folds = ([round(a_ - c_, 6) for a_, c_ in zip(fa, fc)] if len(fa) == len(fc) and fa else [])
        dec = [(x > 0) for x in delta_folds]
        dep = (round(a["expectancy_pct"] - cc_["expectancy_pct"], 6)
               if (a["expectancy_pct"] is not None and cc_["expectancy_pct"] is not None) else None)
        out["pair"] = {
            "arm": al, "control": cl,
            "delta_fold_mean": (round(a["fold_mean"] - cc_["fold_mean"], 6)
                                if (a["fold_mean"] is not None and cc_["fold_mean"] is not None) else None),
            "delta_reported": (round(a["robust_auc"] - cc_["robust_auc"], 6)
                               if (a["robust_auc"] is not None and cc_["robust_auc"] is not None) else None),
            "delta_folds": delta_folds,
            "fold_win_rate": (round(sum(dec) / len(dec), 3) if dec else None),
            "fold_std_ratio": (round(a["fold_std"] / cc_["fold_std"], 2)
                               if (a["fold_std"] and cc_["fold_std"]) else None),
            "delta_expectancy_pct": dep,
            "arm_expectancy_pct": a["expectancy_pct"], "control_expectancy_pct": cc_["expectancy_pct"],
            "arm_expectancy_t": a["expectancy_t"], "control_expectancy_t": cc_["expectancy_t"],
            "n_sessions": a["n_sessions"] or cc_["n_sessions"],
            "arm_halves": (a["halves"] or {}).get("stable"),
            "control_halves": (cc_["halves"] or {}).get("stable"),
            "arm_front_back": [(a["halves"] or {}).get("front_pct"), (a["halves"] or {}).get("back_pct")],
        }


        cp = d.get("c_paired") or {}
        if isinstance(cp, dict) and isinstance(cp.get("delta_mean"), (int, float)):
            out["pair"]["expectancy_paired"] = {
                "arm": cp.get("arm"), "control": cp.get("control"),
                "delta_mean": _f(cp.get("delta_mean")), "sd": _f(cp.get("sd")),
                "se": _f(cp.get("se")), "t": _f(cp.get("t")),
                "pos": cp.get("pos"), "neg": cp.get("neg"), "ties": cp.get("ties"),
                "n_sessions": cp.get("n_sessions"),
                "halves": cp.get("halves"), "folds": cp.get("folds"),
            }
    return out


def judge_metric_audit_pair(item, parsed) -> tuple:
    """감사 프로토콜 같은 런 짝 판정 — 사전등록 두 다리(AUC 다리 + 돈 다리)를 함께 요구한다.

    사전등록(항목 success): 짝 Δ_auc(폴드 평균 차) ≥ +0.02 **그리고** 돈 다리(arm 순기대 > 0 ·
    챔피언/대조 대비 ≥ +0.1%p · arm 분할표본 both_positive). 한쪽만 통과하면 '노이즈'로 닫고
    어느 다리가 막았는지 숫자로 남긴다 — CG168 실측(짝 AUC +0.0294 통과 · 돈 미검출)에서
    'AUC 만 오른 후보는 승격 금지'를 기계적으로 기록하기 위한 장치다(스킬 최우선 규칙).

    ⚠ delta 는 항상 None 을 돌려준다: 한 파일에 arm 과 기준선이 함께 있어 값을 개선폭으로 실으면
    스코어보드가 기준선을 '최고 arm' 으로 오독한다(parsed 에 per_exp 를 안 싣는 이유와 동형).
    ⚠ 단일 arm 런(대조 없음)은 '대조 없음'으로 정직하게 남긴다(CG168 이 그 형태였다).
    """
    if parsed.get("error"):
        return "판정불가", f"요약 없음/미갱신 — {parsed['error']}", None
    arms = parsed.get("arms") or {}
    if parsed.get("single_arm") or not parsed.get("pair"):
        lb = (parsed.get("labels") or ["?"])[0]
        a = arms.get(lb) or {}
        return ("대조 없음",
                f"단일 arm 런({lb}) — 폴드 {a.get('folds')} 평균 "
                f"{a.get('fold_mean')} · 순기대 {a.get('expectancy_pct')}%p(t {a.get('expectancy_t')}) "
                f"· 세션 {a.get('n_sessions')} · 짝 판정 불가(같은 런에 대조 arm 필요)",
                None)
    thr = float(item.get("threshold") or parsed.get("threshold") or 0.02)
    p = parsed["pair"]
    df, dr = p.get("delta_fold_mean"), p.get("delta_reported")
    dep = p.get("delta_expectancy_pct")
    a_exp, c_exp = p.get("arm_expectancy_pct"), p.get("control_expectancy_pct")
    auc_pass = (isinstance(df, float) and df >= thr)
    wp = 0.1

    pp = p.get("expectancy_paired") or {}
    if isinstance(pp.get("delta_mean"), float) and isinstance(pp.get("t"), float):
        ph = (pp.get("halves") or {}).get("stable")
        money_pass = (isinstance(a_exp, float) and a_exp > 0
                      and pp["delta_mean"] >= wp and abs(pp["t"]) >= 2.0
                      and ph == "both_positive")
        paired_line = (f" · 짝(같은 세션) Δ {pp['delta_mean']:+.4f}%p t {pp['t']} "
                       f"(양 {pp.get('pos')}/음 {pp.get('neg')}) 분할 {ph} "
                       f"폴드 {pp.get('folds')}")
        money_path = "짝"
    else:
        money_pass = (isinstance(a_exp, float) and a_exp > 0
                      and isinstance(dep, float) and dep >= wp
                      and p.get("arm_halves") == "both_positive")
        paired_line = ""
        money_path = "주변"
    det_folds = " ".join(f"{x:+.4f}" for x in (p.get("delta_folds") or []))

    def _n(v, fmt="{:+.4f}"):
        return fmt.format(v) if isinstance(v, float) else "?"

    detail = (f"arm {p['arm']} vs 대조 {p['control']} · 같은 런({parsed.get('protocol', {}).get('folds')}폴드"
              f"×{parsed.get('protocol', {}).get('dates_per_fold')}일 · "
              f"{parsed.get('protocol', {}).get('stocks')}종목 · top{parsed.get('protocol', {}).get('topk')})"
              f" · 짝 Δ폴드 [{det_folds}] 평균 {_n(df)}(승률 {p.get('fold_win_rate')}, "
              f"std비 {p.get('fold_std_ratio')}) · AUC Δ(보고값) {_n(dr)}"
              f" · 순기대 arm {_n(a_exp, '{:+.4f}')}%p(t {p.get('arm_expectancy_t')}) vs 대조 "
              f"{_n(c_exp, '{:+.4f}')}%p(t {p.get('control_expectancy_t')}) Δ {_n(dep)}"
              f" · 세션 {p.get('n_sessions')} · 분할 arm {p.get('arm_halves')} {p.get('arm_front_back')}"
              f"{paired_line}"
              f" · 사전문턱[{money_path}]: Δ_auc≥+{thr:.2f} AND arm>0 AND Δ≥+{wp}%p AND 분할 both_positive"
              f" → AUC {'통과' if auc_pass else '미달'} / 돈 {'통과' if money_pass else '미달'}")
    if auc_pass and money_pass:
        return "짝 신호", detail + " → 사전등록 충족(승격 아님 — 별도 승인·게이트 절차)", None
    if auc_pass and not money_pass:
        return ("노이즈",
                detail + " → AUC 만 오른 후보(돈 미검출): 스킬 최우선 규칙대로 **승격 금지**로 기록", None)
    return "노이즈", detail + " → 사전등록 미충족", None


def parse_sns_money_control(path, mtime_floor) -> dict:
    """SNS 점수/유동성 대조 top-k 돈 지표(scripts/sns_ic_probe.py --money). CG248·CG250.

    스키마: {generated_at, since, rows_joined, min_stocks, max_day_chg_pct,
             result:{<feat>:{fwd1:{n_dates,mean_ic,t,...}}},
             money:{fee_roundtrip_pct,min_value_krw,max_day_chg_pct,result:{<feat>:{k3:{" \
             "n_dates,delta_mean_pct,t,pos_dates_pct,split_half_pct,topk_net_pct,pool_net_pct}}}}}
    ⚠ 모델 AUC 실험이 아니다 — per_exp 를 만들지 않는다(스코어보드가 arm 으로 오독한다).
    """
    if not path:
        return {"error": "요약 경로 없음(--json-out 미지정)"}
    if not os.path.exists(path):
        return {"error": "요약 파일 없음"}
    mt = os.path.getmtime(path)
    if mtime_floor and mt <= mtime_floor:
        return {"error": "요약 미갱신(mtime <= 실행 시작 시각) — 옛 결과 오독 방지",
                "summary_mtime": mt, "floor": mtime_floor}
    with open(path, encoding="utf-8") as f:
        try:
            d = json.load(f)
        except json.JSONDecodeError as e:
            return {"error": f"요약 JSON 파싱 실패: {e}", "summary_mtime": mt}
    if not (d.get("money") or {}).get("result"):
        return {"error": "money.result 없음(--money 미지정 또는 집계 실패)",
                "summary_mtime": mt}
    d["summary_mtime"] = mt
    return d


def judge_sns_money_control(item, parsed) -> tuple:
    """SNS 점수(또는 유동성 대조변수) top-k − 풀평균 — 사전문턱으로 판정.

    사전문턱(아이템 judge_spec): Δ ≥ +0.10%p · 짝 t ≥ 2 · 분할표본 앞/뒤 모두 양(+) ·
    세션 ≥ session_min(기본 20). 대조군 판정(spec.role == "control")은 **문턱 판정 대상이 아니라
    '처리군이 대조군을 이기는가'의 비교값**이므로 신호/노이즈 대신 '대조군' 판정을 낸다
    (대조군이 더 크면 그 처리는 정보가 아니다 — CG249 실측: 저관심 +0.46 vs 저거래대금 +1.04).
    """
    if parsed.get("error"):
        return "판정불가", str(parsed["error"]), None
    spec = item.get("judge_spec") or {}
    feat = spec.get("feature")
    ks = spec.get("k") or [5, 10]
    role = spec.get("role") or "arm"
    sess_min = int(spec.get("session_min") or 20)
    res = ((parsed.get("money") or {}).get("result") or {}).get(feat) or {}
    if not res:
        return "판정불가", f"money.result[{feat}] 없음 (keys={list(((parsed.get('money') or {}).get('result') or {}))})", None
    parts, ok_all, first = [], True, None
    for k in ks:
        r = res.get(f"k{k}") or {}
        d = r.get("delta_mean_pct")
        t = r.get("t")
        n = r.get("n_dates") or 0
        halves = r.get("split_half_pct") or [None, None]
        both = all(isinstance(x, (int, float)) and x > 0 for x in halves)
        good = (isinstance(d, (int, float)) and d >= 0.10
                and isinstance(t, (int, float)) and t >= 2
                and both and n >= sess_min)
        ok_all = ok_all and good
        if first is None and isinstance(d, (int, float)):
            first = d
        if not isinstance(d, (int, float)):
            parts.append(f"k={k} 통계 결측")
            continue
        parts.append(f"k={k} Δ {d:+.3f}%p (t {t:+.2f}, 세션 {n}, "
                     f"+세션 {r.get('pos_dates_pct')}%, 분할 {halves}) "
                     f"순기대 top {r.get('topk_net_pct')} vs 풀 {r.get('pool_net_pct')}"
                     + ("" if good else " ✗"))
    detail = (f"{feat}{'(역방향=낮은값 top)' if spec.get('invert') else ''} vs 풀평균"
              f"(무작위 k 기대) · 수수료 왕복 {((parsed.get('money') or {}).get('fee_roundtrip_pct'))}%p"
              f" · 체결성 필터 당일등락<{((parsed.get('money') or {}).get('max_day_chg_pct'))}%"
              f"·거래대금≥{((parsed.get('money') or {}).get('min_value_krw'))}원"
              f" · 표본 {parsed.get('since')}~ · " + " · ".join(parts)
              + " · 사전문턱: Δ≥+0.10%p & t≥2 & 분할 both_positive & 세션≥" + str(sess_min))
    if role == "control":
        return "대조군", detail + " → 처리군과의 비교값(문턱 판정 대상 아님)", first
    if ok_all:
        return "신호있음", detail + " → 선택 규칙 후보(승격은 별도 절차·발행계약 = 리뷰보드)", first
    return "노이즈", detail + " → 사전등록 미충족(선택 규칙 정보 미검출)", first


def parse_source_freshness(path, mtime_floor) -> dict:
    """배선된 피처 원천 신선도(scripts/source_freshness_gate.py --json-out). CG165.

    스키마: {reference:{table,date,lag_trading_days,verdict}, sources:{table:{max_date,
             lag_trading_days, verdict}}, worst:[table, lag, verdict] | None, errors:[]}
    판정 = **최악 원천의 거래일 지연**(lag ≤1 OK · 2~4 WARN · ≥5 CRITICAL) + 기준 자체 지연.
    ⚠ 모델 성능 지표가 아니다 — per_exp 를 만들지 않는다(스코어보드 오독 방지).
    """
    if not path:
        return {"error": "요약 경로 없음(--json-out 미지정)"}
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
    ref = d.get("reference") or {}
    sources = d.get("sources") or {}
    if not sources:
        return {"error": "sources 없음(측정 실패)", "errors": d.get("errors"),
                "summary_mtime": mt}
    worst = d.get("worst")
    lags = {t: (v or {}).get("lag_trading_days") for t, v in sources.items()}
    bad = {t: l for t, l in lags.items() if isinstance(l, int) and l >= 2}
    rlag = ref.get("lag_trading_days")
    if isinstance(rlag, int) and rlag >= 2:
        verdict = f"기준 원천 정지 의심({ref.get('table')} {rlag}거래일 낡음)"
    elif not bad:
        verdict = "충족 — 전 원천 신선(지연 <2거래일)"
    else:
        top_t = max(bad, key=lambda t: bad[t])
        top_l = bad[top_t]
        verdict = (f"신선도 위반({'CRIT' if top_l >= 5 else 'WARN'} {top_l}거래일: {top_t}"
                   f" 최신 {(sources.get(top_t) or {}).get('max_date')})")
    detail = (f"기준 {ref.get('table')}={ref.get('date')}"
              f"(lag {rlag if rlag is not None else '?'})"
              f" · 지연 원천 {len(bad)}/{len(lags)}"
              + ("" if not bad else " " + ", ".join(f"{t}:{bad[t]}" for t in
                                                   sorted(bad, key=lambda x: -bad[x])[:4]))
              + f" · 최악 {(worst[0] + ' ' + str(worst[1]) + '거래일') if worst else '없음'}")
    return {"metric_name": "source_freshness", "reference": ref,
            "lags": lags, "worst": worst, "errors": d.get("errors") or [],
            "verdict": verdict, "detail": detail, "summary_mtime": mt}


def judge_source_freshness(item, parsed) -> tuple:
    """CG165 — 원천 신선도 게이트. 모델 팔 실험이 아니므로 delta=None(무개선 카운터 무관)."""
    if parsed.get("error"):
        return "판정불가", str(parsed["error"]), None
    return (parsed.get("verdict") or "판정불가"), (parsed.get("detail") or ""), None


def judge_robust_auc_se(item, parsed) -> tuple:
    """CG370 — 적립 '판정 가능성' 실측. 모델 팔 실험이 아니므로 delta=None(무개선 카운터 무관)."""
    if parsed.get("error"):
        return "판정불가", str(parsed["error"]), None
    return (parsed.get("verdict") or "측정완료"), (parsed.get("detail") or ""), None


def judge_by_metric(item, parsed, per=None) -> tuple:
    """metric 이름으로 판정기를 고른다(arm 실험은 judge_per, 기준선·게이트는 전용 판정)."""
    kind = _metric_alias(item.get("metric"))
    if kind == "reproducibility_overlap":
        return judge_repro_overlap(item, parsed)
    if kind == "scale_regime_audit":
        return judge_scale_regime_audit(item, parsed)
    if kind == "scale_bisect_probe":
        return judge_scale_bisect_probe(item, parsed)
    if kind == "metric_audit_pair":
        return judge_metric_audit_pair(item, parsed)
    if kind in ("baseline_protocol_gap", "wf_fold_auc", "fold_auc_mean"):
        return judge_baseline_protocol_gap(item, parsed)
    if kind == "score_deciles":
        return judge_score_deciles(item, parsed)
    if kind == "robust_oos_audit":
        return judge_robust_oos_audit(item, parsed)
    if kind == "protocol_noise_floor":
        return judge_protocol_noise_floor(item, parsed)
    if kind == "protocol_noise_curve":
        return judge_protocol_noise_curve(item, parsed)
    if kind == "protocol_pair_anchor":
        return judge_protocol_pair_anchor(item, parsed)
    if kind == "hold_horizon_curve":
        return judge_hold_horizon_curve(item, parsed)
    if kind == "vol_beta_decomp":
        return judge_vol_beta_decomp(item, parsed)
    if kind == "h5_time_profile":
        return judge_h5_time_profile(item, parsed)
    if kind == "capital_path_compare":
        return judge_capital_path_compare(item, parsed)
    if kind in ("h5_regime_exit", "hold_horizon_capital"):
        return judge_h5_regime_exit(item, parsed)
    if kind == "panel_feature_screen":
        return judge_panel_feature_screen(item, parsed)
    if kind in ("screen", "db_feature_screen"):
        return judge_db_feature_screen(item, parsed)
    if kind == "blend_eval":
        return judge_blend_eval(item, parsed)
    if kind == "champion_robust_eval":
        return judge_champion_baseline(item, parsed)
    if kind == "oos_accrual":
        return judge_oos_accrual(item, parsed)
    if kind == "robust_auc_se":
        return judge_robust_auc_se(item, parsed)
    if kind == "date_paired_auc":
        return judge_date_paired_auc(item, parsed)
    if kind == "money_seed_family":
        return judge_money_seed_family(item, parsed)
    if kind == "insample_gap":
        return judge_insample_gap(item, parsed)
    if kind == "horizon_ladder":
        return judge_horizon_ladder(item, parsed)
    if kind == "champion_promote_dryrun":
        return judge_promote_dryrun(item, parsed)
    if kind == "champion_seed_family":
        return judge_seed_family(item, parsed)
    if kind == "anchor_level_auc":
        return judge_anchor_level_auc(item, parsed)
    if kind == "anchor_paired_delta":
        return judge_anchor_paired_delta(item, parsed)
    if kind == "feature_coverage":
        return judge_feature_coverage(item, parsed)
    if kind == "robust_seed_range":

        return judge_robust_seed_range(item, parsed)
    if kind == "robust_paired_seeds":

        return judge_seed_family(item, parsed)
    if kind == "topk_precision":
        return judge_topk_precision(item, parsed)
    if kind == "fillable_topk_expectancy":
        return judge_fillable_topk_expectancy(item, parsed)
    if kind == "fillable_topk_vs_pool":
        return judge_fillable_topk_vs_pool(item, parsed)
    if kind == "fillable_gate_bands":
        return judge_fillable_gate_bands(item, parsed)
    if kind == "fillable_topk_spread":
        return judge_fillable_topk_spread(item, parsed)
    if kind == "money_anchor_repeat":
        return judge_money_anchor_repeat(item, parsed)
    if kind == "rank_ic_money":
        return judge_rank_ic_money(item, parsed)
    if kind == "factor_money_screen":
        return judge_factor_money_screen(item, parsed)
    if kind == "factor_money_fillable":
        return judge_factor_money_fillable(item, parsed)
    if kind == "forward_scorecard":
        return judge_forward_scorecard(item, parsed)
    if kind == "calibration_probe":
        return judge_calibration_probe(item, parsed)
    if kind == "policy_compare":
        return judge_policy_compare(item, parsed)
    if kind == "panel_leak_gate":
        return judge_panel_leak_gate(item, parsed)
    if kind == "regime_ic_screen":
        return judge_regime_ic_screen(item, parsed)
    if kind == "champion_scorecard":
        return judge_champion_scorecard(item, parsed)
    if kind == "intraday_screen":
        return judge_intraday_screen(item, parsed)
    if kind == "sns_money_control":
        return judge_sns_money_control(item, parsed)
    if kind == "source_freshness":
        return judge_source_freshness(item, parsed)
    p = per if per is not None else (parsed.get("per_exp") or {})
    return judge_per(item, p)


def _judge3(res):
    """판정기 반환을 (verdict, detail, delta) 3-튜플로 정규화한다.

    실측(2026-11-19 19:11 tick, rc=1): `judge_date_paired_auc` 만 2-튜플을 돌려주는데
    재판정 경로가 `verdict, detail, delta = judge_by_metric(...)` 로 풀다가 ValueError 로
    **tick 전체가 죽었다**(그 밤 큐 정지). 판정기 반환은 3-튜플로 통일하되, 새 판정기가
    형식을 어겨도 tick 이 통째로 멈추지 않도록 여기서 흡수한다.
    """
    if isinstance(res, (tuple, list)):
        if len(res) >= 3:
            return res[0], res[1], res[2]
        if len(res) == 2:
            return res[0], res[1], None
        if len(res) == 1:
            return res[0], "", None
    return str(res), "", None


def _rejudge_sig() -> str:
    """재판정 코드 서명(모듈 파일 해시) — '같은 판정을 매 틱 다시 쓰는' 낭비를 끊는 기준.

    실측(2026-11-20 21:07 tick): '기준선없음'으로 남은 7건(CG10·CG323·CG329·CG330·CG332·CG336 …)이
    **매 틱 다시 파싱·재판정**되고 있었다. 판정이 똑같으니 원장·백로그는 매시간 재기록되고
    로그에 같은 줄이 14번씩 찍혔다(틱 보고 신호 대비 잡음). 판정기가 바뀌면(파일 해시 변경)
    다시 재판정해야 하므로, 마커에 코드 서명을 함께 남기고 서명이 같을 때만 건너뛴다.
    """
    try:
        with open(__file__, "rb") as f:
            return hashlib.md5(f.read()).hexdigest()[:12]
    except OSError:
        return ""


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
    sig = _rejudge_sig()
    fixed, changed = [], False
    for r in rows:

        _st = r.get("rejudge_stable")
        if isinstance(_st, dict) and sig and _st.get("sig") == sig:
            continue
        pe = r.get("parsed")
        gap = None
        if isinstance(pe, dict) and "parser 없음" in str(pe.get("error", "")):
            gap = "parser"
        elif isinstance(pe, dict) and "conditions.fillable.k 없음" in str(pe.get("error", "")):




            gap = "summary_path"
        elif isinstance(pe, dict) and "요약 미갱신" in str(pe.get("error", "")):



            gap = "summary_path"
        elif isinstance(pe, dict) and "요약 파일 없음" in str(pe.get("error", "")):




            gap = "summary_path"
        elif (str(r.get("verdict", "")) == "기준선없음"
              and isinstance(pe, dict) and isinstance(pe.get("per_exp"), dict)
              and len(pe["per_exp"]) >= 2):


            gap = "judge_pair"
        elif "통계 없음" in str(r.get("detail", "")):


            gap = "judge"
        if not gap:
            continue
        it = next((i for i in b["items"] if i.get("id") == r.get("id")), None)
        if not it or not it.get("command"):
            continue
        parsed = parse_by_metric(it, summary_path(it.get("metric") or "", it.get("command"),
                                                  it.get("summary_out")), 0.0)
        if parsed.get("error"):
            continue
        verdict, detail, delta = _judge3(judge_by_metric(it, parsed))
        unchanged = (verdict == r.get("verdict") and detail == r.get("detail"))
        r["parsed"] = parsed
        r["verdict"], r["detail"] = verdict, detail
        r["rejudged"] = {"ts": now_kst().isoformat(timespec="seconds"),
                         "note": "파서 사후 배선 — 요약 JSON 재파싱으로 판정 교정(재실행 아님)"}
        if unchanged:


            r["rejudge_stable"] = {"ts": now_kst().isoformat(timespec="seconds"), "sig": sig,
                                   "verdict": verdict,
                                   "note": "재판정 결과가 기존 판정과 동일 — 같은 코드 서명에서는 재파싱 생략"}
        else:
            r.pop("rejudge_stable", None)
            fixed.append((r.get("id"), verdict, detail))
        changed = True
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
        pat = ("빌드 중 피처 코드 변경", "체크포인트 무시", "PanelLockBusy", "RuntimeError", "Error", "Traceback")
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


def rc_verdict_from_log(log_path, tail_lines=40):
    """rc 가 **판정을 인코딩**하는 항목(백로그 `rc_is_verdict: true`)에서 로그의 마지막 JSON verdict 를 뽑는다.

    왜(실측 2026-11-24 XR48): 리서처 probe 스크립트는 '가설 채택 → rc 0 / 기각 → rc 1 / 표본부족 → rc 3'
    으로 종료코드에 결론을 싣는다. 구동기는 rc!=0 을 기계적으로 '실행실패(측정값 없음)'로 기록해
    **완주한 33-probe 측정(07:30_이후_발행)이 원장에서 사라졌다**. 예외 흔적도 없는데 실패로 닫히면
    축 종결 근거가 없어져 같은 리서치를 다시 시키게 된다. rc_is_verdict 항목만 이 경로를 탄다
    (일반 항목은 그대로 rc!=0 = 실행실패 — 부분 출력 JSON 을 성공으로 오독하지 않는다).
    """
    if not log_path or not os.path.exists(log_path):
        return None
    try:
        with open(log_path, encoding="utf-8", errors="replace") as f:
            lines = [ln.strip() for ln in f.readlines()[-tail_lines:] if ln.strip()]
    except OSError as e:
        log(f"경고: 로그 읽기 실패({type(e).__name__}: {e}) — rc_is_verdict 채택 불가")
        return None
    for line in reversed(lines):
        if not line.startswith("{"):
            continue
        try:
            d = json.loads(line)
        except json.JSONDecodeError:
            d = None
        if isinstance(d, dict) and d.get("verdict"):
            return d
    return None


def backlog_status_after(rc, cause, gate_rc5, n_attempts):
    """사이클 종료 후 백로그 status 결정(순수 함수 — 자체점검 `_backlog_status_test.py`).

    반환: (status, resume_note|None). status ∈ {'done','pending','failed'}.

    원칙(이 역할의 실측 교훈): **인프라 사고는 가설의 결과가 아니다** — 컨테이너 재생성(137)·
    타임아웃(124)·빌드 중 피처 코드 변경·패널 락 보류(PanelLockBusy)는 'failed' 로 굳히지 않고
    pending 으로 되돌려 다음 틱이 다시 집게 한다(최대 RETRY_MAX 회). 가드/보류를 실행실패로
    남기면 무개선 카운터·'새 레버 필요' 판단이 오염된다.
    """
    cause = cause or ""
    code_churn = rc == 1 and "피처 코드 변경" in cause
    panel_busy = rc == 1 and "PanelLockBusy" in cause
    if rc == 0 or gate_rc5:
        return "done", None
    if (rc in (137, 124) or code_churn or panel_busy) and n_attempts < RETRY_MAX:
        if code_churn:
            return "pending", ("코드 프리즈 후 재빌드 — u3_launcher 프리플라이트가 "
                               "피처 코드 120분 무편집 시 착수")
        if panel_busy:
            return "pending", "패널 락 해제 후 재시도(다른 프로세스가 같은 --panel 보유)"
        return "pending", "체크포인트 재개"
    return "failed", None



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


        return verdict, "요약에 폴드 평균(per_exp)이 없음 — 스키마/경로 불일치 의심", delta







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




    if arm and cf_name not in per:
        _twin = arm[:-4] if arm.endswith("__ff") else (arm + "__ff")
        if _twin in per:
            cf_name = _twin
    base_rec = item.get("baseline")
    best = max(per.items(), key=lambda kv: kv[1]["mean"])
    if arm and arm in per and cf_name in per and cf_name != arm:
        delta = round(per[arm]["mean"] - per[cf_name]["mean"], 4)
        verdict = "신호있음" if delta >= 0.02 else ("악화" if delta <= -0.02 else "노이즈")


        fa, fc = per[arm].get("folds"), per[cf_name].get("folds")
        fw = ""
        if isinstance(fa, list) and isinstance(fc, list) and len(fa) == len(fc) and fa:
            ds = [a - c for a, c in zip(fa, fc)]
            fw = (f" · 폴드 짝승률 {sum(1 for d in ds if d > 0)}/{len(ds)}"
                  f" (Δ폴드 중앙값 {sorted(ds)[len(ds)//2]:+.4f} · 범위 {min(ds):+.4f}~{max(ds):+.4f})")
        detail = (f"가설 {arm} {per[arm]['mean']:.4f} vs 대조군 {cf_name} "
                  f"{per[cf_name]['mean']:.4f} → Δ{delta:+.4f}"
                  f" (같은 런 짝 · 폴드 std {per[arm].get('std', float('nan')):.4f}){fw} "
                  f"(최고: {best[0]} {best[1]['mean']:.4f})")
    elif arm and arm in per and base_rec and base_rec.get("value") is not None:

        base = float(base_rec["value"])
        delta = round(per[arm]["mean"] - base, 4)
        verdict = "신호있음" if delta >= 0.02 else ("악화" if delta <= -0.02 else "노이즈")
        why = "자기대조(대조군이 다른 패널·다른 런)" if cf_name == arm \
            else f"대조군 {cf_name} 이 이 런에 없음"



        strength = ("같은 패널 원본·행 구간만 다름(in-snapshot)" if base_rec.get("same_panel")
                    else "타 패널 대조라 증거 약함")
        detail = (f"가설 {arm} {per[arm]['mean']:.4f} vs 기록 기준선 {base:.4f} "
                  f"({base_rec.get('source', '출처미상')}) → Δ{delta:+.4f} [{why} · {strength}]")
    elif cf_name in per and cf_name != arm:


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
        if arm and arm in per and base_rec:


            verdict = "기준선없음"
            detail = (f"가설 {arm} {per[arm]['mean']:.4f} · 기록 기준선 값 없음"
                      f"(value={base_rec.get('value')!r} · {base_rec.get('source', '출처미상')})"
                      f" — 진단/계측기 항목은 AUC 판정 대상이 아니다")
        else:
            verdict, detail = "기준선없음", (f"최고 {best[0]} {best[1]['mean']:.4f} "
                                        f"(대조군 {cf_name} 미측정 · 기록 기준선 없음)")
    return verdict, detail, delta


def judge_baseline_protocol_gap(item, parsed) -> tuple:
    """같은 런 기준선 재측정의 판정 — 후보 arm 의 **같은 런 폴드 짝 Δ** 만 신호로 인정한다.

    왜(실측 2026-11-14, CG260): 등록 기준선 0.5406 은 누수 판정된 panel_420_asofpatch·구도구 값이고,
    무개선 카운터는 그 값을 분모로 19사이클을 셌다. 같은 도구·같은 패널에서 기준선 arm 을 다시 재면
    '기준선 값 자체의 프로토콜 격차' 와 'arm 델타' 를 분리할 수 있다. 다만 절대값 비교는 금지 —
    패널 교체만으로 폴드 평균이 Δ0.025 움직이므로(panel_prod200 vs panel_420_asphatch 실측) 신호는
    같은 런 짝 Δ 로만 판정한다(사전등록: 평균 ≥ +0.02 · 양(+) 폴드 ≥ n−1).
    기준선 '값' 교체는 리뷰보드 승인 대상이므로 여기서는 격차가 0.02 를 넘는지만 기록한다.

    ⚠ 판정불가로 닫지 말 것: 파서가 arms/pairs 를 못 만들면 그건 '실행실패' 가 아니라 계약 위반이다.
    """
    arms = parsed.get("arms") or {}
    if not arms:
        return "판정불가", f"arm 통계 없음 ({parsed.get('error') or 'results 비어 있음'})", None
    ref = parsed.get("paired_reference") or ""
    pairs = parsed.get("pairs") or {}
    cands = [a for a in (item.get("arm_list") or list(pairs.keys())) if a in arms]
    best_line, best_arm, best_d = None, None, None
    parts = []
    for name in cands:
        a = arms[name]
        line = (f"{name} {a['mean']:.4f}±{a['std']:.4f}"
                f"(폴드 {a['min']:.4f}~{a['max']:.4f} · 승률 {a['fold_win_rate']:.2f})")
        p = pairs.get(name)
        if p:
            line += (f" · 짝Δ vs {p['vs']} {p['mean']:+.4f}"
                     f"(양(+) {p['pos']}/{p['n']} [{', '.join(f'{x:+.4f}' for x in p['deltas'])}])")
            if best_d is None or p["mean"] > best_d:
                best_arm, best_d = name, p["mean"]
        parts.append(line)
        if best_line is None:
            best_line = line
    head = ""
    if ref in arms:
        head = (f"같은 런 기준선 {ref} {arms[ref]['mean']:.4f}±{arms[ref]['std']:.4f}")
        gap = parsed.get("baseline_gap_vs_registered")
        if gap is not None:
            head += (f" vs 등록 {float(parsed['registered_baseline_value']):.4f} → 격차 {gap:+.4f}"
                     f"({'문턱 +0.02 초과 — 기준선 교체 검토 대상' if abs(gap) >= 0.02 else '문턱 미달 — 기준선 유지'})")
        head += " | "
    detail = head + " ; ".join(parts) if parts else head + f"후보 arm 미측정(측정: {', '.join(arms)})"
    if not cands:
        return "기준선없음", detail + " → 후보 arm 없음(기준선 재측정 기록)", None
    verdict = "판정불가"
    if best_d is not None:
        pb = pairs[best_arm]
        if best_d >= 0.02 and pb["pos"] >= pb["n"] - 1:
            verdict = "신호있음"
        elif best_d <= -0.02:
            verdict = "악화"
        else:
            verdict = "노이즈"
        detail += f" → 최고 후보 {best_arm} 짝Δ {best_d:+.4f}"
    else:
        detail += " → 짝 Δ 계산 불가(폴드 키 불일치/기준 arm 미측정)"
    return verdict, detail, best_d


def judge_champion_baseline(item, parsed) -> tuple:
    """배포 챔피언 견고 AUC 실측의 판정 — arm 실험이 아니라 **기준선 실측**이다.

    왜 judge_per 를 쓰지 않는가: 판정 대상이 '가설군 vs 대조군'이 아니라 **승격 게이트가
    비교해야 할 정직한 기준선 숫자**다(하드룰 #1: 단일 분할 AUC 는 승격 기준선으로 쓰지 않는다).
    arm/counterfactual 이 없으므로 judge_per 는 '기준선없음'이라는 무의미한 줄을 남긴다.

    단, **같은 champion_robust_eval 프로토콜로 다른 모델을 잰 항목**(챌린저 vs 챔피언)은 짝 비교가
    성립한다 → 항목에 `counterfactual_value`(같은 프로토콜·같은 창의 대조값)를 주면
    Δ = robust − counterfactual_value 를 계산해 '짝 신호/짝 노이즈'로 판정하고 Δ 를 원장에 싣는다.
    왜(실측 2026-10-01 CG33): 승격 판단에 남은 마지막 공백이 '챌린저를 챔피언과 같은 창에서 재는 것'
    이었는데, 그 값(0.5163 = CG45)은 단일분할 값이 아니라서 종전 문구('기존 승격 기준선 x(단일분할)')를
    붙이면 보고가 거짓이 된다. 대조값이 명시된 항목만 짝 경로로 보낸다(다른 항목 동작 불변).
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
    cf_val = item.get("counterfactual_value")



    if not isinstance(cf_val, (int, float)):
        cm = item.get("counterfactual_model")
        oth = (parsed.get("other_models") or {}).get(cm) if cm else None
        if isinstance(oth, dict) and isinstance(oth.get("robust_auc"), (int, float)):
            cf_val = float(oth["robust_auc"])
            item = dict(item, counterfactual_value=cf_val,
                        counterfactual=f"{cm} (같은 런·같은 창)")
    if isinstance(cf_val, (int, float)):
        delta = float(robust) - float(cf_val)
        cf_name = item.get("counterfactual") or "대조"
        detail += (f" · 같은 프로토콜 대조({cf_name}) {float(cf_val):.4f} → Δ{delta:+.4f}"
                   f" — 문턱 +0.02 미달이면 노이즈로 읽어라")
        return ("짝 신호" if delta >= 0.02 else "짝 노이즈"), detail, delta
    ref = ((item.get("baseline") or {}) if isinstance(item.get("baseline"), dict) else {}).get("value")
    if isinstance(ref, (int, float)):
        detail += (f" · 기존 승격 기준선 {float(ref):.4f}(단일분할) — 프로토콜이 달라 직접비교 금지"
                   f" · 교체는 승인 대상")
    return "기준선 실측", detail, None


def judge_oos_accrual(item, parsed) -> tuple:
    """OOS 적립 판정 — **모델 팔이 아니라 계측기**다(신호/노이즈 어휘를 쓰지 않는다).

    성공조건은 '누적 표본이 문턱 해상도에 도달했는가'다. 왜 문턱을 날짜수로 두는가:
    per-date AUC SD 0.0587 → SE(n)=0.0587/√n 이므로 SE 0.0186(n=10)은 사전문턱 +0.02 와
    같은 크기다. 그 상태에서 '무개선 20사이클'은 모델 무신호가 아니라 **미해상**이다.
    ⚠ verdict 에 '신호' 어휘를 쓰지 않는다 — scoreboard 의 best_validated 판정이
    `"신호" in verdict` 라서 계측기 통과가 '돌파'로 오독된다.
    """
    if parsed.get("error"):
        return "판정불가", parsed["error"], None
    n = parsed.get("union_dates")
    if not isinstance(n, (int, float)) or n <= 0:
        return "판정불가", "누적 날짜수 계산 불가(원장·요약 확인)", None
    se = parsed.get("se_measured") or parsed.get("se_per_date")
    sd_m = parsed.get("sd_measured")
    base = item.get("baseline_dates") or item.get("baseline_union_dates") or 10
    target = item.get("target_dates") or 15
    means = parsed.get("fold_means") or []
    detail = (f"누적 고유 날짜 {int(n)} (원장 {parsed.get('ledger_runs')}회·직전 {base}, 목표 {target})"
              f" · SE(1σ) {se}"
              + (f" (측정 sd {sd_m} · 상수 0.0587 기준 {parsed.get('se_per_date')})" if sd_m is not None else "")
              + f" · 이번 앵커 {parsed.get('window_anchor')} · 견고 AUC "
              f"{parsed.get('robust_auc')}"
              + (f" (창 {len(means)}개 [{', '.join(f'{m:.4f}' for m in means)}])" if means else "")
              + f" · 행 {parsed.get('rows_scored')}"
              + ("" if parsed.get("date_level")
                 else " · ⚠ 날짜 미기록(창키 union = 상한, SE 과소평가 위험)"))
    if n >= target:

        _sd = sd_m if isinstance(sd_m, (int, float)) and sd_m > 0 else PER_DATE_AUC_SD
        need = {"0.010": int(math.ceil((_sd / 0.010) ** 2)),
                "0.007": int(math.ceil((_sd / 0.007) ** 2))}
        detail += (f" → 표본 문턱 도달(SE {se} ≤ 사전문턱 0.02 의 3/4). "
                   f"판정 해상 목표는 SE 0.010 = {need['0.010']}날짜 · SE 0.007 = {need['0.007']}날짜"
                   f" (sd {_sd:.4f} 기준)")
        return "표본 문턱 도달", detail, None
    detail += (f" → 미해상(표본 미증가): SE {se} 가 사전문턱 +0.02 와 동급/이상이라 "
               f"'무개선'을 모델 무신호로 읽을 수 없다")
    return "미해상 — 표본 미증가", detail, None


def parse_money_seed_family(path, mtime_floor) -> dict:
    """시드별 돈 지표 집계(scripts/money_seed_family.py)를 읽는다 — CG355.

    왜 전용인가: fillable_topk_expectancy 는 시드마다 파일이 하나다. 항목의 판정은
    '한 시드가 +0.365'가 아니라 **시드 부호가 유지되는가**이므로, 시드 평균·부호 일치·pooled
    pos/neg 를 한 번에 봐야 한다. 이 파서가 없으면 3시드 실행이 '판정불가(parser 없음)'로 닫힌다
    (CG355 실측: rc=0·3파일 정상 생성인데 결과 소실).
    """
    if not os.path.exists(path):
        return {"error": "요약 파일 없음"}
    mt = os.path.getmtime(path)
    if mtime_floor and mt <= mtime_floor:
        return {"error": "요약 미갱신(mtime <= 실행 시작 시각) — 옛 결과를 새 결과로 오독 방지",
                "summary_mtime": mt, "floor": mtime_floor}
    try:
        with open(path, encoding="utf-8") as f:
            d = json.load(f)
    except json.JSONDecodeError as e:
        return {"error": f"요약 JSON 파싱 실패: {e}", "summary_mtime": mt}
    per_k = d.get("per_k") or {}
    if not per_k:
        return {"error": "per_k 없음(집계 실패 또는 스키마 불일치)", "summary_mtime": mt}
    ks = [k for k, v in per_k.items() if isinstance(v, dict) and isinstance(v.get("mean_delta"), (int, float))]
    if not ks:
        return {"error": "per_k 에 mean_delta 가 있는 k 없음", "summary_mtime": mt}
    best_k = max(ks, key=lambda k: per_k[k]["mean_delta"])
    return {
        "measured_at": d.get("measured_at"), "n_seeds": d.get("n_seeds"),
        "seeds": d.get("seeds"), "ks": ks,
        "per_k": {k: per_k[k] for k in ks},
        "best_k": best_k, "best_mean_delta": per_k[best_k]["mean_delta"],
        "arms": d.get("arms"), "pair": d.get("pair"), "files": d.get("files"),
        "summary_mtime": mt,
    }


def judge_money_seed_family(item, parsed) -> tuple:
    """시드 가족 돈 판정 — **모든 시드가 같은 부호**여야 신호다(단일 시드 금지 하드룰).

    사전등록 문턱(챔피언 대비): 평균 Δ ≥ +0.1%p · 전 시드 양(+) · 평균 t ≥ 2.
    하나라도 어긋나면 '노이즈'로 쓴다 — 실측 CG355: k=3 이 s0 +0.365(t 1.14) 였지만
    3시드로 늘리니 +0.365/+0.171/−0.543, 평균 −0.002 = 시드 잡음이었다.
    """
    if parsed.get("error"):
        return "판정불가", parsed["error"], None
    ks = parsed.get("ks") or []
    if not ks:
        return "판정불가", "k 없음", None
    k = str(item.get("judge_k") or parsed.get("best_k"))
    b = parsed["per_k"].get(k) or parsed["per_k"][parsed["best_k"]]
    mean = b.get("mean_delta")
    detail = (f"k={k} 시드 평균 Δ{mean:+.4f}%p (sd {b.get('sd_delta_across_seeds')}) "
              f"· 시드 양(+) {b.get('n_positive_seeds')}/{b.get('n_seeds')} "
              f"· pooled pos/neg {b.get('pooled_pos')}/{b.get('pooled_neg')} "
              f"· mean t {b.get('mean_t'):+.2f} "
              + (f"· arm 절대 {b['mean_arm_mean']:+.4f}%p (양(+) 시드 {b.get('n_positive_arm_seeds')}/{b['n_seeds']}) "
                 if isinstance(b.get("mean_arm_mean"), (int, float)) else "")
              + " · 시드별 ["
              + ", ".join(f"s{x.get('seed')}: {x.get('delta'):+.3f}(t {x.get('t'):+.2f})"
                          for x in b.get("per_seed") or []
                          if isinstance(x.get("delta"), (int, float)))
              + "]")
    others = [f"k={kk} {parsed['per_k'][kk]['mean_delta']:+.4f}"
              for kk in ks if kk != k]
    if others:
        detail += " · 다른 k [" + ", ".join(others) + "]"
    ok = (isinstance(mean, (int, float)) and mean >= 0.1 and b.get("all_positive")
          and isinstance(b.get("mean_t"), (int, float)) and b["mean_t"] >= 2)
    if ok:
        return "신호있음", detail + " → 전 시드 양(+)·문턱 충족(승격은 dry-run 뒤 별도 판단)", mean
    if isinstance(mean, (int, float)) and mean <= -0.1:
        return "악화", detail + f" → 후보가 챔피언보다 열세(문턱 −0.1%p 이하)", mean
    why = []
    if not b.get("all_positive"):
        why.append(f"시드 부호 불일치 {b.get('n_positive_seeds')}/{b.get('n_seeds')}")
    if isinstance(mean, (int, float)) and mean < 0.1:
        why.append("평균 Δ < +0.1%p")
    if isinstance(b.get("mean_t"), (int, float)) and b["mean_t"] < 2:
        why.append(f"t {b['mean_t']:+.2f} < 2")
    return "노이즈", detail + " → " + " · ".join(why) + " (단일 시드 이득은 시드 잡음)", mean


def _attempts_list(it):
    """백로그 항목의 `attempts` 를 리스트로 정규화해 돌려준다.

    실측(2026-10-01 00:02 CG38): 항목이 `"attempts": 0`(정수)으로 등록돼 있어
    `it.setdefault("attempts", []).append(...)` 가
    `AttributeError: 'int' object has no attribute 'append'` 로 죽었다 — 그런데
    원장 기록(append_ledger)은 그 **앞**에서 이미 끝난 뒤라, 겉으로는 '기록 없이 죽음'이
    아니라 **원장엔 rc=0 이 있고 백로그는 pending** 인 반쪽 상태가 된다 → 다음 틱이 이미
    끝난 46분짜리 실험을 다시 집어 든다(체크포인트 덕에 훈련은 재사용되지만 평가·판정 반복,
    attempts 카운터 어긋남, 상태 보고 오염). 스키마가 int/list 로 갈려 있으므로 쓰기 직전에
    여기서 정규화한다(None·0 → 빈 리스트, 그 밖의 값은 `attempts_legacy` 로 보존).
    """
    a = it.get("attempts")
    if not isinstance(a, list):
        it["attempts"] = []
        if a not in (None, 0):
            it["attempts_legacy"] = a
    return it["attempts"]



def _panel_snapshot_mismatch(run_log) -> str:
    """실행 로그에서 '패널 하드 불일치' 줄을 찾는다(없으면 "").

    왜: build_panel 은 완성 패널을 재사용할 때 요청 조건과 사이드카 메타를 비교해
    `⚠ panel meta 불일치(스냅샷이 다름) ...` 를 남긴다. 그런 런의 숫자는 **요청한 조건의
    측정이 아니다**(2026-11-12 CG199: 앵커를 옮긴 줄 알았는데 같은 패널 → CG195 와 비트동일,
    그런데 판정은 '노이즈'로 기록됐다). 판정 단계에서 이 줄을 보면 '노이즈'가 아니라
    '측정무효'로 남긴다 — 노이즈는 '측정했는데 신호 없음'이고 이건 '측정이 아니다'.
    """
    try:
        with open(run_log, encoding="utf-8", errors="replace") as f:
            for line in f:
                if "panel meta 불일치" in line:
                    return line.strip()
    except OSError:
        return ""
    return ""


def execute(item, force=False):

    os.makedirs(RUNTIME, exist_ok=True)
    ok, why = guards(force, item)
    if not ok:
        log(f"시작 보류: {why}")





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



    spath = summary_path(item.get("metric") or "", item.get("command"), item.get("summary_out"))
    pre_mtime = os.path.getmtime(spath) if os.path.exists(spath) else 0.0


    mtime_floor = max(pre_mtime, time.time())

    log(f"실행: {item['id']} — {item['title']}")
    log(f"로그: {run_log}")



    eff_command = item["command"]
    _t = command_timeout_seconds(eff_command)
    _need = required_timeout_seconds(item.get("est_minutes"))
    if _need and _t is not None and _t < _need:
        eff_command, _chg, _t, _need = effective_command_for(item)
        log(f"⚠ timeout 자기모순 수리: 명령 timeout {_t}s < 필요 {_need}s "
            f"(est_minutes={item.get('est_minutes')}) → {_chg} 로 상향")
        log(f"  효과 명령: {eff_command}")




    _BUILD_TOKENS = ("retrain_champion", "wf_wave", "rebuild_panel", "build_training_features")
    if any(t in eff_command for t in _BUILD_TOKENS) and "--checkpoint" not in eff_command:
        log("⚠ 체크포인트 없음: 빌드형 명령에 --checkpoint-path 가 없다 — 컨테이너 재생성·timeout 으로 "
            "끊기면 진행분이 전량 소실되고 재시도가 0%부터 시작한다(실측 CG346: 23.7% 지점 58분 소실)")



    _pg = prereg_control_gap(item)
    if _pg:
        log(f"⚠ 사전등록 판정불가 위험: {_pg}")
    with open(run_log, "w", encoding="utf-8") as lf:
        lf.write(f"# {item['id']} {item['title']}\n# started {started.isoformat()}\n"
                 f"# command: {item['command']}\n")
        if eff_command != item["command"]:
            lf.write(f"# effective_command: {eff_command}\n")
        lf.write("\n")
        lf.flush()
        proc = subprocess.run(eff_command, shell=True, stdout=lf,
                              stderr=subprocess.STDOUT, cwd=PROJ)
    rc = proc.returncode
    rc_for_status = rc
    cause = ""






    gate_rc5 = False
    if rc == 5 and item.get("metric") == "champion_promote_dryrun":
        _p5 = parse_champion_promote_dryrun(spath, mtime_floor)
        gate_rc5 = not _p5.get("error")

    if rc != 0 and not gate_rc5:

        _rv = rc_verdict_from_log(run_log) if (item.get("rc_is_verdict") and rc == 1) else None
        if _rv is not None:
            parsed = {"rc": rc, "verdict_raw": _rv.get("verdict"), "rc_is_verdict": True,
                      "summary_mtime": os.path.getmtime(spath) if os.path.exists(spath or "") else None}
            verdict = str(_rv["verdict"])
            _side = [f"{k}={v}" for k, v in _rv.items() if k != "verdict"][:8]
            detail = f"rc={rc} 는 판정 인코딩(측정 유효·실행실패 아님) — 로그 JSON 채택 · " + " · ".join(_side)
            delta = _rv.get("delta") if isinstance(_rv.get("delta"), (int, float)) else None
            per = {}
            rc_for_status = 0
        else:
            cause = failure_cause(rc, started, run_log)
            parsed = {"error": "실행 실패 — 측정값 없음", "rc": rc, "cause": cause,
                      "summary_mtime": os.path.getmtime(spath) if os.path.exists(spath) else None}
            verdict, detail, delta = "실행실패", f"측정값 없음 — {cause}", None
            per: dict = {}
            rc_for_status = rc
    else:




        if item.get("metric") == "oos_accrual" or "$" in (spath or "") or not os.path.exists(spath or ""):



            try:
                _sp2 = summary_path(item.get("metric") or "", item.get("command"), item.get("summary_out"))
            except ValueError:
                _sp2 = ""
            if _sp2 and os.path.exists(_sp2) and os.path.getmtime(_sp2) > mtime_floor:
                spath = _sp2
        parsed = parse_by_metric(item, spath, mtime_floor)




        _pe = parsed.get("per_exp")
        per = _pe if isinstance(_pe, dict) else {}
        verdict, detail, delta = _judge3(judge_by_metric(item, parsed, per))
        if parsed.get("error") and not per:
            detail = parsed["error"]








        if verdict != "실행실패":
            _mm = _panel_snapshot_mismatch(run_log)
            if _mm:
                verdict = "측정무효"
                detail = (f"패널 스냅샷 불일치로 요청 조건의 측정이 아니다 — {_mm[:160]} "
                          f"| 원판정 {detail[:120]}")
                delta = None

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






    try:
        b = load_backlog()
        for it in b["items"]:
            if it["id"] == item["id"]:
                _attempts_list(it).append({
                    "ts": rec["ts"], "rc": rc, "verdict": verdict, "detail": detail,
                    "log": rec["log"], "elapsed_min": rec["elapsed_min"],
                })



                st, resume = backlog_status_after(rc_for_status, cause, gate_rc5, len(it["attempts"]))
                if st == "pending":
                    it["status"] = "pending"
                    it["retry_note"] = (f"{rec['ts']} rc={rc} 소실 → 재시도 "
                                        f"{len(it['attempts'])}/{RETRY_MAX} ({resume})")
                else:
                    it["status"] = st
                it["result"] = {"verdict": verdict, "detail": detail, "delta": delta,
                                "per_exp": per or None, "rc": rc}
        save_backlog(b)
    except Exception as e:  # noqa: BLE001
        log(f"경고: 백로그 갱신 실패({type(e).__name__}: {e}) — 원장 기록은 유지된다. "
            f"{item['id']} 상태를 확인하라(반쪽 상태: 원장 O / 백로그 pending)")
    log(f"종료 rc={rc} 경과 {rec['elapsed_min']}분 → 판정: {verdict} {detail}")
    return 0


def _duplicate_running(item_id):
    """살아있는 **동일 ID** 사이클 pid(없으면 None). start_background 중복 기동 차단용.

    실측(2026-11-17 00:0x): CG306 이 23:07:41·23:08:17 두 번 기동돼 학습 2개가 동시에 돌았다
    (4코어 · load1 ~8). 두 실행 모두 58분 뒤 같은 단계(오타 플래그)에서 rc=2 로 죽어 CPU 를
    2배로 낭비하고 원장에 실행실패가 2건 남았다. 원인은 `--start --force` — --force 는
    '가드 무시'지 '같은 실험을 두 번'의 허가가 아니다.
    """
    try:
        with open(STATE, encoding="utf-8") as f:
            st = json.load(f) or {}
    except (OSError, json.JSONDecodeError):
        return None
    pid = st.get("pid")
    if st.get("id") != item_id or not pid:
        return None
    try:
        pid = int(pid)
    except (TypeError, ValueError):
        return None
    return pid if _pid_alive(pid) else None


def start_background(item_id, force=False):
    """nohup 으로 자기 자신을 띄우고 즉시 반환(크론 틱을 붙잡지 않는다)."""
    os.makedirs(RUNTIME, exist_ok=True)
    dup = _duplicate_running(item_id)
    if dup:
        log(f"시작 보류: {item_id} 가 이미 실행 중(pid={dup}) — 중복 기동 금지"
            f"(--force 로도 같은 항목은 두 번 띄우지 않는다)")
        return 3
    cmd = [sys.executable, os.path.abspath(__file__), "--run", item_id]
    if force:
        cmd.append("--force")
    logfile = os.path.join(RUNTIME, f"bg_{item_id}.log")


    with open(logfile, "w", encoding="utf-8") as lf:
        p = subprocess.Popen(cmd, stdout=lf, stderr=subprocess.STDOUT,
                             start_new_session=True, cwd=PROJ)
    with open(PIDFILE, "w", encoding="utf-8") as f:
        f.write(str(p.pid))
    with open(STATE, "w", encoding="utf-8") as f:
        json.dump({"id": item_id, "pid": p.pid, "started": now_kst().isoformat(timespec="seconds")}, f)



    time.sleep(3)
    refused = False
    try:
        with open(logfile, encoding="utf-8") as fh:
            refused = "시작 보류" in fh.read()
    except OSError:
        pass
    if p.poll() is not None or refused:





        started_at, done_ts = None, None
        try:
            with open(STATE, encoding="utf-8") as f:
                started_at = (json.load(f) or {}).get("started")
        except (OSError, json.JSONDecodeError):
            pass
        for r in load_ledger():
            if r.get("id") == item_id and (not started_at or r.get("ts", "") >= started_at):
                done_ts = r.get("ts")
        for f in (PIDFILE, STATE):
            try:
                os.remove(f)
            except OSError:
                pass
        if done_ts and not refused:
            log(f"즉시 완료: {item_id} — 3초 안에 끝나 원장 기록({done_ts})을 남겼다"
                f"(짧은 항목). '실행 중'으로 세우지 않는다")
            return 0
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
        spath = summary_path(it.get("metric") or "", it.get("command"), it.get("summary_out"))
    except ValueError as e:
        log(f"ingest 불가: {e}")
        return 2
    parsed = parse_by_metric(it, spath, 0.0)
    per = parsed.get("per_exp") if isinstance(parsed.get("per_exp"), dict) else {}
    if parsed.get("error") and not per:
        log(f"ingest 실패: {parsed['error']} (파일 {os.path.relpath(spath, PROJ)})")
        return 3
    verdict, detail, delta = _judge3(judge_by_metric(it, parsed, per))
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
            _attempts_list(x).append({
                "ts": rec["ts"], "rc": 0, "verdict": verdict, "detail": detail,
                "log": log_rel or "", "elapsed_min": None, "ingested": True,
            })
            x["status"] = "done"
            x["result"] = {"verdict": verdict, "detail": detail, "delta": delta,
                           "per_exp": per or None, "rc": 0}
    save_backlog(b)
    log(f"ingest {item_id}: {verdict} — {detail}")

    for w in (parsed.get("windows") or []):
        if not isinstance(w, dict):
            continue
        log(f"  창{w.get('fold')}: {w.get('auc_mean')} (std {w.get('auc_std')}) [{w.get('window')}]")
    for name, v in per.items():
        log(f"  {name}: {v.get('mean')} [{v.get('desc')}]")
    log(f"원장 기록 완료(ts={rec['ts']}, reported=False → 다음 틱이 보고한다). "
        f"측정 시각(요약) {parsed.get('measured_at')}")
    return 0



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


def money_baseline_note(path=None):
    """배포 챔피언의 **돈 기준** 실측 한 줄 (참고용 — 기준선·판정 대체 아님).

    WHY(2026-10-09, 엔지니어 자율): 틱 헤드라인(`north_star`)은 스코어보드 best_robust =
    등록 기준선 태그와 맞는 arm 중 최고값인데, 그 arm 은 (a) 배포 불가(rank 변환 = 종목 단위
    스트리밍 추론에서 재현 불가) (b) 누수 패널(panel_420_asofpatch) 값일 수 있다. 즉 19사이클
    '무개선' 경보의 인용 숫자와 **실제 배포 챔피언의 돈 기준 성적**은 다른 자로 잰 값이다.
    실측(2026-10-09 04:0x): 스코어보드 헤드라인 0.5519(AUC) vs 배포 챔피언 돈 기준
    robust_auc 0.4908 · 순기대 −0.182%p/세션(t −0.81) — 후자가 매매에 걸리는 숫자다.
    그래서 매 틱에 후자를 함께 찍는다(값을 바꾸지 않는 표시 전용 — 기준선·카운터 불변).
    """
    p = path or os.path.join(PROJ, "services/xgboost-ml/app/models/champion/robust_oos.json")
    try:
        with open(p, encoding="utf-8") as f:
            d = json.load(f)
    except (OSError, json.JSONDecodeError):
        return "  돈 기준(배포 챔피언): robust_oos.json 없음 — 돈 지표 미측정"
    h = d.get("halves") or {}
    age = ""
    try:
        dt = datetime.fromisoformat(d["created_at"])
        now = now_kst()
        if dt.tzinfo is None and now.tzinfo is not None:
            dt = dt.replace(tzinfo=now.tzinfo)
        age = f" · age {(now - dt).total_seconds() / 86400:.1f}d"
    except (KeyError, TypeError, ValueError):
        pass
    return ("  돈 기준(배포 챔피언·참고, 판정 아님): 로버스트 {ra} · 순기대 {ex}%p"
            "(t {t}) · {ns}세션/{nt}거래 · 앞 {fp}/뒤 {bp}({st}){age}").format(
        ra=d.get("robust_auc"), ex=d.get("expectancy_pct"), t=d.get("expectancy_t"),
        ns=d.get("n_sessions"), nt=d.get("n_trades"),
        fp=h.get("front_pct"), bp=h.get("back_pct"), st=h.get("stable"),
        age=age)


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
    pair = last.rsplit("Build progress:", 1)[-1].strip().split()[0]
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
        "reported": True,

        "reported_at": now_kst().isoformat(timespec="seconds"),
    }
    append_ledger(rec)


    b = load_backlog()
    status = "?"
    for it in b["items"]:
        if it["id"] == orphan:
            _attempts_list(it).append({
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





        detail = r.get("detail") or r.get("note") or "(근거 미기록)"
        print(f"=== 결과 도착: {r.get('id')} — {r.get('title')}")
        print(f"  rc={r.get('rc')} 경과 {r.get('elapsed_min')}분 판정={r.get('verdict')}")
        print(f"  근거: {detail}")
        per: dict = (r.get("parsed") or {}).get("per_exp") or {}
        for name, v in per.items():


            print(f"    {name}: 폴드 평균 {v.get('mean')} std {v.get('std', '-')} "
                  f"(min {v.get('min', '-')} max {v.get('max', '-')}) "
                  f"폴드승률 {v.get('fold_win_rate', '-')} {v.get('folds', [])}")
        print(f"  로그: {r.get('log') or '(없음)'}")


HANDOFF_KEYS = ("from_research", "from_trader", "handoff_from")


def handoff_items(backlog):
    """역할 간 핸드오프로 넘어온 항목(XR*/from_*)을 고른다 — **아직 실행 큐에 없는 것만**.

    왜 필요한가(실측 2026-10-01): 리서처가 넘긴 12건이 `status="backlog"` 로 들어오는데
    구동기의 next_item 은 `pending` 만 본다 → 승격 장치가 없으면 영원히 안 돈다.
    트레이더 보고의 `handoffs_unfilled 13/16` 이 이 사각지대의 숫자다.
    """
    out = []
    for i in backlog.get("items", []):
        if i.get("status") not in ("backlog", "needs_setup"):
            continue
        iid = str(i.get("id", ""))
        if iid.startswith("XR") or any(k in i for k in HANDOFF_KEYS):
            out.append(i)

    def _key(it):
        iid = str(it.get("id", "XR999"))
        num = "".join(ch for ch in iid if ch.isdigit())
        return (int(it.get("priority", 99)), int(num) if num else 999)
    out.sort(key=_key)
    return out


def promote_handoffs(backlog, limit=1):
    """핸드오프 대기 항목을 실행 큐로 승격한다(틱당 최대 limit 건).

    command 가 있으면 `pending`(바로 실행), 없으면 `needs_setup`(규칙 6 — 구동기가 셋업을
    구현해 pending 으로 올린다). 한 틱에 하나씩만 올려 실행 큐를 뒤엎지 않는다. 멱등:
    이미 pending 인 항목은 건드리지 않는다.
    """
    changed = []
    for i in handoff_items(backlog):
        if i.get("status") != "backlog":
            continue
        i["status"] = "pending" if i.get("command") else "needs_setup"
        i["promoted_from"] = "handoff"
        i["promoted_at"] = now_kst().isoformat(timespec="seconds")
        changed.append((i["id"], i["status"]))
        if len(changed) >= limit:
            break
    return changed


def _scorecard_generated_at(path):
    """성적표 JSON 의 `generated_at`(내용 기준 시각). 부재·손상·미상 필드는 None."""
    try:
        with open(path, encoding="utf-8") as f:
            d = json.load(f)
    except Exception:                               # noqa: BLE001 — 부재·손상은 mtime 축으로 폴백
        return None
    if not isinstance(d, dict):
        return None
    return _parse_ts(d.get("generated_at"))


def refresh_scorecard_if_stale(max_age_hours=6.0):
    """배포 챔피언 검증 성적표가 낡았으면 재생성한다(트레이더 계약 2번 — MT49 후속).

    실측(2026-10-06): `data/reports/model_engineer_scorecard.json` 이 2026-10-05T03:16:51 값
    (promote_dryrun = CG9b @10-02 · candidate_auc 0.6173)으로 굳어 있었는데, 트레이더 틱은 그
    파일에 필드가 있으면 계약 2 충족으로 읽어 **낡은 값을 최신으로 오보**했다. 성적표는
    `champion_scorecard.py` 를 아무도 주기적으로 돌리지 않아 생성 시점 이후로 갱신되지 않는다
    → 틱이 스스로 갱신한다(파일 읽기만 하는 스크립트라 수 초 — 비블로킹 유지).

    실측(2026-10-07, 트레이더 환류 '계약2 라벨/내용 시각 불일치'): **mtime 축만 쓰면 git 트래킹
    파일에서 오보가 난다** — 이 성적표는 추적 파일이라 checkout·restore·일괄 복원이 **내용 변화
    없이 mtime 만 갱신**한다(실측: 내용 generated_at=2026-10-06T22:06 인데 mtime=10-07 15:11 →
    틱이 'age 1.8h 최신' 으로 읽어 재생성을 건너뛰고, 트레이더는 17h 낡은 내용을 최신 라벨로
    받았다). → 낡음 판정의 기준 시각은 **내용(generated_at)** 으로 두고, 내용을 못 읽을 때만
    mtime 으로 폴백한다.

    낡음 판정은 두 축이다: ① 내용(generated_at — 없으면 mtime) 경과(max_age_hours)
    ② **원장 최신 기록보다 오래됨**(자정 직후처럼 6h 이내여도 promote_dryrun 이 옛 기록을 가리킨다).

    반환: 사람이 읽을 한 줄(항상 문자열 — 틱 출력에 성적표 상태가 보이게).
    """
    try:
        mtime = os.path.getmtime(SCORECARD)
    except OSError:
        mtime = 0.0
    gen = _scorecard_generated_at(SCORECARD)
    if gen is not None:
        anchor, anchor_label = gen.timestamp(), f"내용 {gen.isoformat(timespec='seconds')}"
    else:
        anchor = mtime
        anchor_label = ("mtime " + datetime.fromtimestamp(mtime).isoformat(timespec="seconds")
                        if mtime else "기준 없음")
    age_h = (time.time() - anchor) / 3600.0 if anchor else None
    newest = 0.0
    for r in load_ledger(limit=50):
        ts = _parse_ts(r.get("ts"))
        if ts is not None:
            newest = max(newest, ts.timestamp())
    content_stale = newest > (anchor + 60)
    if age_h is not None and age_h <= max_age_hours and not content_stale:
        return f"성적표: 최신({anchor_label} · age {age_h:.1f}h)"
    try:
        proc = subprocess.run(
            [sys.executable, os.path.join(PROJ, "scripts", "champion_scorecard.py")],
            capture_output=True, text=True, timeout=120,
        )
    except Exception as e:                          # noqa: BLE001 — 성적표 갱신 실패가 틱을 막지 않는다
        return f"성적표 재생성 실패({type(e).__name__}: {e}) — 옛 값을 유지한다"
    if proc.returncode != 0:
        return (f"성적표 재생성 rc={proc.returncode} — 옛 값을 유지한다"
                f"(성적표 부재·손상이 기록을 막지 않는다)")
    try:
        with open(SCORECARD, encoding="utf-8") as f:
            rep = json.load(f)
        pd = rep.get("promote_dryrun") or {}
        return (f"성적표 재생성: dry-run {pd.get('record_id')}@{pd.get('record_ts')} "
                f"status={pd.get('status')} · 폴드 {rep.get('fold_stats', {}).get('n_folds')}개")
    except Exception:                               # noqa: BLE001
        return "성적표 재생성 완료(요약 파싱 실패)"


def _forward_needs_refresh(mtime, now, max_age_hours=20.0) -> bool:
    """전방 성적표를 재생성해야 하는가 — mtime 축만(내용 축 없음: 매일 행이 늘어난다)."""
    if not mtime:
        return True
    return (now - mtime) / 3600.0 > max_age_hours


def _current_version(window) -> str | None:
    """전방 성적표 창(dict)에서 **현행 판본** = 가장 최근 예측 날짜의 model_version.

    날짜→판본 맵(`date_versions`)이 없으면 None(그러면 요약에 현행 슬라이스를 못 붙인다).
    왜 필요한가: pooled/top10 헤드라인은 판본이 섞이면 구판본이 값을 지배한다
    (실측 2026-11-23: h1 혼합 top10 −0.36% vs champ 단독 +0.60%). '지금 배포된 모델'의
    숫자는 최근 날짜 판본이어야 한다(2026-11-23 CG447).
    """
    dv = (window or {}).get("date_versions") or {}
    if not dv:
        return None
    try:
        last = max(dv)
    except (TypeError, ValueError):
        return None
    return dv.get(last)


def _forward_summary_line(path) -> str:
    """전방 성적표 JSON 을 한 줄로 요약(파싱 실패는 예외 없이 문자열로)."""
    try:
        with open(path, encoding="utf-8") as f:
            d = json.load(f)
    except Exception as e:                          # noqa: BLE001
        return f"전방 성적표 파싱 실패({type(e).__name__})"
    res = d.get("result") or {}
    parts = []
    for h in ("h1", "h5"):
        w = res.get(h) or {}
        auc = w.get("pooled_auc")
        top, allr = w.get("top10_ret_mean"), w.get("all_ret_mean")
        bv = w.get("by_version") or {}
        vnote = ""
        cur_note = ""
        if len(bv) > 1:
            seg = "·".join(f"{str(k).split('-')[0]}:{v.get('n_dates')}d"
                           for k, v in sorted(bv.items(),
                                              key=lambda kv: -(kv[1].get("n_dates") or 0)))
            vnote = f" · ⚠판본혼합 {seg}"





            cur = _current_version(w)
            m = (bv.get(cur) or {}) if cur else {}
            if m and m.get("delta_mean_pct") is not None:
                t = m.get("delta_t")
                sp = m.get("split_half_pct") or [None, None]
                cur_note = (f" · 현행[{str(cur).split('-')[0]}]"
                            f" topk {format(float(m['topk_net_mean_pct']), '+.3f')}%"
                            f" vs 풀 {format(float(m['pool_net_mean_pct']), '+.3f')}%"
                            f" Δ{format(float(m['delta_mean_pct']), '+.3f')}%p"
                            f"(t {'n/a' if t is None else format(float(t), '.2f')}"
                            f", {'n/a' if sp[0] is None else format(float(sp[0]), '+.2f')}"
                            f"/{'n/a' if sp[1] is None else format(float(sp[1]), '+.2f')}"
                            f", {m.get('n_dates')}d)")
        parts.append(
            f"{h} n_dates {w.get('n_dates')} · pooled {auc if auc is None else round(float(auc), 4)}"


            f"(daily {w.get('daily_auc_mean') if w.get('daily_auc_mean') is None else round(float(w['daily_auc_mean']), 4)}) · "
            f"top10 {'n/a' if top is None else format(float(top), '+.4%')}"
            f" vs 전체 {'n/a' if allr is None else format(float(allr), '+.4%')}{vnote}{cur_note}")
    return "전방 성적표: " + " | ".join(parts)


def refresh_forward_scorecard_if_stale(max_age_hours=20.0, path=None):
    """배포 경로 **전방(forward)** 성적표가 하루 넘게 낡았으면 재측정한다(읽기 전용, ~14초).

    WHY (2026-10-06 실측): CG75 의 사전등록 판정은 `n_dates ≥ 10` 을 요구하는데, 전방 성적표를
    주기적으로 돌리는 배선이 없었다(크론 추가는 승인 대상). ml_predictions 는 매일 4,340행씩
    쌓이지만 **아무도 계측기를 다시 돌리지 않아** 표본이 늘어나도 판정이 영원히 '표본부족' 에
    머무른다(CG75 는 done 으로 닫혀 재실행되지 않는다). → 틱이 스스로 하루 1회 갱신한다.

    실측 소요 14.1초(2026-10-07, 컨테이너), 출력 파일은 컨테이너 root 소유 → **읽기만** 한다.

    반환: 사람이 읽을 한 줄(항상 문자열).
    """
    p = path or FORWARD_SCORECARD
    try:
        mtime = os.path.getmtime(p)
    except OSError:
        mtime = 0.0
    if not _forward_needs_refresh(mtime, time.time(), max_age_hours):
        age_h = (time.time() - mtime) / 3600.0
        return f"전방 성적표: 최신(age {age_h:.1f}h) — {_forward_summary_line(p)}"
    cmd = ["docker", "exec", "-e", "PYTHONPATH=/app", "stock_xgboost_ml", "python",
           "/app/scripts/forward_scorecard.py", "--out",
           "/app/reports/overnight/forward_scorecard.json"]
    try:
        proc = subprocess.run(cmd, capture_output=True, text=True, timeout=180)
    except Exception as e:                          # noqa: BLE001 — 계측 실패가 틱을 막지 않는다
        return f"전방 성적표 재측정 실패({type(e).__name__}: {e}) — 옛 값을 유지한다"
    if proc.returncode != 0:
        return (f"전방 성적표 재측정 rc={proc.returncode} — 옛 값을 유지한다"
                f"(마지막 줄: {(proc.stderr or proc.stdout or '').strip().splitlines()[-1:] })")
    return _forward_summary_line(p)


def data_axis_readiness_note(promote=True):
    """남은 데이터 축(인트라데이·공매도·뉴스)의 준비도를 DB 실측으로 채점하고, 준비되면 pending 승격.

    WHY: 구동기 규칙 6 은 "pending 이 비면 셋업을 구현해 pending 으로 승격하라"고 하지만, 남은 세
    축의 선행 조건은 **내가 만들 수 없는 수집 데이터**다. 사람이 매 틱 "이제 됐나"를 재확인하는
    대신 문턱을 기계적으로 채점한다(scripts/data_axis_readiness.py). 준비된 축이 나오면 그 항목을
    pending 으로 올려 다음 틱이 착수하게 한다 — 이것이 규칙 6 의 자동화다.
    보조 기능이므로 실패해도 틱을 죽이지 않는다(읽기 전용·예외 흡수).
    """
    try:
        import data_axis_readiness as dar
    except Exception as e:                          # noqa: BLE001
        return [f"  데이터 축 준비도: 점검 불가({type(e).__name__}: {str(e)[:80]})"]
    try:
        pc = dar.panel_codes()
        axes = dar.evaluate(pc)
    except Exception as e:                          # noqa: BLE001
        return [f"  데이터 축 준비도: 점검 실패({type(e).__name__}: {str(e)[:100]})"]
    out = []
    for ax in axes:
        flag = "CLOSED" if ax.get("closed") else ("READY" if ax["ready"] else "BLOCKED")
        detail = " · ".join(f"{k}={v}" for k, v in ax["measured"].items())
        out.append(f"  · {ax['axis']} [{flag}] {detail}")
        for tbl, tr in (ax.get("tracks") or {}).items():
            eta = f" · 20일 도달 추정 {tr['eta_n_dates']}" if tr.get("eta_n_dates") else ""
            out.append(f"      ↳ {tbl}: 봉={tr['median_bars_per_day']} 이력={tr['n_dates']}일 "
                       f"커버={tr['panel_cover_frac']} ({tr['span']})"
                       f"{' READY' if tr['ready'] else ' — ' + tr['blocking']}{eta}")
        if ax["ready"]:
            out.append(f"    → {', '.join(ax['items'])} 착수 가능(대조군·문턱은 항목에 기재됨)")
    if promote and any(a["ready"] for a in axes):
        try:
            b = load_backlog()
            ch = dar.promote(axes, b)
            if ch:
                save_backlog(b)
                for iid, st in ch:
                    out.append(f"  → 데이터 축 자동 승격: {iid} → {st}")
        except Exception as e:                      # noqa: BLE001
            out.append(f"  → 승격 실패({type(e).__name__}: {str(e)[:80]})")
    return out


def source_axis_sweep_note(max_age_h=12, force=False):
    """미배선 원천 전수 스윕 게이트 — "그 4축 말고 더 없나?" 를 기계적으로 답한다.

    data_axis_readiness 는 **이미 등록된** 4축(인트라데이·공매도·뉴스·SNS)만 채점한다.
    수집기가 새 원천을 붙였는데 아무도 모르면 엔지니어는 'pending 없음' 상태로 밤을 넘긴다
    (2026-10-04 전수 스크린은 **사람이 기억으로** 답한 것이다). 이 노트가 그 사각을 막는다.
    비용: 전 테이블 스윕 ~11초 — 틱을 붙잡지 않도록 **증거 파일이 max_age_h 이내면 캐시만 읽는다**.
    보조 기능이므로 실패해도 틱을 죽이지 않는다(읽기 전용·예외 흡수).
    """
    try:
        import source_coverage_sweep as scs
    except Exception as e:                          # noqa: BLE001
        return [f"  미배선 원천 스윕: 점검 불가({type(e).__name__}: {str(e)[:80]})"]
    try:
        age = (time.time() - os.path.getmtime(scs.EVID)) / 3600.0
    except OSError:
        age = None
    try:
        if (not force) and age is not None and age <= max_age_h:
            with open(scs.EVID, encoding="utf-8") as f:
                res = json.load(f)
            tag = f"캐시 {age:.1f}h"
        else:
            res = scs.sweep()
            tag = "신규 실행"
    except Exception as e:                          # noqa: BLE001
        return [f"  미배선 원천 스윕: 실행 실패({type(e).__name__}: {str(e)[:100]})"]
    out = [f"  미배선 원천 스윕({tag}): {scs.summary_line(res)}"]
    if res.get("new_candidates"):
        out.append("    → 새 원천이 게이트를 통과했다: 피처 배선 실험을 등록하라"
                   "(command·counterfactual·success·est_minutes).")
    return out


def source_freshness_note(max_age_h=1.0):
    """배선된 피처 원천의 신선도 게이트 — 감지 전용(2026-10-11 CG165 후속).

    WHY: 구동기의 미배선 스윕은 이름 그대로 **미배선** 원천만 본다. 그런데 실제로 12일간
    조용히 멈춰 있던 것은 **배선된** 원천이었다(실측 2026-10-11: supply_market_features·
    financial_ratio_features 2026-09-29 정지 = 09-30~10-08 6거래일 결손 · macro_features
    09-28 정지, 그동안 다른 원천은 10-08 정상). 원천이 멈추면 서빙 피처가 낡은 값을 as-of 로
    물고 입력이 조용히 바뀐다 — 사용자가 보는 지표에는 아무 흔적이 없다.
    비용 ~6초(psql 8회). 증거가 max_age_h 이내면 재실행 없이 캐시만 읽는다.
    """
    try:
        import source_freshness_gate as sfg
    except Exception as e:                          # noqa: BLE001
        return f"원천 신선도: 점검 불가({type(e).__name__}: {str(e)[:80]})"
    try:
        age = (time.time() - os.path.getmtime(sfg.EVID)) / 3600.0
    except OSError:
        age = None
    def _run_gate() -> str | None:
        proc = subprocess.run(
            [sys.executable, os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                          "source_freshness_gate.py")],
            capture_output=True, text=True, timeout=180, cwd=PROJ)
        if proc.returncode != 0:
            return (f"원천 신선도: 게이트 실행 실패 rc={proc.returncode} "
                    f"({(proc.stderr or proc.stdout).strip()[-100:]}) — 감시 공백(경보 아님)")
        return None

    try:
        if age is None or age > max_age_h:
            err = _run_gate()
            if err:
                return err
        with open(sfg.EVID, encoding="utf-8") as f:
            res = json.load(f)




        try:
            gen = datetime.fromisoformat(str(res.get("generated_at", "")).replace("Z", "+00:00"))
            skew_h = abs((datetime.now(timezone.utc) - gen).total_seconds()) / 3600.0
        except Exception:                            # noqa: BLE001
            skew_h = 1e9
        if skew_h > max_age_h:
            err = _run_gate()
            if err:
                return err
            with open(sfg.EVID, encoding="utf-8") as f:
                res = json.load(f)
    except Exception as e:                          # noqa: BLE001
        return f"원천 신선도: 점검 불가({type(e).__name__}: {str(e)[:80]})"
    return sfg.summary_line(res)


def recover_unrecorded(backlog, max_age_h=12, dry_run=False):
    """**완주했지만 원장에 기록되지 않은** 사이클을 요약 JSON 에서 복구한다.

    왜(실측 2026-11-11 21:35 CG196): 스윕이 완주해 `cg196_const_summary.json` 을 썼는데
    구동기 프로세스가 `append_ledger` **전에** 사라졌고, pidfile·state 도 이미 정리돼 있어
    위 orphan 검사(pidfile 기반)가 아무것도 잡지 못했다. 결과적으로 그날 유일한 실험 결과가
    원장·스코어보드에서 통째로 빠졌다(다음 틱은 pending 만 보므로 영원히 모른다).
    조용한 실패 감지의 **다른 얼굴**이다 — pidfile 없이도 '요약이 갱신됐는데 원장이 없다'는
    증거만으로 잡아야 한다.

    안전장치(오복구 방지) 4중:
      ① 지금 실행 중인 사이클이 있으면 건너뛴다(진행 중 요약을 반쪽으로 읽지 않는다).
      ② 요약 mtime 이 항목 `created_at` **이후**여야 한다 — 과거 사이클이 같은 경로에 쓴
         낡은 요약을 새 결과로 오독하지 않는다.
      ③ 요약 mtime 이 max_age_h 안이어야 한다.
      ④ 그 id 의 원장 기록이 요약 mtime 이후에 이미 있으면 건너뛴다.
    판정은 `parse_by_metric`+`judge_by_metric`(실행 경로와 **같은** 파서·판정기)을 쓴다.
    실행실패(rc≠0)로는 만들지 않는다 — 요약이 완주해 존재하는 것 자체가 rc=0 의 증거다.
    반환: 복구한 (id, verdict, detail) 목록.
    """
    if os.path.exists(PIDFILE):
        return []
    led = load_ledger()
    now = now_kst()
    out = []
    for it in backlog.get("items", []):
        if it.get("status") not in ("pending", "backlog", "needs_setup"):
            continue
        if not (it.get("metric") and it.get("command")):
            continue
        try:
            spath = summary_path(it.get("metric") or "", it.get("command"), it.get("summary_out"),
                                 quiet=True)
        except Exception:
            continue
        if not spath or not os.path.exists(spath):
            continue
        mt = os.path.getmtime(spath)
        if now.timestamp() - mt > max_age_h * 3600:
            continue
        try:
            created = datetime.fromisoformat(str(it.get("created_at"))).timestamp()
        except (TypeError, ValueError):
            continue
        if mt < created - 60:
            continue
        if any(r.get("id") == it.get("id") and _ledger_ts(r) >= mt - 60 for r in led):
            continue
        parsed = parse_by_metric(it, spath, 0.0)
        if parsed.get("error"):
            continue
        try:
            verdict, detail, delta = _judge3(judge_by_metric(it, parsed))
        except Exception as e:
            log(f"복구 판정 실패 {it.get('id')}: {type(e).__name__}: {e}")
            continue
        out.append((it.get("id"), verdict, detail))
        if dry_run:
            continue
        rec = {
            "ts": now.isoformat(timespec="seconds"),
            "id": it.get("id"), "title": it.get("title"),
            "rc": 0, "elapsed_min": None,
            "log": it.get("log") or "",
            "metric": it.get("metric"), "recovered": True,
            "parsed": parsed, "verdict": verdict, "detail": detail,
            "reported": False,
            "note": ("구동기 기록 누락 복구 — 요약 JSON 이 항목 created_at 이후 갱신됐는데 원장 기록이 없었다"
                     "(pidfile·state 정리 후라 orphan 검사가 못 잡는 경우). 재실행 아님, 같은 파서·판정기 사용."),
        }
        if delta is not None:
            rec["delta"] = delta
        append_ledger(rec)
        it["status"] = "done"
        it["outcome"] = detail
        it["finished_at"] = rec["ts"]
        attempts = _attempts_list(it)
        attempts.append({"at": rec["ts"], "recovered": True})
    if out and not dry_run:
        save_backlog(backlog)
    return out


def _ledger_ts(rec):
    try:
        return datetime.fromisoformat(str(rec.get("ts"))).timestamp()
    except (TypeError, ValueError):
        return 0.0


def tick(force=False):
    ns = north_star("engineer")
    if ns:
        print(ns)
    print(money_baseline_note())
    print(f"  런처: {ensure_launcher()}")

    print(f"  {refresh_scorecard_if_stale()}")


    print(f"  {refresh_forward_scorecard_if_stale()}")


    print(f"  {source_freshness_note()}")


    for line in check_undelivered_reports():
        print(line)



    for iid, verdict, detail in rejudge_parser_gap():
        print(f"  재판정(파서 사후 배선): {iid} → {verdict} — {detail[:160]}")



    _hb = load_backlog()


    for _w in timeout_selfcheck_warnings(_hb.get("items")):
        print(f"  ⚠ 백로그 자기모순: {_w}")
    for _w in prereg_selfcheck_warnings(_hb.get("items")):
        print(f"  ⚠ 판정불가 등록: {_w}")
    _promoted = promote_handoffs(_hb)
    if _promoted:
        save_backlog(_hb)
        for _iid, _st in _promoted:
            print(f"  핸드오프 승격: {_iid} → {_st}")




    _hb_items = handoff_items(_hb)
    _hb_backlog = [i for i in _hb_items if i.get("status") == "backlog"]
    _hb_ns = [i for i in _hb_items if i.get("status") == "needs_setup"]
    if _hb_backlog:
        print(f"  핸드오프 대기 {len(_hb_backlog)}건(backlog) — 매 틱 1건씩 큐로 올린다")
    if _hb_ns:
        print(f"  핸드오프 셋업 대기 {len(_hb_ns)}건(needs_setup) — 승격이 아니라 "
              f"셋업 구현이 필요하다: {', '.join(i['id'] for i in _hb_ns[:6])}"
              f"{' …' if len(_hb_ns) > 6 else ''}")
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

                rec, st_new = _record_orphan(orphan, started)
                print(f"  기록: {rec['verdict']} · {rec['detail']} · 백로그 상태 → {st_new}")
                print("  → 원인을 고치고 다시 시작하라(재시도는 구동기가 체크포인트에서 재개한다).")
                return 0


    recd = recover_unrecorded(load_backlog())
    if recd:
        for iid, v, d in recd:
            print(f"=== ♻ 완주했지만 원장에 없던 사이클을 복구: {iid} → {v} · {d}")
        print("  → 요약 JSON 에서 판정해 원장·백로그에 기록했다(재실행 아님). 위 결과를 보고하라.")
        return 0

    led = load_ledger()
    unreported = [r for r in led if not r.get("reported")]
    if unreported:
        _print_results(unreported)


        stamp = now_kst().isoformat(timespec="seconds")
        for r in unreported:
            r["reported"] = True
            r["reported_at"] = stamp
        _rewrite_ledger(led)
        b = load_backlog()
        p = [i for i in b["items"] if i.get("status") == "pending"]
        print(f"=== 남은 pending: {len(p)}건 "
              f"({', '.join(i['id'] for i in sorted(p, key=lambda x: _prio(x))[:4])})")
        print("→ 이 결과를 사용자에게 3부 형식으로 보고하고, 필요하면 다음 가설을 설계하라.")
        return 0




    it = next_item(load_backlog(), force)
    ok, why = guards(force, it)
    if not ok:
        print(f"대기: {why}")
        return 0
    if not it:


        b = load_backlog()
        setup = [i for i in b["items"] if i.get("status") == "needs_setup"]
        setup.sort(key=lambda i: (_prio(i), str(i.get("id"))))
        print("백로그에 실행 가능한 pending 항목 없음 → 셋업을 구현해 pending 으로 승격하라(규칙 6).")
        for i in setup[:4]:
            print(f"  · {i['id']} (prio {i.get('priority')}) {i['title']}")
            if i.get("setup_needed"):
                print(f"    setup_needed: {str(i['setup_needed'])[:280]}")
            if i.get("note"):
                print(f"    note: {str(i['note'])[:200]}")
        if not setup:
            print("  · 셋업 대기 항목도 없음 → 새 가설을 docs/QUANT_MODEL_BACKLOG.json 에 등록하라"
                  "(command·counterfactual·success·est_minutes 를 반드시 채운다).")
        else:
            print("  → 구현이 끝나면 그 항목을 pending 으로 바꾸고 command·counterfactual·success·"
                  "est_minutes 를 채워라(그러면 다음 틱이 착수한다).")



        for line in data_axis_readiness_note():
            print(line)

        for line in source_axis_sweep_note():
            print(line)
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
    for i in sorted(b["items"], key=lambda x: _prio(x)):
        print(f"  [{i['status']:11s}] {i['id']:3s} {i['title']}")
        if i.get("result"):
            r = i["result"]


            if isinstance(r, dict):
                print(f"                 → {r.get('verdict')} {r.get('detail')}")
            else:
                print(f"                 → {r}")
    for _w in timeout_selfcheck_warnings(b.get("items")):
        print(f"  ⚠ 백로그 자기모순: {_w}")
    for _w in prereg_selfcheck_warnings(b.get("items")):
        print(f"  ⚠ 판정불가 등록: {_w}")
    pid = running_pid()
    print(f"실행 중: {pid if pid else '없음'}")
    led = load_ledger(5)
    if led:
        print("최근 원장(5):")
        for r in led:



            print(f"  {r.get('ts', '?')} {r.get('id', '?')} rc={r.get('rc', '?')} "
                  f"{r.get('verdict', '?')} {r.get('elapsed_min', '?')}분 "
                  f"reported={r.get('reported')}")
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
    ap.add_argument("--promote-handoffs", type=int, default=None, metavar="N",
                    help="핸드오프(다른 역할이 넘긴 XR*) 항목 N건을 실행 큐로 승격하고 종료")
    a = ap.parse_args()

    if a.status:
        return status()
    if a.promote_handoffs is not None:
        b = load_backlog()
        pr = promote_handoffs(b, limit=max(1, a.promote_handoffs))
        if pr:
            save_backlog(b)
        for iid, st in pr:
            print(f"{iid} → {st}")
        print(f"남은 핸드오프(실행 전) {len(handoff_items(b))}건")
        return 0
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



        try:
            if _pid_of(PIDFILE) == os.getpid():
                os.remove(PIDFILE)



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

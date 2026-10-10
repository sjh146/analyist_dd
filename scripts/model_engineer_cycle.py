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
# 미전달 감지의 시간 상한. 이보다 오래된 실패 실행은 되돌리지 않는다 — 밤사이 틱 6회분이라
# 재보고 기회는 충분하고, 그보다 오래된 실패를 되살리면 이미 지나간 상태의 결과를 다시 뿌리게 된다.
UNDELIVERED_LOOKBACK_HOURS = 6
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


SCORECARD = os.path.join(PROJ, "data", "reports", "model_engineer_scorecard.json")
# 배포 경로 전방(forward) 성적표 — ml_predictions × 실현 선행수익(CG68/CG75 계측기).
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
        import champion_scorecard as _cs          # 같은 디렉터리(scripts/) — 지연 임포트
        return _cs.validation_block(SCORECARD)
    except Exception as e:                        # 성적표 부재·손상이 기록을 막지 않는다
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
    # reported_at 이 없으면 ts 로 대체한다 — 세션이 원장 플래그만 수동으로 켠 기록도 감지 대상에 넣기
    # 위해서다(실측 2026-10-01 19:07 CG56: reported=True·reported_at=None 으로 남아 감지기 사각지대).
    cand = [r for r in led if r.get("reported") and (r.get("reported_at") or r.get("ts"))]
    if not cand:
        return []
    try:
        hist = _exec_history(cron_job_id())
    except Exception as e:      # sqlite3 부재·DB 이동·스키마 변경 — 본업을 막지 않는다
        return [f"NOTE: 미전달 감지 불가({type(e).__name__}: {e}) — 원장 플래그는 유지"]
    floor = now_kst() - timedelta(hours=UNDELIVERED_LOOKBACK_HOURS)
    # 미전달 판정에 **status 축**을 추가한다. 종전엔 delivery_outcome in (failed, unknown) 만
    # 봤는데, 호스트/세션 종료로 중단된 실행은 delivery_outcome=NULL 이다(실측 2026-10-01:
    # 18:00·19:00 틱이 'Interrupted by shutdown', status=failed·outcome=NULL → 감지기가 못 잡아
    # CG56 결과가 조용히 소실될 참이었다). 진행 중(running)은 종전대로 보류한다.
    hist_norm = []
    for started, status, outcome in hist:      # hist 는 started_at desc = 최신순
        st = _parse_ts(started)
        if st is None or st < floor:
            continue
        undelivered = (outcome in ("failed", "unknown")
                       or (status in ("failed", "unknown") and outcome != "delivered"))
        hist_norm.append((st, undelivered, started, status, outcome))
    # 기록별로 **가장 최근** 실행이 그 기록을 소비했는지로 판정한다. 종전엔 실패 실행마다 독립적으로
    # 훑어서, 나중 실행이 이미 전달한 기록까지 **옛 실패 실행의 창**에 걸려 중복 보고됐다 — 틱이
    # 매시간이라 창 3600s 가 겹친다(실측 2026-10-05: 04:01 failed·05:00 delivered 가 같은 기록의
    # reported_at=05:00:12 를 함께 덮어 06:00 틱이 CG105/MT49/CG106 을 재보고할 참이었다).
    flagged = {}                               # hist_norm 인덱스 -> [기록]
    for r in cand:
        rt = _parse_ts(r.get("reported_at") or r.get("ts"))
        if rt is None:
            continue
        for idx, (st, undelivered, _started, _status, _outcome) in enumerate(hist_norm):
            dt = (rt - st).total_seconds()
            if -REPORT_EARLY_SEC <= dt <= REPORT_WINDOW_SEC:
                if undelivered:
                    flagged.setdefault(idx, []).append(r)
                break          # 최신 실행이 이 기록을 소비했다 → 그 실행의 전달 여부로 확정
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


# ── 저녁 파이프라인(full_pipeline_dd.sh) 동시 실행 가드 ─────────────────────────
# 왜(실측 2026-10-05 20:0x): 파이프라인은 평일 20:00 에 시작해 실측상 21:40~23:20 에 끝난다.
# 그 안에 Phase 2 '챔피언 재학습'(=학습 1건)이 들어 있는데, 시작 시점의 load1 가드는 파이프라인이
# 아직 **수집 단계**일 때 통과한다(실측 그 시각 load1=2.2 < 3.5) → est 130분짜리 실험이 재학습과
# 정면으로 겹쳐 돌면 서로 3~20배 느려지고(같은 패널·config 가 유휴 7s/cell vs 경쟁 155s/cell),
# 컨테이너 timeout 여유가 1.5배뿐인 항목은 그대로 잘려 결과를 잃는다(하드규칙 5 직렬화).
# 짧은 항목(est < PIPELINE_LONG_MIN)은 종전대로 통과시킨다 — 수집 단계는 가볍고, 막으면
# 큐 전체가 몇 시간씩 굶는다.
PIPELINE_SCRIPTS = ("full_pipeline_dd.sh", "evening_pipeline.sh")
PIPELINE_LONG_MIN = 60      # 이 길이(분) 이상은 파이프라인 실행 중에 시작하지 않는다(--force 예외)
# '실행 중'으로 인정할 첫 토큰(인터프리터/래퍼). 그 밖의 첫 토큰(cat·ls …)이 스크립트 이름을
# 인자로 가진 경우는 '읽는 중'이지 '실행 중'이 아니다.
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
                # 셸 -c 의 명령 문자열은 argv 원소 1개다 — 첫 단어만 본다(`cd /repo && … | head` 같은
                # 진단용 셸이 스크립트 이름을 품고 있어도 '실행 중'으로 오탐하지 않기 위해서다).
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
    # ── 저녁 파이프라인 실행 중 + 긴 실험 = 직렬화 위반(하드규칙 5) ──────────────────
    # 위치가 중요하다: eta_blocks 와 **같은 성격**(지금 시작하면 손해 · 기다리면 된다)이라
    # --force 로는 통과시킨다 — u3_launcher.sh 가 20:35~21:00 창에 `--start <ID> --force` 로
    # 긴 항목을 명시적으로 강행하는 설계를 막으면 안 된다. 반대로 틱(force=False)은 막힌다.
    if not force:
        _p, _pname = pipeline_in_flight()
        _est = (item or {}).get("est_minutes") if isinstance(item, dict) else None
        if _p and isinstance(_est, (int, float)) and _est >= PIPELINE_LONG_MIN:
            return False, (f"저녁 파이프라인 실행 중(pid={_p}, {_pname}) — est {_est:g}분 ≥ "
                           f"{PIPELINE_LONG_MIN}분 항목은 시작하지 않음(4코어 직렬화: 수집·챔피언 "
                           f"재학습과 동시 실행 시 서로 3~20배 느려져 컨테이너 timeout 에 잘린다)"
                           f" · 다음 틱에서 재시도")
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
                # 실측(2026-10-05 CG121): 선행 미완 항목을 그냥 pending 으로 올리면 **빈 산출물로
                # 즉시 실패**해 원장에 잡음만 남는다(CG121 은 CG120 덤프가 없으면 즉시 실패).
                # depends_on 이 선언돼 있으면 여기서 조용히 건너뛰고, 선행이 done 이 되는 틱에 시작한다.
                log(f"{i['id']}: 선행 미완({', '.join(missing)}) → 건너뜀")
                continue
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
        # ⚠ 실측 갭 수리(2026-10-02): 종전엔 기본 경로를 **고정**해 돌려줬다. 그런데
        # wf_label_sweep.py 는 `--summary-out <path>` 를 받으므로, 그걸 준 실행은 기본 경로를
        # 건드리지 않는다 → 구동기가 "요약 미갱신 → 실행실패" 로 오판하고(원장에 rc=0 인데
        # 측정값 없음), 반대로 기본 경로를 덮어써 다른 실험의 산출물을 잃는다.
        # champion_robust_eval 에서 같은 함정을 2026-09-29 에 수리했다(_out_arg).
        out = _arg(command or "", "--summary-out")
        if out:
            return _container_path_to_host(out)
        return os.path.join(PROJ, "services/xgboost-ml/reports/overnight/wf_label_sweep_summary.json")
    if kind == "wf_wave_summary":
        # ⚠ 실측 갭 수리(2026-10-03 CG10): CG10 의 command 는 `wf_wave.py` 인데 metric 을
        # `wf_sweep_summary` 로 등록해 두어, 구동기가 **다른 파일**(wf_label_sweep_summary.json,
        # mtime 22:59 = 실행 시작 이전)을 보고 "요약 미갱신 → 판정불가" 로 기록했다. 실제로는
        # wf_wave 가 `reports/overnight/wf_wave_summary.json`(mtime 03:43)에 WF1~WF5 를 써 뒀다
        # → 2.6시간 실행의 실측이 원장에서 사라질 참이었다(CG43 과 같은 'metric 등록 누락' 함정).
        # 스키마는 wf_label_sweep 과 호환(results[].folds{}.mean)이라 parse_wf_sweep 를 재사용한다.
        out = _arg(command or "", "--summary-out")
        if out:
            return _container_path_to_host(out.strip("'\""))
        return os.path.join(PROJ, "services/xgboost-ml/reports/overnight/wf_wave_summary.json")
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
    if kind == "champion_scorecard":
        # 배포 챔피언 검증 성적표(scripts/champion_scorecard.py --out · MT49). 커맨드는 **호스트
        # python3** 로 돌고 산출물도 호스트 경로에 쓴다 → 상대경로는 PROJ 기준으로 푼다.
        # (실측 2026-10-05: 종전엔 _container_path_to_host 로 보내 상대 `data/reports/...` 가
        #  `services/xgboost-ml/data/reports/...` 로 변환돼 '요약 파일 없음 → 판정불가'가 났다.
        #  컨테이너 산출물(--out /app/...)은 이 분기와 무관하다.)
        out = (_arg(command or "", "--out") or "").strip("'\"")
        if not out:
            return SCORECARD
        if out.startswith("/app/"):
            return _container_path_to_host(out)
        return out if os.path.isabs(out) else os.path.join(PROJ, out)
    if kind == "intraday_screen":
        # 인트라데이(분봉) 피처 **사전 스크린**(scripts/intraday_feature_screen.py --json-out). CG128.
        # 데이터 축(CG101)의 첫 측정 — 수집기 결함(XR26)으로 지금은 장마감 30분(15:01~15:30)만 쌓인다.
        # 그 구간만으로도 '종가 동시호가 전 매매압력'이라는 별개 정보를 담는지 먼저 채점한다.
        out = _arg(command or "", "--json-out")
        if out:
            return _container_path_to_host(out.strip("'\""))
        return os.path.join(PROJ, "services/xgboost-ml/reports/overnight/cg128_intraday_screen.json")
    if kind == "champion_seed_family":
        # 다중 시드(유니버스) 짝 판정 집계(scripts/champion_seed_family_agg.py --agg-out). CG50/CG51.
        # 왜 전용 metric 인가(실측 2026-10-01): 같은 모델·같은 창에서 유니버스 정체만 바꿔도
        # 폴드평균이 0.5042~0.5439(std 0.0133)로 움직여 사전문턱 +0.02 가 1.50σ 였다(CG48/49)
        # → 단일 유니버스 Δ 는 증거가 아니다. 시드 5개를 **짝으로** 비교해야 SE 0.006 으로 줄어
        # 문턱이 3σ 위에 선다. 집계기가 만든 단일 JSON 을 이 metric 이 읽는다(--agg-out).
        out = _arg(command or "", "--agg-out")
        if out:
            return _container_path_to_host(out.strip("'\""))
        log("경고: champion_seed_family 인데 커맨드에 --agg-out 이 없다 → 요약 없음(판정불가)")
        return ""
    if kind == "topk_precision":
        # 상위 k 정밀도·실현수익 짝 집계(scripts/topk_precision.py --json-out). CG54.
        # 왜 전용 metric 인가(2026-10-01): AUC 는 순위 지표라 '사전문턱 미달'이 '돈이 안 된다'를
        # 뜻하지 않는다(CG53: Δ+0.0133·t 2.54 로 후보가 챔피언보다 일관되게 높지만 문턱 미달).
        # 트레이더가 실제로 사는 것은 상위 k 뿐이므로 정밀도·실현수익으로 승격/교체를 판단한다.
        # ⚠ per_exp 를 만들지 않는다(scoreboard 오독 방지) — k별 통계는 `kstats` 로 싣는다.
        out = _arg(command or "", "--json-out")
        if out:
            return _container_path_to_host(out.strip("'\""))
        log("경고: topk_precision 인데 커맨드에 --json-out 이 없다 → 요약 없음(판정불가)")
        return ""
    if kind in ("fillable_topk_expectancy", "fillable_topk_vs_pool"):
        # 돈 지표(체결성·수수료 반영 top-k 순기대) — scripts/fillable_topk_expectancy.py --json-out.
        # `fillable_topk_vs_pool` 은 같은 --json-out 을 읽되 판정만 '모델 top-k vs 널(풀 평균)' 로
        # 바꾼 변형이다(2026-10-04 CG96: CG95 는 arm vs arm 만 봐 둘 다 양(+)이라 베타 구분 불가).
        # 왜 전용 metric 인가(2026-10-04 CG95): CG93/CG94 의 top-k 정밀도는 `--restrict-q` 로
        # **실현 선행수익의 꼬리**를 후보집합으로 삼아 측정된 것이라(예측 시점에 알 수 없는 선택)
        # 그 자체로는 매매 가능한 바스켓이 아니다. 이 역할의 최우선 규칙은 "실험은 '돈' 지표로
        # 측정한다"이므로, 전 유니버스 채점(--dump-all) 위에서 세션별 상위 k 동일비중 순기대를
        # 짝으로 재는 전용 계측기를 쓴다. ⚠ per_exp 를 만들지 않는다(scoreboard 오독 방지).
        out = _arg(command or "", "--json-out")
        if out:
            return _container_path_to_host(out.strip("'\""))
        log("경고: fillable_topk_expectancy 인데 커맨드에 --json-out 이 없다 → 요약 없음(판정불가)")
        return ""
    if kind == "rank_ic_money":
        # 세션별 횡단면 **랭크 IC** 검정 — scripts/rank_ic_money.py --json-out (CG113).
        # 왜 전용 metric 인가(2026-10-05): CG111 실측으로 돈 지표(top-k − 풀평균)의 검출 바닥이
        # k=3 에서 sd 0.415 %p/세션임이 드러났다 → CG96~CG112 의 '노이즈' 판정은 '미검출'과
        # 구분되지 않는다. k=3 바스켓(세션당 3종목) 대신 **세션당 수백 종목의 랭크상관**을 쓰면
        # 같은 표본에서 검정력이 한 자릿수 올라간다. IC 는 순기대가 아니라 **필요조건** 검정이다.
        # ⚠ per_exp 를 만들지 않는다(scoreboard 오독 방지).
        out = _arg(command or "", "--json-out")
        if out:
            return _container_path_to_host(out.strip("'\""))
        log("경고: rank_ic_money 인데 커맨드에 --json-out 이 없다 → 요약 없음(판정불가)")
        return ""
    if kind == "factor_money_screen":
        # 고전 팩터(가치·퀄리티·모멘텀·저변동·멀티팩터) **랭킹 정보** 스크린 —
        # scripts/factor_money_screen.py --out (CG115). 왜 전용 metric 인가(2026-10-05):
        # 모델 랭킹의 돈 방향 정보 축이 닫힌 뒤(CG114) 남은 질문은 '정보가 데이터에 없는가,
        # 모델이 못 뽑는가'이고 팩터는 그 대조군이다. 스키마가 다르다(factors{...}).
        # ⚠ per_exp 를 만들지 않는다(scoreboard 오독 방지).
        out = _out_arg(command or "")
        if out:
            return _container_path_to_host(out)
        return os.path.join(PROJ, "services/xgboost-ml/reports/overnight/factor_money_screen.json")
    if kind == "factor_money_fillable":
        # 같은 스크립트의 --fillable 실행(CG116): top-k vs 세션 풀평균(널 기준선) 돈 판정.
        out = _out_arg(command or "")
        if out:
            return _container_path_to_host(out)
        return os.path.join(PROJ, "services/xgboost-ml/reports/overnight/factor_money_screen_fillable.json")
    if kind == "blend_eval":
        # 라벨 다양성 앙상블(scripts/blend_eval.py --out). CG56.
        # 왜 전용 metric 인가(실측 2026-10-01): 요약 스키마가 champion_robust_eval 과 **다르다**
        # (`folds` 없이 `fold_means`/`windows`/`paired`) — 그 metric 이름을 재사용하면 파서가
        # "folds 비어 있음(유효 창 없음)" 으로 판정불가를 내고 rc=0 으로 항목이 done 으로 닫혀
        # 항목의 유일한 산출물(짝 Δ)이 원장에서 사라진다(CG43 과 같은 함정).
        # ⚠ per_exp 를 만들지 않는다(scoreboard 오독 방지) — 시드별 값은 `seeds` 로 싣는다.
        out = _out_arg(command or "")
        if out:
            return _container_path_to_host(out)
        log("경고: blend_eval 인데 커맨드에 --out 이 없다 → 요약 없음(판정불가)")
        return ""
    if kind == "forward_scorecard":
        # 배포 경로 **전방(forward)** 성적표(scripts/forward_scorecard.py --out). CG75.
        # 왜 전용 metric 인가(2026-10-03): 창 기반 champion_robust_eval 은 학습구간이 항상
        # 최신까지라 남는 창이 **학습 이전**뿐이다(CG45/58/61/62) → 전방 검증 수단이 없었다.
        # ml_predictions × 실현 선행수익이 유일한 전방 표본이므로 그 계측기를 metric 으로
        # 배선해 둔다. ⚠ 미배선 상태로 pending 이 되면 구동기가 '판정불가'로 기록하고 rc=0 이라
        # done 으로 닫혀 항목의 유일한 산출물이 사라진다(CG43/CG10 함정) — CG75 의 setup_needed ④.
        # ⚠ per_exp 를 만들지 않는다(scoreboard 오독 방지) — 창별 값은 `windows` 로 싣는다.
        out = _out_arg(command or "")
        if out:
            return _container_path_to_host(out.strip("'\""))
        return os.path.join(PROJ, "services/xgboost-ml/reports/overnight/forward_scorecard.json")
    if kind == "calibration_probe":
        # 확률 보정 계측기(scripts/calibration_probe.py --json-out). CG83.
        # 왜 전용 metric 인가(2026-10-03 CG81 진단): OOS 확률의 과신(최대 +0.1783)·절대문턱
        # 0.55 의 스케일 의존을 실측으로 확인했고, 보정 계층의 효과(Brier·ECE)를 원장에 남기려면
        # Brier/ECE 를 담는 전용 파서가 필요하다(wf_sweep 파서로는 폴드 AUC 만 남아 판정 대상이
        # 사라진다 — CG43/CG10 함정). ⚠ per_exp 는 만들지 않는다(scoreboard 오독 방지).
        out = _arg(command or "", "--json-out")
        if out:
            return _container_path_to_host(out.strip("'\""))
        log("경고: calibration_probe 인데 커맨드에 --json-out 이 없다 → 요약 없음(판정불가)")
        return ""
    if kind == "policy_compare":
        # 소비 문턱 정책 비교 계측기(scripts/policy_compare_probe.py --json-out). CG84.
        # 왜 전용 metric 인가(2026-10-03): CG83 으로 '절대문턱은 확률 보정으로도 성립하지 않는다'
        # 가 확정됐고(보정 후 0.55 초과 37.0%→6.2%), 남은 판단은 '절대문턱 vs 분위(top-k)' 중
        # 무엇이 실제로 돈이 되는가다. 그 값(실현수익 짝 Δ·부호검정)은 폴드 AUC 도 Brier 도 아니라
        # 기존 파서 어디에도 안 담긴다 → 전용 파서가 필요하다(CG43/CG10 함정).
        # ⚠ per_exp 를 만들지 않는다(scoreboard 오독 방지) — k별 통계는 `policy` 로 싣는다.
        out = _arg(command or "", "--json-out")
        if out:
            return _container_path_to_host(out.strip("'\""))
        log("경고: policy_compare 인데 커맨드에 --json-out 이 없다 → 요약 없음(판정불가)")
        return ""
    if kind == "panel_leak_gate":
        # 패널 누수 지문 사전 게이트(scripts/panel_leak_gate.py --json-out). CG85.
        # 하드 판정규칙 ③(누수 게이트)은 '종목 상수 피처가 선별을 지배하면 중단'인데, 그 지문을
        # 실험 **전에** 판정하는 계측기가 없었다 — 실측(2026-10-03)으로 패널 함대가 as-of 수리
        # 시점(2026-10-02) 전후로 갈리는 것을 확인했고(CLEAN: asof3/asof4ev/prod200 ·
        # LEAKY: 150u/995/asofpatch/asof2), 그 판정을 원장에 남기려면 전용 파서가 필요하다.
        # ⚠ per_exp 를 만들지 않는다(scoreboard 오독 방지) — 패널별 판정은 `panels` 로 싣는다.
        out = _arg(command or "", "--json-out")
        if out:
            return _container_path_to_host(out.strip("'\""))
        log("경고: panel_leak_gate 인데 커맨드에 --json-out 이 없다 → 요약 없음(판정불가)")
        return ""
    if kind == "regime_ic_screen":
        # 시장 국면별 랭크 IC 분해 화면(scripts/regime_ic_screen.py --json-out). CG126/CG127.
        # 왜 전용 metric 인가(2026-10-06): 모델측 AUC 축 전부와 돈 축이 닫힌 뒤 남은 유일한
        # '재개 조건'이 XR11 노트의 국면 조건화였다. 스키마가 IC·국면버킷·ΔIC 라 기존 파서에
        # 담기지 않는다(CG43/CG10 함정 방지). ⚠ per_exp 를 만들지 않는다(scoreboard 오독 방지).
        out = _arg(command or "", "--json-out")
        if out:
            return _container_path_to_host(out.strip("'\""))
        log("경고: regime_ic_screen 인데 커맨드에 --json-out 이 없다 → 요약 없음(판정불가)")
        return ""
    if kind == "protocol_noise_floor":
        # 프로토콜 **재현 잡음바닥** (scripts/cg143_noise_floor.sh — 고정 앵커 반복). CG143.
        # 왜 전용 metric 인가(2026-10-09 실측): 창 앵커가 CURRENT_DATE 라 하루만 지나도 표본 창·
        # 유니버스가 통째로 밀린다 → 배포 챔피언(2026-09-24 이후 **모델 불변**)을 같은 계열
        # 프로토콜로 재측정한 13회가 0.4935~0.5448(폭 0.0513), 명시적 동일 프로토콜 1일 차
        # 2회(CG136/139) Δ0.0227 = **사전문턱 +0.02 와 같은 크기**다. 이 값이 문턱보다 크면
        # 그동안의 '신호있음' 판정 다수가 잡음과 구분 불가라는 뜻 → 문턱·프로토콜 재정의 근거.
        # 스키마가 folds/robust_auc 가 아니라 결정성·앵커이동 Δ 라 기존 파서에 안 담긴다.
        # ⚠ per_exp 를 만들지 않는다(scoreboard 오독 방지).
        return os.path.join(PROJ, "services/xgboost-ml/reports/overnight/cg143_noise_floor.json")
    if kind == "protocol_noise_curve":
        # 프로토콜 잡음바닥 **스케일링**(scripts/cg144_noise_curve.sh — 앵커 1·2·5·7거래일). CG144.
        # 왜 전용 metric 인가(2026-10-09): CG143 의 단일 1일 차 Δ(0.0175)만으로는 '이동폭이 커지면
        # 문턱을 넘나'를 알 수 없다 — 그 곡선이 일 단위 판정 가능성(= 문턱 재정의 필요성)을 결정한다.
        # ⚠ per_exp 를 만들지 않는다(scoreboard 오독 방지).
        return os.path.join(PROJ, "services/xgboost-ml/reports/overnight/cg144_noise_curve.json")
    if kind == "protocol_pair_anchor":
        # 앵커 **평균** 짝 Δ 실측(scripts/cg145_pair_anchor.sh — 산출물 접두는 `TAGPFX=` env). CG145/CG146.
        # 왜 전용 metric 인가(2026-10-09 실측): CG143/144 는 '같은 모델의 레벨'이 앵커만 옮겨도
        # 최대 |Δ| 0.0597(사전문턱의 3배) 로 흔들림을 보였다. 남은 질문은 '두 arm 을 같은 앵커에서
        # **짝으로** 재면 그 공통분이 상쇄돼 Δ 가 안정되는가'이고, 그게 승격 판정 문턱의 근거다.
        # 요약 파일명이 실행마다 TAGPFX 로 달라지므로 커맨드에서 뽑는다(고정 경로면 이전 실측을 잃는다).
        # ⚠ per_exp 를 만들지 않는다(scoreboard 오독 방지) — 값이 AUC 자체가 아니라 앵커 잡음이다.
        pfx = _env_arg(command or "", "TAGPFX") or "cg145"
        return os.path.join(PROJ, f"services/xgboost-ml/reports/overnight/{pfx}_pair_anchor.json")
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
    # 2026-10-02(CG59) 하드닝: 두 arm 이 다른 창/폴드 수에서 채점됐다면 Δ 자체가 무의미하다
    # (실측 CG24: 얇은 라벨 arm 이 '표본 부족'으로 2/5 폴드만 측정돼도 판정 요약에는 안 드러났다).
    if p.get("window_mismatch"):
        return "판정불가", f"두 arm 의 채점 창이 다름(Δ 무의미) — {p['window_mismatch'][0]}", dm
    if p.get("fold_mismatch"):
        return "판정불가", f"두 arm 의 폴드 수가 다름(Δ 무의미) — {p['fold_mismatch'][0]}", dm
    if n < 3:
        return "판정불가", f"시드 수 부족({n}<3) — SE 과대. {detail}", dm
    # 기대 시드 수: 항목 커맨드에 `--expect-seeds N` 이 있으면 그 수를 강제한다.
    # WHY: 시드 하나가 중단으로 빠지면 '3/3 신호있음'이 조용히 성립한다(집계기는 통과시킨다).
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
        # ⚠ 실측 갭 수리(2026-10-05 CG122): 종전엔 status·reason 만 실어, 승격을 실제로 막은
        # **돈 기준 게이트의 구조화 증거**(expectancy_gate: pct·sessions·trades·halves·
        # champion_pct)와 라이브 스코어 게이트가 원장에서 통째로 사라졌다(요약 JSON 에는 있는데
        # 원장에는 prose 사유만). 승격/보류 판정의 근거 수치를 원장에서 직접 읽을 수 있어야
        # 다음 역할(트레이더)이 재검증 없이 신뢰할 수 있다.
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
    # ⚠ 실측 갭 수리(2026-10-07 CG134): 비-dry-run 경로는 dry-run 분기 **뒤**에서
    # 라이브 스코어 게이트를 검사한다(champion_promote L~404) → 후보가 AUC·돈 기준을 모두
    # 통과해도 토큰이 blocked 면 status="blocked_live_score" 로 끝난다. 종전 매핑에 이 키가
    # 없어 "판정불가" 로 기록됐다 — 그러면 '게이트가 막았다'는 사실이 원장에서 사라진다.
    verdict = {"would_promote": "게이트 통과(승격후보 생성)",
               "kept_incumbent": "후보 생성·게이트 거부",
               "promoted": "승격됨(비-dry-run)",
               "blocked_live_score": "게이트 통과·라이브 스코어 차단(미승격)",
               "invalid_candidate": "후보 무효"}.get(st, "판정불가")
    return verdict, " · ".join(parts), None


def parse_by_metric(item, spath, mtime_floor=0.0) -> dict:
    """metric 이름으로 파서를 고른다(모르는 metric 은 예외 없이 오류 dict)."""
    kind = item.get("metric")
    if kind in ("wf_sweep_summary", "wf_wave_summary"):
        # 두 요약의 스키마가 같다(results[].folds{}.mean · exp/desc) → 같은 파서를 쓴다.
        return parse_wf_sweep(spath, mtime_floor)
    if kind == "champion_robust_eval":
        return parse_champion_robust(spath, mtime_floor)
    if kind == "champion_promote_dryrun":
        return parse_champion_promote_dryrun(spath, mtime_floor)
    if kind == "champion_seed_family":
        return parse_champion_seed_family(spath, mtime_floor)
    if kind == "topk_precision":
        return parse_topk_precision(spath, mtime_floor)
    if kind in ("fillable_topk_expectancy", "fillable_topk_vs_pool"):
        return parse_fillable_topk_expectancy(spath, mtime_floor)
    if kind == "rank_ic_money":
        return parse_rank_ic_money(spath, mtime_floor)
    if kind == "factor_money_screen":
        return parse_factor_money_screen(spath, mtime_floor)
    if kind == "factor_money_fillable":
        return parse_factor_money_fillable(spath, mtime_floor)
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
    if kind == "protocol_noise_floor":
        return parse_protocol_noise_floor(spath, mtime_floor)
    if kind == "protocol_noise_curve":
        return parse_protocol_noise_curve(spath, mtime_floor)
    if kind == "protocol_pair_anchor":
        return parse_protocol_pair_anchor(spath, mtime_floor)
    return {"error": f"parser 없음 (metric={kind!r})"}


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
                            "money", "money_filter")}
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
        "kstats": d.get("per_exp") or {},     # ⚠ per_exp 키로 올리지 않는다(위 주석)
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
        "rows": d.get("rows"), "n_sessions_fillable": d.get("n_sessions_fillable"),
        "pool_median_fillable": d.get("pool_median_fillable"),
        "conditions": conds, "summary_mtime": mt,
    }


def judge_fillable_topk_expectancy(item, parsed) -> tuple:
    """돈 지표 짝 판정 — 사전 등록(CG95): k=3 **과** k=5 둘 다

      (a) arm(q0.05) 순기대 > 0  (b) 짝 Δ(arm − 대조군) ≥ +0.1%p/세션
      (c) 짝 t ≥ 2              (d) 분할표본 앞/뒤 모두 양(+)
    를 만족하면 '신호있음'. 하나라도 미달이면 '노이즈'(축 종결 근거).

    왜 이 기준인가: (a)(b)(d)는 이 회사의 승격 표준(`champion_promote --require-expectancy`
    = 순기대>0 · 분할표본 both_positive · 챔피언 대비 +0.1%p)과 동일하고, (c)는 표본(세션 80)에서
    +0.1%p 가 잡음과 구분되는지(≈2σ) 보는 장치다. AUC 처럼 폴드 std ±0.03 을 감안한 +0.02 문턱이
    아니라 **돈 단위(%p/세션)** 문턱이라는 점이 다르다 — 이 역할의 최우선 규칙.
    """
    if parsed.get("error"):
        return "판정불가", f"요약 없음/미갱신 — {parsed['error']}", None
    conds = parsed.get("conditions") or {}
    fill = (conds.get("fillable") or {}).get("k") or {}
    unf = (conds.get("unfiltered") or {}).get("k") or {}
    parts, ok_all, first = [], True, None
    for k in ("3", "5"):
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
    if isinstance(unf.get("3"), dict):
        ua = (unf["3"].get("arm") or {}).get("mean")
        ud = (unf["3"].get("paired") or {}).get("delta_mean")
        ref = (f" · 참고(체결성 필터 OFF): k=3 arm {ua:+.3f}%p · Δ{ud:+.3f}"
               if isinstance(ua, (int, float)) and isinstance(ud, (int, float)) else "")
    detail = (f"arm {parsed.get('arm_tag')} vs 대조군 {parsed.get('control_tag')} · "
              f"exit {parsed.get('exit')} h{parsed.get('horizon')} · 수수료 왕복 "
              f"{parsed.get('fee_roundtrip_pct')}%p · 세션 {parsed.get('n_sessions_fillable')} · "
              f"풀 중앙값 {parsed.get('pool_median_fillable')} · " + " · ".join(parts)
              + ref + " · 사전문턱: 순기대>0 & Δ≥+0.1%p & t≥2 & 분할 both_positive")
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
    for k in ("3", "5"):
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
    detail = (f"arm {parsed.get('arm_tag')} · 널=세션 풀 평균(무작위 k 기대) · exit "
              f"{parsed.get('exit')} h{parsed.get('horizon')} · 수수료 왕복 "
              f"{parsed.get('fee_roundtrip_pct')}%p · 세션 {parsed.get('n_sessions_fillable')} · "
              + " · ".join(parts)
              + f" · 풀평균 t {bp.get('t')} · 사전문턱: arm>0 & Δ≥+0.1%p & t≥2 & 분할 both_positive")
    if ok_all:
        return "신호있음", detail + " → 모델이 시장 베타를 넘는 돈 엣지(승격은 별도 절차)", first
    return "노이즈", detail + " → 사전등록 미충족(베타 초과 미검출)", first


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
        "robust_auc": d.get("robust_auc"),                 # 결합 arm 의 시드평균
        "auc_std_across_folds": d.get("auc_std_across_folds"),
        "fold_means": d.get("fold_means"),                 # 시드별 결합 AUC(창=시드)
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
        best = None                      # (|ic|, name, 블록)
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
        # 등록 누락 가드(실측 2026-10-05 CG119): 요약에 짝 블록이 있으면 그 항목은 **짝 판정 대상**이다.
        # 플래그가 없다고 CG115(패널 팩터) 규칙으로 조용히 폴백하면 로그의 "= 모델 우위 없음" 과
        # 원장 verdict("정보있음") 가 어긋난다 → 잘못된 verdict 대신 판정불가로 세우고 등록을 고친다.
        return ("판정불가",
                "항목에 paired_vs_factor 플래그가 없는데 요약에 짝 블록이 있다 — 등록 누락(수리 필요)",
                None)
    if item.get("paired_vs_factor"):
        # CG117 짝 경로: 같은 (code,date) 행에서 ΔIC = IC(모델) − IC(멀티팩터) 를 판정한다.
        # ⚠ IC 는 순기대가 아니다 — 승격 근거가 아니라 'ML 이 팩터 대비 정보 우위가 있는가'의
        #    복잡도 유지 근거 판정이다. per_exp 를 만들지 않는다(scoreboard 오독 방지).
        # 분기 기준은 **항목 플래그**다: 플래그가 있는데 요약에 짝 블록이 없으면(예: --arm-jsonl
        # 없이 실행) CG115 규칙으로 조용히 폴백하지 않고 판정불가로 세운다 — 잘못된 판정 방지.
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


def judge_by_metric(item, parsed, per=None) -> tuple:
    """metric 이름으로 판정기를 고른다(arm 실험은 judge_per, 기준선·게이트는 전용 판정)."""
    kind = item.get("metric")
    if kind == "protocol_noise_floor":
        return judge_protocol_noise_floor(item, parsed)
    if kind == "protocol_noise_curve":
        return judge_protocol_noise_curve(item, parsed)
    if kind == "protocol_pair_anchor":
        return judge_protocol_pair_anchor(item, parsed)
    if kind == "blend_eval":
        return judge_blend_eval(item, parsed)
    if kind == "champion_robust_eval":
        return judge_champion_baseline(item, parsed)
    if kind == "champion_promote_dryrun":
        return judge_promote_dryrun(item, parsed)
    if kind == "champion_seed_family":
        return judge_seed_family(item, parsed)
    if kind == "topk_precision":
        return judge_topk_precision(item, parsed)
    if kind == "fillable_topk_expectancy":
        return judge_fillable_topk_expectancy(item, parsed)
    if kind == "fillable_topk_vs_pool":
        return judge_fillable_topk_vs_pool(item, parsed)
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
    elif arm and arm in per and base_rec and base_rec.get("value") is not None:
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
        if arm and arm in per and base_rec:
            # baseline 은 있는데 value 가 null(진단·계측기 항목) — float(None) 으로 죽으면 원장 기록 없이
            # 사라진다(설계원칙 4 위반). 숫자 AUC 판정 대상이 아님을 정직하게 적는다.
            verdict = "기준선없음"
            detail = (f"가설 {arm} {per[arm]['mean']:.4f} · 기록 기준선 값 없음"
                      f"(value={base_rec.get('value')!r} · {base_rec.get('source', '출처미상')})"
                      f" — 진단/계측기 항목은 AUC 판정 대상이 아니다")
        else:
            verdict, detail = "기준선없음", (f"최고 {best[0]} {best[1]['mean']:.4f} "
                                        f"(대조군 {cf_name} 미측정 · 기록 기준선 없음)")
    return verdict, detail, delta


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

    # ── 백로그 갱신: 여기서 예외가 나도 원장 기록은 이미 남았다 ────────────────────
    # 실측(2026-10-01 00:02 CG38): attempts 가 int 였던 항목에서 append 가 AttributeError 로
    # 죽어 `--run` 프로세스가 통째로 사라졌고, 원장에는 rc=0 기록이 있는데 백로그는 pending 인
    # 반쪽 상태가 됐다(다음 틱이 이미 끝난 실험을 재실행). 원인은 정규화로 고쳤지만, 앞으로
    # 다른 예외가 나도 이 블록이 사이클 전체를 죽이지 않게 감싼다.
    try:
        b = load_backlog()
        for it in b["items"]:
            if it["id"] == item["id"]:
                _attempts_list(it).append({
                    "ts": rec["ts"], "rc": rc, "verdict": verdict, "detail": detail,
                    "log": rec["log"], "elapsed_min": rec["elapsed_min"],
                })
                # 인프라 사고(컨테이너 재생성·타임아웃·빌드 중 피처 코드 변경·패널 락 보류)는
                # 가설의 결과가 아니다 → pending 으로 되돌려 다시 돌린다(최대 RETRY_MAX 회).
                # 결정 로직은 순수 함수로 뽑아 자체점검한다(scripts/_backlog_status_test.py).
                st, resume = backlog_status_after(rc, cause, gate_rc5, len(it["attempts"]))
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
        # 즉시 종료가 **정상 완료**인지 구분한다(실측 2026-10-03, _orphan_record_test [2]):
        # `command` 가 `true` 나 진단 쿼리처럼 3초 안에 끝나는 항목은 자식이 원장에 기록을 남기고
        # 정상 종료한다 — 그런데 종전엔 이를 "가드 거부 또는 즉시 종료"로 **단정**해 ① 운영자에게
        # 거짓 실패를 보고하고 ② 그 항목이 실제로 수행됐는지 알 길이 없었다. 시작 시각 이후의
        # 원장 기록을 확인해 구분한다(가드 거부는 로그에 '시작 보류' 가 있고 원장 기록이 없다).
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
            _attempts_list(x).append({
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
            dt = dt.replace(tzinfo=now.tzinfo)      # 기록은 KST 벽시계(naive)로 쓴다
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
    # 우선순위 → id 숫자 순으로 결정적으로 소비한다(파일 순서에 의존하면 승격 순서가 흔들린다).
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
    content_stale = newest > (anchor + 60)         # 60s 여유(쓰기 순서 경쟁)
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
    if not mtime:                                   # 파일 부재(mtime=0)
        return True
    return (now - mtime) / 3600.0 > max_age_hours


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
        parts.append(
            f"{h} n_dates {w.get('n_dates')} · pooled {auc if auc is None else round(float(auc), 4)} · "
            f"top10 {'n/a' if top is None else format(float(top), '+.4%')}"
            f" vs 전체 {'n/a' if allr is None else format(float(allr), '+.4%')}")
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
        flag = "READY" if ax["ready"] else "BLOCKED"
        detail = " · ".join(f"{k}={v}" for k, v in ax["measured"].items())
        out.append(f"  · {ax['axis']} [{flag}] {detail}")
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


def tick(force=False):
    ns = north_star("engineer")
    if ns:
        print(ns)
    print(money_baseline_note())
    print(f"  런처: {ensure_launcher()}")
    # 성적표(MK49)가 낡으면 여기서 재생성한다 — 트레이더 계약 2번이 옛 값을 최신으로 오보하는 것을 막는다.
    print(f"  {refresh_scorecard_if_stale()}")
    # 전방(forward) 성적표 — 배포 경로의 유일한 전방 검증인데 아무도 주기 실행하지 않아
    # 표본이 늘어도 '표본부족' 에 머물렀다(CG75). 틱이 하루 1회 갱신한다(읽기 전용, ~14초).
    print(f"  {refresh_forward_scorecard_if_stale()}")
    # 이전 틱이 '보고 처리'했지만 크론 실행이 전달 실패한 기록을 되돌린다 → 아래 unreported 블록이
    # 같은 결과를 다시 출력한다(영구 소실 방지). 실측 2026-09-30: 제공자 불통 4시간 동안 U3 소실.
    for line in check_undelivered_reports():
        print(line)
    # 파서가 없어 '판정불가(parser 없음)'로 남은 기록을 요약 JSON 재파싱으로 교정한다
    # (실측 2026-09-30 CG43 — 실행 중 프로세스는 옛 모듈을 들고 돌므로 착수 후 배선한 파서가
    #  반영되지 않는다. 재실행 없이 결과를 살린다).
    for iid, verdict, detail in rejudge_parser_gap():
        print(f"  재판정(파서 사후 배선): {iid} → {verdict} — {detail[:160]}")
    # ── 핸드오프 소비(실측 2026-10-01): 다른 역할이 넘긴 항목은 `backlog` 로 들어오는데
    #    next_item 은 `pending` 만 본다 → 승격 장치가 없으면 영원히 안 돈다(트레이더가 보고한
    #    handoffs_unfilled 13/16 의 정체). 틱당 1건씩 큐에 올리고 남은 수를 항상 보고한다.
    _hb = load_backlog()
    _promoted = promote_handoffs(_hb)
    if _promoted:
        save_backlog(_hb)
        for _iid, _st in _promoted:
            print(f"  핸드오프 승격: {_iid} → {_st}")
    # 실측(2026-10-04 00:0x): handoff_items 는 status in (backlog, needs_setup) 을 모두 세는데
    # promote_handoffs 는 **backlog 만** 승격한다. 그런데 종전 메시지는 합계를 그대로 찍어
    # "매 틱 1건씩 큐로 올린다"고 오보했다 → 11건 전부 needs_setup 인 동안 큐가 전혀 줄지
    # 않는데도 구동기가 스스로 도는 것처럼 보였다(운영자가 '자동 소비 중'으로 오독).
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

    # 후보를 **먼저** 고른 뒤 가드에 넘긴다: 가드 중에는 항목 길이(est_minutes)에 의존하는 것
    # (저녁 파이프라인 동시 실행)이 있어, item 없이 부르면 그 가드가 판단을 못 하고 다음 단계
    # (start_background→execute)에서 rc=3 으로 죽어 틱이 '기동 실패'처럼 보인다(2026-10-05).
    it = next_item(load_backlog(), force)
    ok, why = guards(force, it)
    if not ok:
        print(f"대기: {why}")
        return 0
    if not it:
        # 규칙 6 을 **기계적으로** 만든다: 예전엔 "새 가설을 설계하라" 한 줄만 찍혀서 틱 에이전트가
        # needs_setup·핸드오프를 찾아 헤매거나 그냥 넘어갔다(실측 2026-10-01: 남은 핸드오프 12건).
        b = load_backlog()
        setup = [i for i in b["items"] if i.get("status") == "needs_setup"]
        setup.sort(key=lambda i: (i.get("priority", 99), str(i.get("id"))))
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
        # 데이터 축 준비도 — 남은 3축(인트라데이·공매도·뉴스)의 선행은 **수집 데이터**라 내가 만들 수
        # 없다. 사람이 "이제 됐나"를 매번 확인하는 대신 DB 실측으로 채점하고, 문턱을 넘으면 그 항목을
        # pending 으로 자동 승격한다(자율: 실험 등록). 미달이면 막는 숫자를 남긴다.
        for line in data_axis_readiness_note():
            print(line)
        # 미배선 원천 전수 스윕 — '등록된 4축 말고 더 없나?' 를 기계적으로 답한다(위 노트의 사각).
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

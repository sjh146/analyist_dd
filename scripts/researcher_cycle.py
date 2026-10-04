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
import time
from datetime import datetime, timedelta

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


def _dotenv_get(key):
    """저장소 `.env` 에서 키 하나를 읽는다(자식 프로세스 환경 보강용 폴백).

    `.env` 는 컨테이너용이지만 **자격증명(POSTGRES_USER/PASSWORD/DB)은 호스트도 같은 DB** 이므로
    호스트 좌표로 바꾼 뒤 비어 있는 값만 채우는 데는 안전하다. 값이 이미 있으면 절대 덮지 않는다.
    """
    try:
        with open(os.path.join(PROJ, ".env"), encoding="utf-8") as fh:
            for ln in fh:
                ln = ln.strip()
                if not ln or ln.startswith("#") or "=" not in ln:
                    continue
                k, _, v = ln.partition("=")
                if k.strip() == key:
                    return v.strip().strip('"').strip("'")
    except OSError:
        return ""
    return ""


def _host_db_env():
    """호스트에서 도는 사이클의 DB 좌표를 정규화한다(자식 프로세스에만 영향).

    WHY: 저장소의 `.env` 는 **컨테이너용** 값이다(`POSTGRES_HOST=postgres`, `POSTGRES_PORT=5432`).
    크론 틱의 환경에는 POSTGRES_* 가 없고, 백로그 명령이 `.env` 를 스스로 읽는 스크립트
    (예: `scripts/r16_window_coverage.py::env_from_dotenv`)는 컨테이너 서비스명을 물려받아
    호스트에서 이름해석에 실패한다 — 실측 2026-09-28 06:00 R16: `could not translate host name
    "postgres" to address` → rc=1 '실패'로 원장에 기록(명령 자체는 멀쩡했다). 같은 함정은
    백로그의 모든 'DB 를 읽는' 명령에 잠복한다(R3 만 명령 안에서 export 를 직접 하고 있었다).
    → 호스트 포트 매핑(5434→5432, `docker port stock_postgres` 로 확인)으로 바꿔 물려준다.
    명령 안에서 `export POSTGRES_HOST=...` 를 다시 하면 셸이 그 값을 우선하므로 무해하다.
    """
    env = dict(os.environ)
    if env.get("POSTGRES_HOST", "") in ("", "postgres", "localhost", "0.0.0.0"):
        env["POSTGRES_HOST"] = "127.0.0.1"
    if env.get("POSTGRES_PORT", "") in ("", "5432"):
        env["POSTGRES_PORT"] = "5434"
    # 좌표만 맞추고 자격증명을 비워 두면 'no password supplied' 로 죽는다 — 실측 2026-09-28 15:35
    # R11(build_macro_features.py, 기본값 password="")이 그렇게 rc=1 '미달'로 기록됐다. 명령은
    # 멀쩡했고 **환경만** 비어 있었다(R16 과 같은 함정의 다른 얼굴: 호스트/포트는 고쳤으나 자격증명을
    # 빠뜨렸다). 루프백으로 확정된 경우에만, 그리고 비어 있는 값에만 `.env` 값을 채운다 —
    # 원격 좌표(운영자 명시)에는 손대지 않는다(로컬 자격증명을 원격 DB 에 조용히 쓰는 사고 방지).
    if env.get("POSTGRES_HOST") in ("127.0.0.1", "::1"):
        for key in ("POSTGRES_USER", "POSTGRES_PASSWORD", "POSTGRES_DB"):
            if not env.get(key):
                val = _dotenv_get(key)
                if val:
                    env[key] = val
    # 서비스 자격증명(수집 API 키)도 같은 함정을 만든다 — 실측 2026-09-29 04:00 R19:
    # `kis_supply_backfill.py` 가 `KIS_APP_KEY/KIS_APP_SECRET 미설정` 으로 즉시 종료(rc=2)하고
    # 원장에는 rc=2 '미달'로만 남았다(명령·러너는 정상, **환경만** 비어 있었다 — 토큰 발급 전에 죽어
    # 호출 0회). 백로그 command 가 `.env` 를 직접 source 하는 항목(R3)만 살아남는 구조라
    # 항목마다 손으로 export 를 넣는 방식은 또 빠뜨린다.
    # 좌표와 달리 이 키들은 **대상 호스트를 가리키지 않으므로**(어디에 붙어도 같은 서비스) 루프백
    # 조건과 무관하게 비어 있을 때만 채운다 — 원격 DB 좌표 판정에는 관여하지 않는다.
    for key in ("KIS_APP_KEY", "KIS_APP_SECRET", "KIS_BASE_URL", "KIS_ACCOUNT_NO",
                "DART_API_KEY", "KRX_API_KEY", "ECOS_API_KEY"):
        if not env.get(key):
            val = _dotenv_get(key)
            if val:
                env[key] = val
    return env


HOST_DB_ENV = _host_db_env()


# ── 모니터링 ────────────────────────────────────────────────────────────────
def snapshot(hours=24):
    """DQ 스냅샷 + 차트를 남기고 (rc, stdout) 을 돌려준다. 실패해도 예외로 죽지 않는다."""
    try:
        p = subprocess.run(["/usr/bin/python3", SNAPSHOT, "--hours", str(hours)],
                           capture_output=True, text=True, timeout=600, cwd=PROJ, env=HOST_DB_ENV)
        return p.returncode, (p.stdout or "") + (p.stderr or "")
    except Exception as exc:   # noqa: BLE001 - 모니터링 실패가 수집을 막으면 안 된다
        return 99, f"스냅샷 실행 실패: {exc}"


def snapshot_brief(out):
    """틱 출력용 요약 — 위반/경고 줄과 차트 경로만 뽑는다."""
    # [경로] 는 스크랩 건강 줄 — 사고 때만 뜨는 ★/· 와 달리 **매 틱** 보여야 '모니터링이 살아 있다'가
    #  증거로 남는다(실측 2026-09-28: 11분 실명을 어떤 틱도 보고하지 않아 3시간 뒤에야 발견했다).
    lines = [ln for ln in out.splitlines()
             if ln.strip().startswith(("[WARN]", "[위반]", "★", "·", "[경로]", "차트:"))]
    return lines


# ── check(수치) 판정 ────────────────────────────────────────────────────────
def eval_check(item):
    chk = item.get("check")
    tgt = item.get("check_target") or {}
    if not chk:
        return None, "check 미정의", None
    try:
        p = subprocess.run(chk, shell=True, capture_output=True, text=True, timeout=180, cwd=PROJ,
                           env=HOST_DB_ENV)
    except subprocess.TimeoutExpired:
        return None, "check 타임아웃", None
    nums = re.findall(r"[-+]?\d+(?:\.\d+)?", p.stdout or "")
    if not nums:
        return None, f"check 출력에 수치 없음: {(p.stdout or '').strip()[:80]}", None
    val = float(nums[-1])
    # ⚠ check_target.value 가 수치가 아니면(예: "opnd_yn == 'Y'" 같은 서술 문자열) 종전에는 float() 이
    #   ValueError 로 **스크립트를 통째로 죽였다** — 명령은 실행됐는데 원장 기록 전에 프로세스가 사라져
    #   '기록 없이 죽었다'(R24 실측 2026-10-01 15:35)가 되고 그 틱의 판정이 통째로 소실된다.
    #   구동기는 어떤 항목 정의에도 죽지 않고 '판정불가'로 정직하게 남아야 한다.
    try:
        op, tval = tgt.get("op", ">="), float(tgt.get("value", 0))
    except (TypeError, ValueError):
        return val, (f"{item['id']}: check_target 값이 수치가 아님({tgt.get('value')!r}) → 판정불가 "
                     f"— check 는 수치 하나를 마지막에 출력해야 한다"), None
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


def _merge_authorship_detail(art_msg, art_info, detail):
    """저작 단계 결과를 판정 문구에 합친다(**덮어쓰지 않는다**).

    WHY(실측 2026-09-28 15:36 R11): 산출물이 이미 있어 저작을 건너뛴 실행은 info 에 `skipped` 만 있고
    `exists` 키가 없어 else 로 떨어져 ① 라벨이 `[저작실패]` 로 잘못 찍히고 ② 그 문구가 check 판정
    문구를 **통째로 대체**해 `5 >= 3 → 충족` 이라는 수치 근거가 사라졌다. 원장만 보면 성공이 실패로,
    실패의 근거는 실종으로 보인다 — 자율 루프에서 판정 문구는 유일한 증거다.
    """
    if not (art_msg and art_info):
        return detail
    if art_info.get("skipped"):
        tag = "생략"
    elif art_info.get("exists"):
        tag = ""
    else:
        tag = "실패"
    return f"[저작{tag}] {art_msg} | {detail}"


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
    # 금지 경로 가드(2026-10-04): 이 경로에는 검사가 없어 `authoring.target` 이 실주문·정책 값을
    # 가리켜도 막을 코드가 없었다(오케스트레이터만 검사). 목록은 scripts/protected_paths.py 단일 진실원.
    # fail-closed: 가드를 불러오지 못하면 저작하지 않는다(자율 저작은 드물고, 침묵 사고가 더 비싸다).
    try:
        import protected_paths as _pp
        _blocked = _pp.violation(target)
    except Exception as exc:                                  # noqa: BLE001
        log(f"저작 거부(가드 로드 실패 {exc}) — fail-closed: {item['id']} → {target}")
        return False, f"저작 거부(금지경로 가드 로드 실패: {exc})", {
            "target": target, "needs_human": True, "guard_error": str(exc)[:200]}
    if _blocked:
        log(f"저작 거부(금지 경로): {item['id']} → {target} | {_blocked}")
        return False, f"금지 경로 저작 거부({_blocked})", {
            "target": target, "needs_human": True, "blocked": _blocked}
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


def _resumable_rc(item):
    """러너가 '부분 완료 — 다음 실행에서 재개'를 알리는 종료코드(항목별 선언). 미선언이면 빈 집합."""
    v = item.get("resumable_rc") or []
    if isinstance(v, int):
        v = [v]
    return tuple(int(x) for x in v)


def status_after(item, rc, passed):
    """실행 결과(rc·check 통과) → 백로그 상태.

    WHY(`resumable_rc`, 2026-09-28 실측): 수집 러너는 자기 호출 상한에 걸리면 **스스로 부분 완료**로
    끝난다 — `kis_supply_extend_history.py` 는 hit_limit 시 `return 3`("호출 상한 도달 — 중단, 다음
    실행에서 재개")이다. 그런데 `rc != 0 → failed` 로 적으면 `base.next_item()` 이 **pending 만**
    후보로 보므로 그 항목은 큐에서 영구히 빠진다. 실측: R3 가 343→378종목으로 진행 중이던 실행
    (신규 11,967행 적재·자기신고 일치)이 rc=3 이었다는 이유로 failed 가 되어, 이후 틱들이 R3 를
    다시 집지 않았다(= 점진 수집이 조용히 정지). 그래서 항목이 `resumable_rc` 로 "이 종료코드는
    재개 신호"라고 **선언**한 경우에만 pending 으로 되돌린다. 기본값을 넓히지 않는 이유: 진짜 실패
    (러너 크래시 = rc 1/2)를 부분 완료로 오독하면 이번엔 실패가 조용해진다.
    """
    if item.get("kind") == "investigate":
        return "done" if rc == 0 else "failed"
    if item.get("recurring"):
        # 상시 감시(recurring) 항목은 **어떤 결과에서도 큐에서 사라지지 않는다** — 항상 pending.
        # WHY(2026-09-29 실측): 종전에는 (rc=0 + 통과) 일 때만 pending 을 유지했고,
        # · check 미달이면 아래 `partial`, · 러너 크래시(rc=1/2)면 `failed` 로 적혀
        # **경보가 뜬 그 순간이 감시의 마지막 회차**가 됐다(가장 필요한 시점에 눈이 먼다).
        # 감시 항목의 KPI 는 '한 번 통과'가 아니라 '계속 지켜봄'이다. 실패·미달은 원장(ledger)과
        # 틱 보고가 매 회차 알리므로 상태로 표현할 필요가 없다(정보는 남고 감시는 살아 있다).
        return "pending"
    if rc == 0 and passed:
        return "done"
    if rc == 0:
        # 수집형은 목표 미달이면 partial — 다음 사이클에 이어서 수집한다(점진 수집이 정상).
        return "pending" if item.get("incremental") else "partial"
    if item.get("incremental") and rc in _resumable_rc(item):
        return "pending"
    return "failed"


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
                            stderr=subprocess.STDOUT, cwd=PROJ, env=HOST_DB_ENV).returncode

    val, detail, passed = eval_check(item)
    # 저작 결과를 판정 문구에 합친다 — 저작 실패면 명령도 실패하므로 원인이 한 줄에 남아야 한다.
    # (⚠ eval_check 뒤에 합쳐야 한다: detail 은 그 호출이 만든다 — 앞에서 참조하면 NameError.)
    detail = _merge_authorship_detail(art_msg, art_info, detail)
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
            # attempts 는 list 계약이지만, 손편집·구버전 항목이 **정수**로 남아 있을 수 있다.
            # 실측(2026-10-03 18:00 R28): 백로그에 `"attempts": 0` 이 있어 setdefault 가 0 을 돌려주고
            # `.append` 가 AttributeError 로 죽었다. 이 줄은 append_ledger **뒤**라 원장에는 결과가
            # 남는데 백로그 status·result 는 갱신되지 않았고, traceback 종료로 `--run` 의 pidfile
            # 정리도 스킵돼 고아 running.pid(=hygiene breach 재발)가 남았다.
            # 러너는 계약 위반 데이터를 만나도 죽지 않는다 — 형태를 맞추고 이어간다.
            att = it.get("attempts")
            if not isinstance(att, list):
                att = []
                it["attempts"] = att
            att.append({"ts": rec["ts"], "rc": rc, "detail": detail})
            new_status = status_after(item, rc, passed)
            if new_status == "pending" and rc != 0:
                log(f"{item['id']}: rc={rc} 는 러너가 선언한 재개 신호(resumable_rc) "
                    f"→ status=pending(다음 사이클에서 이어서 수집)")
            it["status"] = new_status
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


# 상시 감시(recurring)의 재실행 간격 하한(분). 틱은 2시간 간격이지만 **시작 틱은 그보다
# 드물 수 있다**(결과 보고 틱이 한 번을 쓰고, 부하 가드가 막으면 또 한 번을 쓴다).
# 실측(2026-09-30 22:0x): 180(=1.5틱)으로 두면 ① 보고 틱이 시작 기회를 삼키던 시절엔
# R21 이 매 시작 틱을 독식(원장 R21 1건/R23 0건), ② 보고 틱 수리 후에도 챔피언 재학습으로
# 시작이 막히면(load1 6.05 > 3.5) 다음 시작 틱에서 R21 이 다시 후보가 돼 뒤 번호 pending 이
# 굶는다. → 최소 2틱(240분) 이상으로 잡아 '한 번 막혀도 다음 틱에 양보'가 성립하게 한다.
# 큐에 다른 실행 가능 항목이 없으면 pick_item 이 감시를 **다시 집으므로**(폴백) 감시 자체는
# 굶지 않는다 — 실제 모니터링(dq_snapshot)은 이 항목과 무관하게 매 틱 돈다.
RECUR_COOLDOWN_MIN = 300


def _recent_recurring_ids(minutes=RECUR_COOLDOWN_MIN):
    """최근 <minutes> 분 안에 **실행을 마친** 항목 id 집합.

    WHY(실측 2026-09-30 20:0x): recurring 항목은 `status_after()` 가 어떤 결과에서도
    status=pending 을 유지한다(경보가 뜬 순간 감시가 죽지 않게 하는 **의도된** 설계 —
    tests/test_researcher_cycle_recurring.py). 그런데 구동기는 `base.next_item()` 으로
    (priority, id) **정렬만** 하므로, 상시 감시가 다른 pending 보다 앞 번호면 매 틱 그 항목만
    뽑혀 **큐 전체가 굶는다**. 실측: R21(prio 6, recurring, KIS 0회 프로브) vs R23(prio 7) —
    원장에 R21 실행 기록 0건·R23 은 영구 pending 이었다(그 전엔 R3·R12(prio 3·4)가 앞에 있어
    가려졌을 뿐, 그들이 done 이 되는 순간 드러난다).
    → 상시 감시는 **순서를 양보**한다: 최근 실행된 항목은 이번 틱 후보에서 뒤로 미룬다.
    """
    cutoff = now_kst() - timedelta(minutes=minutes)
    ids = set()
    try:
        rows = base.load_ledger()
    except Exception:   # noqa: BLE001 — 원장이 깨져도 감시·수집을 죽이지 않는다
        return ids
    for r in rows:
        try:
            ts = datetime.fromisoformat(str(r.get("ts")))
        except (TypeError, ValueError):
            continue
        if ts >= cutoff and r.get("id"):
            ids.add(r["id"])
    return ids


def _last_run_map():
    """id → 원장상 마지막 실행 시각(KST 벽시계 · tzinfo 제거). 기록 없는 id 는 **키가 없다**(= 가장 오래 굶은 항목).

    tzinfo 를 떼는 이유: 원장 ts 는 대개 `+09:00` aware 지만 과거 행·테스트 픽스처에 naive 가 섞이면
    naive/aware 비교가 `TypeError` 로 후보 선정을 죽인다(모두 KST 벽시계라 절대시각 비교에 문제 없음).
    """
    out = {}
    try:
        rows = base.load_ledger()
    except Exception:   # noqa: BLE001 — 원장이 깨져도 후보 선정을 죽이지 않는다
        return out
    for r in rows:
        try:
            ts = datetime.fromisoformat(str(r.get("ts"))).replace(tzinfo=None)
        except (TypeError, ValueError):
            continue
        iid = r.get("id")
        if not iid:
            continue
        if iid not in out or ts > out[iid]:
            out[iid] = ts
    return out


def _stale_ids(cands, last):
    """후보 중 '자기보다 더 오래(또는 한 번도) 실행되지 않은 다른 후보'가 있는 id 집합.

    WHY(실측 2026-10-01 02:0x): 쿨다운은 **벽시계** 기준이라 그 사이 틱이 부하·교차 락 가드로
    소비되면 양보가 발동할 틱 자체가 없어 만료돼 버린다 — 원장 실측 R21 2건(20:12·02:00) /
    **R23 0건**(계속 pending, 읽기 전용 0.05초 항목인데도 굶었다). 실행 여부를 '몇 분 지났나'가
    아니라 '다른 후보보다 더 최근에 돌았나'로 비교하면 막힌 틱이 몇 번 끼든 회전이 성립한다.
    """
    epoch = datetime(1970, 1, 1)
    out = set()
    for i in cands:
        mine = last.get(i["id"], epoch)
        for j in cands:
            if j["id"] != i["id"] and last.get(j["id"], epoch) < mine:
                out.add(i["id"])
                break
    return out


def pick_item(b, force=False):
    """다음 실행 항목 — `base.next_item()` 과 같되 **상시 감시를 회전에서 양보시킨다**.

    양보 조건 2가지: ① 최근 `RECUR_COOLDOWN_MIN` 분 내 실행(벽시계 쿨다운) ② 다른 실행 가능
    후보가 나보다 **더 오래** 굶었음(원장 기준 · 한 번도 실행 안 된 항목이 가장 오래됨).
    ②가 없으면 앞 번호 상시 감시가 큐 전체를 굶긴다(실측 2026-10-01 02:0x: R21 2건/R23 0건).

    미룬 결과 실행할 항목이 없으면(큐에 감시밖에 없고 그마저 방금 돌았다면) 감시를 다시 집는다 —
    회전이 감시를 죽이면 안 된다. force 는 종전대로 쿨다운·회전을 무시한다(운영자 명시 실행).
    """
    if force:
        return base.next_item(b, force)
    recent = _recent_recurring_ids()
    pend = sorted([i for i in b["items"] if i.get("status") == "pending"],
                  key=lambda i: (i.get("priority", 99), i["id"]))
    runnable, yielded = [], []
    for i in pend:
        if not i.get("command"):
            continue
        blocked, why = base.eta_blocks(i)
        if blocked:
            log(f"{i['id']}: ETA 가드로 건너뜀 — {why}")
            continue
        runnable.append(i)
    stale = _stale_ids(runnable, _last_run_map()) if runnable else set()
    runnable_ids = {i["id"] for i in runnable}
    for i in pend:
        if i["id"] not in runnable_ids:
            continue
        if i.get("recurring") and (i["id"] in recent or i["id"] in stale):
            yielded.append(i["id"])
            continue
        if yielded:
            log(f"양보: 상시 감시 {', '.join(yielded)} — 최근 {RECUR_COOLDOWN_MIN}분 내 실행이거나"
                f" 다른 후보가 더 오래 굶음 → {i['id']} 를 먼저 집는다(감시 굶주림 방지)")
        return i
    if yielded:
        log(f"실행 가능한 다른 pending 없음 → 상시 감시 {', '.join(yielded)} 재실행")
    return base.next_item(b, force)


def _as_text_list(v):
    """setup_needed/needs 는 list·str 이 섞여 들어온다(백로그 관례).

    문자열을 그대로 for 로 돌리면 **문자 단위로 쪼개져** 보고가 오염된다 → 읽는 쪽에서 분기한다.
    """
    if isinstance(v, str):
        return [v] if v.strip() else []
    if isinstance(v, (list, tuple)):
        return [str(x) for x in v if str(x).strip()]
    return []


def approval_asks(item):
    """그 항목이 사용자에게 요구하는 승인·사람 단계 문구(중복 제거).

    WHY(2026-10-01 실측): 틱 보고는 `setup_needed` 만 읽었다. R25 는 승인 요청을 `needs`
    (문자열)에 적어 등록돼 있어 status=needs_approval 인데도 **어떤 틱 보고에도 안 올라왔다**
    — '백로그에서 자동 추출이라 누락이 없다'는 전제가 필드명 하나로 깨진다. 둘 다 읽는다.
    """
    out = _as_text_list(item.get("setup_needed"))
    for s in _as_text_list(item.get("needs")):
        if s not in out:
            out.append(s)
    return out


# 자식 '기동 확인' 창(초). 짧은 항목(R27·R28 pytest 0.02초)이 남기는 고아 pidfile 을 막는다.
ORPHAN_SETTLE = 3.0


def start_item(cmd, item_id, settle=None):
    """자식을 백그라운드로 띄우고 pidfile·state 를 기록한다 → (proc, finished_early).

    WHY (실측 2026-10-03 06:00 R28): 리서처 tick 은 자체 Popen 경로라, ME 구동기
    (`base.start_background`)에 있는 '3초 기동 확인' 가드가 없었다. pytest 처럼 0.02초에 끝나는
    자식은 `--run` 종료 시 **자기 pidfile 을 먼저 지우고**, 부모가 그 뒤에 pidfile 을 쓰는 경합이
    생긴다 → 죽은 pid 를 가리키는 고아 pidfile 이 남아 hygiene 이
    '죽은 프로세스의 사이클 pidfile 잔존: researcher' breach 를 06:30~15:30(9시간) 보고했다.
    (정상 실행에서는 자식이 자기 pidfile 을 지워 정리되므로 이 경합에서만 샌다.)

    가드: settle 초 뒤 자식이 **이미 끝났으면** pidfile·state 를 지운다 → '실행 중'으로 세우지
    않는다(결과는 다음 틱이 원장에서 읽는다). 자식이 살아 있으면 그대로 두고, 자식이 종료 시
    자기 pidfile 을 지운다.
    """
    os.makedirs(RES_RUNTIME, exist_ok=True)
    bg = os.path.join(RES_RUNTIME, f"bg_{item_id}.log")
    with open(bg, "w", encoding="utf-8") as lf:
        p = subprocess.Popen(cmd, stdout=lf, stderr=subprocess.STDOUT,
                             start_new_session=True, cwd=PROJ)
    with open(base.PIDFILE, "w", encoding="utf-8") as f:
        f.write(str(p.pid))
    with open(base.STATE, "w", encoding="utf-8") as f:
        json.dump({"id": item_id, "pid": p.pid,
                   "started": now_kst().isoformat(timespec="seconds")}, f)
    time.sleep(ORPHAN_SETTLE if settle is None else settle)
    if p.poll() is not None:
        for f in (base.PIDFILE, base.STATE):
            try:
                os.remove(f)
            except OSError:
                pass
        return p, True
    return p, False


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
            for s in approval_asks(i):
                print(f"  ⚠ [{i['id']}] 사람/승인 필요: {s}")
            if i.get("blocked_by"):
                print(f"  ⛔ [{i['id']}] 차단: {i['blocked_by']}")
            res = i.get("result") if isinstance(i.get("result"), dict) else {}
            # 상시 감시(recurring)는 크래시에도 status=pending 을 유지한다(감시가 일시 오류로
            # 영구히 죽지 않게). 대신 실패가 조용해지지 않도록 여기서 rc≠0 을 직접 크게 찍는다.
            if i.get("status") == "failed" or (i.get("recurring") and res.get("rc") not in (None, 0)):
                print(f"  ✗ [{i['id']}] 실패 — 로그 확인 필요: {res.get('log', '-')}")
    # (2026-09-30 수리) 결과를 보고했다고 이 틱을 끝내지 않는다 — 종전 `return 0` 은 시작 기회를
    # 삼켜, 회전 항목의 실효 실행 간격이 '시작 틱 → 보고 틱 → 시작 틱' = 4시간이 됐다. 그러면
    # 쿨다운(180분 = 1.5틱)은 그 사이에 **항상 만료**되어 pick_item 의 양보가 영원히 발동하지 않는다.
    # 실측(2026-09-30 22:0x): R21(prio 6, recurring, 0.1분 프로브)이 매 시작 틱을 독식 —
    # 원장 R21 1건 / R23 0건, 00:00 시각을 가정해 pick_item 을 돌리면 여전히 R21 반환.
    # 보고는 이 틱에서 그대로 하고, 후보 선정은 pick_item 이 최근 실행된 상시 감시를 뒤로 미룬다.
    ok, why = base.guards(force)
    if not ok:
        print(f"대기: {why}")
        return 0
    nxt = pick_item(b, force)
    if not nxt:
        print("실행 가능한 pending 없음 → setup_needed 해소 또는 새 항목 설계가 필요하다.")
        return 0
    cmd = [sys.executable, os.path.abspath(__file__), "--run", nxt["id"]]
    if force:
        cmd.append("--force")
    _, done = start_item(cmd, nxt["id"])
    tail = " (기동 확인: 3초 내 완료 — 결과는 다음 틱에 원장에서 읽는다)" if done else ""
    print(f"시작: {nxt['id']} — {nxt['title']} (비용 {nxt.get('cost')}){tail}")
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
    ap.add_argument("--author-only", action="store_true",
                    help="저작만 수행하고 실행·가드는 건너뛴다(저작은 API 대기 중심 → CPU 직렬화 대상 아님)")
    ap.add_argument("--force", action="store_true")
    a = ap.parse_args()
    if a.status:
        return status()
    if a.tick:
        return tick(a.force)
    if a.run and a.author_only:
        # 저작 전용 모드: 교차 락(다른 역할 실행 중)에 막히지 않는다.
        # WHY(2026-09-25 실측): R10 위임이 U1 실행 중 교차 락에 걸려 "시작 보류(rc=3)"로 죽었다.
        # 그런데 저작 위임은 대부분 **API 응답 대기**라 CPU 부하가 작고, CPU 를 쓰는 것은 저작된
        # 스크립트의 **실행**이다. 그래서 저작과 실행을 분리해 밤 사이 대기 시간을 활용한다.
        b = json.load(open(RES_BACKLOG, encoding="utf-8"))
        it = next((i for i in b["items"] if i["id"] == a.run), None)
        if not it:
            log(f"백로그에 {a.run} 없음")
            return 2
        os.makedirs(RES_LOGDIR, exist_ok=True)
        ok, msg, info = ensure_artifact(it, "")
        log(f"[저작전용] {a.run}: ok={ok} | {msg}")
        if info:
            print(json.dumps(info, ensure_ascii=False, indent=2)[:900])
        return 0 if ok else 1
    if a.run:
        b = json.load(open(RES_BACKLOG, encoding="utf-8"))
        it = next((i for i in b["items"] if i["id"] == a.run), None)
        if not it:
            log(f"백로그에 {a.run} 없음")
            return 2
        # 실행이 **예외로 죽어도** pidfile 은 반드시 지운다. 안 지우면 hygiene 이
        # '죽은 프로세스의 사이클 pidfile 잔존: researcher' breach 를 다음 점검까지(수 시간) 보고한다
        # (실측 2026-10-03 18:00: execute() 내부 AttributeError 로 traceback 종료 → 종전에는 이 줄에
        #  도달하지 못해 고아 running.pid 가 남았고, 부모 tick 은 자식을 이미 종료로 보므로 재수거도 없다).
        try:
            rc = execute(it, a.force)
        finally:
            try:
                os.remove(base.PIDFILE)
            except OSError:
                pass
        return rc
    ap.print_help()
    return 0


if __name__ == "__main__":
    sys.exit(main())

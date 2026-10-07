#!/usr/bin/env python3
"""panel_meta — 패널 캐시(npz)의 '빌드 조건' 사이드카 검증. (백로그 RB2 / F3)

왜 필요한가 (이 역할의 실측 교훈, 반복 사고 목록):
  `wf_wave.build_panel` 은 기존 npz 가 있으면 **파일명만 보고** 재사용한다
  (`if os.path.exists(cache): ... panel cache 재사용`). 그런데 그 파일이
  어떤 조건으로 만들어졌는지(피처 코드 버전·유니버스·구간·종목수)는 파일 안에 없다.
  그래서 다음 두 사고가 구조적으로 가능하다 — 그리고 값은 그럴듯하게 나온다:

    ① 피처 코드(feature_engine)가 바뀐 뒤 옛 패널을 재사용
       → `feature_pipeline` 은 **빌드 체크포인트**의 재개에만 code_sig 를 건다.
         '완성된 npz 재사용' 경로에는 그 검증이 없었다.
    ② 유니버스/구간/종목수를 바꿔 요청했는데 옛 파일이 같은 경로에 남아 있음
       → 조용히 다른 데이터로 측정한다(대조군·arm 이 서로 다른 스냅샷이 된다).

이 모듈은 완성된 npz 옆에 `panel_*.npz.build_meta.json` 사이드카를 **쓰고**,
재사용 시점에 요청 조건과 **비교**해 불일치를 드러낸다.

두 종류를 구분한다(거짓 경보 방지 — 자체점검 16c 실측):
  · **hard (불일치)** — universe/limit/days/end_date/opts 가 다르다 = **데이터 스냅샷이 다르다**.
    이건 반드시 드러내야 한다(대조군과 arm 이 다른 데이터로 채점되는 사고).
  · **soft (코드 변경)** — code_sig 만 다르다 = 피처 코드가 이 패널 빌드 이후 편집됐다.
    피처 값이 실제로 달라졌는지는 코드 변경 성격에 달렸다(주석·기본 OFF 옵션 추가는 무해).
    그래서 '확인 필요'로 알리되 **불일치로 단정하지 않는다**.

설계 제약 (RB2 노트, 반드시 유지):
  · 기본 동작은 **경고**다 — 검증 실패로 실행을 거부하면 사이드카가 없는 기존 패널
    (현재 모든 패널이 그렇다)에서 U1/RB1 류 실행이 전부 막힌다.
  · 강제(예외)는 env `PANEL_META_STRICT=1` 로만 켜고, 그마저 **hard 불일치만** 막는다.
  · 사이드카 **부재**(레거시 패널)는 '불일치'가 아니라 '알 수 없음'이다 — STRICT 에서도 막지 않는다.
  · 순수 표준 라이브러리만 쓴다(호스트·컨테이너 양쪽에서 import 가능해야 자체점검이 된다).

검증: `python3 scripts/_panel_meta_test.py`
      (컨테이너 e2e 포함: `docker exec -e PYTHONPATH=/app -e PANEL_META_E2E=1
       stock_xgboost_ml python /app/scripts/_panel_meta_test.py`)
"""

import json
import os
import tempfile
import time
from datetime import datetime

try:                                     # POSIX 전용. 비-POSIX(예: Windows 파이썬)에선 잠금 생략.
    import fcntl
except ImportError:                      # pragma: no cover
    fcntl = None

META_SUFFIX = ".build_meta.json"
LOCK_SUFFIX = ".lock"

# 요청 조건 중 사이드카와 비교할 키(hard). 순서는 로그 가독성용.
REQUEST_KEYS = ("universe", "universe_seed", "limit", "days", "end_date", "universe_opts")

STATUS_OK = "ok"
STATUS_MISSING = "missing"          # 사이드카 없음(레거시 패널) = 알 수 없음
STATUS_MISMATCH = "mismatch"        # hard 조건 불일치 = 스냅샷이 다르다
STATUS_CODE_CHANGED = "code_changed"  # soft: 피처 코드만 변경됨


def meta_path(cache):
    """npz 경로 → 사이드카 경로."""
    return cache + META_SUFFIX


def write_meta(cache, fields):
    """사이드카를 원자적으로(tmp → os.replace) 쓴다. 반환: 기록된 경로.

    실패하면 예외를 올리되(호출부가 try 로 감싼다) tmp 파일은 남기지 않는다.
    """
    path = meta_path(cache)
    payload = dict(fields)
    payload.setdefault("created_at", datetime.now().isoformat(timespec="seconds"))
    payload["builder"] = payload.get("builder", "wf_wave.build_panel")
    d = os.path.dirname(path)
    if d:
        os.makedirs(d, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=d or ".", prefix=".pmeta-", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(payload, f, ensure_ascii=False, indent=2, sort_keys=True)
            f.write("\n")
        os.replace(tmp, path)
    except BaseException:
        try:
            os.remove(tmp)
        except OSError:
            pass
        raise
    return path


def read_meta(cache):
    """사이드카를 읽는다. 반환: (meta|None, error|None).

    파일 없음 → (None, None). 파싱 실패/형식 오류 → (None, "사유").
    """
    path = meta_path(cache)
    if not os.path.exists(path):
        return None, None
    try:
        with open(path, "r", encoding="utf-8") as f:
            m = json.load(f)
    except Exception as e:                       # noqa: BLE001
        return None, f"{type(e).__name__}: {e}"
    if not isinstance(m, dict):
        return None, f"형식 오류(최상위가 dict 아님: {type(m).__name__})"
    return m, None


def _norm(v):
    """비교용 정규화 — int/str/float 혼재를 흡수한다.

    명령줄에서 온 값은 문자열("200"/"111.0"), 사이드카는 숫자(200/111.0)다.
    정수값이면 정수 문자열로, 아니면 float repr 로 접는다 — 안 하면 같은 조건이
    'code_sig: meta=111 now=111.0' 같은 거짓 불일치로 뜬다(자체점검 13 이 잡았다).
    """
    if v is None:
        return None
    if isinstance(v, bool):
        return str(v).lower()
    if isinstance(v, (int, float)):
        f = float(v)
        return str(int(f)) if f.is_integer() else repr(f)
    s = str(v).strip()
    try:
        f = float(s)
    except (TypeError, ValueError):
        return s
    return str(int(f)) if f.is_integer() else repr(f)


def verify(cache, request=None, current_code_sig=None):
    """사이드카와 '이번 요청 조건'을 비교한다.

    request: {"universe":..., "limit":..., "days":..., "end_date":..., ...}
             값이 None 인 키는 비교하지 않는다(end_date=None = 실행 시각 = 매번 달라짐).
    current_code_sig: 지금 feature_engine 코드 서명(있으면 메타와 비교).

    반환 dict:
      status    : 'ok' | 'missing' | 'mismatch' | 'code_changed'
      hard_diffs: 스냅샷 조건 불일치 목록(데이터가 다르다)
      soft_diffs: 피처 코드 변경 목록(확인 필요)
      diffs     : hard + soft (로그 편의용)
      meta/error: 읽은 메타 / 파싱 실패 사유
    """
    request = request or {}
    meta, err = read_meta(cache)
    out = {"status": STATUS_MISSING, "diffs": [], "hard_diffs": [], "soft_diffs": [],
           "meta": meta, "error": err}
    if meta is None:
        return out

    hard, soft = [], []
    for k in REQUEST_KEYS:
        if k not in request:
            continue
        rv = request.get(k)
        if rv is None:
            continue                        # 요청이 '미지정' → 비교 불가(경고하지 않음)
        if k == "universe_opts":
            rv = {kk: vv for kk, vv in (rv or {}).items() if vv is not None}
            if not rv:
                continue
            if k not in meta or meta.get(k) is None:
                continue                    # 메타에 없으면 판단 불가(거짓 불일치 금지)
            mv = {kk: vv for kk, vv in (meta.get(k) or {}).items() if vv is not None}
            if _norm(json.dumps(rv, sort_keys=True)) != _norm(json.dumps(mv, sort_keys=True)):
                hard.append(f"{k}: meta={json.dumps(mv, sort_keys=True)} "
                            f"request={json.dumps(rv, sort_keys=True)}")
            continue
        if k not in meta or meta.get(k) is None:
            continue                        # 메타에 없으면 판단 불가
        if _norm(meta.get(k)) != _norm(rv):
            hard.append(f"{k}: meta={meta.get(k)} request={rv}")

    if current_code_sig is not None and meta.get("code_sig"):
        if _norm(meta["code_sig"]) != _norm(current_code_sig):
            soft.append(f"code_sig: meta={meta['code_sig']} now={current_code_sig}")

    out["hard_diffs"] = hard
    out["soft_diffs"] = soft
    out["diffs"] = hard + soft
    if hard:
        out["status"] = STATUS_MISMATCH
    elif soft:
        out["status"] = STATUS_CODE_CHANGED
    else:
        out["status"] = STATUS_OK
    return out


def should_fail(status, strict):
    """STRICT 라도 'hard 불일치'만 막는다.

    부재(레거시 패널)·코드변경(soft)은 막지 않는다 — 둘 다 흔하고, 막으면 작업이 멈춘다.
    """
    return bool(strict) and status == STATUS_MISMATCH


def env_strict(default=False):
    """PANEL_META_STRICT=1/true/yes → True."""
    v = os.environ.get("PANEL_META_STRICT")
    if v is None:
        return bool(default)
    return str(v).strip().lower() in ("1", "true", "yes", "on")


def format_line(status, diffs, meta=None):
    """사람이 읽는 한 줄. 로그에 그대로 넣는다."""
    if status == STATUS_OK:
        m = meta or {}
        return (f"panel meta OK (code_sig={m.get('code_sig')} · universe={m.get('universe')} "
                f"limit={m.get('limit')} days={m.get('days')})")
    if status == STATUS_MISSING:
        return ("panel meta 없음(사이드카 미기록 패널) — 빌드 조건 미검증. "
                "이 패널을 다시 빌드하면 사이드카가 생긴다")
    if status == STATUS_CODE_CHANGED:
        return ("panel meta: 피처 코드가 이 패널 빌드 이후 변경됨(" + " | ".join(diffs) +
                ") — 값이 달라졌을 수 있으니 같은 경로로 A/B 하는지 확인하라")
    return ("⚠ panel meta 불일치(스냅샷이 다름) — 이 npz 는 지금 요청과 다른 조건으로 만들어졌다: "
            + " | ".join(diffs) + " → 재빌드하거나 요청을 맞춰라(조용한 스냅샷 불일치 방지)")


# ---------------------------------------------------------------------------
# 패널 경로 단위 advisory 락 (백로그 RB2b ①)
#
# 왜 필요한가 (이 역할의 실측 사고 목록):
#   `build_panel` 은 '완성 npz 재사용'과 '신규 빌드' 두 길로 들어간다. 둘 다 **같은 경로**를
#   쓰는데 프로세스 간 조정이 없었다(구동기 락은 '사이클' 단위일 뿐 특정 --panel 경로를 강제하지
#   않는다). 그래서 다음이 구조적으로 가능하다 — 그리고 값은 그럴듯하게 나온다:
#     ① 두 프로세스가 같은 --panel 을 동시에 빌드 → 같은 체크포인트에 교차 저장,
#        실측(2026-10-04 CG92) pair/s 5.05 → 1.16 으로 반토막(한쪽은 timeout 잔존 프로세스였다).
#     ② 한쪽이 빌드 중인 경로를 다른 쪽이 재사용 → 반쯤/옛 상태를 읽는 조용한 스냅샷 불일치.
#     ③ 같은 덤프·산출물 경로에 동시 쓰기 → 로그/결과가 섞인다(2026-10-04 q05 평가 3중 기동).
#
# 설계(반드시 유지):
#   · 락 파일은 패널 옆 `<cache>.lock` — 락 **대상**은 패널 경로다.
#   · **reader 는 shared(SH), builder 는 exclusive(EX)**. 완성 패널을 여러 프로세스가 읽는 것은
#     무해하므로 SH-SH 는 공존하고, 빌드(EX)만 reader 와 상호배타다. 이래야 정상 재사용이
#     서로 막히지 않는다.
#   · 기본은 **비블로킹**이다 — 못 잡으면 즉시 `PanelLockBusy` 로 **명시 실패**한다(조용히 겹쳐
#     돌지 않는다). 기다리려면 env `PANEL_LOCK_WAIT=<초>`.
#   · 락은 fd 기반이라 프로세스가 죽으면(SIGKILL 포함) 커널이 자동 해제한다 — stale 락이 없다.
#   · fcntl 이 없는 플랫폼에선 잠금을 생략하고 경고만 남긴다(측정을 막지 않는다).
# ---------------------------------------------------------------------------

class PanelLockBusy(RuntimeError):
    """다른 프로세스가 같은 패널 경로를 빌드/사용 중이라 잠금을 못 잡았다."""


def lock_path(cache):
    """패널 경로 → 락 파일 경로."""
    return cache + LOCK_SUFFIX


def _lock_wait_sec():
    """PANEL_LOCK_WAIT(초). 파싱 실패/미설정 → 0(즉시 실패)."""
    try:
        return max(0.0, float(os.environ.get("PANEL_LOCK_WAIT", "0")))
    except (TypeError, ValueError):
        return 0.0


class PanelLock:
    """패널 경로 단위 advisory 락.

    mode: 'ex'(빌드/쓰기) | 'sh'(완성 패널 읽기 재사용)
    사용: `with PanelLock(cache, mode='ex', log=log): ...`
    """

    def __init__(self, cache, mode="ex", log=print, wait=None):
        if mode not in ("ex", "sh"):
            raise ValueError(f"mode 는 'ex'|'sh' 여야 한다: {mode!r}")
        self.cache = cache
        self.mode = mode
        self.log = log
        self.wait = _lock_wait_sec() if wait is None else max(0.0, float(wait))
        self.path = lock_path(cache)
        self.fd = None

    def _holder_hint(self):
        try:
            with open(self.path, "r", encoding="utf-8") as f:
                txt = " ".join(f.read().split())
            return f" · 보유자: {txt}" if txt else ""
        except OSError:
            return ""

    def acquire(self):
        if fcntl is None:
            self.log("panel lock: fcntl 없음(비-POSIX) — 잠금 생략")
            return self
        d = os.path.dirname(self.path)
        if d:
            os.makedirs(d, exist_ok=True)
        self.fd = os.open(self.path, os.O_CREAT | os.O_RDWR, 0o644)
        flag = fcntl.LOCK_EX if self.mode == "ex" else fcntl.LOCK_SH
        deadline = time.time() + self.wait
        while True:
            try:
                fcntl.flock(self.fd, flag | fcntl.LOCK_NB)
                break
            except OSError:
                if time.time() >= deadline:
                    try:
                        os.close(self.fd)
                    finally:
                        self.fd = None
                    what = "빌드" if self.mode == "ex" else "사용"
                    raise PanelLockBusy(
                        f"다른 프로세스가 같은 패널({self.cache})을 {what} 중이다 — 잠금을 못 잡았다"
                        + self._holder_hint()
                        + ". 중복 실행은 같은 체크포인트 교차 저장(pair/s 반토막)이나 반쯤 쓰인 "
                          "패널 read 를 만든다. 기다리려면 PANEL_LOCK_WAIT=<초> 를 주라.")
                time.sleep(0.2)
        try:                              # 보유자 힌트(디버깅용 — 락 semantics 와 무관)
            os.ftruncate(self.fd, 0)
            os.write(self.fd, (
                f"pid={os.getpid()} mode={self.mode} "
                f"at={datetime.now().isoformat(timespec='seconds')}\n").encode("utf-8"))
        except OSError:
            pass
        return self

    def release(self):
        if self.fd is None:
            return
        try:
            if fcntl is not None:
                try:
                    fcntl.flock(self.fd, fcntl.LOCK_UN)
                except OSError:
                    pass
        finally:
            try:
                os.close(self.fd)
            finally:
                self.fd = None

    def __enter__(self):
        return self.acquire()

    def __exit__(self, *exc):
        self.release()
        return False

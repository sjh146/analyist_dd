#!/usr/bin/env python3
"""수집 호출 정책 공용 모듈 — IP 차단 회피를 '프로세스 밖에서' 강제한다.

WHY (2026-10-02 실측): 각 수집기는 **자기 프로세스 안에서만** 지연을 지킨다
(`KIS_REQUEST_DELAY=3.0+지터`, `KRX_REQUEST_DELAY`, DART `DELAY_BASE`). 그래서 같은 호스트에
두 프로세스가 겹치면 — 크론 + 수동 실행, 또는 크론 두 개 — 실제 호출률은 **2배**가 된다.
"지연은 지켰는데 왜 차단됐나"의 전형적 원인이 이것이고, 로그에는 두 러너 모두 정상으로 보인다.

이 모듈은 호스트(또는 자격증명) 키별 상태 파일 + `flock` 으로 다음을 **프로세스 경계를 넘어** 강제한다.

  1. 최소 호출 간격(대기)   2. 일일 호출 예산   3. 차단 신호 감지 시 쿨다운
  4. 호출·차단 이벤트 기록(data/reports/net_guard/events.jsonl) → 감사·경보가 읽는다

정확도는 건드리지 않는다 — 호출 **시각**만 조정하고, 무엇을 얼마나 받아 어떻게 적재하는지는
각 러너가 그대로 한다(부분 수집·스킵 없음).

정책(env, 러너별 기존 이름을 그대로 존중):
    NET_GUARD_DISABLE=1          가드 전체 비활성(디버깅용, 위험)
    NET_GUARD_STATE_DIR          상태 디렉터리(기본 <repo>/data/state/net_guard)
    NET_GUARD_FAIL_OPEN=0        가드 자체 오류 시 호출을 막을지(기본 1=열어준다)

사용:
    from net_guard import guard
    g = guard("kis", delay=3.0, jitter=0.5, budget=20000)
    try:
        g.acquire()                      # 프로세스 간 최소 간격·예산·쿨다운 강제
    except BudgetExhausted as e:
        ...                              # clean stop, 다음 실행에서 재개
    except Blocked as e:
        ...                              # 즉시 종료(재시도 금지)
    status, body = do_http(...)
    g.note(status, body)                 # 차단/일시오류 분류·기록·쿨다운
    g.fail(err)                          # 전송 오류 기록

CLI:
    python3 scripts/net_guard.py status            호스트별 오늘 호출·예산·차단 상태
    python3 scripts/net_guard.py events -n 20      최근 이벤트
    python3 scripts/net_guard.py clear --key ki3f2 쿨다운 해제(사유 기록)
"""
from __future__ import annotations

import argparse
import contextlib
import datetime as dt
import errno
import fcntl
import hashlib
import json
import os
import random
import sys
import time

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
STATE_DIR = os.environ.get("NET_GUARD_STATE_DIR") or os.path.join(REPO, "data/state/net_guard")
EVENTS = (os.environ.get("NET_GUARD_EVENTS")
          or os.path.join(REPO, "data/reports/net_guard/events.jsonl"))
LOCK_TIMEOUT = float(os.environ.get("NET_GUARD_LOCK_TIMEOUT", "900"))
FAIL_OPEN = os.environ.get("NET_GUARD_FAIL_OPEN", "1") != "0"
DISABLED = os.environ.get("NET_GUARD_DISABLE") == "1"

BLOCKED_STATUS = {401, 403, 407, 418, 451, 999}
TRANSIENT_STATUS = {408, 425, 429, 500, 502, 503, 504, 522, 524}


class GuardError(Exception):
    """기반 오류."""


class BudgetExhausted(GuardError):
    """일일 호출 예산 소진 — 실패가 아니라 '다음 실행에서 이어서'."""


class Blocked(GuardError):
    """차단 신호 감지(또는 쿨다운 중) — 재시도 금지."""


def classify(status, body="") -> str:
    """HTTP 응답을 ok / transient / blocked 로 분류한다.

    blocked = 401·403·WAF(HTML 본문)·차단 전용 코드 → 재시도 무의미, 쿨다운.
    주의: 404 는 ok 로 둔다(의미상 '없음'이지 차단이 아니다 — 없는 경로 탐색은 §1 규칙).
    """
    text = (body or "")
    if isinstance(text, bytes):
        text = text.decode("utf-8", "replace")
    s = str(text).lstrip()
    if s.startswith("<") and not s.lower().startswith("<?xml"):
        return "blocked"          # WAF/차단 페이지(JSON API 가 HTML 을 주면 차단이다)
    try:
        code = int(status)
    except (TypeError, ValueError):
        return "ok"
    if code in BLOCKED_STATUS:
        return "blocked"
    if code in TRANSIENT_STATUS or code >= 500:
        return "transient"
    return "ok"


def retry_after(headers, default=0.0) -> float:
    """Retry-After 헤더를 초로. dict/email.message 모두 허용."""
    if not headers:
        return default
    try:
        raw = headers.get("Retry-After") or headers.get("retry-after")
    except AttributeError:
        return default
    if not raw:
        return default
    try:
        return max(0.0, float(str(raw).strip()))
    except ValueError:
        try:
            when = dt.datetime.strptime(str(raw).strip(), "%a, %d %b %Y %H:%M:%S %Z")
            return max(0.0, when.timestamp() - time.time())
        except ValueError:
            return default


def _key_slug(key: str) -> str:
    safe = "".join(c if c.isalnum() or c in "-_." else "_" for c in key)[:40]
    digest = hashlib.sha256(key.encode()).hexdigest()[:6]
    return f"{safe}-{digest}"


def _paths(key: str):
    os.makedirs(STATE_DIR, exist_ok=True)
    slug = _key_slug(key)
    return (os.path.join(STATE_DIR, slug + ".json"), os.path.join(STATE_DIR, slug + ".lock"))


def _record(event: dict) -> None:
    try:
        os.makedirs(os.path.dirname(EVENTS), exist_ok=True)
        event = {"ts": dt.datetime.now().isoformat(timespec="seconds"), **event}
        with open(EVENTS, "a", encoding="utf-8") as f:
            f.write(json.dumps(event, ensure_ascii=False) + "\n")
    except OSError:
        pass


def load_state(key: str) -> dict:
    path, _ = _paths(key)
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return {}


def _save_state(key: str, state: dict) -> None:
    path, _ = _paths(key)
    tmp = path + ".tmp"
    try:
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(state, f, ensure_ascii=False)
        os.replace(tmp, path)
    except OSError:
        pass


class Guard:
    """호스트(자격증명) 키 단위 호출 정책."""

    def __init__(self, key: str, *, delay=0.0, jitter=0.0, budget=0,
                 block_cooldown=float(os.environ.get("NET_GUARD_BLOCK_COOLDOWN", "900")),
                 enabled=None):
        self.key = key
        self.delay = max(0.0, float(delay))
        self.jitter = max(0.0, float(jitter))
        self.budget = int(budget or 0)
        self.block_cooldown = float(block_cooldown)
        self.enabled = (not DISABLED) if enabled is None else bool(enabled)

    # ── 상태 ────────────────────────────────────────────────────────────
    def _today(self) -> str:
        return dt.date.today().isoformat()

    def _roll(self, state: dict) -> dict:
        if state.get("day") != self._today():
            state = {"day": self._today(), "calls": 0,
                     "blocked_until": 0.0, "blocked_reason": "",
                     "total_calls": int(state.get("total_calls", 0)),
                     "blocked_count": int(state.get("blocked_count", 0))}
        state["key"] = self.key          # 상태파일 스템은 슬러그라서 키를 파일에 함께 남긴다
        return state

    def status(self) -> dict:
        st = self._roll(load_state(self.key))
        st["key"] = self.key
        st["budget"] = self.budget or None
        st["blocked_now"] = float(st.get("blocked_until", 0)) > time.time()
        return st

    # ── 핵심 ────────────────────────────────────────────────────────────
    @contextlib.contextmanager
    def _lock(self):
        _, path = _paths(self.key)
        fd = os.open(path, os.O_CREAT | os.O_RDWR, 0o644)
        deadline = time.time() + LOCK_TIMEOUT
        try:
            while True:
                try:
                    fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
                    break
                except OSError as e:                      # noqa: PERF203
                    if e.errno not in (errno.EACCES, errno.EAGAIN):
                        raise
                    if time.time() > deadline:
                        raise GuardError(f"락 대기 초과({LOCK_TIMEOUT}s): {self.key}") from e
                    time.sleep(0.05 + random.uniform(0, 0.05))
            yield fd
        finally:
            with contextlib.suppress(OSError):
                fcntl.flock(fd, fcntl.LOCK_UN)
            os.close(fd)

    def acquire(self, *, log=None) -> float:
        """호출 직전 게이트. 반환=실제 대기한 초. 예산/쿨다운 위반은 예외로 알린다."""
        if not self.enabled:
            return 0.0
        try:
            return self._acquire_locked(log=log)
        except (BudgetExhausted, Blocked):
            raise
        except Exception as e:                            # noqa: BLE001 — 가드 자체 오류
            _record({"key": self.key, "event": "guard_error", "error": repr(e)[:200]})
            if FAIL_OPEN:
                if log:
                    log(f"[net_guard] 가드 오류(무시하고 진행): {e!r}")
                return 0.0
            raise

    def _acquire_locked(self, *, log=None) -> float:
        waited = 0.0
        with self._lock():
            st = self._roll(load_state(self.key))
            now = time.time()
            if st.get("blocked_until", 0) > now:
                left = st["blocked_until"] - now
                raise Blocked(f"쿨다운 중 {left:.0f}s — {st.get('blocked_reason', '')[:80]}")
            if self.budget and st.get("calls", 0) >= self.budget:
                raise BudgetExhausted(
                    f"일일 예산 소진 {st['calls']}/{self.budget} ({self.key})")
            # 정책은 '더 보수적인 쪽'을 쓴다(러너별 기본값이 달라도 겹칠 때 느린 쪽으로 수렴)
            delay = max(float(st.get("delay", 0) or 0), self.delay)
            jitter = max(float(st.get("jitter", 0) or 0), self.jitter)
            target = float(st.get("last_call_ts", 0)) + delay + random.uniform(0, jitter)
            if target > now:
                waited = target - now
                if log:
                    log(f"[net_guard] 대기 {waited:.2f}s (프로세스 간 간격 {delay:.2f}s+{jitter:.2f}s)")
                time.sleep(waited)
                now = time.time()
            st.update({"delay": delay, "jitter": jitter, "last_call_ts": now,
                       "calls": int(st.get("calls", 0)) + 1,
                       "total_calls": int(st.get("total_calls", 0)) + 1,
                       "updated": dt.datetime.now().isoformat(timespec="seconds")})
            _save_state(self.key, st)
        return waited

    def note(self, status=None, body="", *, headers=None, error=None,
             retry_backoff=0.0, log=None) -> str:
        """호출 결과 분류·기록. 반환=분류결과(ok/transient/blocked/error)."""
        if error is not None:
            kind = "error"
            _record({"key": self.key, "event": "transport_error", "error": repr(error)[:200]})
            if log:
                log(f"[net_guard] 전송 오류 기록: {error!r}")
            return kind
        kind = classify(status, body)
        ra = retry_after(headers)
        if kind == "blocked":
            self.block(f"HTTP {status} {'WAF/HTML' if str(body)[:1] == '<' else str(body)[:60]}", log=log)
        elif kind == "transient":
            _record({"key": self.key, "event": "transient", "status": status,
                     "retry_after": ra, "backoff": retry_backoff})
            if log:
                log(f"[net_guard] 일시오류 {status} — 백오프 {max(ra, retry_backoff):.1f}s 권고")
        return kind

    def block(self, reason: str, *, cooldown=None, log=None) -> float:
        """차단 신호 기록 + 쿨다운 설정(모든 프로세스가 즉시 중단하도록)."""
        cd = float(cooldown if cooldown is not None else self.block_cooldown)
        try:
            with self._lock():
                st = self._roll(load_state(self.key))
                st.update({"blocked_until": time.time() + cd,
                           "blocked_reason": str(reason)[:200],
                           "blocked_count": int(st.get("blocked_count", 0)) + 1})
                _save_state(self.key, st)
        except Exception:                                 # noqa: BLE001
            pass
        _record({"key": self.key, "event": "blocked", "reason": str(reason)[:200],
                 "cooldown_s": cd})
        if log:
            log(f"[net_guard] ★차단 신호: {reason} — {cd:.0f}s 쿨다운(재시도 금지)")
        return cd

    def clear(self, note="수동 해제") -> None:
        try:
            with self._lock():
                st = self._roll(load_state(self.key))
                st.update({"blocked_until": 0.0, "blocked_reason": ""})
                _save_state(self.key, st)
        except Exception:                                 # noqa: BLE001
            pass
        _record({"key": self.key, "event": "clear", "note": note})


_CACHE: dict = {}


def guard(key: str, **kw) -> Guard:
    """가드 인스턴스 캐시(한 프로세스에서 같은 키는 같은 정책)."""
    if key not in _CACHE:
        _CACHE[key] = Guard(key, **kw)
    return _CACHE[key]


def kis_key(appkey: str = "") -> str:
    """KIS 는 앱키 단위로 쿼터가 걸린다 → 키에 앱키 해시를 넣어 분리 집계."""
    h = hashlib.sha256((appkey or os.environ.get("KIS_APP_KEY", "")).encode()).hexdigest()[:6]
    return f"kis-{h}"


# ── CLI ─────────────────────────────────────────────────────────────────
def _cmd_status(_args) -> int:
    os.makedirs(STATE_DIR, exist_ok=True)
    rows = []
    for name in sorted(os.listdir(STATE_DIR)):
        if not name.endswith(".json"):
            continue
        key = name[:-5]
        st = _roll_read(os.path.join(STATE_DIR, name))
        rows.append(st)
    if not rows:
        print("net_guard: 상태 파일 없음(아직 호출 없음)")
        return 0
    now = time.time()
    print(f"{'key':28} {'오늘호출':>9} {'예산':>7} {'최근호출':>19} {'차단':>6}")
    for st in sorted(rows, key=lambda s: -int(s.get("calls", 0))):
        last = st.get("last_call_ts") or 0
        last_s = dt.datetime.fromtimestamp(last).strftime("%m-%d %H:%M:%S") if last else "-"
        blk = st.get("blocked_until") or 0
        tag = f"{blk - now:.0f}s" if blk > now else "-"
        print(f"{(st.get('key') or '?'):28} {int(st.get('calls', 0)):>9} "
              f"{st.get('budget') or '-':>7} {last_s:>19} {tag:>6}")
        if st.get("blocked_reason"):
            print(f"{'':28} └ {str(st['blocked_reason'])[:110]}")
    return 0


def _roll_read(path: str) -> dict:
    try:
        with open(path, encoding="utf-8") as f:
            st = json.load(f)
    except (OSError, ValueError):
        return {}
    if st.get("day") != dt.date.today().isoformat():
        st["calls"] = 0
        st["blocked_until"] = 0.0
    if not st.get("key"):                    # 구버전 상태파일 폴백: 파일명 스템을 보여준다
        st["key"] = os.path.basename(path)[:-5]
    return st


def _cmd_events(args) -> int:
    if not os.path.exists(EVENTS):
        print("net_guard: 이벤트 없음")
        return 0
    lines = [l for l in open(EVENTS, encoding="utf-8") if l.strip()]
    for line in lines[-args.n:]:
        print(line.rstrip())
    return 0


def _cmd_clear(args) -> int:
    g = Guard(args.key, enabled=True)
    g.clear(note=args.note or "CLI 수동 해제")
    print(f"해제: {args.key}")
    return 0


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="수집 호출 정책 가드")
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("status")
    ev = sub.add_parser("events")
    ev.add_argument("-n", type=int, default=20)
    cl = sub.add_parser("clear")
    cl.add_argument("--key", required=True)
    cl.add_argument("--note", default="")
    args = ap.parse_args(argv)
    return {"status": _cmd_status, "events": _cmd_events, "clear": _cmd_clear}[args.cmd](args)


if __name__ == "__main__":
    sys.exit(main())

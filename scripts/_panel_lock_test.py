#!/usr/bin/env python3
"""_panel_lock_test — `scripts/panel_meta.py` 의 패널 경로 advisory 락 자체점검 (백로그 RB2b ①).

이 스택엔 pytest 가 없다 → PASS/FAIL 을 출력하고 실패 시 exit 1 (기존 _*_test.py 관례).

검사 대상은 **락 semantics**이며, 성공 기준(RB2b)은 '악화 유도 → 가드가 막거나 명시 실패'를
실측으로 보이는 것이다. 즉 ①빌드(EX) 중에는 같은 경로의 다른 사용이 명시 실패하고
②정상 재사용끼리(SH-SH)는 서로 막지 않는다.

호스트/컨테이너 양쪽에서 돈다:
    python3 scripts/_panel_lock_test.py
    docker exec -e PYTHONPATH=/app stock_xgboost_ml python /app/scripts/_panel_lock_test.py

wf_wave.build_panel 배선 검사(마지막 블록)는 numpy·pandas·DB 모듈이 필요하므로 import 가
성공할 때만 돈다(호스트 python3 에 numpy 가 없으면 NOTE 로 건너뛴다).
"""

import os
import subprocess
import sys
import tempfile
import time

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import panel_meta  # noqa: E402

RESULTS = []


def check(name, cond, detail=""):
    RESULTS.append((name, bool(cond), detail))
    print(f"[{'PASS' if cond else 'FAIL'}] {name}" + (f"  — {detail}" if detail else ""))


# 자식 프로세스: 락을 잡고 'release' 파일이 생길 때까지 보유한다.
CHILD = r'''
import os, sys, time
sys.path.insert(0, sys.argv[1])
import panel_meta
cache, mode, sig = sys.argv[2], sys.argv[3], sys.argv[4]
lk = panel_meta.PanelLock(cache, mode=mode, log=lambda *a: None)
try:
    lk.acquire()
except panel_meta.PanelLockBusy:
    open(sig + "_busy", "w").write("busy"); sys.exit(3)
except Exception as e:
    open(sig + "_err", "w").write(repr(e)); sys.exit(4)
open(sig + "_ok", "w").write("ok")
t0 = time.time()
while not os.path.exists(sig + "_release") and time.time() < t0 + 30:
    time.sleep(0.05)
lk.release()
'''


class ChildHolder:
    """자식 프로세스가 락을 잡고 있는 상태를 만든다(결정적: release 파일로 해제)."""

    def __init__(self, cache, mode):
        self.sig = cache + f".sig_{mode}_{os.getpid()}"
        self.p = subprocess.Popen([sys.executable, "-c", CHILD, HERE, cache, mode, self.sig])
        self.ok = False
        t0 = time.time()
        while time.time() < t0 + 15:
            if os.path.exists(self.sig + "_ok"):
                self.ok = True
                return
            if os.path.exists(self.sig + "_busy") or os.path.exists(self.sig + "_err"):
                return
            if self.p.poll() is not None:
                return
            time.sleep(0.05)

    def release(self):
        try:
            open(self.sig + "_release", "w").write("1")
        except OSError:
            pass
        try:
            self.p.wait(timeout=10)
        except Exception:
            self.p.kill()
        for suf in ("_ok", "_busy", "_err", "_release"):
            try:
                os.remove(self.sig + suf)
            except OSError:
                pass


def try_lock(cache, mode, wait=0.0):
    """(status, detail) — 'ok' | 'busy' | 'err'."""
    lk = panel_meta.PanelLock(cache, mode=mode, log=lambda *a: None, wait=wait)
    try:
        lk.acquire()
    except panel_meta.PanelLockBusy as e:
        return "busy", str(e)
    except Exception as e:                       # noqa: BLE001
        return "err", repr(e)
    lk.release()
    return "ok", ""


def main():
    with tempfile.TemporaryDirectory() as td:
        cache = os.path.join(td, "sub", "panel_x.npz")   # 디렉터리도 아직 없음

        # --- 1. 락 파일 경로 규약 --------------------------------------------
        check("1 lock_path = <cache>.lock", panel_meta.lock_path(cache) == cache + ".lock",
              panel_meta.lock_path(cache))
        _lk2 = panel_meta.PanelLock(cache, "ex", log=lambda *a: None)
        _lk2.acquire()
        _made = os.path.exists(cache + ".lock")
        _lk2.release()
        check("2 디렉터리가 없어도 acquire 가 락파일을 만든다", _made, cache + ".lock")

        # --- 3~7. 같은 프로세스 안(서로 다른 open-file-description) ----------
        a = panel_meta.PanelLock(cache, "ex", log=lambda *a: None)
        b = panel_meta.PanelLock(cache, "ex", log=lambda *a: None)
        a.acquire()
        st, _ = try_lock(cache, "ex")
        check("3 EX 보유 중 다른 EX → busy(명시 실패)", st == "busy", st)
        st, _ = try_lock(cache, "sh")
        check("4 EX 보유 중 SH → busy(빌드 중 read 차단)", st == "busy", st)
        a.release()
        st, _ = try_lock(cache, "ex")
        check("5 EX 해제 후 EX 재획득 → ok", st == "ok", st)

        s1 = panel_meta.PanelLock(cache, "sh", log=lambda *a: None)
        s1.acquire()
        st, _ = try_lock(cache, "sh")
        check("6 SH 보유 중 다른 SH → ok(정상 재사용 공존)", st == "ok", st)
        st, _ = try_lock(cache, "ex")
        check("7 SH 보유 중 EX → busy", st == "busy", st)
        s1.release()

        # --- 8~10. 기다리기(wait) -------------------------------------------
        h = ChildHolder(cache, "ex")
        check("8 자식이 EX 를 잡았다", h.ok, "child failed to acquire")
        t0 = time.time()
        st, _ = try_lock(cache, "ex", wait=0.6)
        elapsed = time.time() - t0
        check("9 wait=0.6 은 대략 그만큼 기다린 뒤 busy", st == "busy" and 0.5 <= elapsed < 3.0,
              f"{st} {elapsed:.2f}s")
        h.release()

        h2 = ChildHolder(cache, "sh")
        st, _ = try_lock(cache, "ex")            # 기본 즉시 실패
        check("10 SH 보유 중 EX(무대기) → busy", st == "busy", st)
        h2.release()

        # --- 11. 교차 프로세스 -------------------------------------------------
        h3 = ChildHolder(cache, "ex")
        st, detail = try_lock(cache, "ex")
        check("11 다른 프로세스가 빌드 중이면 명시 실패(메시지에 경로·힌트)",
              st == "busy" and "panel_x.npz" in detail and "빌드" in detail, detail[:70])
        h3.release()

        # --- 12. 보유자 힌트 기록 ---------------------------------------------
        lk = panel_meta.PanelLock(cache, "ex", log=lambda *a: None)
        lk.acquire()
        hint = open(panel_meta.lock_path(cache), "r", encoding="utf-8").read()
        lk.release()
        check("12 락파일에 보유자(pid·mode) 힌트가 남는다", ("pid=" in hint and "mode=ex" in hint),
              hint.strip())

        # --- 13. fcntl 부재 폴백(비-POSIX) -----------------------------------
        saved = panel_meta.fcntl
        logs = []
        try:
            panel_meta.fcntl = None
            lk = panel_meta.PanelLock(cache, "ex", log=lambda m: logs.append(m))
            lk.acquire()
            lk.release()
            ok = any("fcntl 없음" in m for m in logs)
        finally:
            panel_meta.fcntl = saved
        check("13 fcntl 없으면 잠금 생략 + 경고(측정을 막지 않음)", ok, str(logs))

        # --- 14. 잘못된 mode 는 즉시 오류 -------------------------------------
        try:
            panel_meta.PanelLock(cache, "xx")
            bad = False
        except ValueError:
            bad = True
        check("14 mode 는 'ex'|'sh' 만 허용", bad)

        # --- 15~17. wf_wave.build_panel 배선(컨테이너/numpy 있을 때만) --------
        try:
            import numpy as np
            import wf_wave as W
        except Exception as e:                   # noqa: BLE001
            print(f"NOTE: wf_wave 배선 검사 건너뜀({type(e).__name__}: {e}) — "
                  f"컨테이너에서 돌리면 검사된다")
        else:
            pcache = os.path.join(td, "wire", "panel_w.npz")
            os.makedirs(os.path.dirname(pcache), exist_ok=True)
            np.savez_compressed(
                pcache, X=np.zeros((4, 2), dtype=np.float32),
                feature_names=np.array(["a", "b"]),
                dates=np.array(["2026-01-01", "2026-01-01", "2026-01-02", "2026-01-02"]),
                codes=np.array(["c1", "c2", "c1", "c2"]),
                price=np.ones(4, dtype=np.float64))
            noop = lambda *a: None
            # 15) 완성 패널 + 다른 프로세스 SH → 공존(성공적으로 재사용)
            h4 = ChildHolder(pcache, "sh")
            try:
                df, names = W.build_panel(pcache, 50, 420, log=noop)
                ok15 = (len(df) == 4 and list(names) == ["a", "b"])
                d15 = f"rows={len(df)}"
            except Exception as e:               # noqa: BLE001
                ok15, d15 = False, f"{type(e).__name__}: {e}"
            check("15 완성 패널 재사용은 SH 라 서로 막지 않는다", ok15, d15)
            h4.release()
            # 16) 완성 패널 + 다른 프로세스 EX → SH 획득 실패
            h5 = ChildHolder(pcache, "ex")
            try:
                W.build_panel(pcache, 50, 420, log=noop)
                ok16, d16 = False, "예외 없이 통과 = 가드 미배선"
            except panel_meta.PanelLockBusy as e:
                ok16, d16 = True, str(e)[:70]
            except Exception as e:               # noqa: BLE001
                ok16, d16 = False, f"PanelLockBusy 가 아닌 {type(e).__name__}: {e}"
            check("16 빌드(EX) 중 같은 패널 재사용 → PanelLockBusy", ok16, d16)
            h5.release()
            # 17) 미완성 패널(파일 없음) + 다른 프로세스 EX → 빌드 진입 전에 차단
            missing = os.path.join(td, "wire", "panel_new.npz")
            h6 = ChildHolder(missing, "ex")
            try:
                W.build_panel(missing, 50, 420, log=noop)
                ok17, d17 = False, "예외 없이 통과 = 가드 미배선"
            except panel_meta.PanelLockBusy:
                ok17, d17 = True, "빌드(DB) 진입 전 차단"
            except Exception as e:               # noqa: BLE001
                ok17, d17 = False, f"PanelLockBusy 가 아닌 {type(e).__name__}: {e}"
            check("17 신규 빌드는 EX — 중복 빌드를 DB 진입 전에 차단", ok17, d17)
            h6.release()

            # 18~19) RB2b ②: 저장된 feature_names 에 중복 라벨이 있어도 재사용 시
            #         이름이 유일화되고 X 열 수와 1:1 로 맞아야 한다(dedupe_names 는 '이름만'
            #         유일화 — 열을 늘리거나 줄이지 않으므로 shape 검사를 통과한다).
            dcache = os.path.join(td, "wire", "panel_dup.npz")
            np.savez_compressed(
                dcache, X=np.zeros((4, 4), dtype=np.float32),
                feature_names=np.array(["a", "b", "a", "b"]),   # 중복 2쌍
                dates=np.array(["2026-01-01", "2026-01-01", "2026-01-02", "2026-01-02"]),
                codes=np.array(["c1", "c2", "c1", "c2"]),
                price=np.ones(4, dtype=np.float64))
            try:
                df2, names2 = W.build_panel(dcache, 50, 420, log=noop)
                # npz X 는 (4,4) → 이름 4개, 전부 유일해야 한다(df2.shape[1] 은
                # date·stock_code·price 3열이 더해져 7 이므로 비교 대상이 아니다).
                ok18 = len(names2) == 4 and len(set(names2)) == len(names2)
                d18 = f"names={list(names2)}"
            except Exception as e:               # noqa: BLE001
                names2 = None
                ok18, d18 = False, f"{type(e).__name__}: {e}"
            check("18 중복 라벨 패널 재사용 → 이름 유일화·X 열과 1:1", ok18, d18)
            ok19 = bool(ok18 and names2 is not None and list(names2) == ["a", "b", "a__dup1", "b__dup1"])
            check("19 중복은 dedupe 규약(a,b,a__dup1,b__dup1)대로", ok19,
                  str(list(names2)) if ok18 else "N/A")

    n = len(RESULTS)
    nfail = sum(1 for _, ok, _ in RESULTS if not ok)
    print(f"\n== {n - nfail}/{n} PASS ==" + ("" if not nfail else f"  ({nfail} FAIL)"))
    for name, ok, detail in RESULTS:
        if not ok:
            print(f"  FAIL: {name} — {detail}")
    return 1 if nfail else 0


if __name__ == "__main__":
    sys.exit(main())

#!/usr/bin/env python3
"""_panel_meta_test — `scripts/panel_meta.py` 자체점검 (백로그 RB2 / F3).

이 스택엔 pytest 가 없다 → PASS/FAIL 을 출력하고 실패 시 exit 1 (기존 _*_test.py 관례).
표준 라이브러리만 쓰므로 **호스트·컨테이너 양쪽**에서 그대로 돈다:
    python3 scripts/_panel_meta_test.py
    docker exec -e PYTHONPATH=/app stock_xgboost_ml python /app/scripts/_panel_meta_test.py

검사 대상은 '사이드카 검증 로직'이며, 가드가 실제로 막는지는 STRICT 정책 함수로 확인한다.
성공 기준(RB2): '악화 유도 → 가드가 경고하거나 명시 실패'를 실측으로 보인다.
"""

import json
import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import panel_meta  # noqa: E402

RESULTS = []


def check(name, cond, detail=""):
    RESULTS.append((name, bool(cond), detail))
    print(f"[{'PASS' if cond else 'FAIL'}] {name}" + (f"  — {detail}" if detail else ""))


def touch_npz(path):
    """내용은 상관없다 — 존재만 하면 된다(검증은 사이드카만 본다)."""
    with open(path, "wb") as f:
        f.write(b"PK\x03\x04 fake npz")


BASE_REQ = {"universe": "prod", "universe_seed": 0, "limit": 200, "days": 420,
            "end_date": "2026-09-27", "universe_opts": {"market": "all"}}


def main():
    with tempfile.TemporaryDirectory() as td:
        # --- 1. 사이드카 부재(레거시 패널) → 'missing', 불일치 아님 --------
        p = os.path.join(td, "panel_a.npz")
        touch_npz(p)
        r = panel_meta.verify(p, request=BASE_REQ, current_code_sig=111.0)
        check("1 부재→missing", r["status"] == panel_meta.STATUS_MISSING, str(r["status"]))
        check("1b 부재→diffs 없음", r["diffs"] == [], str(r["diffs"]))
        check("1c 부재→error 없음", r["error"] is None, str(r["error"]))

        # --- 2. 기록 후 같은 조건 재사용 → 'ok' ---------------------------
        panel_meta.write_meta(p, {"code_sig": 111.0, **BASE_REQ})
        r = panel_meta.verify(p, request=BASE_REQ, current_code_sig=111.0)
        check("2 동일조건→ok", r["status"] == panel_meta.STATUS_OK, str(r))

        # --- 3. limit 불일치 → mismatch, 사유에 limit ---------------
        r = panel_meta.verify(p, request={**BASE_REQ, "limit": 50}, current_code_sig=111.0)
        check("3 limit불일치→mismatch", r["status"] == panel_meta.STATUS_MISMATCH, str(r["status"]))
        check("3b 사유에 limit", any("limit" in d for d in r["diffs"]), str(r["diffs"]))

        # --- 4. code_sig 변경(피처 코드 수정 후) → soft 'code_changed' ---------
        #      (스냅샷 불일치와 구분한다 — 주석/기본OFF 옵션 추가는 값이 안 바뀐다)
        r = panel_meta.verify(p, request=BASE_REQ, current_code_sig=222.0)
        check("4 code_sig변경→code_changed",
              r["status"] == panel_meta.STATUS_CODE_CHANGED, str(r["status"]))
        check("4b soft_diffs에 code_sig", any("code_sig" in d for d in r["soft_diffs"]), str(r["soft_diffs"]))
        check("4c hard_diffs 비어있음", r["hard_diffs"] == [], str(r["hard_diffs"]))
        # --- 4d. hard + soft 동시 → 'mismatch'(hard 우선), 둘 다 사유에 -------------
        r = panel_meta.verify(p, request={**BASE_REQ, "days": 90}, current_code_sig=222.0)
        check("4d hard+soft→mismatch", r["status"] == panel_meta.STATUS_MISMATCH, str(r["status"]))
        check("4e 사유에 둘 다", any("days" in d for d in r["hard_diffs"])
              and any("code_sig" in d for d in r["soft_diffs"]), str(r["diffs"]))

        # --- 5. end_date=None 요청은 비교 제외(매번 달라지는 값) -------------
        r = panel_meta.verify(p, request={**BASE_REQ, "end_date": None}, current_code_sig=111.0)
        check("5 end_date=None→ok", r["status"] == panel_meta.STATUS_OK, str(r["diffs"]))

        # --- 6. universe_opts 불일치 감지 ----------------------------------
        r = panel_meta.verify(p, request={**BASE_REQ, "universe_opts": {"market": "KOSDAQ"}},
                              current_code_sig=111.0)
        check("6 opts불일치→mismatch", r["status"] == panel_meta.STATUS_MISMATCH, str(r["status"]))

        # --- 7. 메타에 없는 키는 오탐하지 않는다 ---------------------------
        panel_meta.write_meta(p, {"code_sig": 111.0, "universe": "prod"})
        r = panel_meta.verify(p, request=BASE_REQ, current_code_sig=111.0)
        check("7 메타에 없는 키 오탐 없음", r["status"] == panel_meta.STATUS_OK, str(r["diffs"]))

        # --- 8. 깨진 사이드카 → missing + error(크래시 금지) ----------------
        p2 = os.path.join(td, "panel_b.npz")
        touch_npz(p2)
        with open(panel_meta.meta_path(p2), "w") as f:
            f.write("{ not json ")
        r = panel_meta.verify(p2, request=BASE_REQ, current_code_sig=111.0)
        check("8 깨진메타→missing", r["status"] == panel_meta.STATUS_MISSING, str(r["status"]))
        check("8b 깨진메타→error 기록", bool(r["error"]), str(r["error"]))
        check("8c 깨진메타→크래시 없음", True)

        # --- 9. STRICT 정책: 불일치만 막고, 부재·정상은 막지 않는다 --------
        check("9 mismatch+strict→막음", panel_meta.should_fail(panel_meta.STATUS_MISMATCH, True))
        check("9b mismatch+비strict→통과",
              not panel_meta.should_fail(panel_meta.STATUS_MISMATCH, False))
        check("9c missing+strict→통과(레거시 패널 보호)",
              not panel_meta.should_fail(panel_meta.STATUS_MISSING, True))
        check("9d ok+strict→통과", not panel_meta.should_fail(panel_meta.STATUS_OK, True))
        check("9e code_changed+strict→통과(soft 는 막지 않음)",
              not panel_meta.should_fail(panel_meta.STATUS_CODE_CHANGED, True))

        # --- 10. env_strict 파싱 -------------------------------------------
        old = os.environ.get("PANEL_META_STRICT")
        try:
            for v, exp in (("1", True), ("true", True), ("YES", True),
                           ("0", False), ("no", False), ("", False)):
                os.environ["PANEL_META_STRICT"] = v
                check(f"10 env_strict({v!r})", panel_meta.env_strict() is exp)
            os.environ.pop("PANEL_META_STRICT", None)
            check("10b env 미설정 기본 False", panel_meta.env_strict() is False)
        finally:
            if old is None:
                os.environ.pop("PANEL_META_STRICT", None)
            else:
                os.environ["PANEL_META_STRICT"] = old

        # --- 11. 원자적 쓰기: tmp 잔존 없음 + 재기록 멱등 -------------------
        p3 = os.path.join(td, "panel_c.npz")
        touch_npz(p3)
        panel_meta.write_meta(p3, {"code_sig": 1, "limit": 10})
        leftovers = [f for f in os.listdir(td) if f.startswith(".pmeta-")]
        check("11 tmp 잔존 없음", leftovers == [], str(leftovers))
        panel_meta.write_meta(p3, {"code_sig": 2, "limit": 20})
        m, e = panel_meta.read_meta(p3)
        check("11b 재기록 멱등(최신값)", m and m.get("limit") == 20 and m.get("code_sig") == 2,
              json.dumps(m, ensure_ascii=False))

        # --- 12. read_meta 왕복 + created_at 자동 --------------------------
        check("12 meta dict 반환", isinstance(m, dict) and "created_at" in m,
              str(list(m.keys()) if isinstance(m, dict) else m))
        check("12b 파일 없음→(None,None)", panel_meta.read_meta(os.path.join(td, "nope.npz")) == (None, None))

        # --- 13. int/float/str 혼재 정규화(명령은 문자열, 메타는 숫자) -------
        p4 = os.path.join(td, "panel_d.npz")
        touch_npz(p4)
        panel_meta.write_meta(p4, {"code_sig": 111, "limit": 200, "universe_seed": 0})
        r = panel_meta.verify(p4, request={"limit": "200", "universe_seed": "0",
                                           "universe": "prod"}, current_code_sig="111.0")
        check("13 타입 혼재→ok", r["status"] == panel_meta.STATUS_OK, str(r["diffs"]))

        # --- 14. 코드 서명 미전달 시 code_sig 비교 생략 ---------------------
        r = panel_meta.verify(p4, request={}, current_code_sig=None)
        check("14 코드서명 미전달→ok", r["status"] == panel_meta.STATUS_OK, str(r["diffs"]))

        # --- 15. 로그 문구 토큰(경고/불일치가 눈에 띄는가) ------------------
        line_ok = panel_meta.format_line(panel_meta.STATUS_OK, [], {"code_sig": 1})
        line_mm = panel_meta.format_line(panel_meta.STATUS_MISMATCH, ["limit: meta=1 request=2"], {})
        line_ms = panel_meta.format_line(panel_meta.STATUS_MISSING, [], None)
        check("15 OK 문구", "OK" in line_ok, line_ok)
        check("15b 불일치 문구 경고표시", "⚠" in line_mm and "limit" in line_mm, line_mm)
        check("15c 부재 문구", "없음" in line_ms, line_ms)
        line_cc = panel_meta.format_line(panel_meta.STATUS_CODE_CHANGED,
                                         ["code_sig: meta=1 now=2"], {})
        check("15d 코드변경 문구(⚠ 아님·확인요청)",
              "⚠" not in line_cc and "변경" in line_cc, line_cc)

        # --- 16. e2e: 실제 build_panel '재사용' 경로가 경고를 내는가 --------
        # 컨테이너 전용(wf_wave import 필요). PANEL_META_E2E=1 일 때만 돈다.
        if os.environ.get("PANEL_META_E2E") == "1":
            try:
                import numpy as np
                import wf_wave
            except Exception as e:                       # noqa: BLE001
                check("16 e2e 환경(wf_wave/numpy)", False, f"{type(e).__name__}: {e}")
            else:
                lines = []
                cache = os.path.join(td, "e2e_panel.npz")
                np.savez_compressed(
                    cache,
                    X=np.zeros((4, 2), dtype=np.float32),
                    feature_names=np.array(["f_a", "f_b"]),
                    dates=np.array(["2026-09-25", "2026-09-26", "2026-09-25", "2026-09-26"]),
                    codes=np.array(["005930", "005930", "000660", "000660"]),
                    price=np.ones(4, dtype=np.float64))
                # (a) 사이드카 없음 → '없음' 경고, 크래시 없음
                df0, names0 = wf_wave.build_panel(cache, limit=200, days=420,
                                                  log=lines.append, end_date="2026-09-27",
                                                  universe="prod")
                check("16a 재사용 로그에 meta 없음",
                      any("panel meta 없음" in s for s in lines), " / ".join(lines))
                check("16b 재사용 성공(크래시 없음)", df0.shape[0] == 4 and list(names0) == ["f_a", "f_b"],
                      f"{df0.shape} {names0}")
                # (b) 불일치 사이드카 → ⚠ 경고 (기본 = 경고만, 예외 아님)
                lines.clear()
                panel_meta.write_meta(cache, {"code_sig": 999.0, "universe": "prod",
                                              "limit": 50, "days": 420})
                df1, _ = wf_wave.build_panel(cache, limit=200, days=420, log=lines.append,
                                             end_date="2026-09-27", universe="prod")
                check("16c 불일치→⚠ 경고 로그",
                      any("panel meta 불일치" in s for s in lines), " / ".join(lines))
                check("16d 기본은 경고(거부 아님)", df1.shape[0] == 4)
                # (c) STRICT → 같은 조건에서 명시 실패
                lines.clear()
                os.environ["PANEL_META_STRICT"] = "1"
                try:
                    wf_wave.build_panel(cache, limit=200, days=420, log=lines.append,
                                        end_date="2026-09-27", universe="prod")
                    check("16e STRICT→명시 실패", False, "예외가 나지 않았다")
                except RuntimeError as e:
                    check("16e STRICT→명시 실패", "panel meta 불일치" in str(e), str(e)[:120])
                finally:
                    os.environ.pop("PANEL_META_STRICT", None)
                # (d) 조건은 같은데 코드서명만 다름 → '코드 변경' 안내(⚠ 아님).
                #     ★현실 상황: 기존 패널 전부가 이 경우다(universe/limit/days 는 맞지만
                #     feature_engine 이 그 뒤 편집됨) → 거짓 경보로 작업을 막지 않는지 확인.
                lines.clear()
                panel_meta.write_meta(cache, {"code_sig": 999.0, "universe": "prod",
                                              "limit": 200, "days": 420,
                                              "end_date": "2026-09-27"})
                os.environ["PANEL_META_STRICT"] = "1"
                try:
                    df2, _ = wf_wave.build_panel(cache, limit=200, days=420, log=lines.append,
                                                 end_date="2026-09-27", universe="prod")
                    check("16f 코드서명만 다름→경고·통과(STRICT 도 막지 않음)",
                          any("피처 코드가 이 패널 빌드 이후 변경됨" in s for s in lines)
                          and df2.shape[0] == 4, " / ".join(lines))
                finally:
                    os.environ.pop("PANEL_META_STRICT", None)

    n = len(RESULTS)
    nfail = sum(1 for _, ok, _ in RESULTS if not ok)
    print(f"\n== {n - nfail}/{n} PASS ==" + ("" if not nfail else f"  ({nfail} FAIL)"))
    return 1 if nfail else 0


if __name__ == "__main__":
    sys.exit(main())

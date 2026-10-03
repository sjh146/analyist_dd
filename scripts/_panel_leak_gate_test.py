#!/usr/bin/env python3
"""CG85 셋업 자체점검 — 패널 누수 게이트 + 구동기 배선(parse/judge/summary_path).

numpy 가 필요하므로 **컨테이너에서** 돈다:
  docker exec -e PYTHONPATH=/app stock_xgboost_ml python /app/scripts/_panel_leak_gate_test.py

검사:
  [1] classify — 종목별 유니크 3(청정) → CLEAN
  [2] classify — 비영인데 종목 상수 → LEAKY(+컬럼 목록)
  [3] classify — 종목 상수지만 비영 < 5%(데이터 부재) → CLEAN (누수로 단정하지 않는다)
  [4] summary_path('panel_leak_gate', ...) 이 --json-out 을 호스트/컨테이너 경로로 매핑
  [5] parse_panel_leak_gate — 스키마 파싱 + per_exp 를 만들지 않는다
  [6] judge_panel_leak_gate — LEAKY → '누수 발견' / CLEAN → '누수 없음' / require_clean 부분집합
  [7] e2e — 합성 npz 로 스크립트 실행 → JSON 산출 + 판정 일치
"""
import json
import os
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

FA = FB = 0
FAILS = []


def check(name, cond, got=""):
    global FA, FB
    if cond:
        FA += 1
        print(f"  PASS {name}")
    else:
        FB += 1
        FAILS.append(name)
        print(f"  FAIL {name} {got}")


def _panel(n_stocks=4, n_dates=12, constant=False, zero=False, two_valued=False, cols=None):
    """합성 패널 — codes/dates/feature_names/X.

    n_dates 기본 12: value_* 는 '일별 변동' 계층(청정이면 유니크 ≥ 10)이라 6일짜리 합성 패널은
    누수로 오판된다(그게 실제 규칙이다).
    """
    import numpy as np
    names = cols or ["value_pbr", "quality_roa", "momentum_1m", "noise_feat"]
    codes, dates, rows = [], [], []
    for s in range(n_stocks):
        for d in range(n_dates):
            codes.append(f"{s:06d}")
            dates.append(f"2026-01-{d+1:02d}")
            vals = []
            for i, n in enumerate(names):
                if constant:
                    vals.append(0.5 if not zero else 0.0)
                elif two_valued and n in ("value_pbr", "value_per", "value_psr", "value_pcr"):
                    vals.append(0.5 if d < n_dates // 2 else 0.6)     # 종목당 유니크 2 (거의 상수)
                else:
                    vals.append(0.1 * (s + 1) + 0.01 * (d + 1) + 0.001 * i)
            rows.append(vals)
    return names, np.array(rows, dtype=float), codes, dates


def main():
    import numpy as np
    import panel_leak_gate as g
    import model_engineer_cycle as m

    print("[1] classify — 청정(유니크 3)")
    r = g.classify(*_panel(constant=False))
    check("verdict CLEAN", r["verdict"] == "CLEAN", got=r["verdict"])
    check("leaky_columns 비어 있음", r["leaky_columns"] == [], got=str(r["leaky_columns"]))
    check("offenders 기록", r["offenders"]["value_pbr"]["median_unique"] > 1,
          got=str(r["offenders"].get("value_pbr")))

    print("[2] classify — 비영 종목상수(누수 지문)")
    r2 = g.classify(*_panel(constant=True))
    check("verdict LEAKY", r2["verdict"] == "LEAKY", got=r2["verdict"])
    check("누수 컬럼에 value_pbr", "value_pbr" in r2["leaky_columns"], got=str(r2["leaky_columns"]))
    check("quality_roa 도 잡힘", "quality_roa" in r2["leaky_columns"], got=str(r2["leaky_columns"]))
    check("OFFENDERS 밖 피처는 무시(momentum_1m)", "momentum_1m" not in r2["offenders"])

    print("[3] classify — 상수지만 전부 0(데이터 부재) → CLEAN")
    r3 = g.classify(*_panel(constant=True, zero=True))
    check("verdict CLEAN", r3["verdict"] == "CLEAN", got=f"{r3['verdict']} {r3['leaky_columns']}")

    print("[3b] classify — '거의 상수'(유니크 2) price ratio → LEAKY")
    r3b = g.classify(*_panel(two_valued=True))
    check("verdict LEAKY", r3b["verdict"] == "LEAKY", got=r3b["verdict"])
    check("value_pbr 잡힘(임계 10)", "value_pbr" in r3b["leaky_columns"],
          got=str(r3b["leaky_columns"]))
    check("quality_roa 는 통과(임계 2)", "quality_roa" not in r3b["leaky_columns"],
          got=str(r3b["leaky_columns"]))

    print("[4] summary_path 배선")
    cmd = ("docker exec stock_xgboost_ml python /app/scripts/panel_leak_gate.py "
           "--json-out /app/reports/overnight/cg85_panel_gate.json")
    sp = m.summary_path("panel_leak_gate", cmd)
    check("--json-out → 서비스 경로", sp.endswith("xgboost-ml/reports/overnight/cg85_panel_gate.json"),
          got=sp)
    check("--json-out 없으면 빈 경로", m.summary_path("panel_leak_gate", "python x.py") == "")

    print("[5-6] parse / judge")
    with tempfile.TemporaryDirectory() as tmp:
        jp = os.path.join(tmp, "gate.json")
        payload = {
            "generated_at": "2026-10-03T14:20:00+00:00", "n_panels": 3,
            "n_clean": 1, "n_leaky": 2,
            "clean": ["panel_prod200.npz"],
            "leaky": ["panel_150u.npz", "panel_995.npz"],
            "missing": [],
            "panels": {
                "panel_prod200.npz": {"verdict": "CLEAN", "rows": 54800, "cols": 213,
                                      "n_stocks": 200, "date_min": "2025-08-04",
                                      "date_max": "2026-09-23", "leaky_columns": []},
                "panel_150u.npz": {"verdict": "LEAKY", "rows": 41893, "cols": 210,
                                   "n_stocks": 150, "leaky_columns": ["quality_roa", "value_per"]},
            },
        }
        with open(jp, "w", encoding="utf-8") as f:
            json.dump(payload, f)
        p = m.parse_panel_leak_gate(jp, 0.0)
        check("error 없음", not p.get("error"), got=str(p.get("error")))
        check("per_exp 를 만들지 않음", "per_exp" not in p)
        check("leaky 목록 유지", p["leaky"] == ["panel_150u.npz", "panel_995.npz"])

        item = {"metric": "panel_leak_gate", "id": "CG85"}
        v, d, dl = m.judge_panel_leak_gate(item, p)
        check("전체 스캔 LEAKY → 누수 발견", v == "누수 발견", got=v)
        check("delta = 누수 패널 수", dl == 2.0, got=str(dl))

        item_req = {"metric": "panel_leak_gate", "id": "CG85",
                    "require_clean": ["panel_prod200.npz"]}
        v2, _, _ = m.judge_panel_leak_gate(item_req, p)
        check("require_clean 만 보면 누수 없음", v2 == "누수 없음", got=v2)

        item_req2 = {"metric": "panel_leak_gate", "id": "CG85",
                     "require_clean": ["panel_150u.npz"]}
        v3, _, _ = m.judge_panel_leak_gate(item_req2, p)
        check("require_clean 이 누수면 발견", v3 == "누수 발견", got=v3)

        v4, _, dl4 = m.judge_panel_leak_gate(item, {"error": "요약 파일 없음"})
        check("error → 판정불가", v4 == "판정불가" and dl4 is None, got=v4)

        print("[7] e2e — 합성 npz 로 스크립트 실행")
        names, X, codes, dates = _panel(constant=True)
        good_names, Xg, gc, gd = _panel(constant=False)
        np.savez(os.path.join(tmp, "panel_leakydemo.npz"), feature_names=np.array(names),
                 X=X, codes=np.array(codes), dates=np.array(dates))
        np.savez(os.path.join(tmp, "panel_cleandemo.npz"), feature_names=np.array(good_names),
                 X=Xg, codes=np.array(gc), dates=np.array(gd))
        out = os.path.join(tmp, "gate_out.json")
        r = subprocess.run([sys.executable, os.path.join(HERE, "panel_leak_gate.py"),
                            "--dir", tmp, "--json-out", out],
                           capture_output=True, text=True, timeout=180)
        ok = r.returncode == 0 and os.path.exists(out)
        check("rc=0 · JSON 산출", ok, got=(r.stdout[-300:] + r.stderr[-300:]))
        if ok:
            with open(out, encoding="utf-8") as f:
                dd = json.load(f)
            check("leakydemo LEAKY", dd["panels"]["panel_leakydemo.npz"]["verdict"] == "LEAKY")
            check("cleandemo CLEAN", dd["panels"]["panel_cleandemo.npz"]["verdict"] == "CLEAN")
            r2 = subprocess.run([sys.executable, os.path.join(HERE, "panel_leak_gate.py"),
                                 "--dir", tmp, "--strict"],
                                capture_output=True, text=True, timeout=180)
            check("--strict 는 누수 시 rc=1", r2.returncode == 1, got=str(r2.returncode))

    print(f"\n결과: {FA} PASS / {FB} FAIL")
    if FAILS:
        print("실패:", ", ".join(FAILS))
    return 1 if FB else 0


if __name__ == "__main__":
    sys.exit(main())

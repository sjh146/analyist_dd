#!/usr/bin/env python3
"""구동기 metric `screen`(DB 원천 컬럼 스크린) 배선 회귀 테스트 (2026-11-14).

WHY: CG270 이 metric='screen' 으로 등록됐는데 `summary_path`·`parse_by_metric`·`judge_by_metric`
어디에도 분기가 없어, rc=0 완주(13~20초)가 원장에 "판정불가(parser 없음)"으로 남았다. 스크린은
패널 재빌드(1~2시간) 전 게이트라 값 자체가 다음 레버 결정의 근거인데, 판정이 사라지면
'데이터 축 종결'도 근거 없이 남는다. 게다가 이 항목의 커맨드는 산출물을 `--out` 이 아니라
**셸 리다이렉트**(`>`)로 쓰므로 `_arg` 만으로는 경로를 못 찾는다.

검증하는 것:
  ① summary_path 가 셸 리다이렉트(`>`)와 `--out` 둘 다 해석
  ② 실제 산출물(cg271 q0.30) → '후보 없음' · delta=None
  ③ 문턱을 넘는 **살아있는** 후보 → '데이터 축 후보 있음'
  ④ 문턱은 넘지만 커버리지/종목상수에서 죽은 후보(short_selling_ratio 형) → 여전히 '후보 없음'
  ⑤ 오류 경로(빈 경로·파일 없음·mtime 미갱신)는 '판정불가'
  ⑥ per_exp 를 만들지 않는다(스크린은 arm 실험이 아니다 — 스코어보드 오독 방지)

실행: python3 scripts/_db_screen_metric_test.py
"""
from __future__ import annotations

import importlib.util
import json
import os
import sys
import tempfile

PROJ = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(PROJ, "scripts"))

REAL = os.path.join(PROJ, "services/xgboost-ml/reports/overnight/cg271_db_screen_q30.json")


def load_cycle():
    spec = importlib.util.spec_from_file_location(
        "model_engineer_cycle", os.path.join(PROJ, "scripts/model_engineer_cycle.py"))
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


def write(payload: dict) -> str:
    fd, p = tempfile.mkstemp(suffix=".json")
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False)
    return p


def feat(auc, cov=0.9, const=0.0, mkt=0.0):
    return {"coverage": cov, "stock_const_ratio": const, "market_level_rate": mkt,
            "single_feature_auc": auc, "xs_ic_mean": 0.01, "xs_ic_t": 1.0, "n_ic_dates": 200}


def run() -> int:
    m = load_cycle()
    fails = []

    def check(name, cond, extra=""):
        print(("PASS " if cond else "FAIL ") + name + ("" if cond else f"  → {extra}"))
        if not cond:
            fails.append(name)

    item = {"id": "CG270", "metric": "screen", "screen_threshold": 0.065, "screen_cov_min": 0.05}

    # ① 경로 해석 — 리다이렉트 / --out / 기본
    cmd = ("docker exec stock_xgboost_ml sh -c 'cd /app && python scripts/_db_feature_screen.py "
           "> reports/overnight/cg270_db_screen.json'")
    p = m.summary_path("screen", cmd)
    check("① 리다이렉트 대상 해석", p.endswith("services/xgboost-ml/reports/overnight/cg270_db_screen.json"), p)
    p2 = m.summary_path("screen", "docker exec x python s.py --out /app/reports/overnight/a.json")
    check("① --out 우선", p2.endswith("services/xgboost-ml/reports/overnight/a.json"), p2)
    p3 = m.summary_path("screen", "python s.py")
    check("① 기본 경로 폴백", p3.endswith("overnight/db_feature_screen.json"), p3)
    check("① metric 별칭 db_feature_screen", m.summary_path("db_feature_screen", cmd) == p, m.summary_path("db_feature_screen", cmd))

    # ② 실제 산출물 — 두 라벨 모두 문턱 미달
    if os.path.exists(REAL):
        pm = m.parse_by_metric(item, REAL, 0.0)
        check("② 실제 파일 파싱", not pm.get("error"), pm.get("error"))
        v, d, delta = m.judge_by_metric(item, pm)
        check("② 후보 없음 판정", v.startswith("후보 없음"), v)
        check("② delta None(arm 아님)", delta is None, delta)
        check("② detail 에 채점 수", "채점" in d, d)
    else:
        check("② 실제 파일 존재", False, REAL)

    # ③ 문턱 통과 살아있는 후보
    p_ok = write({"panel": {"file": "panel_x.npz"}, "features": {
        "t.a": feat(0.575), "t.b": feat(0.505)}})
    pm = m.parse_by_metric(item, p_ok, 0.0)
    v, d, delta = m.judge_by_metric(item, pm)
    check("③ 후보 있음 판정", v.startswith("데이터 축 후보 있음"), v)
    check("③ delta = |AUC−0.5|", abs((delta or 0) - 0.075) < 1e-9, delta)

    # ④ 문턱은 넘지만 커버리지/종목상수에서 죽은 후보 → 후보 없음 + 탈락 문구
    p_dead = write({"panel": {"file": "panel_x.npz"}, "features": {
        "t.hi_auc_dead_cov": feat(0.571, cov=0.018, const=0.94),
        "t.const": feat(0.600, cov=0.9, const=1.0),
        "t.mkt": feat(0.600, cov=0.9, mkt=1.0),
        "t.alive": feat(0.503)}})
    pm = m.parse_by_metric(item, p_dead, 0.0)
    v, d, delta = m.judge_by_metric(item, pm)
    check("④ 죽은 후보는 후보 아님", v.startswith("후보 없음"), v)
    check("④ 탈락 사유 문구", "탈락" in d, d)

    # ⑤ 오류 경로
    check("⑤ 빈 경로", "판정불가" == m.judge_by_metric(item, m.parse_by_metric(item, "", 0.0))[0])
    check("⑤ 파일 없음", "판정불가" == m.judge_by_metric(
        item, m.parse_by_metric(item, "/tmp/없는파일_xyz.json", 0.0))[0])
    p_old = write({"panel": {"file": "p.npz"}, "features": {"t.a": feat(0.6)}})
    st = m.parse_by_metric(item, p_old, os.path.getmtime(p_old) + 1)
    check("⑤ mtime 미갱신", "판정불가" == m.judge_by_metric(item, st)[0], st.get("error"))

    # ⑥ per_exp 미생성
    pm = m.parse_by_metric(item, p_ok, 0.0)
    check("⑥ per_exp 없음", "per_exp" not in pm, list(pm.keys()))

    for q in (p_ok, p_dead, p_old):
        os.remove(q)
    print(f"\n{'ALL PASS' if not fails else 'FAILED: ' + ', '.join(fails)}")
    return 0 if not fails else 1


if __name__ == "__main__":
    sys.exit(run())

#!/usr/bin/env python3
"""data/state/edge_params.json 생성기 — 백테스트 recommendation 을 배선 파라미터로 옮긴다.

WHY (docs/spec_feed_edge_score.md §3, 2026-11-17): 엣지 게이트는 발행측(피드)과 소비측
(트레이더, spec_trader_edge_gate.md)이 **같은 문턱**을 봐야 한다. 백테스트
(data/reports/edge_stack_backtest.json)의 recommendation 을 단일 배선 파일
data/state/edge_params.json 으로 기록해 하드코딩을 없앤다.

실측(2026-11-17):
    ls data/reports/edge_stack_backtest.json → 16:29 에 병렬 실행된 백테스트가 산출
    recommendation = {"found": false, "threshold": null, "stop": null, "tp": null,
                      "why": "추천 조건(①②③④ 모두)을 만족하는 조합 없음. ..."}
백테스트가 추천할 조합을 찾지 못했다 → threshold=null(+ found/why/limits 보존)을 기록한다.
추천이 생기면 이 스크립트를 재실행하면 된다(스펙 §3: 하드코딩 금지 — 항상 백테스트를 읽는다).
백테스트가 아예 없던 시점에는 {"threshold": null, "source": "backtest_missing"} 을 기록한다.

멱등: 매 실행마다 백테스트를 **다시 읽어** 같은 내용을 tmp+replace 로 원자 교체한다.
실행 시각을 기록하지 않으므로 같은 입력이면 같은 바이트다.
자기신고(3분리): dq_claim.record_claim 재사용 — source_rows=백테스트 유무(0/1),
claimed_rows=1(파서가 만든 파라미터 1건), persisted_rows=1(실제 기록한 파일 1건).
자기신고 실패는 WARN 만 남기고 파일 기록을 깨지 않는다(dq_claim 설계 규약).

사용:
    /usr/bin/python3 scripts/build_edge_params.py
"""
from __future__ import annotations

import json
import logging
import os
import sys

PROJ = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if os.path.dirname(os.path.abspath(__file__)) not in sys.path:
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

BACKTEST_PATH = os.path.join(PROJ, "data", "reports", "edge_stack_backtest.json")
OUT_PATH = os.path.join(PROJ, "data", "state", "edge_params.json")

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger("build_edge_params")


def build_params():
    """백테스트 산출물 → 배선 파라미터 dict. 없으면 backtest_missing."""
    if os.path.exists(BACKTEST_PATH):
        with open(BACKTEST_PATH, encoding="utf-8") as fh:
            data = json.load(fh)
        rec = data.get("recommendation") or {}
        # 스펙 §3 스키마 중 확실한 것만 채운다. stop_mode/atr_stop_mult/atr_tp_mult/
        # min_rr 은 recommendation 의 stop/tp 표기가 확정된 뒤 매핑한다 —
        # 지금은 추측 매핑 금지(규칙: 추측 금지). stop/tp 는 원본 그대로 보존하고,
        # 추천이 없을 때(found=false) 그 이유(why/limits)를 함께 기록해 소비측이
        # threshold=null 의 의미를 오해하지 않게 한다.
        params = {
            "threshold": rec.get("threshold"),
            "stop": rec.get("stop"),
            "tp": rec.get("tp"),
            "min_rr": None,
            "recommendation": {
                "found": rec.get("found"),
                "why": rec.get("why"),
                "limits": rec.get("limits"),
            },
            "source": "data/reports/edge_stack_backtest.json",
            "measured": {
                "data_range": data.get("data_range"),
                "signals": data.get("signals"),
                "edge_score_dist": data.get("edge_score_dist"),
            },
        }
        return params, 1, "backtest_found found=%r threshold=%r" % (
            rec.get("found"), params["threshold"])
    return {"threshold": None, "source": "backtest_missing"}, 0, "backtest_missing"


def _record_claim(source_rows, persisted_rows, note):
    try:
        from dq_claim import _open_conn, record_claim  # noqa: PLC0415
        conn = _open_conn()
        try:
            record_claim(conn, runner="build_edge_params", table_name="edge_params",
                         source_rows=source_rows, claimed_rows=1,
                         persisted_rows=persisted_rows, note=note)
        finally:
            conn.close()
    except Exception as e:  # noqa: BLE001
        logger.warning("자기신고 기록 실패(%s: %s) — 파일 기록에는 영향 없음",
                       type(e).__name__, e)


def main():
    params, source_rows, note = build_params()
    os.makedirs(os.path.dirname(OUT_PATH), exist_ok=True)
    tmp = OUT_PATH + ".tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(params, fh, ensure_ascii=False, indent=1)
        fh.write("\n")
    os.replace(tmp, OUT_PATH)  # 원자 교체
    logger.info("기록 완료: %s (threshold=%r, source=%r)", OUT_PATH,
                params.get("threshold"), params.get("source"))
    _record_claim(source_rows, 1, note)
    return 0


if __name__ == "__main__":
    sys.exit(main())

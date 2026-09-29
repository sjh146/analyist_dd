#!/usr/bin/env python3
"""스크리너 전체 스코어 유니버스 파일 — 쓰기/읽기/백분위 (stdlib only).

WHY (피드 계약 v1.1 ③ 백분위 계약)
스크리너는 원래 top20 CSV 만 남겼다. 그래서 발행측은 "이 후보가 모델 분포에서 상위 몇 %인지"를
알 수 없었고, 트레이더의 R1 문턱은 확률 **절대값**(예: 평균 0.58)에 묶여 모델 분포가 이동하면
의미를 잃었다(2026-09-29: swing 확률이 0.50~0.62 에 몰려 경로가 닫힘). 백분위는 "모델 자체
순위에서 상위 몇 %"라 레짐·모델 교체에 강건하다.

형식 (JSON)
    {"date": "2026-09-28", "scored": 300,
     "scores": [{"stock_code": "475830", "confidence": 0.6245}, ...]}

이 모듈은 의존성이 없어서 스크리너(컨테이너, /opt/scripts ro 마운트)와 발행측·테스트가
같은 코드를 쓴다 — 파일 형식이 양쪽에서 어긋나지 않게 하는 것이 목적이다.
"""
from __future__ import annotations

import json
import os
from typing import Any, Dict, Iterable, List, Optional


def dump_universe(candidates: Iterable[Dict[str, Any]], path: str, day: str) -> Optional[str]:
    """전체 스코어 유니버스(코드·확률)를 JSON 으로 남기고 경로를 돌려준다.

    확률(confidence)이 없는 항목은 넣지 않는다 — 0 으로 채우면 백분위가 왜곡된다.
    """
    if not path:
        return None
    scores: List[Dict[str, Any]] = []
    for candidate in candidates or []:
        try:
            code = str(candidate.get("stock_code") or "").strip()
            conf = float(candidate.get("confidence"))
        except (AttributeError, TypeError, ValueError):
            continue
        if not code:
            continue
        scores.append({"stock_code": code, "confidence": round(conf, 4)})
    if not scores:
        return None
    payload = {"date": str(day), "scored": len(scores), "scores": scores}
    directory = os.path.dirname(path)
    if directory:
        os.makedirs(directory, exist_ok=True)
    with open(path, "w", encoding="utf-8") as handle:
        json.dump(payload, handle, ensure_ascii=False)
    return path


def load_scores(path: str) -> Dict[str, float]:
    """유니버스 파일 → ``{code: confidence}``. 없거나 깨졌으면 빈 dict(추정하지 않는다)."""
    try:
        with open(path, encoding="utf-8") as handle:
            payload = json.load(handle)
    except (OSError, ValueError):
        return {}
    if not isinstance(payload, dict):
        return {}
    result: Dict[str, float] = {}
    for row in payload.get("scores") or []:
        if not isinstance(row, dict):
            continue
        code = str(row.get("stock_code") or "").strip().lstrip("A")
        try:
            confidence = float(row.get("confidence"))
        except (TypeError, ValueError):
            continue
        if code:
            result[code] = confidence
    return result


def percentile_of(value: Optional[float], population: Iterable[float]) -> Optional[float]:
    """*value* 의 백분위(0~100). 동일값은 중간 순위로 본다. 표본/값이 없으면 None."""
    if value is None:
        return None
    values = [float(item) for item in population if item is not None]
    if not values:
        return None
    below = sum(1 for item in values if item < value)
    equal = sum(1 for item in values if item == value)
    return round(100.0 * (below + 0.5 * equal) / len(values), 2)

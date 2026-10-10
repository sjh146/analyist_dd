"""
퇴화일 가드 — 하루 예측이 단일 상수면 **발행 전에** 감지해 흔적을 남긴다.

측정정합성·생산 (2026-10-10 CG160, 엔지니어 자율).

WHY (실측 근거):
  2026-09-22 발행분은 `ml_predictions` 2,678행이 **전부 같은 값 0.1429** 였다(11초 만에 배치 종료).
  퇴화일에서는 종목 간 순위가 정의상 존재하지 않으므로 그날의 전방 AUC 는 정확히 0.5 다 —
  그런데 시스템은 이 사실을 어디에도 남기지 않고 그대로 발행했다. 소비자(트레이더)는 top-k 를
  뽑을 수 없고, 사후 감사(`scripts/forward_live_audit.py`)에서야 '퇴화 날짜'로 드러난다.
  CG159(학습 밖 ETF/ETN 이 상수 confidence 로 발행)와 함께 **전방 확률 스케일 이상**의 두 번째
  결함이며, 이 둘은 서로 다른 원인이라 한쪽 필터로 다른 쪽이 고쳐지지 않는다(CG159 note 참조).

무엇을 하는가 (범위를 의도적으로 좁게):
  ① 발행 직전(저장 루프 앞)에 그날 예측의 distinct(confidence)/n 을 계산한다.
  ② distinct == 1 이거나 distinct 비율 < 1% 면 **로그 CRITICAL** + 증거 파일을 남긴다.
  ③ **발행 목록 자체는 절대 바꾸지 않는다** — 발행 리스트 계약 변경은 리뷰보드 승인 대상이고
     (헌장 §3-B), 이 항목은 '감지·표시'까지만 담당한다. 그래서 승인 없이 배선할 수 있다.
  ④ 경보 기록 실패가 발행을 막지 않는다(가드는 부가 기능 — 예외를 밖으로 던지지 않는다).

증거 파일: `<report_dir>/degenerate_day_<YYYY-MM-DD>.json`
  기본 report_dir = `/app/reports` (= 호스트 `services/xgboost-ml/reports`) — 컨테이너에서 쓰는
  기존 증거 위치와 동일하다(예: `/app/reports/overnight/*.jsonl`). `DEGENERATE_DAY_REPORT_DIR`
  환경변수로 덮어쓸 수 있다.

자체점검: `docker exec stock_xgboost_ml sh -c 'cd /app && python -u scripts/_degenerate_day_guard_test.py'`
"""
from __future__ import annotations

import json
import logging
import os
from collections import Counter
from datetime import datetime
from typing import Dict, Optional, Sequence

logger = logging.getLogger(__name__)

DEGENERATE_DAY_REPORT_DIR_ENV = "DEGENERATE_DAY_REPORT_DIR"
DEFAULT_REPORT_DIR = "/app/reports"
FILE_PREFIX = "degenerate_day_"
PRIOR_RUN_FILE_PREFIX = "prior_run_"
MIN_DISTINCT_RATIO = 0.01  # distinct(confidence)/n 하한 — 이 미만이면 '사실상 단일값'
_ROUND_NDIGITS = 6


def _as_confidence(value) -> Optional[float]:
    """예측 dict 또는 숫자에서 confidence 를 꺼낸다. NaN/비수치는 None(집계 제외)."""
    try:
        raw = value.get("confidence") if hasattr(value, "get") else value
        v = float(raw)
    except Exception:
        return None
    if v != v:  # NaN
        return None
    return round(v, _ROUND_NDIGITS)


def confidence_uniqueness(predictions: Sequence) -> Dict:
    """confidence 분포 요약. 예측 목록을 읽기만 한다(수정 금지)."""
    vals = [v for v in (_as_confidence(p) for p in (predictions or [])) if v is not None]
    n = len(vals)
    if n == 0:
        return {"n": 0, "n_distinct": 0, "top_value": None, "top_share": None,
                "distinct_ratio": None}
    counts = Counter(vals)
    top_value, top_count = counts.most_common(1)[0]
    return {
        "n": n,
        "n_distinct": len(counts),
        "top_value": top_value,
        "top_share": round(top_count / n, 6),
        "distinct_ratio": round(len(counts) / n, 6),
    }


def degenerate_reason(stats: Dict, min_distinct_ratio: float = MIN_DISTINCT_RATIO) -> Optional[str]:
    """퇴화 사유. 아니면 None. n<2 는 퇴화가 아니다(1행·0행은 '하루 전체 상수'가 아니다)."""
    n = stats.get("n") or 0
    if n < 2:
        return None
    if stats.get("n_distinct") == 1:
        return "single_value"
    ratio = stats.get("distinct_ratio")
    if ratio is not None and ratio < min_distinct_ratio:
        return "low_distinct"
    return None


def _infer_date(predictions: Sequence) -> Optional[str]:
    """예측에 실린 발행 예정일(prediction_date)의 최빈값. 없으면 None."""
    dates = []
    for p in (predictions or []):
        if hasattr(p, "get"):
            d = p.get("prediction_date")
            if d:
                dates.append(str(d)[:10])
    if not dates:
        return None
    return Counter(dates).most_common(1)[0][0]


def check_and_report_degenerate_day(
    predictions: Sequence,
    *,
    date: Optional[str] = None,
    report_dir: Optional[str] = None,
    min_distinct_ratio: float = MIN_DISTINCT_RATIO,
) -> Dict:
    """발행 직전 퇴화일 감지. 반환 = 판정 dict(테스트·감사용). 예외를 밖으로 던지지 않는다.

    **입력 `predictions` 는 절대 수정하지 않는다**(발행 계약 무변경).
    """
    resolved_date = date or _infer_date(predictions) or datetime.now().strftime("%Y-%m-%d")
    stats = confidence_uniqueness(predictions)
    reason = degenerate_reason(stats, min_distinct_ratio)

    out = {
        "metric": "degenerate_day_guard",
        "date": resolved_date,
        "degenerate": reason is not None,
        "reason": reason,
        "min_distinct_ratio": min_distinct_ratio,
        "report_path": None,
        "checked_at": datetime.now().isoformat(timespec="seconds"),
        **stats,
    }
    if reason is None:
        return out

    logger.critical(
        "퇴화일 감지(%s): %s 예측 %d행 · distinct %d (%.3f%%) · 최빈값 %s (비중 %.1f%%) "
        "— 발행 목록은 변경하지 않는다(감지·표시 전용)",
        reason, resolved_date, stats["n"], stats["n_distinct"],
        100.0 * (stats["distinct_ratio"] or 0.0),
        stats["top_value"], 100.0 * (stats["top_share"] or 0.0),
    )
    target_dir = (
        report_dir
        or os.environ.get(DEGENERATE_DAY_REPORT_DIR_ENV)
        or DEFAULT_REPORT_DIR
    )
    try:
        os.makedirs(target_dir, exist_ok=True)
        path = os.path.join(target_dir, f"{FILE_PREFIX}{resolved_date}.json")
        with open(path, "w", encoding="utf-8") as fh:
            json.dump(out, fh, ensure_ascii=False, indent=2, sort_keys=True)
        out["report_path"] = path
    except Exception as exc:  # 경보 기록 실패가 발행을 막아서는 안 된다
        logger.error("퇴화일 증거 파일 기록 실패(dir=%s): %s", target_dir, exc)
        out["report_error"] = str(exc)
    return out


def prior_run_reason(existing: Optional[int], n_new: Optional[int]) -> Optional[str]:
    """같은 prediction_date 에 이미 행이 있는데 이번 실행이 행을 더하려는가.

    - existing None/0 → None(첫 실행 = 정상)
    - 0 < existing < n_new → "partial_mix"  (실측 09-23: 이미 2,770행 + 이번 4,314행 → 1,544행 추가
      = 한 날짜의 행 집합이 **두 시점 상태의 혼합**. 먼저 들어간 2,770행은 DO NOTHING 으로 고정)
    - existing >= n_new → "rerun_no_add"  (전량 중복 = 이번 실행은 아무것도 못 넣는다)
    - n_new 0/None → None(집계 대상 아님)
    """
    if not n_new or existing is None or existing <= 0:
        return None
    if existing < n_new:
        return "partial_mix"
    return "rerun_no_add"


def check_and_report_prior_run(
    existing: Optional[int],
    n_new: Optional[int],
    *,
    date: Optional[str] = None,
    report_dir: Optional[str] = None,
) -> Dict:
    """발행 직전 '같은 날짜의 기존 행' 경보(CG161). **DB·발행 목록을 바꾸지 않는다**(감지 전용).

    혼합의 원인은 `save_prediction` 의 `ON CONFLICT DO NOTHING` + 하루 두 번 실행이다.
    교체(선행 행 삭제/덮어쓰기)는 발행 계약·데이터 변경이라 리뷰보드 승인 대상이므로 여기서 하지 않는다.
    """
    reason = prior_run_reason(existing, n_new)
    out = {
        "metric": "prior_run_guard",
        "date": date,
        "existing_rows": existing,
        "new_rows": n_new,
        "prior_run": reason is not None,
        "reason": reason,
        "report_path": None,
        "checked_at": datetime.now().isoformat(timespec="seconds"),
    }
    if reason is None:
        return out

    if reason == "partial_mix":
        logger.critical(
            "같은 prediction_date 재실행 감지(partial_mix): %s 기존 %s행 + 이번 %s행 → "
            "기존 행이 ON CONFLICT DO NOTHING 으로 고정돼 **한 날짜가 두 실행 시점의 혼합**이 된다",
            date, existing, n_new,
        )
    else:
        logger.warning(
            "같은 prediction_date 재실행(rerun_no_add): %s 기존 %s행 ≥ 이번 %s행 → 신규 저장 0행",
            date, existing, n_new,
        )
    target_dir = (
        report_dir
        or os.environ.get(DEGENERATE_DAY_REPORT_DIR_ENV)
        or DEFAULT_REPORT_DIR
    )
    try:
        os.makedirs(target_dir, exist_ok=True)
        path = os.path.join(target_dir, f"{PRIOR_RUN_FILE_PREFIX}{date}.json")
        with open(path, "w", encoding="utf-8") as fh:
            json.dump(out, fh, ensure_ascii=False, indent=2, sort_keys=True)
        out["report_path"] = path
    except Exception as exc:  # noqa: BLE001
        logger.error("재실행 증거 파일 기록 실패(dir=%s): %s", target_dir, exc)
        out["report_error"] = str(exc)
    return out

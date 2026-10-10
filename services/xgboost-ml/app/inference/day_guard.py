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
import time
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

# 모듈 임포트 시각 = 프로세스 기동 시각의 대리값. `app/main.py` 가 기동 직후 이 모듈을 임포트한다.
PROCESS_START_TS = time.time()
# 승격≠재기동 가드(CG163) — 프로세스가 실제로 **로드하는** 산출물만 본다.
# (robust_auc.json 은 평가 러너가 수시로 다시 쓴다 → 신원 판정에서 제외한다. 그것은 '태그 신선도'이지
#  '모델 신선도'가 아니다 — `predictor.py:89-91` 의 version 캐시는 별개 이슈.)
CHAMPION_ARTIFACTS = ("xgboost_model.pkl", "feature_names.json")
STALE_MODEL_FILE_PREFIX = "stale_model_"
STALE_MTIME_TOLERANCE_S = 5.0


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


def _kst(ts: Optional[float]) -> Optional[str]:
    """epoch → KST 문자열(로깅·증거용)."""
    if not ts:
        return None
    from datetime import timedelta, timezone

    return datetime.fromtimestamp(float(ts), tz=timezone(timedelta(hours=9))).strftime(
        "%Y-%m-%d %H:%M:%S"
    )


def champion_newest_mtime(champion_dir: str) -> Optional[float]:
    """champion 디렉터리에서 **프로세스가 로드하는** 산출물들의 최신 mtime(없으면 None).

    CHAMPION_ARTIFACTS = xgboost_model.pkl · feature_names.json (= 모델 신원).
    """ 
    newest: Optional[float] = None
    for name in CHAMPION_ARTIFACTS:
        try:
            mt = os.path.getmtime(os.path.join(champion_dir, name))
        except Exception:  # noqa: BLE001
            continue
        if newest is None or mt > newest:
            newest = mt
    return newest


def stale_model_reason(
    artifact_mtime: Optional[float],
    loaded_mtime: Optional[float],
    *,
    tolerance_s: float = STALE_MTIME_TOLERANCE_S,
) -> Optional[str]:
    """디스크의 champion 이 로드 시점 **이후**에 바뀌었는가(프로세스가 옛 모델을 들고 있는가).

    loaded_mtime 이 None 이면 판정 불가(None). tolerance_s 는 '기동 → load' 지연 오탐 여유.
    실측(2026-10-02): 승격이 프로세스 기동 13분 뒤에 일어나 tolerance 밖이었다.
    """
    if artifact_mtime is None or loaded_mtime is None:
        return None
    try:
        if float(artifact_mtime) > float(loaded_mtime) + float(tolerance_s):
            return "champion_newer_than_process"
    except (TypeError, ValueError):
        return None
    return None


def check_and_report_stale_model(
    champion_dir: str,
    *,
    loaded_mtime: Optional[float] = None,
    process_start_ts: Optional[float] = None,
    tolerance_s: float = STALE_MTIME_TOLERANCE_S,
    date: Optional[str] = None,
    report_dir: Optional[str] = None,
) -> Dict:
    """승격≠재기동 감지·표시(CG163). 반환 = 판정 dict(테스트·감사용). 예외를 밖으로 던지지 않는다.

    불변식: **살아 있는 추론 프로세스는 디스크의 현재 champion 을 들고 있어야 한다.**
    모델은 `app/main.py:43-47` 에서 기동 시 1회 joblib.load 되고 피처명은
    `app/inference/predictor.py:163` 에서 Predictor 생성 시 1회 로드되며 **파일 감시·재로드가 없다**
    → champion/ 을 교체(promote)해도 프로세스를 재기동하지 않으면 옛 모델로 계속 채점한다.
    실측 근거(CG162): 2026-10-02 02:31 실행 이후 7일간 전 종목 confidence 최대 < 0.30(소비 문턱 0.55
    도달 0행)이고, 10-09 09:22Z 컨테이너 재시작 직후 첫 발행은 정상 스케일이었다(frac 0.1506).

    **모델·발행을 바꾸지 않는다**(감지·표시 전용 = 승인 불필요 범위). 수리(승격 시 프로세스 재기동
    강제, 또는 champion 신선도 게이트)는 배포 절차·발행 계약 변경이라 리뷰보드 승인 대상이다.
    """
    ref = loaded_mtime if loaded_mtime is not None else process_start_ts
    if ref is None:
        ref = PROCESS_START_TS
    try:
        artifact_mtime = champion_newest_mtime(champion_dir)
    except Exception:  # noqa: BLE001
        artifact_mtime = None
    reason = stale_model_reason(artifact_mtime, ref, tolerance_s=tolerance_s)
    resolved_date = date or datetime.now().strftime("%Y-%m-%d")

    out = {
        "metric": "stale_model_guard",
        "date": resolved_date,
        "stale": reason is not None,
        "reason": reason,
        "champion_dir": champion_dir,
        "champion_artifacts": list(CHAMPION_ARTIFACTS),
        "champion_mtime": artifact_mtime,
        "champion_mtime_kst": _kst(artifact_mtime),
        "loaded_mtime": ref,
        "loaded_mtime_kst": _kst(ref),
        "tolerance_s": tolerance_s,
        "report_path": None,
        "checked_at": datetime.now().isoformat(timespec="seconds"),
    }
    if reason is None:
        return out

    logger.critical(
        "승격≠재기동 감지(%s): champion 산출물이 프로세스 로드 이후에 바뀌었다 "
        "(champion %s > load %s, 여유 %.0fs) — **살아 있는 프로세스는 옛 모델로 채점 중**이다. "
        "재기동 전까지 발행 스코어가 승격 모델이 아니다(모델·발행은 변경하지 않는다).",
        reason, out["champion_mtime_kst"], out["loaded_mtime_kst"], tolerance_s,
    )
    target_dir = (
        report_dir
        or os.environ.get(DEGENERATE_DAY_REPORT_DIR_ENV)
        or DEFAULT_REPORT_DIR
    )
    try:
        os.makedirs(target_dir, exist_ok=True)
        path = os.path.join(target_dir, f"{STALE_MODEL_FILE_PREFIX}{resolved_date}.json")
        with open(path, "w", encoding="utf-8") as fh:
            json.dump(out, fh, ensure_ascii=False, indent=2, sort_keys=True)
        out["report_path"] = path
    except Exception as exc:  # noqa: BLE001
        logger.error("승격≠재기동 증거 파일 기록 실패(dir=%s): %s", target_dir, exc)
        out["report_error"] = str(exc)
    return out

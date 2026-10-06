"""Intraday(분봉) 피처 — 데이터 축(CG101) 준비물 ②.

WHY (2026-10-06)
- 모델측 레버(변환·HP·앙상블·선별·가중·목적함수·라벨·유니버스·창·정규화·국면)는 실측으로 전부
  닫혔고, 남은 축은 '데이터'뿐이다. 보유 원천 중 **한 번도 측정된 적 없는 정보 클래스**는
  가격경로(인트라데이) 하나다 — news_events/sns 등은 커버리지가 패널 유니버스를 덮지 못한다.
- 현 `minute_bars` 는 수집기 결함(XR26: 1회 30봉 상한 < fid_cnt 100 비교로 1페이지 break) 때문에
  **15:01~15:30 30봉만** 쌓인다. 그 표본으로 `scripts/intraday_feature_screen.py`(CG128)를 돌렸으나
  n_dates 4 < 20 으로 '판정불가'였다 → 수집기 수리·백필 승인 전에 **빌더를 미리 갖춰 두면**
  승인 직후 재측정(CG129)이 곧바로 가능하다(CG101 준비물 ②).

정의 — 창(window)을 **시각 경계로 고정**한다(수집 결함 전/후에 같은 정의가 성립해야 한다):
    open30  = 09:00:00 ~ 09:30:00   (개장 30분)
    close30 = 15:01:00 ~ 15:30:00   (종가 동시호가 직전 30분 — 현재 데이터가 있는 유일한 창)
  창별 통계(6종): ret(마지막/첫 종가-1) · range((최고-최저)/마지막 종가) ·
    vwap_dev(마지막 종가/VWAP-1, VWAP=Σ거래대금/Σ거래량) · slope(OLS 기울기/평균 종가) ·
    up_ratio(직전봉 대비 상승 비율) · realvol(1봉 단순수익률의 모집단 표준편차)
  일봉 대비 점유(2종): vol_share · tv_share (Σ거래량/일 거래량, Σ거래대금/일 거래대금)
  파생(2종): id_am_pm_ret_diff(오후 − 오전 수익률, 전 구간 수집 후에만 유효) · id_n_bars(그날 봉 수)

⚠ 정의는 `scripts/intraday_feature_screen.py`(CG128 스크린)와 **원소 단위로 동일**하다 —
  스크린의 `l30_*` 는 이 모듈의 `id_close30_*` 와 같은 식이다(회귀: `scripts/_intraday_features_test.py`).
  한쪽을 고치면 반드시 다른 쪽을 함께 고쳐라(비교 불가가 된다).

시점정합(as-of)
- 학습행 (종목, 날짜 t) 은 **그날 종가 시점의 결정**이고 라벨은 close(t)→close(t+h) 라서,
  t 세션(≤15:30)의 값은 관측 가능하다 → `trade_date = t` 의 봉만 쓴다(미래 세션을 절대 섞지 않는다).
- `date=None` 은 그 종목의 **최대 trade_date**(현재 시점 추론용)를 쓴다. 과거 학습행에는 절대
  date 를 생략하지 마라(모든 행이 같은 값이 되어 라벨과 무관해진다 — sentiment 경로의 실측 교훈).

결측 규약
- 봉이 없거나 계산 불가(봉 2개 미만·종가 0)면 **0.0**(파이프라인 공통 규약: 0.0 = 결측).
- 따라서 커버리지(비영 비율)를 반드시 함께 찍어 판독하라 — 0.0 이 '무정보'인지 '데이터 없음'인지는
  비영 비율로만 구분된다(CG73 규율).

배선: `FeaturePipeline` 은 env `INTRADAY_FEATURES=1` 일 때만 이 모듈을 계산·등록한다(기본 OFF —
현 6~7일 표본을 상시 열로 넣으면 무정보 열만 늘어난다). 패널 A/B 는 전체 재빌드 대신
`scripts/patch_panel_intraday.py`(컬럼만 as-of 재계산)로 같은 행 위에서 돌린다.
"""
from __future__ import annotations

import logging
import statistics
from datetime import date as _date, datetime
from typing import Dict, List, Optional, Sequence, Tuple

logger = logging.getLogger(__name__)

# (창 이름, 시작 HHMMSS, 끝 HHMMSS) — 경계 포함. 시각 문자열은 zero-padded 6자리라
# 사전순 비교가 곧 시각 비교다(`minute_bars.time` = varchar).
WINDOWS: Tuple[Tuple[str, str, str], ...] = (
    ("open30", "090000", "093000"),
    ("close30", "150100", "153000"),
)
AM_WINDOW: Tuple[str, str] = ("090000", "120000")
PM_WINDOW: Tuple[str, str] = ("120100", "153000")

_STATS = ("ret", "range", "vwap_dev", "slope", "up_ratio", "realvol")
_SHARE_STATS = ("vol_share", "tv_share")
# 봉이 이 개수 미만이면 창 통계를 계산하지 않는다(수익률·기울기가 정의되지 않음).
MIN_BARS = 2


def feature_names() -> List[str]:
    """이 모듈이 만드는 피처 이름 전체(고정 순서)."""
    names: List[str] = []
    for win, _lo, _hi in WINDOWS:
        names.extend(f"id_{win}_{s}" for s in _STATS)
        names.extend(f"id_{win}_{s}" for s in _SHARE_STATS)
    names.extend(["id_am_pm_ret_diff", "id_n_bars"])
    return names


def _pstdev(vals: Sequence[float]) -> float:
    return float(statistics.pstdev(vals)) if len(vals) > 1 else 0.0


def _slope(vals: Sequence[float]) -> Optional[float]:
    """OLS 기울기(정규화 전). 3봉 미만이면 None — 스크린과 동일."""
    n = len(vals)
    if n < 3:
        return None
    mx = (n - 1) / 2.0
    my = sum(vals) / n
    den = sum((i - mx) ** 2 for i in range(n))
    if den == 0:
        return None
    num = sum((i - mx) * (v - my) for i, v in enumerate(vals))
    return num / den


def _window_stats(bars: Sequence[Sequence]) -> Optional[Dict[str, float]]:
    """봉 목록(time,o,h,l,c,volume,trading_value) → 창 통계. 계산 불가면 None."""
    if len(bars) < MIN_BARS:
        return None
    closes = [float(b[4] or 0.0) for b in bars]
    highs = [float(b[2] or 0.0) for b in bars]
    lows = [float(b[3] or 0.0) for b in bars]
    vols = [float(b[5] or 0.0) for b in bars]
    tvs = [float(b[6] or 0.0) for b in bars]
    if closes[0] <= 0 or closes[-1] <= 0:
        return None
    sv, st = sum(vols), sum(tvs)
    vwap = (st / sv) if sv > 0 else closes[-1]
    out: Dict[str, float] = {
        "ret": closes[-1] / closes[0] - 1.0,
        "range": (max(highs) - min(lows)) / closes[-1],
        "vwap_dev": (closes[-1] / vwap - 1.0) if vwap > 0 else 0.0,
        "up_ratio": (sum(1 for i in range(1, len(closes)) if closes[i] > closes[i - 1])
                     / max(1, len(closes) - 1)),
    }
    sl = _slope(closes)
    mean_close = sum(closes) / len(closes)
    out["slope"] = (sl / mean_close) if (sl is not None and mean_close > 0) else 0.0
    rets = [closes[i] / closes[i - 1] - 1.0 for i in range(1, len(closes)) if closes[i - 1] > 0]
    out["realvol"] = _pstdev(rets)
    out["_sum_vol"] = sv
    out["_sum_tv"] = st
    return out


def compute_intraday_features(bars: Sequence[Sequence],
                              daily: Optional[Tuple[float, float]] = None) -> Dict[str, float]:
    """봉 목록 → 피처 dict. `bars` 는 시각 오름차순이어야 한다.

    daily = (일 거래량, 일 거래대금) — vol_share/tv_share 의 분모. 없으면 그 두 피처는 0.0.
    """
    feats: Dict[str, float] = {n: 0.0 for n in feature_names()}
    bars = list(bars or [])
    feats["id_n_bars"] = float(len(bars))
    if not bars:
        return feats
    dv, dt = (float(daily[0] or 0.0), float(daily[1] or 0.0)) if daily else (0.0, 0.0)
    for win, lo, hi in WINDOWS:
        bl = [b for b in bars if lo <= str(b[0]) <= hi]
        st = _window_stats(bl)
        if st is None:
            continue
        for k in _STATS:
            feats[f"id_{win}_{k}"] = float(st[k])
        if dv > 0:
            feats[f"id_{win}_vol_share"] = st["_sum_vol"] / dv
        if dt > 0:
            feats[f"id_{win}_tv_share"] = st["_sum_tv"] / dt
    am = _window_stats([b for b in bars if AM_WINDOW[0] <= str(b[0]) <= AM_WINDOW[1]])
    pm = _window_stats([b for b in bars if PM_WINDOW[0] <= str(b[0]) <= PM_WINDOW[1]])
    if am is not None and pm is not None:
        feats["id_am_pm_ret_diff"] = float(pm["ret"] - am["ret"])
    return feats


class IntradayFeatures:
    """`minute_bars`(× `market_data`)에서 (종목, 날짜) 단위 인트라데이 피처를 만든다."""

    def __init__(self) -> None:
        self._cache: Dict[Tuple[str, str], Dict[str, float]] = {}

    # ---------------------------------------------------------------- db
    def _load_bars(self, cur, stock_code: str, date_str: str) -> List[Tuple]:
        cur.execute(
            "SELECT \"time\", open_price, high_price, low_price, close_price, volume, trading_value "
            "FROM minute_bars WHERE stock_code = %s AND trade_date = %s::date ORDER BY \"time\"",
            (stock_code, date_str))
        return [(str(r[0]), r[1], r[2], r[3], r[4], r[5], r[6]) for r in cur.fetchall()]

    def _load_daily(self, cur, stock_code: str, date_str: str) -> Optional[Tuple[float, float]]:
        cur.execute(
            "SELECT volume, trading_value FROM market_data "
            "WHERE stock_code = %s AND trade_date = %s::date",
            (stock_code, date_str))
        row = cur.fetchone()
        if not row:
            return None
        return (float(row[0] or 0.0), float(row[1] or 0.0))

    def _latest_date(self, cur, stock_code: str) -> Optional[str]:
        cur.execute("SELECT max(trade_date) FROM minute_bars WHERE stock_code = %s", (stock_code,))
        row = cur.fetchone()
        return str(row[0])[:10] if row and row[0] else None

    # ------------------------------------------------------------ public
    def get_all_features(self, stock_code: str, db_conn=None, date=None) -> Dict[str, float]:
        """(종목, 날짜) 인트라데이 피처. `date=None` 이면 그 종목의 최대 trade_date(추론용)."""
        if db_conn is None:
            return {n: 0.0 for n in feature_names()}
        date_str = None
        if date is not None:
            if isinstance(date, (_date, datetime)):
                date_str = date.strftime("%Y-%m-%d")
            else:
                date_str = str(date)[:10]
        key = (stock_code, date_str or "")
        if key in self._cache:
            return dict(self._cache[key])
        try:
            cur = db_conn.cursor()
            try:
                if date_str is None:
                    date_str = self._latest_date(cur, stock_code)
                if date_str is None:
                    feats = {n: 0.0 for n in feature_names()}
                else:
                    bars = self._load_bars(cur, stock_code, date_str)
                    daily = self._load_daily(cur, stock_code, date_str) if bars else None
                    feats = compute_intraday_features(bars, daily)
            finally:
                try:
                    cur.close()
                except Exception:
                    pass
        except Exception as e:  # 결측은 0.0 — 파이프라인 계약(피처 하나 때문에 행을 버리지 않는다)
            try:
                db_conn.rollback()
            except Exception:
                pass
            logger.debug(f"intraday features failed for {stock_code}@{date}: {e}")
            feats = {n: 0.0 for n in feature_names()}
        self._cache[key] = dict(feats)
        return dict(feats)

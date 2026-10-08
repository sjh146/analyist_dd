#!/usr/bin/env python3
"""배포 챔피언 모델의 **견고 성능** 측정 — 시간창 다중 분할 프로토콜.

WHY: 승격 게이트가 오랫동안 `champion/auc.txt` 의 **단일 시드 운값**(0.6131)을 비교
기준으로 썼다. 그래서 그 값이 실제 성능인지조차 확인되지 않은 채 승격이 잠겼다.
이 스크립트는 배포된 모델 자체를 **여러 시간창에서, 실제 추론 경로 그대로**
(스크리너와 동일: FeaturePipeline.build_features → 크로스섹션 랭크 주입 → 챔피언
feature_names 순서로 벡터화 → EnsembleModel.predict) 평가해 기준선을 실측한다.

프로토콜:
  * 최근 거래일을 5개의 **연속 시간창**으로 나눈다(창마다 균등 간격 날짜 표본).
  * 날짜별로 유동성 상위 종목 일부를 표본으로 잡고 그 날짜의 피처를 as-of 로 빌드한다.
  * 라벨: h거래일 뒤 **시장(표본) 상대** 초과수익 — 그 날짜 표본의 중앙값 대비 상위=1.
    (창의 마지막 h 거래일은 미래 가격을 쓰므로 purge 로 제외)
  * 지표: 날짜별 크로스섹션 AUC 의 평균(창 대표값) → 창 평균 ± 표준편차.
    순위 모델이므로 크로스섹션 AUC 가 맞는 지표다(풀링 AUC 도 참고로 함께 낸다).

출력: reports/champion_robust_eval.json
      `--write` 를 주면 champion/robust_auc.json 도 기록(승격 기준선 갱신).

사용(컨테이너 안, cwd=/app):
    python scripts/champion_robust_eval.py --folds 5 --dates-per-fold 10 --stocks 80
    python scripts/champion_robust_eval.py --write
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import statistics
import sys
from datetime import datetime

sys.path.insert(0, "/app")

import numpy as np  # noqa: E402
import psycopg2  # noqa: E402

from app.feature_engine.feature_pipeline import FeaturePipeline  # noqa: E402
from app.models.ensemble_model import EnsembleModel  # noqa: E402

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger("champion_robust_eval")

UNIVERSE_SQL = """
SELECT m.stock_code,
       count(*) FILTER (WHERE m.close_price > 0) AS n_days,
       avg(COALESCE(m.trading_value, m.close_price * m.volume)) AS avg_value
FROM market_data m
JOIN stocks s ON s.stock_code = m.stock_code
WHERE m.trade_date BETWEEN %(start)s AND %(end)s
  AND m.close_price > 0
  AND s.stock_name NOT LIKE '%%스팩%%'
GROUP BY m.stock_code
HAVING count(*) FILTER (WHERE m.close_price > 0) >= 60
   AND avg(COALESCE(m.trading_value, m.close_price * m.volume)) > 0
ORDER BY avg_value DESC
LIMIT %(limit)s
"""


def auc(y: list[int], p: list[float]) -> float:
    """Mann-Whitney AUC (sklearn 의존 없이). 동점은 0.5 로 처리한다."""
    pos = sum(1 for v in y if v == 1)
    neg = len(y) - pos
    if pos == 0 or neg == 0:
        return float("nan")
    order = sorted(range(len(p)), key=lambda i: p[i])
    ranks = [0.0] * len(p)
    i = 0
    while i < len(order):
        j = i
        while j + 1 < len(order) and p[order[j + 1]] == p[order[i]]:
            j += 1
        avg_rank = (i + j) / 2.0 + 1.0
        for k in range(i, j + 1):
            ranks[order[k]] = avg_rank
        i = j + 1
    rank_sum = sum(ranks[i] for i in range(len(p)) if y[i] == 1)
    return (rank_sum - pos * (pos + 1) / 2.0) / (pos * neg)


def trading_dates(conn, n_dates: int, asof_date: str | None = None) -> list[str]:
    """최근 n_dates 거래일(내림차순).

    asof_date=None 이면 **현행과 비트 동일**(앵커 = CURRENT_DATE).
    asof_date='YYYY-MM-DD' 를 주면 그 날짜를 앵커로 창을 **고정**한다.

    왜(2026-10-09 실측): 앵커가 CURRENT_DATE 라 하루만 지나도 표본 창이 통째로 밀린다
    → 같은 모델을 같은 프로토콜로 재도 값이 달라져 '기준선 대비 Δ'가 사과(모델)와
    오렌지(창)를 섞는다. 배포 챔피언(2026-09-24 이후 모델 불변) 재측정 13회 실측:
    0.4935~0.5448(폭 0.0513) · 명시적 동일 프로토콜 2회(CG136/139) Δ0.0227 —
    사전문턱 +0.02 와 같은 크기다. 창 고정으로 일 단위 비교를 성립시킨다.
    """
    if asof_date:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT DISTINCT trade_date FROM market_data
                WHERE close_price > 0
                  AND trade_date <= %(anchor)s::date - INTERVAL '7 days'
                ORDER BY trade_date DESC LIMIT %(n)s
                """,
                {"anchor": asof_date, "n": n_dates},
            )
            return [r[0].strftime("%Y-%m-%d") for r in cur.fetchall()]
    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT DISTINCT trade_date FROM market_data
            WHERE close_price > 0 AND trade_date <= CURRENT_DATE - INTERVAL '7 days'
            ORDER BY trade_date DESC LIMIT %s
            """,
            (n_dates,),
        )
        return [r[0].strftime("%Y-%m-%d") for r in cur.fetchall()]


def load_prices(conn, codes: list[str], start: str, end: str) -> dict[str, list[tuple[str, float]]]:
    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT stock_code, trade_date, close_price FROM market_data
            WHERE stock_code = ANY(%s) AND trade_date BETWEEN %s AND %s AND close_price > 0
            ORDER BY stock_code, trade_date
            """,
            (codes, start, end),
        )
        out: dict[str, list[tuple[str, float]]] = {}
        for code, d, close in cur.fetchall():
            out.setdefault(code, []).append((d.strftime("%Y-%m-%d"), float(close)))
        return out


def forward_return(prices: list[tuple[str, float]], date: str, h: int) -> float | None:
    """date 종가 대비 h거래일 뒤 종가 수익률. 데이터가 모자라면 None."""
    idx = None
    for i, (d, _) in enumerate(prices):
        if d == date:
            idx = i
            break
    if idx is None or idx + h >= len(prices):
        return None
    base = prices[idx][1]
    if base <= 0:
        return None
    return prices[idx + h][1] / base - 1.0


def _meta_range(model_dir: str) -> tuple[str | None, str | None]:
    """모델 디렉터리의 최신 training-result-*.json 에서 **(학습 시작일, 종료일)** 을 읽는다.

    왜(2026-09-29 실측): 배포 챔피언은 `retrain_champion --days 90` 로 2026-06-25~09-23 을
    학습했는데, 평가창 5개 중 2개(05-29~07-20·07-28~09-15)가 **학습구간과 겹쳤다** →
    창 평균 0.5302 가 오염됐다(겹친 창 0.5883 vs 학습구간 밖 0.4914). 창은 서로 독립
    표본이라 겹치는 창만 버리면 재학습 없이 정직한 OOS 값을 얻는다.
    retrain_champion 이 data_start/data_end 를 기록하기 시작한 뒤로는 자동으로 잡힌다.
    """
    try:
        cands = [os.path.join(model_dir, f) for f in os.listdir(model_dir)
                 if f.startswith("training-result-") and f.endswith(".json")]
        if not cands:
            return None, None
        newest = max(cands, key=os.path.getmtime)
        with open(newest) as f:
            meta = json.load(f)
        return (meta.get("data_start") or meta.get("start_date"),
                meta.get("data_end") or meta.get("end_date"))
    except Exception:
        return None, None


def _split_oos(windows: list[list[str]], train_start: str,
               train_end: str | None = None) -> tuple[list[list[str]], list[list[str]]]:
    """(채점 가능한 창, 학습구간과 겹쳐 제외한 창).

    학습구간 [train_start, train_end] 과 **겹치지 않는** 창만 OOS 다:
    창 전체가 학습 시작 이전이거나, 창 시작이 학습 종료 이후여야 한다.
    (train_end 를 주지 않으면 '이전 창'만 인정 — 종료일을 모르면 미래 창을 안전하게 판정 불가)
    """
    def keep(w: list[str]) -> bool:
        if not w:
            return False
        if w[-1] < train_start:
            return True
        return bool(train_end) and w[0] > train_end

    return [w for w in windows if keep(w)], [w for w in windows if not keep(w)]


def _make_labels(rets, kind: str = "rel", q: float | None = None) -> list:
    """라벨 생성 — kind='rel'(기본) = 시장상대 중앙값, kind='abs' = 절대 방향(r > 0).

    왜 'abs' 가 필요한가 (2026-09-29 실측 CG36): 이 스크립트의 라벨은 **시장상대 중앙값**인데
    배포 챔피언이 학습한 라벨은 **절대 1일 선행 종가 방향**이다(retrain_champion._create_labels
    L44-58). 즉 CG34(h=5)·CG36(h=1)는 모두 '다른 과제' 점수이고, 챔피언의 자기 과제 OOS 는
    아직 측정된 적이 없다. 같은 날짜·같은 모델·같은 피처에서 라벨만 바꾸면 그 값이 나온다.
    기본값은 'rel' 이라 기존 호출·기록은 비트 동일하게 유지된다.

    kind='quantile'(2026-10-04 CG92): 그날 표본의 **분위 꼬리** 라벨 —
    r > quantile(1-q) = 1, r < quantile(q) = 0, 가운데는 **None**(채점 제외).
    retrain_champion --label-kind quantile 과 **같은 정의**여야 '자기 과제' 점수가 된다
    (CG36/37 교훈: 학습 라벨과 평가 라벨의 종류가 다르면 그 값은 자기 과제가 아니다).
    ⚠ 반환 리스트에 None 이 섞이므로 호출자가 행을 걸러내야 한다(main 참조).
    """
    if kind == "abs":
        return [1 if r > 0 else 0 for r in rets]
    if kind == "quantile":
        if not q or not (0.0 < float(q) < 0.5):
            raise ValueError(f"quantile 라벨은 q in (0, 0.5) 가 필요하다 (받은 값: {q!r})")
        arr = np.asarray(rets, dtype=float)
        hi = float(np.quantile(arr, 1.0 - float(q)))
        lo = float(np.quantile(arr, float(q)))
        return [1 if r > hi else (0 if r < lo else None) for r in arr]
    med = statistics.median(rets)
    return [1 if r > med else 0 for r in rets]


def main() -> int:
    ap = argparse.ArgumentParser(description="배포 챔피언 견고 성능 측정(시간창 다중 분할)")
    ap.add_argument("--folds", type=int, default=5)
    ap.add_argument("--dates-per-fold", type=int, default=10)
    ap.add_argument("--stocks", type=int, default=80)
    ap.add_argument("--horizon", type=int, default=5, help="라벨 호라이즌(거래일)")
    ap.add_argument("--label-kind", choices=("rel", "abs", "quantile"), default="rel",
                    help="라벨 종류. rel=시장상대 중앙값(기존 프로토콜·기본값), "
                         "abs=절대 방향(r>0) — abs 는 배포 챔피언이 실제로 학습한 과제다"
                         "(retrain_champion._create_labels: 1일 선행 종가 방향), "
                         "quantile=그날 분위 꼬리(--label-q 로 q 지정; 가운데 분위는 채점 제외) — "
                         "retrain_champion --label-kind quantile 로 학습한 후보의 **자기 과제** 점수")
    ap.add_argument("--label-q", dest="label_q", type=float, default=None,
                    help="분위 꼬리 라벨의 q (0<q<0.5). --label-kind quantile 과 함께만 쓴다. "
                         "학습 라벨의 q 와 **같은 값**을 줘야 자기 과제가 된다(CG36/37). "
                         "기본 None = 현행.")
    ap.add_argument("--train-start", default=None,
                    help="학습 데이터 **시작일**(YYYY-MM-DD). 학습구간과 겹치는 창은 AUC 가 "
                         "부풀려지므로 제외한다. 미지정 시 model_dir 의 training-result-*.json "
                         "data_start 로 자동 판정")
    ap.add_argument("--train-end", default=None,
                    help="학습 데이터 **종료일**(YYYY-MM-DD). 주면 학습 종료 **이후** 창(진짜 미래 "
                         "구간)도 채점 대상에 포함된다 — 컷오프를 과거로 고정해 학습한 모델을 "
                         "그 이후 창에서 평가할 때 필요하다. 미지정 시 meta 의 data_end")
    ap.add_argument("--model-dir", default="app/models/champion")
    ap.add_argument("--asof-date", dest="asof_date", default=None,
                    help="창 앵커(YYYY-MM-DD) — 주면 표본 거래일 창을 그 날짜 기준으로 **고정**한다"
                         "(미지정 = 현행 비트 동일, 앵커=CURRENT_DATE). 왜(2026-10-09 실측): 앵커가 "
                         "오늘이면 하루만 지나도 창이 밀려 같은 모델·같은 프로토콜의 값이 0.4935~0.5448 "
                         "로 흔들린다(폭 0.0513) — 일 단위 Δ 비교가 성립하지 않는다.")
    ap.add_argument("--universe", choices=("liquidity", "training"), default="liquidity",
                    help="표본 유니버스 선택. liquidity=유동성 상위(현행 프로토콜·기본값), "
                         "training=select_training_universe(학습 경로와 동형 — ETF/ETN·파생 제외). "
                         "왜(실측 2026-09-30): 유동성 상위 표본 80종목 중 **26개가 ETF/ETN/레버리지**"
                         "(KODEX 200·KODEX 레버리지·TIGER 200 등)이고 학습 유니버스(200종목)와의 "
                         "교집합이 **5종목**뿐이었다 → '배포 챔피언 자기 과제 OOS 0.4410' 은 자기 "
                         "과제가 아니라 **다른 도메인 채점**이었다. 이 축은 A/B 로만 비교하고 "
                         "기본값(기준선 산출)은 승인 없이 바꾸지 않는다.")
    ap.add_argument("--universe-seed", type=int, default=0,
                    help="--universe training 일 때 select_training_universe 의 시드(기본 0 = 현행). "
                         "같은 크기의 **서로 다른 결정적 유니버스**를 뽑아 짝(pairs) 설계에 쓴다. "
                         "왜(실측 2026-10-01 CG46): 유니버스 정체만 바꿔도 폴드 평균이 Δ0.0287 움직인다"
                         "(CG13, 서로소 30종목 5구간) — 단일 유니버스의 짝 Δ +0.02 는 그 잡음보다 작아 "
                         "여러 시드에서 부호가 유지되는지 확인해야 승격 근거가 된다.")
    ap.add_argument("--out", default="app/reports/champion_robust_eval.json")
    ap.add_argument("--dump-preds", dest="dump_preds", default=None,
                    help="(선택) (fold,date,code) 별 확률·실현 선행수익 jsonl 을 이 경로에 쓴다. "
                         "스키마는 topk_precision.py 가 읽는 exp/fold/date/code/y_true/y_pred/"
                         "fwd_ret — 상위 k 정밀도·기대수익 집계용(CG54). 미지정이면 현행과 "
                         "비트 동일(쓰지 않음).")
    ap.add_argument("--dump-tag", dest="dump_tag", default=None,
                    help="--dump-preds 의 exp 이름(기본 = model_dir 의 basename). 두 모델을 "
                         "한 파일로 합칠 때 --arm/--control 이름으로 쓴다.")
    ap.add_argument("--dump-all", dest="dump_all", action="store_true",
                    help="--dump-preds 를 **전 유니버스 행**으로 쓴다(quantile 라벨의 가운데 행도 "
                         "y_true=null 로 포함). 왜 필요한가(2026-10-04 CG95): 기본 덤프는 라벨이 "
                         "있는 행만 담으므로 후보 풀이 '실현 선행수익의 꼬리'로 사후 선택되고, "
                         "그 위에서 잰 top-k 정밀도·기대수익은 매매 가능한 바스켓이 아니다"
                         "(예측 시점에 그 꼬리를 알 수 없다). 돈 지표는 전 유니버스 채점 위에서 "
                         "재야 한다(scripts/fillable_topk_expectancy.py). 기본값은 현행 비트 동일.")
    ap.add_argument("--write", action="store_true",
                    help="robust_walkforward.json 도 기록(정보용 견고성 지표). "
                         "승격 기준선 robust_auc.json 은 champion_promote 가 지표 동형으로 "
                         "기록한다 — 여기서 덮어쓰면 단일 분할/워크포워드가 섞여 판정이 느슨해진다")
    args = ap.parse_args()

    # 라벨 옵션 정합(2026-10-04 CG92): quantile 은 q 필수, q 는 quantile 전용.
    if args.label_kind == "quantile" and args.label_q is None:
        ap.error("--label-kind quantile 은 --label-q <분위> 가 필요하다 (예: --label-q 0.05)")
    if args.label_q is not None and args.label_kind != "quantile":
        ap.error("--label-q 는 --label-kind quantile 과만 함께 쓴다")
    if args.label_q is not None and not (0.0 < args.label_q < 0.5):
        ap.error("--label-q 는 0 < q < 0.5 범위여야 한다")

    conn = psycopg2.connect(
        host=os.environ.get("POSTGRES_HOST", "postgres"),
        port=int(os.environ.get("POSTGRES_PORT", 5432)),
        dbname=os.environ.get("POSTGRES_DB", "stock_trading"),
        user=os.environ.get("POSTGRES_USER", "stock_user"),
        password=os.environ.get("POSTGRES_PASSWORD"),
    )
    # 창 크기: 폴드당 h 간격 날짜를 dates-per-fold 개 뽑을 수 있게 창을 넉넉히(≈2배) 잡는다.
    span = args.dates_per_fold * 2 * args.folds
    dates_desc = trading_dates(conn, max(span + 5 * args.horizon, 200), asof_date=args.asof_date)
    if not dates_desc:
        logger.error("거래일을 찾지 못했습니다")
        return 2
    dates_asc = sorted(dates_desc)
    logger.info("거래일 %d개 확보 (%s ~ %s) — 창 앵커 %s",
                len(dates_asc), dates_asc[0], dates_asc[-1],
                args.asof_date or "CURRENT_DATE(현행)")

    chunk = max(1, len(dates_asc) // args.folds)
    windows = [dates_asc[i * chunk:(i + 1) * chunk] for i in range(args.folds)]
    windows = [w for w in windows if len(w) > args.horizon + 1]

    # ── 학습구간 오염 차단 (2026-09-29 실측) ──────────────────────────────────────
    # 배포 챔피언 창평균 0.5302 중 2개 창이 학습구간(2026-06-25~09-23)과 겹쳐 부풀려졌다
    # (겹친 창 0.5883 vs 학습구간 밖 0.4914). 겹치는 창만 버리고 나머지를 채점한다.
    windows_all = list(windows)
    meta_start, meta_end = _meta_range(args.model_dir)
    train_start = args.train_start or meta_start
    train_end = args.train_end or meta_end
    excluded: list[list[str]] = []
    if train_start:
        windows, excluded = _split_oos(windows_all, train_start, train_end)
        logger.info("학습구간 %s ~ %s — 겹치는 창 %d개 제외, OOS 창 %d개 채점",
                    train_start, train_end or "?", len(excluded), len(windows))
        for w in excluded:
            logger.info("  제외 창: %s ~ %s (학습구간과 겹침)", w[0] if w else "-", w[-1] if w else "-")
        if not windows:
            logger.error("학습구간 이전 창이 없습니다 — OOS 평가 불가 (기준선으로 쓸 수 없음)")
            return 2
    else:
        logger.warning("학습구간 정보 없음(model_dir meta 에 data_start 없음) — 전 창 채점: "
                       "겹침 가능성이 있으므로 --trained-through 로 학습 시작일을 명시하라")

    # 유동성 유니버스는 **폴드 창 전체**가 아니라 최근 120거래일 기준으로 뽑는다
    # (창 하나만 보면 n_days 조건을 만족하는 종목이 없어 유니버스가 빈다 — 실측 함정).
    univ_start = dates_asc[max(0, len(dates_asc) - 120)]
    univ_end = dates_asc[-1]
    if args.universe == "liquidity":
        with conn.cursor() as cur:
            cur.execute(UNIVERSE_SQL, {"start": univ_start, "end": univ_end, "limit": args.stocks})
            universe = [r[0] for r in cur.fetchall()]
        logger.info("표본 유니버스 %d종목(유동성 상위 — 현행 프로토콜)", len(universe))
    else:
        from app.training.universe import select_training_universe
        universe = select_training_universe(conn, limit=args.stocks, min_days=30,
                                            seed=args.universe_seed)
        logger.info("표본 유니버스 %d종목(학습 경로와 동형 — ETF/ETN·파생 제외, seed=%d)",
                    len(universe), args.universe_seed)
    if not universe:
        logger.error("유니버스가 비었습니다")
        return 2

    # 표본 구성 로깅 — 결과 파일에 '무엇을 채점했는가'가 남아야 해석을 틀리지 않는다.
    # 실측(2026-09-30): 유동성 상위 80종목 중 26개가 ETF/ETN/레버리지, 학습 유니버스(200)와의
    # 교집합 5종목 → 그 OOS 0.4410 을 '자기 과제 성적'으로 읽으면 안 된다.
    universe_info = {"mode": args.universe, "n": len(universe)}
    if args.universe == "training":
        universe_info["seed"] = args.universe_seed
    try:
        from app.training.universe import is_etf_etn, select_training_universe as _stu
        with conn.cursor() as cur:
            cur.execute("SELECT stock_code, stock_name FROM stocks WHERE stock_code = ANY(%s)",
                        (universe,))
            _names = {c: (n or "") for c, n in cur.fetchall()}
        _train_uni = set(_stu(conn, limit=200, min_days=30, seed=0))
        universe_info["n_etf_etn"] = sum(1 for c in universe if is_etf_etn(_names.get(c)))
        universe_info["overlap_train200"] = len(set(universe) & _train_uni)
        logger.info("표본 구성: %d종목 · ETF/ETN/파생 %d개 · 학습유니버스(200) 교집합 %d",
                    len(universe), universe_info["n_etf_etn"], universe_info["overlap_train200"])
    except Exception as e:      # 로깅 실패가 평가를 막지 않는다
        logger.warning("표본 구성 로깅 실패(무시): %s: %s", type(e).__name__, e)

    pipeline = FeaturePipeline(pg_conn=conn)
    ensemble = EnsembleModel(model_dir=args.model_dir)
    ensemble.load(args.model_dir)
    if not ensemble._is_trained:
        logger.error("챔피언 모델 로드 실패: %s", args.model_dir)
        return 2
    with open(os.path.join(args.model_dir, "feature_names.json")) as f:
        feature_names = json.load(f)
    logger.info("챔피언 계약 %d피처", len(feature_names))

    prices = load_prices(conn, universe, dates_asc[0], dates_asc[-1])

    scored_dates = 0
    fold_stats = []
    per_date_aucs: list[float] = []
    pooled_y: list[int] = []
    pooled_p: list[float] = []
    errors = 0
    # --dump-preds(선택, 2026-10-01 CG54): 상위 k 정밀도·실현수익 집계기(topk_precision.py)의
    # 입력을 남긴다. 스키마는 topk_precision.load_rows 가 기대하는 exp/fold/date/code/
    # y_true/y_pred/fwd_ret 다(sweep 의 --dump-preds 와 동일). 미지정이면 아무 것도 쓰지 않아
    # 기존 동작과 비트 동일하다(AUC 는 라벨·확률만 쓰므로 이 배열은 계산에 영향을 주지 않는다).
    dump_rows = [] if args.dump_preds else None
    dump_tag = args.dump_tag or os.path.basename((args.model_dir or "").rstrip("/"))

    for fi, window in enumerate(windows):
        # purge: 창의 마지막 h 거래일은 미래 가격이 필요하므로 제외
        usable = window[:-args.horizon] if len(window) > args.horizon else []
        if not usable:
            continue
        step = max(1, len(usable) // args.dates_per_fold)
        sample_dates = usable[::step][:args.dates_per_fold]
        w_aucs: list[float] = []

        for date in sample_dates:
            features_by_code = {}
            for code in universe:
                try:
                    feats = pipeline.build_features(code, date)
                    if feats and feats.get("feature_count", 0) >= 10:
                        features_by_code[code] = feats
                except Exception as e:  # 피처 하나 실패는 그 종목만 건너뛴다
                    errors += 1
                    if errors <= 3:
                        logger.warning("피처 실패 %s %s: %s: %s", code, date, type(e).__name__, e)
            if len(features_by_code) < 10:
                logger.warning("%s: 유효 종목 %d개 — 건너뜀", date, len(features_by_code))
                continue
            pipeline.compute_cross_sectional_ranks(features_by_code)

            codes, probs, rets = [], [], []
            for code, feats in features_by_code.items():
                plist = prices.get(code)
                if not plist:
                    continue
                r = forward_return(plist, date, args.horizon)
                if r is None:
                    continue
                try:
                    vec = np.nan_to_num(
                        np.array([float(feats.get(f, 0.0)) for f in feature_names], dtype=np.float32)
                    )
                    probs.append(float(ensemble.predict(np.array([vec]))[0]))
                    rets.append(r)
                    codes.append(code)
                except Exception as e:
                    errors += 1
                    if errors <= 3:
                        logger.warning("추론 실패 %s %s: %s: %s", code, date, type(e).__name__, e)
            if len(rets) < 10:
                continue

            y = _make_labels(rets, args.label_kind, args.label_q)
            if dump_rows is not None and args.dump_all:
                # 전 유니버스 덤프(CG95): 라벨 가운데(None)도 포함한다 — 예측 시점에 실제로
                # 고를 수 있는 풀은 '그날 채점된 전 종목'이지 실현수익 꼬리가 아니다.
                for _c, _p, _r, _yi in zip(codes, probs, rets, y):
                    dump_rows.append({"exp": dump_tag, "fold": fi + 1, "date": date,
                                      "code": _c,
                                      "y_true": (int(_yi) if _yi is not None else None),
                                      "y_pred": round(float(_p), 6),
                                      "fwd_ret": round(float(_r), 6)})
            if args.label_kind == "quantile":
                # 가운데 분위(None)는 채점에서 제외한다 — 학습 라벨과 같은 행 도메인이어야
                # '자기 과제' 점수가 된다(retrain_champion --label-kind quantile 과 동일 규칙).
                _keep = [i for i, v in enumerate(y) if v is not None]
                if len(_keep) < 10:
                    logger.info("%s: quantile 라벨 후 표본 %d개 — 건너뜀", date, len(_keep))
                    continue
                codes = [codes[i] for i in _keep]
                probs = [probs[i] for i in _keep]
                rets = [rets[i] for i in _keep]
                y = [int(y[i]) for i in _keep]
            if len(set(y)) < 2:
                # 전 종목 동일 라벨(예: 급등일 전부 상승) → AUC 정의 불가. 날짜만 세고 버린다.
                logger.info("%s: 라벨 단일값 — AUC 정의 불가, 건너뜀", date)
                continue
            if dump_rows is not None and not args.dump_all:
                for _c, _p, _r, _yi in zip(codes, probs, rets, y):
                    dump_rows.append({"exp": dump_tag, "fold": fi + 1, "date": date,
                                      "code": _c, "y_true": int(_yi),
                                      "y_pred": round(float(_p), 6),
                                      "fwd_ret": round(float(_r), 6)})
            a = auc(y, probs)
            if a == a:  # NaN 아님
                w_aucs.append(a)
                per_date_aucs.append(a)
                pooled_y.extend(y)
                pooled_p.extend(probs)
            scored_dates += 1

        if w_aucs:
            fold_stats.append({
                "fold": fi + 1,
                "window": [usable[0], usable[-1]],
                "n_dates": len(w_aucs),
                "auc_mean": round(statistics.mean(w_aucs), 4),
                "auc_std": round(statistics.pstdev(w_aucs), 4) if len(w_aucs) > 1 else None,
            })
            logger.info("폴드 %d: AUC %.4f (날짜 %d개, %s~%s)",
                        fi + 1, fold_stats[-1]["auc_mean"], len(w_aucs),
                        usable[0], usable[-1])

    if not fold_stats:
        # AUC 폴드가 하나도 안 남아도, --dump-all 로 모은 전 유니버스 행은 **쓴다**(CG95).
        # 왜: 돈 지표(fillable_topk_expectancy)는 AUC 폴드 유효성과 무관하게 세션별 순기대를
        # 재는데, 여기서 그냥 return 하면 몇 시간치 채점 결과가 통째로 사라진다.
        if args.dump_preds and dump_rows:
            os.makedirs(os.path.dirname(args.dump_preds) or ".", exist_ok=True)
            with open(args.dump_preds, "w", encoding="utf-8") as f:
                for _row in dump_rows:
                    f.write(json.dumps(_row, ensure_ascii=False) + "\n")
            logger.warning("유효 폴드 없음 — 그래도 --dump-preds %d행 기록(돈 지표는 폴드 무관)",
                           len(dump_rows))
        logger.error("유효 폴드가 없습니다")
        return 2

    fold_means = [f["auc_mean"] for f in fold_stats]
    _lbl_desc = {"rel": "시장상대 중앙값", "abs": "절대 방향(>0)",
                 "quantile": f"분위 꼬리 q={args.label_q}(가운데 제외)"}[args.label_kind]
    payload = {
        "model_dir": args.model_dir,
        "window_anchor": args.asof_date,   # None = CURRENT_DATE(현행). 값이 있으면 창이 고정됐다
        "protocol": (f"{len(fold_stats)}-fold 연속 시간창, h={args.horizon} "
                     f"{_lbl_desc} 라벨, "
                     f"크로스섹션 AUC, purge={args.horizon}거래일"),
        "label_kind": args.label_kind,
        "label_q": (float(args.label_q) if args.label_q is not None else None),
        "universe": universe_info,          # 무엇을 채점했는가(모드·ETF 수·학습유니버스 교집합)
        "metric": "cross_sectional_auc_mean",
        "robust_auc": round(statistics.mean(fold_means), 4),
        # 학습구간 오염 차단 기록 (2026-09-29): 창이 학습구간과 겹치면 AUC 가 부풀려진다
        "train_start": train_start,
        "train_end": train_end,
        "oos_only": bool(train_start),
        "windows_excluded": [[(w[0] if w else None), (w[-1] if w else None)] for w in excluded],
        "n_windows_excluded": len(excluded),
        "auc_std_across_folds": round(statistics.pstdev(fold_means), 4) if len(fold_means) > 1 else None,
        "folds": fold_stats,
        "dates_scored": scored_dates,
        "rows_scored": len(pooled_y),
        "auc_pooled": round(auc(pooled_y, pooled_p), 4) if pooled_y else None,
        "auc_per_date_mean": round(statistics.mean(per_date_aucs), 4) if per_date_aucs else None,
        "model_aucs": getattr(ensemble, "model_aucs", None) or None,
        "errors": errors,
        "measured_at": datetime.now().isoformat(timespec="seconds"),
    }
    os.makedirs(os.path.dirname(args.out) or ".", exist_ok=True)
    with open(args.out, "w") as f:
        json.dump(payload, f, ensure_ascii=False, indent=2)
    if args.dump_preds:
        os.makedirs(os.path.dirname(args.dump_preds) or ".", exist_ok=True)
        with open(args.dump_preds, "w", encoding="utf-8") as f:
            for _row in dump_rows or []:
                f.write(json.dumps(_row, ensure_ascii=False) + "\n")
        logger.info("--dump-preds %d행 (exp=%s) → %s",
                    len(dump_rows or []), dump_tag, args.dump_preds)
    logger.info("견고 AUC = %.4f (폴드 %s) → %s",
                payload["robust_auc"], fold_means, args.out)

    if args.write:
        # 2026-09-25: 승격 기준선(robust_auc.json)과 **분리**한다. 이 평가의 프로토콜은
        # 워크포워드 다중 폴드(auc_mean 아님)인데, 생산 챌린저 지표는 단일 분할
        # ensemble_auc 다. 두 값을 섞으면 비교가 한쪽으로 기울어(워크포워드 값이 보통 더
        # 낮아 단일 분할 후보가 쉽게 통과) 승격 판정이 느슨해진다. robust_auc.json 은
        # **동일 지표**를 기록하는 champion_promote 만 쓴다.
        target = os.path.join(args.model_dir, "robust_walkforward.json")
        with open(target, "w") as f:
            json.dump({
                "robust_auc": payload["robust_auc"],
                "metric": payload["metric"],
                "auc_std": payload["auc_std_across_folds"],
                "protocol": payload["protocol"],
                "rows_scored": payload["rows_scored"],
                "folds": [f["auc_mean"] for f in fold_stats],
                "measured_at": payload["measured_at"],
                "train_start": train_start,
                "oos_only": bool(train_start),
                "windows_excluded": payload["windows_excluded"],
                "note": "정보용 워크포워드 견고성 기록 — 승격 기준선은 robust_auc.json",
            }, f, ensure_ascii=False, indent=2)
        logger.info("기준선 기록: %s", target)

    print(json.dumps(payload, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

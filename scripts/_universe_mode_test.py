#!/usr/bin/env python3
"""_universe_mode_test.py — CG57 '학습 유니버스 정렬' 셋업 검증 (컨테이너에서 실행).

  docker exec stock_xgboost_ml python /app/scripts/_universe_mode_test.py

검사(자체 PASS/FAIL — 이 스택엔 pytest 가 없다, 2026-09-29 실측):
 ① 기본값 회귀: `select_training_universe(pg, limit, min_days, seed)`(mode 미지정) 이
    mode="recency" 와 **비트 동일** — 생산 경로(retrain_champion 기본) 동작 불변.
 ② 결정성: liquidity 모드는 같은 커넥션 4회·새 커넥션 3회 모두 교집합 100%.
 ③ 정렬의 실체: liquidity 결과가 실제로 일평균 거래대금 내림차순이고, 표본의 중앙 거래대금이
    recency 표본보다 유효하게 크다(메커니즘이 살아있는가 — 라벨만 붙은 빈 구현 배제).
 ④ 위생: 두 모드 모두 ETF/ETN 0개 · 코드 중복 0 · 길이 == limit.
 ⑤ 계약: 알 수 없는 mode 는 ValueError(조용히 recency 로 떨어지지 않는다).

⚠ 이 테스트는 운영 DB 를 **읽기만** 한다(주문·쓰기 경로 없음). 실행 전 load1<3.5 확인.
"""
import os
import sys

# 컨테이너에서 `/app/scripts/x.py` 로 실행하면 sys.path[0] 이 /app/scripts 라 `app` 패키지가 안 잡힌다
# → 패키지 루트(/app)를 직접 넣는다(다른 _*_test.py 는 `python /app/scripts/...` 로 돌려 sys.path[0]
# 이 /app/scripts 이지만 그쪽은 app 을 import 하지 않는다). 실측 2026-10-01: 이 한 줄이 없어
# `ModuleNotFoundError: No module named 'app'` 로 즉시 죽었다.
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import psycopg2

from app.training.universe import (          # noqa: E402 — 컨테이너 cwd=/app
    _fetch_liquid, is_etf_etn, select_training_universe,
)

FAIL = []


def check(name, cond, extra=""):
    print(("PASS " if cond else "FAIL ") + name + (f" — {extra}" if extra else ""))
    if not cond:
        FAIL.append(name)


def conn():
    return psycopg2.connect(
        host=os.environ.get("POSTGRES_HOST", "postgres"),
        port=int(os.environ.get("POSTGRES_PORT", 5432)),
        dbname=os.environ.get("POSTGRES_DB", "stock_trading"),
        user=os.environ.get("POSTGRES_USER", "stock_user"),
        password=os.environ.get("POSTGRES_PASSWORD"),
    )


LIMIT = 200
c = conn()

# ① 기본값 회귀
base = select_training_universe(c, limit=LIMIT, min_days=30, seed=0)
explicit = select_training_universe(c, limit=LIMIT, min_days=30, seed=0, mode="recency")
check("① 기본값(mode 미지정) == mode='recency'", base == explicit)

# ② 결정성 — 같은 커넥션 4회
liq_runs = [select_training_universe(c, limit=LIMIT, min_days=30, seed=0, mode="liquidity")
            for _ in range(4)]
check("② 같은 커넥션 4회 결과 동일(집합)",
      all(set(r) == set(liq_runs[0]) for r in liq_runs),
      f"교집합 {[len(set(liq_runs[0]) & set(r)) for r in liq_runs]}/{len(liq_runs[0])}")
check("② 같은 커넥션 4회 결과 동일(순서까지)", len({tuple(r) for r in liq_runs}) == 1)

# ② 새 커넥션 3회
new_runs = []
for _ in range(3):
    c2 = conn()
    new_runs.append(select_training_universe(c2, limit=LIMIT, min_days=30, seed=0, mode="liquidity"))
    c2.close()
check("② 새 커넥션 3회도 동일", all(set(r) == set(liq_runs[0]) for r in new_runs))

# ③ 정렬 실체 — 거래대금 내림차순 + recency 대비 중앙값
from datetime import datetime, timedelta  # noqa: E402
date_from = (datetime.now() - timedelta(days=60)).strftime("%Y-%m-%d")
ranked = _fetch_liquid(c, date_from, 30)
vals = [r["avg_value"] for r in ranked]
check("③ _fetch_liquid 가 거래대금 내림차순", all(vals[i] >= vals[i + 1] for i in range(len(vals) - 1)))
pick = {r["code"]: r["avg_value"] for r in ranked}
liq_med = sorted(pick[c_] for c_ in liq_runs[0] if c_ in pick)[len(liq_runs[0]) // 2]
rec_vals = sorted(pick[c_] for c_ in base if c_ in pick)
rec_med = rec_vals[len(rec_vals) // 2] if rec_vals else 0.0
check("③ liquidity 표본 중앙 거래대금 > recency 표본", liq_med > rec_med * 3,
      f"liq {liq_med/1e8:.1f}억 vs rec {rec_med/1e8:.1f}억 · 교집합 {len(set(liq_runs[0]) & set(base))}/{LIMIT}")

# ④ 위생
for label, uni in (("liquidity", liq_runs[0]), ("recency", base)):
    check(f"④ {label}: 길이 == {LIMIT}", len(uni) == LIMIT, str(len(uni)))
    check(f"④ {label}: 중복 없음", len(set(uni)) == len(uni))
cur = c.cursor()
cur.execute("SELECT stock_code, stock_name FROM stocks WHERE stock_code = ANY(%s)", (list(liq_runs[0]),))
etf = [n for _c, n in cur.fetchall() if is_etf_etn(n)]
cur.close()
check("④ liquidity: ETF/ETN 0개", not etf, str(etf[:5]))

# ⑤ 계약
try:
    select_training_universe(c, limit=10, mode="nonsense")
    check("⑤ 알 수 없는 mode → ValueError", False, "예외 없음")
except ValueError:
    check("⑤ 알 수 없는 mode → ValueError", True)
except Exception as e:  # noqa: BLE001
    check("⑤ 알 수 없는 mode → ValueError", False, f"{type(e).__name__}: {e}")

print("\n" + ("전부 PASS" if not FAIL else f"FAIL {len(FAIL)}건: {FAIL}"))
sys.exit(1 if FAIL else 0)

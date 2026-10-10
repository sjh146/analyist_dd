#!/usr/bin/env python3
"""자체점검 — 배포 추론 유니버스 필터(CG159) 의 무회귀·효과 검증.

왜: CG159(2026-10-10 감사) 는 배포 추론이 `stocks` 전 종목(실측 4,343)을 순회하는데
학습 유니버스는 ETF/ETN 을 제외한다는 사실을 실측했다 — 상수 블록 373행 중 369행이 ETN,
값 0.5689 로 소비 문턱 0.55 를 넘는다(그날 ≥0.55 대역의 56.8% 가 정보 없는 행).
수리 플래그(`PREDICT_EXCLUDE_ETFETN`)는 **기본 OFF = 종전과 비트 동일**이어야 하므로,
그 무회귀를 여기서 증명한다.  활성화는 리뷰보드 승인 대상(발행 리스트 계약 변경).

재현:
  docker exec stock_xgboost_ml sh -c 'cd /app && python3 -u scripts/_predict_universe_filter_test.py'

검사(사전 고정):
  ① is_etf_etn 분류 sanity(주식은 False · ETF/ETN 은 True)
  ② 플래그 미설정/거짓값 → filter 가 **입력 객체 그대로**(동일성·순서·길이) 돌려준다 = 무회귀
  ③ 플래그 참값("1"/"true"/"yes"/"ON"/" y ") → True 로 해석
  ④ 플래그 ON → ETF/ETN 만 제거, 생존자 순서 보존, 길이 감소분 == 제거 수
  ⑤ 배선 가드 — 예측 루프 2곳(app/inference/predictor.py::predict_all · app/main.py::run_predictions)
     이 실제로 filter 를 통과하는가(호출 제거 회귀 방지)
  ⑥ (DB, 선택) 실 stocks 테이블에서 ON 이 실제로 몇 행을 떨어뜨리는가 — 읽기 전용
"""
from __future__ import annotations

import os
import sys

_HERE = os.path.dirname(os.path.abspath(__file__))
for _cand in (os.path.dirname(_HERE), os.path.join(os.path.dirname(_HERE), "services", "xgboost-ml")):
    if _cand not in sys.path:
        sys.path.insert(0, _cand)

PASS = 0
FAIL = 0


def check(name: str, cond: bool, detail: str = "") -> None:
    global PASS, FAIL
    if cond:
        PASS += 1
        print(f"  PASS  {name}" + (f"  [{detail}]" if detail else ""))
    else:
        FAIL += 1
        print(f"  FAIL  {name}  {detail}")


def main() -> int:
    from app.inference.predictor import (
        PREDICT_EXCLUDE_ETFETN_ENV,
        filter_prediction_universe,
        prediction_universe_excludes_etf_etn,
    )
    from app.training.universe import is_etf_etn

    print("① is_etf_etn 분류 sanity")
    cases = [
        ("삼성전자", False), ("SK하이닉스", False), ("금호석유", False),
        ("TIGER 국고채10년 ETN", True), ("KODEX 200", True), ("RISE 2차전지레버리지", True),
        ("", False), (None, False),
    ]
    for name, want in cases:
        check(f"is_etf_etn({name!r}) == {want}", is_etf_etn(name) is want, f"got {is_etf_etn(name)}")

    fake = [
        {"stock_code": "005930", "stock_name": "삼성전자"},
        {"stock_code": "Q500001", "stock_name": "TIGER 국고채10년 ETN"},
        {"stock_code": "000660", "stock_name": "SK하이닉스"},
        {"stock_code": "Q500002", "stock_name": "KODEX 200"},
        {"stock_code": "035720", "stock_name": "카카오"},
    ]

    print("② 미설정/거짓값 → 무회귀(입력 객체 동일)")
    for val in (None, "", "0", "false", "no", "off", " "):
        if val is None:
            os.environ.pop(PREDICT_EXCLUDE_ETFETN_ENV, None)
        else:
            os.environ[PREDICT_EXCLUDE_ETFETN_ENV] = val
        out = filter_prediction_universe(fake)
        check(
            f"flag={val!r} → 부울 False & 객체 동일",
            prediction_universe_excludes_etf_etn() is False and out is fake,
            f"bool={prediction_universe_excludes_etf_etn()} same={out is fake}",
        )

    print("③ 참값 해석")
    for val in ("1", "true", "TRUE", "yes", "On", " y "):
        os.environ[PREDICT_EXCLUDE_ETFETN_ENV] = val
        check(f"flag={val!r} → True", prediction_universe_excludes_etf_etn() is True)

    print("④ ON → ETF/ETN 만 제거 + 순서 보존")
    os.environ[PREDICT_EXCLUDE_ETFETN_ENV] = "1"
    out = filter_prediction_universe(fake)
    codes = [s["stock_code"] for s in out]
    check("제거 후 코드 목록 == 주식만(순서 보존)", codes == ["005930", "000660", "035720"], f"got {codes}")
    check("길이 감소분 == 제거 수", len(fake) - len(out) == 2, f"{len(fake)} -> {len(out)}")
    check("새 리스트(입력 비변형)", out is not fake and len(fake) == 5, f"input_len={len(fake)}")
    # 이름 키가 없는 dict 도 크래시 없이 통과해야 한다(방어적)
    out2 = filter_prediction_universe([{"stock_code": "000000"}])
    check("stock_name 키 부재 → 예외 없음·보존", [s["stock_code"] for s in out2] == ["000000"])

    print("⑤ 배선 가드 — 예측 루프가 filter 를 통과하는가")
    base = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    for rel, marker in (
        ("app/inference/predictor.py", "filter_prediction_universe(self.storage.get_all_stocks())"),
        ("app/main.py", "filter_prediction_universe(self.pg_storage.get_all_stocks())"),
    ):
        try:
            src = open(os.path.join(base, rel), encoding="utf-8").read()
            check(f"{rel} 배선 존재", marker in src)
        except OSError as exc:
            check(f"{rel} 읽기", False, str(exc))

    print("⑥ 실 DB — ON 이 실제로 몇 행을 떨어뜨리는가(읽기 전용, 선택)")
    try:
        import psycopg2  # noqa: F401
        conn = psycopg2.connect(
            host=os.environ.get("POSTGRES_HOST", "postgres"),
            port=int(os.environ.get("POSTGRES_PORT", "5432")),
            dbname=os.environ.get("POSTGRES_DB", "stock_trading"),
            user=os.environ.get("POSTGRES_USER", "stock_user"),
            password=os.environ.get("POSTGRES_PASSWORD", ""),
        )
        cur = conn.cursor()
        cur.execute("SELECT stock_code, stock_name FROM stocks")
        rows = [{"stock_code": r[0], "stock_name": r[1]} for r in cur.fetchall()]
        cur.close()
        conn.close()
        os.environ.pop(PREDICT_EXCLUDE_ETFETN_ENV, None)
        off = filter_prediction_universe(rows)
        os.environ[PREDICT_EXCLUDE_ETFETN_ENV] = "1"
        on = filter_prediction_universe(rows)
        n_etf = sum(1 for r in rows if is_etf_etn(r["stock_name"]))
        check("OFF == 전체", len(off) == len(rows), f"{len(off)}/{len(rows)}")
        check("ON == 전체 − ETF/ETN", len(on) == len(rows) - n_etf, f"{len(on)} vs {len(rows)}-{n_etf}")
        check("실측 제거 행 > 1000(CG159 감사 규모)", n_etf > 1000, f"dropped={n_etf}")
        print(f"    실측: stocks {len(rows)}행 → ON {len(on)}행 (ETF/ETN {n_etf} 제외)")
    except Exception as exc:  # DB 없으면 건너뜀(구조 검사는 이미 통과)
        print(f"    SKIP  DB 검사 — {type(exc).__name__}: {exc}")

    os.environ.pop(PREDICT_EXCLUDE_ETFETN_ENV, None)
    print(f"\n결과: {PASS} PASS · {FAIL} FAIL")
    return 0 if FAIL == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())

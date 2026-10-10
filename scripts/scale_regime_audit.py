#!/usr/bin/env python3
"""배포 스코어 스케일 체제 감사 — '구조적 무진입' 구간을 날짜별로 탐지한다.

왜 이 도구가 필요한가 (2026-10-11 엔지니어 자율, 측정 정합성 전용):
  - 실측: 2026-10-02 02:31 실행 ~ 2026-10-08 까지 **7일(6거래일)** 동안 전 종목
    `ml_predictions.confidence` 의 **최대값이 0.2277~0.2944** 로, 소비자 절대문턱 0.55 에
    **한 행도 닿지 않았다**(frac>=0.55 = 0.0000). 앞 구간(09-25~10-01)과 뒤 구간(10-09)은
    매일 14~15% 가 문턱을 넘는다. 즉 이 기간 '무진입'은 신호 품질 문제가 아니라
    **점수 스케일이 소비 문턱에 닿지 못하는 체제(regime) 문제**다(MT116 기제, CG82/CG158 진단).
  - CG160 가드(퇴화일: 하루 전체가 단일값)는 이 구간을 **잡지 못한다** — 값은 다양하다.
    CG159(상수블록=ETF/ETN)도 아니다. 이 감사는 소비 관점 결함(**문턱 도달 0행**)을 잡는다.
  - 체제 전환 경계 실측: 10-02 02:31 실행에서 압축 시작, 10-09 09:40 실행에서 복구.
    같은 기간 champion/ 모델 바이너리는 09-23 15:18 로 **변화 없음**(모델 파일 크기·mtime 동일)
    → 원인은 모델 파일이 아니라 입력/스코어 경로 후보다(원인 미확정, CG162 로 이관).

읽기 전용. 판정·기준선·문턱을 건드리지 않는다(측정 정합성 전용).
사용:
  docker exec stock_xgboost_ml sh -c 'cd /app && python -u scripts/scale_regime_audit.py'
  docker exec stock_xgboost_ml sh -c 'cd /app && python -u scripts/scale_regime_audit.py --selftest'
"""
from __future__ import annotations

import argparse
import json
import os
from datetime import date

DEFAULT_THRESHOLD = float(os.environ.get("CONSUMER_THRESHOLD", "0.55"))
OUT_PATH = os.environ.get(
    "SCALE_REGIME_OUT", "/app/reports/overnight/scale_regime_audit.json"
)


def _connect():
    import psycopg2

    return psycopg2.connect(
        host=os.environ.get("POSTGRES_HOST", "postgres"),
        port=int(os.environ.get("POSTGRES_PORT", "5432")),
        dbname=os.environ.get("POSTGRES_DB", "stock_trading"),
        user=os.environ.get("POSTGRES_USER", "stock_user"),
        password=os.environ.get("POSTGRES_PASSWORD", ""),
    )


def classify(rows, threshold: float) -> list[dict]:
    """rows: [(prediction_date(str), n(int), nd(int), mn, p50, p90, mx, ge(int), version(s))]

    날짜별로 소비 관점 상태를 판정한다. 판정 규칙(사전 등록):
      - degenerate : nd == 1                      (하루 전체가 단일값 = AUC 정의상 0.5)
      - blocked    : mx < threshold               (문턱 도달 0행 = 구조적 무진입)
      - sparse     : 0 < ge < n*0.01              (도달 가능하나 사실상 무시 가능)
      - ok         : 그 밖
    """
    out = []
    for r in rows:
        (d, n, nd, mn, p50, p90, mx, ge, ver) = r
        n = int(n or 0)
        nd = int(nd or 0)
        ge = int(ge or 0)
        mx = float(mx or 0.0)
        if n == 0:
            continue
        if nd == 1:
            state = "degenerate"
        elif mx < threshold:
            state = "blocked"
        elif ge < max(1.0, n * 0.01):
            state = "sparse"
        else:
            state = "ok"
        out.append(
            {
                "date": str(d),
                "n": n,
                "n_distinct": nd,
                "min": float(mn or 0.0),
                "p50": float(p50 or 0.0),
                "p90": float(p90 or 0.0),
                "max": mx,
                "n_ge_threshold": ge,
                "frac_ge_threshold": round(ge / n, 4),
                "state": state,
                "model_version": ver,
            }
        )
    return out


def summarize(per_date: list[dict], threshold: float) -> dict:
    blocked = [d["date"] for d in per_date if d["state"] == "blocked"]
    degenerate = [d["date"] for d in per_date if d["state"] == "degenerate"]
    # 최근 연속 blocked 스트릭(날짜 오름차순 꼬리) + 마지막 날 직전의 연속 스트릭
    def _tail_streak(seq: list[dict]) -> int:
        n = 0
        for d in reversed(seq):
            if d["state"] in ("blocked", "degenerate"):
                n += 1
            else:
                break
        return n

    streak = _tail_streak(per_date)
    prev_streak = _tail_streak(per_date[:-1]) if per_date else 0
    last = per_date[-1] if per_date else None
    if last is None:
        verdict = "데이터 없음"
    elif last["state"] == "blocked":
        verdict = "구조적 무진입 진행 중 — 최근 %d일 문턱 도달 0행" % streak
    elif last["state"] == "degenerate":
        verdict = "퇴화일 진행 중 — 최근 %d일 단일값" % streak
    elif prev_streak:
        verdict = "복구됨(직전 %d일 무진입/퇴화)" % prev_streak
    else:
        verdict = "정상"
    return {
        "threshold": threshold,
        "n_dates": len(per_date),
        "span": [per_date[0]["date"], per_date[-1]["date"]] if per_date else None,
        "blocked_dates": blocked,
        "degenerate_dates": degenerate,
        "trailing_blocked_streak": streak,
        "verdict": verdict,
    }


def model_inventory(models_dir: str = "/app/app/models") -> list[dict]:
    """배포 모델 산출물 인벤토리 — '스코어 체제'와 '모델 산출물'을 같은 표에 놓기 위한 증거.

    왜: 정상 스케일 구간(10-09~)의 발행 행은 model_version='champ-<robust_auc.recorded_at>-<auc>'
    로 태깅되는데, 압축 구간(10-02~10-08)은 전부 'v1.0'(= 프로세스가 태그 해석 코드/파일을
    보기 전에 기동된 기본값)이다. 즉 **태그 = 추론 프로세스 세대(identity)의 대리지표**이고,
    체제 전환이 프로세스 세대 전환과 정확히 일치한다 → 원인은 '프로세스/모델 로딩' 후보다.
    산출물(mtime·피처 수)을 함께 찍어 두면 다음 사이클이 이분법 실험을 설계할 수 있다.
    """
    import glob
    import os

    out = []
    for d in sorted(glob.glob(os.path.join(models_dir, "champion*"))):
        if not os.path.isdir(d):
            continue
        rec: dict = {"dir": os.path.basename(d)}
        # 대표 산출물 mtime = 디렉터리 내 최신 mtime(태그·메타 갱신 포함)
        newest, newest_f = 0.0, None
        for root, _dirs, files in os.walk(d):
            for f in files:
                fp = os.path.join(root, f)
                try:
                    m = os.path.getmtime(fp)
                except OSError:
                    continue
                if m > newest:
                    newest, newest_f = m, os.path.relpath(fp, d)
        rec["newest_mtime_kst"] = _kst(newest) if newest else None
        rec["newest_file"] = newest_f
        fn = os.path.join(d, "feature_names.json")
        try:
            with open(fn, encoding="utf-8") as f:
                names = json.load(f)
            rec["n_features"] = len(names) if isinstance(names, list) else None
        except Exception:  # noqa: BLE001
            rec["n_features"] = None
        for meta in ("robust_auc.json", "training-result-20261001-122645.json"):
            fp = os.path.join(d, meta)
            if os.path.exists(fp):
                try:
                    with open(fp, encoding="utf-8") as f:
                        rec[meta] = json.load(f)
                except Exception:  # noqa: BLE001
                    pass
        for binname in ("xgboost_model.pkl", "lightgbm_model.pkl", "catboost_model.pkl"):
            fp = os.path.join(d, binname)
            if os.path.exists(fp):
                try:
                    rec.setdefault("binaries", {})[binname] = _kst(os.path.getmtime(fp))
                except OSError:
                    pass
        out.append(rec)
    return out


def _kst(ts: float) -> str:
    from datetime import datetime, timedelta, timezone

    return datetime.fromtimestamp(ts, tz=timezone(timedelta(hours=9))).strftime("%Y-%m-%d %H:%M:%S")


def _selftest() -> int:
    T = 0.55
    # 1) 정상일
    rows = [("2026-01-01", 100, 50, 0.0, 0.3, 0.5689, 0.83, 15, "v1.0")]
    assert classify(rows, T)[0]["state"] == "ok"
    # 2) 압축일(전 종목 최대가 문턱 미달) = blocked
    rows = [("2026-01-02", 100, 50, 0.0, 0.05, 0.12, 0.29, 0, "v1.0")]
    assert classify(rows, T)[0]["state"] == "blocked"
    # 3) 퇴화일(단일값) 우선
    rows = [("2026-01-03", 100, 1, 0.1429, 0.1429, 0.1429, 0.1429, 0, "v1.0")]
    assert classify(rows, T)[0]["state"] == "degenerate"
    # 4) 도달가능하나 1% 미만 = sparse
    rows = [("2026-01-04", 1000, 50, 0.0, 0.3, 0.4, 0.6, 3, "v1.0")]
    assert classify(rows, T)[0]["state"] == "sparse"
    # 5) 스트릭/판정: ok → blocked×3 → verdict 진행 중
    rows = [
        ("2026-01-01", 10, 5, 0, 0.3, 0.5, 0.6, 2, "v1.0"),
        ("2026-01-02", 10, 5, 0, 0.05, 0.1, 0.2, 0, "v1.0"),
        ("2026-01-03", 10, 5, 0, 0.05, 0.1, 0.2, 0, "v1.0"),
        ("2026-01-04", 10, 5, 0, 0.05, 0.1, 0.2, 0, "v1.0"),
    ]
    s = summarize(classify(rows, T), T)
    assert s["trailing_blocked_streak"] == 3, s
    assert "진행 중" in s["verdict"], s
    assert len(s["blocked_dates"]) == 3, s
    # 6) 복구 판정
    rows = rows + [("2026-01-05", 10, 5, 0, 0.3, 0.5, 0.6, 2, "v1.0")]
    s = summarize(classify(rows, T), T)
    assert s["trailing_blocked_streak"] == 0, s
    assert "복구됨" in s["verdict"], s
    # 7) 실측 구간 회귀(2026-10-02~10-08 = blocked, 10-09 = ok)
    rows = [
        ("2026-10-02", 4343, 1460, 0.0001, 0.0614, 0.1335, 0.2910, 0, "v1.0"),
        ("2026-10-08", 4343, 1567, 0.0, 0.0738, 0.1434, 0.2809, 0, "v1.0"),
        ("2026-10-09", 4343, 2934, 0.0, 0.2727, 0.5689, 0.8341, 654, "champ-20261002T024420-0.5513"),
    ]
    got = classify(rows, T)
    assert [g["state"] for g in got] == ["blocked", "blocked", "ok"], got
    s = summarize(got, T)
    assert "복구됨" in s["verdict"] and s["trailing_blocked_streak"] == 0, s
    print("selftest: 7/7 PASS")
    return 0


SQL = """
SELECT prediction_date,
       count(*) AS n,
       count(DISTINCT confidence) AS nd,
       min(confidence) AS mn,
       percentile_cont(0.5) WITHIN GROUP (ORDER BY confidence) AS p50,
       percentile_cont(0.9) WITHIN GROUP (ORDER BY confidence) AS p90,
       max(confidence) AS mx,
       sum((confidence >= %(thr)s)::int) AS ge,
       min(model_version) AS ver
FROM ml_predictions
GROUP BY prediction_date
ORDER BY prediction_date
"""


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--selftest", action="store_true")
    ap.add_argument("--threshold", type=float, default=DEFAULT_THRESHOLD)
    ap.add_argument("--last", type=int, default=0, help="최근 N일만 표시(0=전체)")
    ap.add_argument("--json-out", default=OUT_PATH)
    a = ap.parse_args()
    if a.selftest:
        return _selftest()

    conn = _connect()
    try:
        with conn.cursor() as cur:
            cur.execute(SQL, {"thr": a.threshold})
            rows = cur.fetchall()
    finally:
        conn.close()

    per_date = classify(rows, a.threshold)
    summ = summarize(per_date, a.threshold)
    shown = per_date[-a.last:] if a.last else per_date

    print("=== 배포 스코어 스케일 체제 감사 (문턱 %.4f) ===" % a.threshold)
    print("날짜        n     nd    p50     p90     max     >=thr  frac   상태        version")
    for d in shown:
        print(
            "%-11s %-5d %-5d %-7.4f %-7.4f %-7.4f %-6d %-6.4f %-11s %s"
            % (
                d["date"], d["n"], d["n_distinct"], d["p50"], d["p90"], d["max"],
                d["n_ge_threshold"], d["frac_ge_threshold"], d["state"], d["model_version"],
            )
        )
    print("---")
    print("판정: %s" % summ["verdict"])
    print("blocked %d일: %s" % (len(summ["blocked_dates"]), ", ".join(summ["blocked_dates"])))
    print("degenerate %d일: %s" % (len(summ["degenerate_dates"]), ", ".join(summ["degenerate_dates"])))

    inv = model_inventory(os.environ.get("MODELS_DIR", "/app/app/models"))
    print("--- 배포 챔피언 산출물 (체제 전환과 대조용) ---")
    for m in inv:
        if not m.get("n_features") and "robust_auc.json" not in m:
            continue
        print(
            "%-34s feats=%-4s newest=%s (%s) auc=%s"
            % (
                m["dir"],
                m.get("n_features"),
                m.get("newest_mtime_kst"),
                m.get("newest_file"),
                (m.get("robust_auc.json") or {}).get("robust_auc"),
            )
        )
    print("  ※ model_version 태그 = 프로세스 세대 대리지표: 'v1.0'=태그해석 코드/파일 이전 기동 프로세스")

    payload = {
        "generated_at_kst": str(date.today()),
        "summary": summ,
        "per_date": per_date,
        "model_inventory": inv,
    }
    try:
        os.makedirs(os.path.dirname(a.json_out), exist_ok=True)
        with open(a.json_out, "w", encoding="utf-8") as f:
            json.dump(payload, f, ensure_ascii=False, indent=1)
        print("[ok] %s" % a.json_out)
    except Exception as e:  # noqa: BLE001
        print("[warn] JSON 기록 실패: %s" % e)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

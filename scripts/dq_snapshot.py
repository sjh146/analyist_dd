#!/usr/bin/env python3
"""dq_snapshot — 데이터 수집·품질 스냅샷 (퀀트 리서처의 모니터링 심장부).

무엇을 하는가
  Prometheus 에서 DQ/수집 메트릭을 **직접 조회**해 ① 임계값 판정(ok/warn/breach)
  ② 추세 차트 PNG 생성 ③ 이력 JSONL 기록 을 한 번에 한다.

WHY Grafana 스크린샷이 아니라 직접 조회인가 (실측 2026-09-25)
  - Grafana 13.2.2 에는 grafana-image-renderer 플러그인이 **없다**(플러그인 51개 중 renderer 0개,
    /render 는 500 반환). 익명 접근도 401 로 막혀 있다.
  - 그래서 "그래프를 본다"를 자율적으로 하려면 렌더러 설치(컨테이너 변경·수백 MB)가 필요하다.
  - 대신 Prometheus HTTP API 는 **인증 없이** 열려 있고, 호스트에 matplotlib 이 있어
    같은 데이터를 같은 주기로 그릴 수 있다. 의존성이 없고 자율 실행이 가능하다.
  → 판정의 근거는 항상 **수치**이고, PNG 는 그 수치의 시각 자료다(이미지가 근거가 아니다).

사용
  /usr/bin/python3 scripts/dq_snapshot.py                 # 기본 7일 추세
  /usr/bin/python3 scripts/dq_snapshot.py --hours 24      # 최근 24시간
  /usr/bin/python3 scripts/dq_snapshot.py --no-chart      # 차트 없이 수치만
  종료코드: 0=정상, 2=경고, 3=위반
"""

import argparse
import json
import os
import sys
import urllib.parse
import urllib.request
from datetime import datetime, timedelta, timezone

import matplotlib
matplotlib.use("Agg")   # 헤드리스 — 디스플레이 없이 PNG 생성
import matplotlib.dates as mdates   # noqa: E402
import matplotlib.pyplot as plt     # noqa: E402
from matplotlib import font_manager  # noqa: E402

PROM = os.environ.get("PROM_URL", "http://127.0.0.1:9090")
PROJ = os.environ.get("RES_PROJ", "/home/jhshi/analyist_dd")
OUTDIR = os.path.join(PROJ, "data/reports/dq_snapshots")
HISTORY = os.path.join(OUTDIR, "history.jsonl")
KST = timezone(timedelta(hours=9))

# 한글 폰트가 없으면 라벨이 깨진다 → 있는 것만 골라 쓰고, 없으면 영문 라벨을 쓴다.
_KO = None
for _cand in ("NanumGothic", "Noto Sans CJK KR", "Malgun Gothic", "Noto Sans KR"):
    try:
        font_manager.findfont(_cand, fallback_to_default=False)
        _KO = _cand
        break
    except Exception:
        continue
if _KO:
    plt.rcParams["font.family"] = _KO
plt.rcParams["axes.unicode_minus"] = False

# (메트릭, 집계, warn, breach, 라벨)  ※ warn/breach=None 이면 정보용
SPECS = [
    # ⚠ 임계값은 **비거래일**을 감안한다: 9/24~25 추석 휴장이라 2일은 정상인데 warn 이 떴다.
    # 주말/연휴가 끼면 3일까지는 정상 → warn 3 / breach 5.
    ("market_data_freshness_days", "max", 3.0, 5.0, "데이터 신선도(일)"),
    ("market_data_rows_recent", "sum", None, None, "최근 적재 행수"),
    ("market_data_zero_volume_ratio_20d", "max", 0.05, 0.10, "거래량0 비율"),
    ("market_data_frozen_ratio_20d", "max", 0.05, 0.15, "동결(가격 불변) 비율"),
    ("dq_asof_violation_rows", "sum", None, 0.0, "as-of 위반 행수"),
    ("dq_claim_parse_failure", "sum", None, 0.0, "러너 파서 실패"),
    # gap 은 **0 이 정상**이다. warn=0 으로 두면 0>=0 이 성립해 매번 경고가 뜬다(실측 버그).
    ("dq_claim_gap", "sum", 1.0, 10.0, "자기신고 갭"),
    ("dq_claim_source", "sum", None, None, "소스 수신 행수"),
    ("dq_feature_stock_constant_ratio", "max", 0.35, 0.45, "종목상수 피처 비율"),
    ("dq_feature_coverage_illusion_max", "max", 0.005, 0.02, "커버리지 착시"),
    ("dq_feature_null_ratio_max", "max", 0.30, 0.60, "피처 결측 최대"),
    ("dq_feature_market_level_count", "max", None, None, "시장레벨 피처 수"),
    ("dq_padding_rows_before_listing", "max", None, 0.0, "상장 전 행(padding)"),
    ("feature_alive_count", "max", None, None, "살아있는 피처"),
    ("feature_dead_count", "max", None, None, "죽은 피처"),
]

# 차트 라벨은 영문으로 쓴다 — 이 WSL 에는 한글 폰트가 없어 글리프가 전부 깨진다
# (실측 2026-09-25: "Glyph ... missing from current font" 경고가 라벨 수만큼 발생).
# 콘솔·JSON 은 한글 label 을 그대로 쓴다(사람이 읽는 쪽은 한글이 맞다).
EN = {
    "market_data_freshness_days": "data freshness (days)",
    "market_data_rows_recent": "rows ingested (recent)",
    "market_data_zero_volume_ratio_20d": "zero-volume ratio 20d",
    "market_data_frozen_ratio_20d": "frozen price ratio 20d",
    "dq_asof_violation_rows": "as-of violations (rows)",
    "dq_claim_parse_failure": "runner parse failures",
    "dq_claim_gap": "self-report gap",
    "dq_claim_source": "rows from source",
    "dq_feature_stock_constant_ratio": "stock-constant feature ratio",
    "dq_feature_coverage_illusion_max": "coverage illusion (max)",
    "dq_feature_null_ratio_max": "max feature null ratio",
    "dq_feature_market_level_count": "market-level features",
    "dq_padding_rows_before_listing": "pre-listing padded rows",
    "feature_alive_count": "alive features",
    "feature_dead_count": "dead features",
}


def _api(path, params):
    url = f"{PROM}{path}?" + urllib.parse.urlencode(params)
    with urllib.request.urlopen(url, timeout=20) as r:
        return json.load(r)


def instant(name):
    """즉시 조회 → (대표값, 계열목록). 대표값은 aggregate(계열 중 max/sum)."""
    d = _api("/api/v1/query", {"query": name})
    res = d.get("data", {}).get("result", [])
    if not res:
        return None, []
    vals = []
    series = []
    for r in res:
        try:
            v = float(r["value"][1])
        except (KeyError, IndexError, ValueError):
            continue
        vals.append(v)
        series.append({"labels": {k: v2 for k, v2 in r["metric"].items() if k != "__name__"},
                       "value": v})
    return vals, series


def range_series(name, hours, step=1800):
    end = datetime.now(KST)
    start = end - timedelta(hours=hours)
    d = _api("/api/v1/query_range", {
        "query": name, "start": int(start.timestamp()),
        "end": int(end.timestamp()), "step": step})
    out = []
    for r in d.get("data", {}).get("result", []):
        pts = [(datetime.fromtimestamp(float(t), KST), float(v))
               for t, v in r["values"]]
        if pts:
            out.append({"labels": {k: v2 for k, v2 in r["metric"].items() if k != "__name__"},
                        "points": pts})
    return out


def verdict(val, warn, breach):
    if val is None or (warn is None and breach is None):
        return "info"
    if breach is not None and val >= breach and (breach > 0 or val > 0 or breach == 0.0 and val > 0):
        if breach == 0.0:
            return "breach" if val > 0 else "ok"
        return "breach"
    if warn is not None and val >= warn:
        return "warn"
    return "ok"


def main():
    ap = argparse.ArgumentParser(description="DQ/수집 스냅샷")
    ap.add_argument("--hours", type=float, default=168.0, help="추세 조회 기간(시간)")
    ap.add_argument("--no-chart", action="store_true")
    a = ap.parse_args()

    now = datetime.now(KST)
    snap = {"ts": now.isoformat(timespec="seconds"), "hours": a.hours, "metrics": {}}
    breaches, warns = [], []

    for name, agg, warn, breach, label in SPECS:
        vals, series = instant(name)
        if not vals:
            snap["metrics"][name] = {"label": label, "value": None, "status": "nodata"}
            continue
        val = max(vals) if agg == "max" else sum(vals)
        st = verdict(val, warn, breach)
        snap["metrics"][name] = {"label": label, "value": val, "agg": agg, "status": st,
                                 "warn": warn, "breach": breach, "n_series": len(series),
                                 "series": series[:8]}
        if st == "breach":
            breaches.append(f"{label}({name})={val:g} ≥ {breach:g}")
        elif st == "warn":
            warns.append(f"{label}({name})={val:g} ≥ {warn:g}")

    # ── 출력 ──
    print(f"[dq_snapshot] {now.isoformat(timespec='seconds')} (추세 {a.hours:g}h)")
    for name, m in snap["metrics"].items():
        v = "없음" if m["value"] is None else f"{m['value']:g}"
        mark = {"ok": "OK  ", "warn": "WARN", "breach": "위반", "info": "·   ", "nodata": "데이터X"}[m["status"]]
        print(f"  [{mark}] {m['label']:22s} {v:>12s}")
    if breaches:
        print(f"  ★ 위반 {len(breaches)}: " + " | ".join(breaches))
    if warns:
        print(f"  · 경고 {len(warns)}: " + " | ".join(warns))
    snap["breaches"] = breaches
    snap["warns"] = warns

    os.makedirs(OUTDIR, exist_ok=True)
    stamp = now.strftime("%Y%m%d-%H%M%S")
    jpath = os.path.join(OUTDIR, f"dq_{stamp}.json")

    # ── 차트 ──
    png = None
    if not a.no_chart:
        series_by = {}
        for name, agg, warn, breach, label in SPECS:
            s = range_series(name, a.hours)
            if s:
                series_by[name] = (s, label, warn, breach)
        if series_by:
            keys = list(series_by)[:12]
            cols = 3
            rows = (len(keys) + cols - 1) // cols
            fig, axes = plt.subplots(rows, cols, figsize=(5.2 * cols, 3.0 * rows), squeeze=False)
            for i, name in enumerate(keys):
                ax = axes[i // cols][i % cols]
                s, label, warn, breach = series_by[name]
                for ser in s[:4]:
                    lb = ",".join(f"{k}={v}" for k, v in list(ser["labels"].items())[:2]) or name
                    xs = [p[0] for p in ser["points"]]
                    ys = [p[1] for p in ser["points"]]
                    ax.plot(xs, ys, marker=".", linewidth=1.2, label=lb[:22])
                if breach is not None:
                    ax.axhline(breach, color="crimson", linestyle="--", linewidth=1,
                               label=f"breach {breach:g}")
                if warn is not None:
                    ax.axhline(warn, color="orange", linestyle=":", linewidth=1,
                               label=f"warn {warn:g}")
                ax.set_title(EN.get(name, name), fontsize=10)
                ax.grid(alpha=0.25)
                ax.xaxis.set_major_formatter(mdates.DateFormatter("%m-%d %H:%M"))
                for lb in ax.get_xticklabels():
                    lb.set_rotation(30)
                    lb.set_fontsize(7)
                ax.tick_params(axis="y", labelsize=8)
                if s[0]["labels"] or breach is not None:
                    ax.legend(fontsize=6, loc="best")
            for j in range(len(keys), rows * cols):
                axes[j // cols][j % cols].axis("off")
            fig.suptitle(f"analyist_dd DQ / ingestion monitoring  {now.strftime('%Y-%m-%d %H:%M')} KST"
                         f"   (breach {len(breaches)} / warn {len(warns)})", fontsize=12)
            fig.tight_layout(rect=(0, 0, 1, 0.97))
            png = os.path.join(OUTDIR, f"dq_{stamp}.png")
            fig.savefig(png, dpi=110)
            plt.close(fig)
            snap["chart"] = os.path.relpath(png, PROJ)
            print(f"  차트: {snap['chart']}")

    with open(jpath, "w", encoding="utf-8") as f:
        json.dump(snap, f, ensure_ascii=False, indent=2)
    with open(HISTORY, "a", encoding="utf-8") as f:
        f.write(json.dumps({"ts": snap["ts"], "breaches": breaches, "warns": warns,
                            "values": {k: v["value"] for k, v in snap["metrics"].items()
                                       if v.get("value") is not None},
                            "chart": snap.get("chart")}, ensure_ascii=False) + "\n")

    if breaches:
        return 3
    if warns:
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main())

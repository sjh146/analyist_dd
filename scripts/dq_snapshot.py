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
import glob
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
    # gap 은 **정보용(임계값 없음)** — 어떤 수치 문턱도 옳지 않다.
    # WHY (2026-09-25 실측 위반 오탐): gap = claimed(파서생성) - persisted(테이블 델타) 인데,
    #   멱등 upsert 러너가 이미 적재된 구간을 재실행하면 기존행이 ON CONFLICT 로 빠져
    #   gap 이 **적재량 규모로** 커진다(실측: claimed 117,155 / 신규 54,324 → gap 62,831 ≥ 5,000 breach).
    #   같은 실행이 실제로는 54,324행을 신규 적재한 **성공 실행**이었다.
    #   gap 을 문턱으로 잡으면 "재수집할수록 위반"이 되어 큰 백필이 항상 breach 로 뜬다.
    #   Prometheus 알림도 같은 이유로 gap 을 **알림하지 않는다**(config/prometheus/alert.dq.rules.yml NOTE 1).
    #   진짜 실패는 gap 이 아니라 source>0 AND claimed==0 = dq_claim_parse_failure 로 잡는다(그건 breach 0 유지).
    ("dq_claim_gap", "sum", None, None, "자기신고 갭(중복재수집 포함·정보용)"),
    ("dq_claim_source", "sum", None, None, "소스 수신 행수"),
    # ⚠ 임계값은 **기준선 위**에 둔다. 살아있는(nonzero_ratio>0) 피처만 분모로 세므로
    #    상수 피처는 정의상 여기 포함되고, 실측 기준선이 0.38(29/76)이다.
    #    warn 을 0.35 로 두면 0.38 >= 0.35 가 매 틱 성립해 **상시 경고**가 뜬다(실측 2026-09-25:
    #    값이 0.3816 으로 15스냅샷 내내 불변인데 warn). 문턱은 Prometheus 알림(>0.50)과 맞춘다.
    ("dq_feature_stock_constant_ratio", "max", 0.45, 0.50, "종목상수 피처 비율"),
    # ⚠ 착시/결측은 **개수(count)** 로 본다 — MAX 는 최악 피처 하나가 값을 지배해
    #   전체 테이블 기준 기준선이 0.9998 이다(R10 의 near-empty 피처). 실측 2026-09-25 20:20.
    #   임계값은 실측 기준선 **위**에 둔다: 착시>0.10 = 36개, 결측>0.90 = 8개.
    #   2026-09-25 21:17 기준선 이동: 15 → 36. 악화가 아니라 **측정 인구가 180 → 199개**로
    #   늘어난 결과다(R11 거시 2개 + R12 재무비율 19개 = +21). 같은 피처의 착시가 커진 게 아니다
    #   — 착시는 배치별로 R10 15 / R11 2 / R12 19 이고 09-24 패널 배치는 0개다(실측).
    #   인구가 늘면 개수 메트릭의 기준선도 함께 늘어난다 → 문턱도 그 위로 옮긴다.
    #   MAX 메트릭은 아래에 정보용으로 남겨 추세만 본다(임계값 없음).
    ("dq_feature_coverage_illusion_count", "max", 40.0, 45.0, "커버리지 착시 피처 수(기준선 36)"),
    ("dq_feature_null_ratio_high_count", "max", 10.0, 15.0, "결측90%↑ 피처 수(기준선 8)"),
    ("dq_feature_coverage_illusion_max", "max", None, None, "커버리지 착시 최대(정보용)"),
    ("dq_feature_null_ratio_max", "max", None, None, "피처 결측 최대(정보용)"),
    ("dq_feature_oldest_age_days", "max", None, None, "가장 오래된 피처 측정 나이(일)"),
    ("dq_feature_market_level_count", "max", None, None, "시장레벨 피처 수"),
    ("dq_padding_rows_before_listing", "max", None, 0.0, "상장 전 행(padding)"),
    # 뉴스 분석 파이프라인 (앱은 30분 주기). 실측 2026-09-25: news_analysis 의 url 유니크 제약 누락으로
    # **2일간 저장이 전멸**(24h 3,657건 폐기)했는데 지표가 없어 로그 grep 전엔 아무도 몰랐다.
    # 신선도로 "돌지만 저장이 안 되는" 상태를 잡는다(2시간 넘게 새 저장 없으면 warn).
    ("news_analysis_freshness_hours", "max", 2.0, 6.0, "뉴스 저장 신선도(시간)"),
    ("news_analysis_freshness_rows_24h", "sum", None, None, "뉴스 24h 저장 행수"),
    ("feature_alive_count", "max", None, None, "살아있는 피처"),
    ("feature_dead_count", "max", None, None, "죽은 피처"),
]

# 차트 라벨은 영문으로 쓴다 — 이 WSL 에는 한글 폰트가 없어 글리프가 전부 깨진다
# (실측 2026-09-25: "Glyph ... missing from current font" 경고가 라벨 수만큼 발생).
# 콘솔·JSON 은 한글 label 을 그대로 쓴다(사람이 읽는 쪽은 한글이 맞다).
# ── 모니터링 사각지대 기준선 (실측 2026-09-25) ────────────────────────────────
# 아래 15개는 **정상 가동 스냅샷 21개에서 21/21 전부 값이 있었다**(history.jsonl 실측).
# news_analysis_* 2개는 도중에 추가된 지표라 9/21 — 기준선에서 제외한다.
# 판정에 Prometheus 의 5분 lookback 을 이용한다: 스크랩 1회(60초) 누락으로는 nodata 가
# 생기지 않고, nodata = "5분 이상 연속 소실" = 진짜 장애다.
CORE_ALWAYS = {
    "market_data_freshness_days", "market_data_rows_recent",
    "market_data_zero_volume_ratio_20d", "market_data_frozen_ratio_20d",
    "dq_asof_violation_rows", "dq_claim_parse_failure", "dq_claim_gap",
    "dq_claim_source", "dq_feature_stock_constant_ratio",
    "dq_feature_coverage_illusion_max", "dq_feature_null_ratio_max",
    "dq_feature_market_level_count", "dq_padding_rows_before_listing",
    "feature_alive_count", "feature_dead_count",
}

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


def _host_uptime_s():
    """호스트 업타임(초). 읽을 수 없으면 None — 판정은 '모름'으로 두고 위반을 유지한다."""
    try:
        with open("/proc/uptime", encoding="ascii") as f:
            return float(f.read().split()[0])
    except (OSError, ValueError, IndexError):
        return None


def classify_blackout(nodata, n_total, core_missing, uptime_s):
    """조회 실패(nodata)를 위반/경고로 판정 → (severity, message). severity: 'breach' | 'warn'.

    기본은 **위반**이다 — nodata 를 판정에서 빼면 '모니터링 실명'이 '정상'으로 보고된다
    (실측 2026-09-25 20:01: postgres-exporter 스크랩 12.7초 > scrape_timeout 10초로 dq_* 51개가
    통째로 사라졌는데 rc=0 이었다).

    예외는 **기동 과도기**뿐이다(실측 2026-09-26 18:44: WSL 재부팅 18:44, 스냅샷 18:44:39,
    exporter 의 DB 커넥션 수립 18:44:44). 조건을 좁게 둔다 — ①전면 소실(≥90%)이고
    ②호스트 업타임이 300초 미만일 때만 경고로 낮춘다. 부분 소실(타겟 1개만 죽음)이나
    업타임이 지난 뒤의 소실은 그대로 위반이고, 다음 틱에서는 어떤 경우든 위반으로 재판정된다.
    """
    msg = (f"모니터링 사각지대: 핵심 {len(core_missing)}/{len(CORE_ALWAYS)}개 미조회 "
           f"(전체 nodata {len(nodata)}/{n_total}) — Prometheus 타겟/exporter 스크랩 확인")
    if nodata and len(nodata) / n_total >= 0.9 and uptime_s is not None and uptime_s < 300:
        return "warn", f"{msg} [기동 과도기: 호스트 업타임 {uptime_s:.0f}s — 다음 틱 재판정]"
    return "breach", msg


def _prune(outdir, keep_png=48, keep_json=48, keep_hist=2000):
    """보존 정책 — 스냅샷은 격 2시간마다 쌓인다(하루 12개, PNG ~200KB).

    정리하지 않으면 무한 증가한다(실측: 하루 12 PNG ≈ 2.4MB). 최근 48개(약 4일)만 남긴다.
    history.jsonl 은 요약 1줄씩이라 작지만 상한을 둬 장기적으로도 안전하게 만든다.
    """
    for ext, keep in ((".png", keep_png), (".json", keep_json)):
        files = sorted(glob.glob(os.path.join(outdir, "*" + ext)))
        for f in files[:-keep] if len(files) > keep else []:
            try:
                os.remove(f)
            except OSError:
                pass
    try:
        with open(HISTORY, encoding="utf-8") as f:
            lines = f.readlines()
        if len(lines) > keep_hist:
            with open(HISTORY, "w", encoding="utf-8") as f:
                f.writelines(lines[-keep_hist:])
    except OSError:
        pass


def _api(path, params):
    """Prometheus 조회. 도달 불가·응답 지연·JSON 파손이면 빈 dict(=값 없음)로 흘려보낸다.

    WHY: 예전에는 URLError 가 그대로 올라와 트레이스백과 함께 rc=1 로 죽었다(실측 2026-09-26:
    PROM_URL 을 닫힌 포트로 두면 `urllib.error.URLError: Connection refused` 스택만 남았다).
    사각지대를 판정하려고 만든 코드가 정작 사각지대에서 판정 줄 대신 스택을 출력한 것이다.
    조회 실패를 '값 없음'으로 넘기면 전 메트릭이 nodata 가 되어 위반/기동과도기로 **판정**된다.
    """
    url = f"{PROM}{path}?" + urllib.parse.urlencode(params)
    try:
        with urllib.request.urlopen(url, timeout=20) as r:
            return json.load(r)
    except (OSError, json.JSONDecodeError):
        return {}


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
    breaches, warns, nodata = [], [], []

    for name, agg, warn, breach, label in SPECS:
        vals, series = instant(name)
        if not vals:
            snap["metrics"][name] = {"label": label, "value": None, "status": "nodata"}
            nodata.append(name)
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

    # ── 모니터링 사각지대 판정 ────────────────────────────────────────────────
    # WHY: nodata 를 그냥 넘기면 완전 실명이 rc=0 으로 "정상" 보고된다 — 실측 2026-09-25 20:01:
    #   postgres-exporter 스크랩이 12.7초인데 Prometheus scrape_timeout 이 10초여서 매 스크랩이
    #   실패 → dq_* 51개 전부 소실 → dq_snapshot 은 17개 전부 '없음' 인데 rc=0 을 반환했다.
    #   "실패할 수 없는 check 는 check 가 아니다"의 같은 함정: 판정에서 nodata 를 빼면
    #   모니터링이 죽은 것이 모니터링이 잘 도는 것과 구분되지 않는다.
    core_missing = [n for n in nodata if n in CORE_ALWAYS]
    n_total = len(SPECS)
    if core_missing or len(nodata) / n_total >= 0.3:
        severity, msg = classify_blackout(nodata, n_total, core_missing, _host_uptime_s())
        (breaches if severity == "breach" else warns).append(msg)
    snap["nodata"] = nodata
    snap["nodata_core"] = core_missing

    # ── 출력 ──
    print(f"[dq_snapshot] {now.isoformat(timespec='seconds')} (추세 {a.hours:g}h)")
    if nodata:
        print(f"  [!!] 조회 실패(nodata) {len(nodata)}/{n_total}: " + ", ".join(nodata))
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
                            "nodata": len(nodata), "nodata_core": len(snap["nodata_core"]),
                            "values": {k: v["value"] for k, v in snap["metrics"].items()
                                       if v.get("value") is not None},
                            "chart": snap.get("chart")}, ensure_ascii=False) + "\n")

    _prune(OUTDIR)   # 스냅샷 보존 정책(최근 48개) — 무한 증가 방지
    if breaches:
        return 3
    if warns:
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main())

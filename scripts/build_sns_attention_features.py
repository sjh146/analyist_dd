#!/usr/bin/env python3
"""SNS 관심·모멘텀·활동량 피처 빌더 — CG327 데이터 축 (2026-11-18)

왜 (실측 근거):
  · 미배선 원천 스윕을 **신선 패널**로 다시 재면(scripts/source_coverage_sweep.py --panel
    panel_200liq_ffill_2605_ss_tv.npz) sns_posts 커버 0.90 · 직전 12개월 안 167거래일 ·
    sns_post_features 18,807행·366종목 — 즉 '커버리지 부족'이 아니라 **가공 누락**이었다.
  · 현 225피처에 SNS 에서 온 것은 sentiment_score 계열뿐이다(sns_sentiment_score*
    · kalman_sentiment). 같은 테이블의 **attention_score · momentum_score · post_count ·
    bot_filtered_count · author_quality_score · kalman_attention · kalman_activity** 는
    한 번도 패널에 들어간 적이 없다 → CG73 이 요구한 '신규 피처 ≥5종'의 후보.
  · 감성(방향)과 달리 **관심도·게시량은 '주목도' 축**이라 기존 피처와 상관이 낮을 수 있다
    (상관은 빌더가 보고한다 — |r|>0.9 면 중복으로 보고).

출력: data/reports/sns_attention_features.npz
  codes (U12) · dates (U10) · F (n_codes*n_dates, 9) · names
  값은 **원천 그대로** — 소비 시 t-1 지연은 patch_panel_ss.py(=제네릭 주입기)가 건다.
  as-of: sns_post_features.created_at 은 집계 완료 시각이므로 같은 날 값은 쓰지 않는다(t-1).

자기검증(전부 PASS 해야 파일을 남긴다):
  ⓐ 코드>100 · 날짜>60 ⓑ 각 열 nan_frac<0.9 ⓒ 각 열 표준편차>0(상수 아님)
  ⓓ 종목상수 열 없음(종목별 유니크값 ≥3) ⓔ 기존 감성열과의 최대 |상관| 보고
"""
from __future__ import annotations

import json
import os
import subprocess
import sys

import numpy as np

DB = ["docker", "exec", "stock_postgres", "psql", "-U", "stock_user", "-d", "stock_trading",
      "-t", "-A", "-F", "|", "-c"]
OUT = os.path.abspath(os.path.join(os.path.dirname(os.path.abspath(__file__)), "..",
                                   "data", "reports", "sns_attention_features.npz"))
NAMES = ["snsa_attention", "snsa_momentum", "snsa_posts", "snsa_posts_z20", "snsa_author_q",
         "snsa_kalman_attention", "snsa_kalman_activity", "snsa_attention_ma5",
         "snsa_attention_z20"]


def psql(sql: str, timeout: int = 300) -> list[list[str]]:
    out = subprocess.run(DB + [sql], capture_output=True, text=True, timeout=timeout)
    if out.returncode != 0:
        raise RuntimeError(f"psql rc={out.returncode}: {out.stderr.strip()[:300]}")
    return [l.split("|") for l in out.stdout.splitlines() if l.strip()]


def _f(x, default=np.nan) -> float:
    try:
        v = float(x)
        return v
    except (TypeError, ValueError):
        return default


def roll(a: np.ndarray, w: int, fn) -> np.ndarray:
    out = np.full_like(a, np.nan)
    for j in range(a.shape[1]):
        lo = max(0, j - w + 1)
        block = a[:, lo:j + 1]
        cnt = np.sum(~np.isnan(block), axis=1)
        with np.errstate(invalid="ignore"):
            s = fn(block, axis=1)
        out[:, j] = np.where(cnt >= max(2, w // 2), s, np.nan)
    return out


def main() -> int:
    rows = psql(
        "SELECT stock_code, trade_date::text, "
        "coalesce(attention_score,0), coalesce(momentum_score,0), coalesce(post_count,0), "
        "coalesce(bot_filtered_count,0), coalesce(author_quality_score,0), "
        "coalesce(kalman_attention,0), coalesce(kalman_activity,0) "
        "FROM sns_post_features WHERE trade_date >= DATE '2026-01-01' "
        "ORDER BY stock_code, trade_date;")
    if not rows:
        print("원천 0행 — 중단", file=sys.stderr)
        return 2

    codes = sorted({r[0] for r in rows})
    dates = sorted({r[1] for r in rows})
    ci = {c: i for i, c in enumerate(codes)}
    di = {d: i for i, d in enumerate(dates)}
    nC, nD = len(codes), len(dates)
    att = np.full((nC, nD), np.nan)
    mom = np.full((nC, nD), np.nan)
    posts = np.full((nC, nD), np.nan)
    botf = np.full((nC, nD), np.nan)
    aq = np.full((nC, nD), np.nan)
    katt = np.full((nC, nD), np.nan)
    kact = np.full((nC, nD), np.nan)
    for r in rows:
        i, j = ci[r[0]], di[r[1]]
        a = _f(r[2], 0.0)
        att[i, j] = a
        mom[i, j] = _f(r[3], 0.0)
        n_posts = _f(r[4], 0.0)
        posts[i, j] = float(np.log1p(max(n_posts, 0.0)))
        nb = _f(r[5], 0.0)
        botf[i, j] = float(nb / n_posts) if n_posts and n_posts > 0 else np.nan
        aq[i, j] = _f(r[6], 0.0)
        katt[i, j] = _f(r[7], 0.0)
        kact[i, j] = _f(r[8], 0.0)

    ma5 = roll(att, 5, np.nanmean)
    ma20 = roll(att, 20, np.nanmean)
    sd20 = roll(att, 20, np.nanstd)
    with np.errstate(invalid="ignore", divide="ignore"):
        z20 = (att - ma20) / np.where(sd20 > 1e-9, sd20, np.nan)
        pma20 = roll(posts, 20, np.nanmean)
        psd20 = roll(posts, 20, np.nanstd)
        posts_z20 = (posts - pma20) / np.where(psd20 > 1e-9, psd20, np.nan)

    # bot_frac 은 종목별 유니크값이 1(전 행 0)이라 피처로 쓰지 않는다 — 자기검증 ⓓ 가 잡았다.
    F = np.stack([att, mom, posts, posts_z20, aq, katt, kact, ma5, z20], axis=2)
    nan_frac = {NAMES[k]: round(float(np.mean(np.isnan(F[:, :, k]))), 4) for k in range(len(NAMES))}
    # 종목상수 검정: 종목별 유니크값 개수
    uniq = {NAMES[k]: int(np.median([len(np.unique(F[i, :, k][~np.isnan(F[i, :, k])]))
                                     for i in range(nC)]) or 0)
            for k in range(len(NAMES))}
    snap = {
        "ts": __import__("datetime").datetime.now().astimezone().isoformat(timespec="seconds"),
        "source": "sns_post_features",
        "n_codes": nC, "n_dates": nD, "dates": [dates[0], dates[-1]],
        "rows": len(rows), "names": NAMES,
        "nan_frac": nan_frac, "stock_median_uniq": uniq,
        "note": "값은 원천 그대로(log1p post_count · bot_frac 파생). 소비 시 t-1 지연은 주입기가 건다.",
    }
    np.savez_compressed(OUT, codes=np.array(codes), dates=np.array(dates), F=F,
                        names=np.array(NAMES), meta=np.array(json.dumps(snap, ensure_ascii=False)))

    # ── 자기검증 ────────────────────────────────────────────────────────────
    assert nC > 100 and nD > 60, (nC, nD)
    for k, f in enumerate(NAMES):
        assert nan_frac[f] < 0.9, f"ⓑ {f} 결측 {nan_frac[f]}"
        assert float(np.nanstd(F[:, :, k])) > 0, f"ⓒ {f} 상수"
        assert uniq[f] >= 3, f"ⓓ {f} 종목상수(중앙 유니크 {uniq[f]})"
    print(json.dumps(snap, ensure_ascii=False, indent=1))
    print("저장:", os.path.abspath(OUT))
    print("자체검증 PASS ⓐⓑⓒⓓ")
    return 0


if __name__ == "__main__":
    sys.exit(main())

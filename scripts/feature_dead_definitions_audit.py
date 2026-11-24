#!/usr/bin/env python3
"""feature_dead_definitions_audit.py — '죽은 피처'가 계기판마다 다른 수로 보이는 이유를 수치로 분해한다.

WHY (실측 2026-11-24 22:4x)
  같은 날 같은 테이블에서 두 계기판이 다른 수를 보고했다:
    · 스코어보드(리서처 stanza, Prometheus feature_dead_count)  : 죽은 17 / 살아 192
    · 계약검사 FEAT-1 (nonzero_ratio_honest < 0.01)             : 죽은 41 / 209
    · 11-23 보드 기록                                           : 죽은 12 / 살아 190
  분모(202 vs 209)와 문턱(정확히 0 vs 1% 미만)이 둘 다 달라서, 둘 다 "통과"로 보이면서
  어느 쪽이 판정 기준인지 알 수 없었다. 수를 하나로 맞추는 게 아니라 **정의를 못 박고
  분해표를 남기는 것**이 목적이다.

정의 3종(모두 feature_coverage 현재상태 테이블, 피처당 1행)
  A. 완전사망(naive) : nonzero_ratio = 0            → Prometheus feature_dead_count (Grafana 계기판)
  B. 희소사망(honest): nonzero_ratio_honest < 0.01  → 계약검사 FEAT-1 (문턱 45)
  C. 차집합         : B − A = 값이 있긴 한데 1% 미만 (종목 몇 개만 값이 있는 피처)

측정 인구 주의(실측 2026-11-24)
  feature_coverage 는 (피처, 창, 표본)이 다른 측정이 섞인 **현재상태** 테이블이다.
  window_days 3종(30/525/42일) · 표본 30~60종목으로 갱신된 행이 섞여 있어, 죽음/생존이
  **빌드 순서·표본 크기에 따라 흔들린다**(R49 실측: 60종목 표본 갱신으로 죽은 12→24).
  그래서 죽은 수를 인용할 때는 반드시 window_days 분포와 최고령 측정일을 함께 본다.

사용
  /usr/bin/python3 scripts/feature_dead_definitions_audit.py [--json out.json]
"""
import argparse
import json
import subprocess
import sys

SQL = """
SELECT
  count(*) AS n_rows,
  count(*) FILTER (WHERE nonzero_ratio = 0) AS dead_naive,
  count(*) FILTER (WHERE nonzero_ratio > 0) AS alive_naive,
  count(*) FILTER (WHERE nonzero_ratio_honest < 0.01) AS dead_honest,
  count(*) FILTER (WHERE nonzero_ratio_honest >= 0.01) AS alive_honest,
  count(*) FILTER (WHERE nonzero_ratio > 0 AND nonzero_ratio_honest < 0.01) AS sparse_only,
  count(*) FILTER (WHERE null_ratio >= 0.99) AS null_99,
  count(DISTINCT window_days) AS window_variants,
  max(computed_at)::date AS newest,
  min(computed_at)::date AS oldest
FROM feature_coverage
"""

WIN_SQL = "SELECT window_days, count(*), min(computed_at)::date, max(computed_at)::date FROM feature_coverage GROUP BY 1 ORDER BY 1"

SPARSE_SQL = """
SELECT feature_name, round(nonzero_ratio::numeric, 4), round(nonzero_ratio_honest::numeric, 4),
       round(null_ratio::numeric, 3), window_days, computed_at::date
FROM feature_coverage
WHERE nonzero_ratio > 0 AND nonzero_ratio_honest < 0.01
ORDER BY nonzero_ratio ASC
"""


def psql(sql):
    cmd = ["docker", "exec", "stock_postgres", "psql", "-U", "stock_user", "-d", "stock_trading",
           "-t", "-A", "-F", "|", "-c", sql]
    out = subprocess.run(cmd, capture_output=True, text=True, timeout=120)
    if out.returncode != 0:
        raise SystemExit(f"psql rc={out.returncode}: {out.stderr.strip()[:300]}")
    return [ln.split("|") for ln in out.stdout.strip().splitlines() if ln.strip()]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--json", default=None)
    a = ap.parse_args()

    r = psql(SQL)[0]
    keys = ["n_rows", "dead_naive", "alive_naive", "dead_honest", "alive_honest", "sparse_only",
            "null_99", "window_variants", "newest", "oldest"]
    d = {k: (int(v) if k not in ("newest", "oldest") else v) for k, v in zip(keys, r)}

    wins = [{"window_days": int(w), "rows": int(n), "oldest": o, "newest": nw}
            for w, n, o, nw in psql(WIN_SQL)]
    sparse = [{"feature": f, "nonzero_naive": float(a1), "nonzero_honest": float(a2),
               "null_ratio": float(nl), "window_days": int(w), "computed_at": c}
              for f, a1, a2, nl, w, c in psql(SPARSE_SQL)]

    d["windows"] = wins
    d["sparse_features"] = sparse

    print(f"[죽은 피처 정의 분해] 행 {d['n_rows']} · 창 {d['window_variants']}종 "
          f"(최고령 {d['oldest']} ~ 최신 {d['newest']})")
    print(f"  A 완전사망(nonzero=0, Grafana feature_dead_count) : {d['dead_naive']}  "
          f"(살아 {d['alive_naive']})")
    print(f"  B 희소사망(honest<1%, 계약검사 FEAT-1·문턱 45)   : {d['dead_honest']}  "
          f"(살아 {d['alive_honest']})")
    print(f"  C 차집합(0<naive, honest<1% = 몇 종목만 값 있음)  : {d['sparse_only']}")
    print(f"  결측 99%↑ {d['null_99']}개")
    print("  창 분포: " + " · ".join(f"{w['window_days']}일 {w['rows']}행({w['oldest']}~{w['newest']})"
                                    for w in wins))
    print("  판정 기준: **B(희소사망)** — 계약 문턱 45가 이 정의로 설정됐고, '1% 미만 비영' 피처는 "
          "횡단면 선별에 기여할 수 없다. A는 '완전사망' 정보용으로 병기한다.")
    print("  ⚠ 인구 주의: 창·표본이 섞인 현재상태 값이라 죽은 수는 빌드 순서·표본에 따라 흔들린다"
          "(R49: 60종목 표본으로 12→24). R51(창 120일 고정) 승인 전까지 인구 비교는 금지.")
    if sparse:
        print(f"  C 목록(상위 5): " + ", ".join(f"{s['feature']}({s['nonzero_honest']:.3f})"
                                              for s in sparse[:5]))
    if a.json:
        with open(a.json, "w") as f:
            json.dump(d, f, ensure_ascii=False, indent=1)
        print(f"[ok] {a.json}")


if __name__ == "__main__":
    sys.exit(main())

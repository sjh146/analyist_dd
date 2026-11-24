#!/usr/bin/env python3
"""feature_dead_definitions_audit.py — '죽은 피처'가 계기판마다 다른 수로 보이는 이유를 수치로 분해한다.

WHY (실측 2026-11-24 22:4x)
  같은 날 같은 테이블에서 두 계기판이 다른 수를 보고했다.
    · 스코어보드(리서처 stanza, Prometheus feature_dead_count) : 죽은 17 / 살아 192
    · 계약검사 FEAT-1 (nonzero_ratio_honest < 0.01, 문턱 45)   : 죽은 41 / 209
    · 11-23 보드 기록                                          : 죽은 12 / 살아 190
  분모(202 vs 209)와 문턱(정확히 0 vs 1% 미만)이 둘 다 달라서, 둘 다 "통과"로 보이면서
  어느 쪽이 판정 기준인지 알 수 없었다. 수를 하나로 맞추는 게 아니라 **정의를 못 박고
  분해표를 남기는 것**이 목적이다.

정의 3종(feature_coverage 현재상태 테이블, 피처당 1행)
  A. 완전사망(naive)  : nonzero_ratio = 0            → Prometheus feature_dead_count (Grafana)
  B. 희소사망(honest) : nonzero_ratio_honest < 0.01  → 계약검사 FEAT-1 (문턱 45)
  C. 차집합           : B − A = 값이 있긴 한데 1% 미만(종목 몇 개만 값이 있는 피처)

판정 기준은 B다. 계약 문턱 45가 이 정의로 설정됐고(contract_check.py), 1% 미만 비영 피처는
횡단면 선별에 기여할 수 없다. A는 '완전사망' 정보용으로 병기한다.

측정 인구 주의(실측 2026-11-24)
  feature_coverage 는 (피처, 창, 표본)이 다른 측정이 섞인 **현재상태** 테이블이다.
  창 3종(30/42/525일) · 표본 30~60종목으로 갱신된 행이 섞여 있고 **모델 창(120일) 행은 0개**라,
  헤드라인 B 수는 모델이 실제 쓰는 창을 한 행도 포함하지 않는다. 죽은 수는 빌드 순서·표본에
  따라 흔들린다(R49 실측: 60종목 표본 갱신으로 12→24). 그래서 창별 분해와 인구 혼재 플래그를
  JSON 에도 함께 남긴다 — 기계 소비자가 41을 단독 인용하지 못하게 하기 위해서다.

정합성 불변(스스로 검증한다)
  dead_naive + alive_naive + naive_null            == n_rows
  dead_honest + alive_honest + honest_null         == n_rows
  dead_naive + sparse_only + (naive=0 & honest NULL) == dead_honest
  honest 컬럼은 nullable 로 ALTER 됐고 백필이 없어(feature_coverage_report.py 등) NULL 이 생기면
  FEAT-1 게이트(`nonzero_ratio_honest < 0.01`)가 그 행을 죽은 수에서 **조용히 제외**한다 —
  과소집계로 게이트가 통과할 수 있으므로 이 감사가 그 자리를 메운다.

사용
  /usr/bin/python3 scripts/feature_dead_definitions_audit.py [--json out.json] [--strict]
  --strict: 불변 위반 시 rc=3 (크론·계약검사용). 기본은 rc=0.
"""
from __future__ import annotations

import argparse
import json
import subprocess
import sys

SQL = r"""
WITH base AS (
  SELECT * FROM feature_coverage
)
SELECT json_build_object(
  'n_rows', count(*),
  'dead_naive', count(*) FILTER (WHERE nonzero_ratio = 0),
  'alive_naive', count(*) FILTER (WHERE nonzero_ratio > 0),
  'naive_null', count(*) FILTER (WHERE nonzero_ratio IS NULL),
  'dead_honest', count(*) FILTER (WHERE nonzero_ratio_honest < 0.01),
  'alive_honest', count(*) FILTER (WHERE nonzero_ratio_honest >= 0.01),
  'honest_null', count(*) FILTER (WHERE nonzero_ratio_honest IS NULL),
  'sparse_only', count(*) FILTER (WHERE nonzero_ratio > 0 AND nonzero_ratio_honest < 0.01),
  'naive_dead_honest_null', count(*) FILTER (WHERE nonzero_ratio = 0 AND nonzero_ratio_honest IS NULL),
  'null_99', count(*) FILTER (WHERE null_ratio >= 0.99),
  'null_ratio_null', count(*) FILTER (WHERE null_ratio IS NULL),
  'window_variants', count(DISTINCT window_days),
  'newest', max(computed_at)::date,
  'oldest', min(computed_at)::date,
  'model_window_rows', count(*) FILTER (WHERE window_days = 120),
  'model_window_dead_honest', count(*) FILTER (WHERE window_days = 120 AND nonzero_ratio_honest < 0.01),
  'windows', (
    SELECT json_agg(w ORDER BY w.window_days) FROM (
      SELECT window_days,
             count(*) AS rows,
             count(*) FILTER (WHERE nonzero_ratio = 0) AS dead_naive,
             count(*) FILTER (WHERE nonzero_ratio_honest < 0.01) AS dead_honest,
             min(computed_at)::date AS oldest,
             max(computed_at)::date AS newest
      FROM base GROUP BY 1
    ) w
  ),
  'sparse_features', (
    SELECT json_agg(s) FROM (
      SELECT feature_name,
             round(nonzero_ratio::numeric, 4) AS nonzero_naive,
             round(nonzero_ratio_honest::numeric, 4) AS nonzero_honest,
             round(COALESCE(null_ratio, 1)::numeric, 3) AS null_ratio,
             window_days, computed_at::date AS computed_at
      FROM base
      WHERE nonzero_ratio > 0 AND nonzero_ratio_honest < 0.01
      ORDER BY nonzero_ratio ASC
      LIMIT 40
    ) s
  )
) AS j
FROM base
"""


def psql_scalar(sql, timeout=180):
    cmd = ["docker", "exec", "stock_postgres", "psql", "-U", "stock_user", "-d", "stock_trading",
           "-t", "-A", "-c", sql]
    out = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
    if out.returncode != 0:
        raise SystemExit(f"psql rc={out.returncode}: {out.stderr.strip()[:300]}")
    return out.stdout.strip()


def invariants(d):
    checks = []
    checks.append(("naive 합", d["dead_naive"] + d["alive_naive"] + d["naive_null"], d["n_rows"]))
    checks.append(("honest 합", d["dead_honest"] + d["alive_honest"] + d["honest_null"], d["n_rows"]))
    checks.append(("B = A + C + (A&honestNULL)",
                   d["dead_naive"] + d["sparse_only"] + d["naive_dead_honest_null"], d["dead_honest"]))
    return [{"check": c, "got": g, "want": w, "ok": g == w} for c, g, w in checks]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--json", default=None)
    ap.add_argument("--strict", action="store_true")
    a = ap.parse_args()

    raw = psql_scalar(SQL)
    try:
        d = json.loads(raw)
    except json.JSONDecodeError as e:
        raise SystemExit(f"JSON 파싱 실패: {e} — psql 출력: {raw[:300]}")

    inv = invariants(d)
    d["invariants"] = inv
    d["invariants_ok"] = all(c["ok"] for c in inv)
    mixed = d["window_variants"] > 1 or d["model_window_rows"] == 0
    d["population_mixed"] = bool(mixed)
    d["citation_guard"] = (
        "인용 금지: 이 수는 창·표본이 섞인 현재상태 값이고 모델 창(120일) 행이 "
        f"{d['model_window_rows']}개다. 창별 분해(windows)와 함께, R51(창 120일 고정) 승인 후에만 "
        "전후 비교에 쓴다."
    ) if mixed else "인구 단일(창 1종·모델 창 행 존재) — 인용 가능."

    wins = d.get("windows") or []
    print(f"[죽은 피처 정의 분해] 행 {d['n_rows']} · 창 {d['window_variants']}종 "
          f"({d['oldest']}~{d['newest']}) · 모델창(120일) 행 {d['model_window_rows']}")
    print(f"  A 완전사망(nonzero=0, Grafana feature_dead_count) : {d['dead_naive']}  "
          f"(살아 {d['alive_naive']} · NULL {d['naive_null']})")
    print(f"  B 희소사망(honest<1%, 계약검사 FEAT-1·문턱 45)   : {d['dead_honest']}  "
          f"(살아 {d['alive_honest']} · NULL {d['honest_null']})")
    print(f"  C 차집합(0<naive, honest<1% = 몇 종목만 값 있음)  : {d['sparse_only']} · 결측99%↑ {d['null_99']}")
    if wins:
        print("  창별: " + " · ".join(
            f"{w['window_days']}일 {w['rows']}행(A {w['dead_naive']}·B {w['dead_honest']}, {w['oldest']}~{w['newest']})"
            for w in wins))
    print("  판정 기준: **B(희소사망)** — 계약 문턱 45가 이 정의로 설정됐고 '1% 미만 비영' 피처는 "
          "횡단면 선별에 기여할 수 없다. A는 '완전사망' 정보용 병기.")
    bad = [c for c in inv if not c["ok"]]
    if bad:
        print("  ⛔ 불변 위반: " + " · ".join(f"{c['check']} {c['got']}≠{c['want']}" for c in bad))
    else:
        print("  정합성: 불변 3종 일치")
    if mixed:
        print("  ⚠ " + d["citation_guard"])
    if d.get("sparse_features"):
        print("  C 상위 5: " + ", ".join(f"{s['feature_name']}(honest {s['nonzero_honest']:.3f}·"
                                          f"{s['window_days']}일)"
                                          for s in d["sparse_features"][:5]))
    if a.json:
        with open(a.json, "w") as f:
            json.dump(d, f, ensure_ascii=False, indent=1)
        print(f"[ok] {a.json}")
    return 3 if (a.strict and (bad or not d["invariants_ok"])) else 0


if __name__ == "__main__":
    sys.exit(main())

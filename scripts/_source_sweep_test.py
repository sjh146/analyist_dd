#!/usr/bin/env python3
"""자체점검 — source_coverage_sweep 의 게이트 규칙 + 구동기 캐시 경로 (DB 없이 돈다).

회귀 대상(실측 사고에서 유도):
  ① 종목상수/메타 테이블이 '신규 후보'로 오보되지 않는다(created_at 만 있고 5일 이하 → STATIC)
  ② 시장레벨(코드 컬럼 없음)은 횡단면 모델 후보가 아니다 → MARKET_LEVEL
  ③ 알려진 부분커버 원천(CLOSED_NOTE)은 게이트를 통과해도 CLOSED 로 남는다
  ④ 게이트는 **커버리지·이력 둘 다** 요구한다(cover 만 크고 이력 짧으면 BLOCKED)
  ⑤ 증거 경로는 절대경로다(구동기가 cwd 무관하게 읽는다)
  ⑥ 구동기 캐시 경로는 psql 을 부르지 않는다(틱 소요 시간 상수화)
"""
import json, os, subprocess, sys, tempfile, time

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import source_coverage_sweep as scs   # noqa: E402
import model_engineer_cycle as mec    # noqa: E402

fails = []


def check(name, cond, detail=""):
    print(("  PASS " if cond else "  FAIL ") + name + ((" — " + detail) if detail else ""))
    if not cond:
        fails.append(name)


print("① 종목상수/메타 → STATIC")
check("stocks(created_at 5일)", scs.classify(
    {"table": "stocks", "code_col": "stock_code", "date_col": "created_at",
     "rows": 4343, "n_days": 5}) == "STATIC")
check("stock_vectors", scs.classify(
    {"table": "stock_vectors", "code_col": "stock_code", "date_col": "created_at",
     "rows": 4343, "n_days": 5}) == "STATIC")

print("② 시장레벨")
check("krx_program_trading", scs.classify(
    {"table": "krx_program_trading", "code_col": None, "date_col": "trade_date",
     "rows": 212}) == "MARKET_LEVEL")

print("③ 알려진 부분커버는 게이트를 넘겨도 CLOSED")
check("foreign_institutional", scs.classify(
    {"table": "foreign_institutional", "code_col": "stock_code", "date_col": "trade_date",
     "rows": 148344, "n_codes": 1071, "n_days": 279, "panel_cover_frac": 0.99}) == "CLOSED")

print("④ 게이트는 커버리지 AND 이력")
check("cover 0.9 · days 100 → BLOCKED", scs.classify(
    {"table": "brand_new", "code_col": "stock_code", "date_col": "trade_date",
     "rows": 1, "n_days": 100, "panel_cover_frac": 0.9}) == "BLOCKED")
check("cover 0.5 · days 300 → BLOCKED", scs.classify(
    {"table": "brand_new", "code_col": "stock_code", "date_col": "trade_date",
     "rows": 1, "n_days": 300, "panel_cover_frac": 0.5}) == "BLOCKED")
check("cover 0.9 · days 300 → NEW_CANDIDATE", scs.classify(
    {"table": "brand_new", "code_col": "stock_code", "date_col": "trade_date",
     "rows": 1, "n_days": 300, "panel_cover_frac": 0.9}) == "NEW_CANDIDATE")

print("⑤ 요약줄")
res_none = {"new_candidates": [], "tables": [{"verdict": "BLOCKED"}, {"verdict": "CLOSED"},
                                             {"verdict": "MARKET_LEVEL"}]}
check("후보 없음 문구", "신규 후보 없음" in scs.summary_line(res_none))
check("후보 있음 문구", "피처 배선 실험 등록 필요" in scs.summary_line({"new_candidates": ["x"], "tables": []}))
check("EVID 절대경로", os.path.isabs(scs.EVID), scs.EVID)

print("⑥ 구동기 캐시 경로는 psql 을 부르지 않는다")
tmp = tempfile.mkdtemp()
res = {"ts": "2026-10-10T19:00:00+09:00", "new_candidates": [], "tables": [{"verdict": "CLOSED"}]}
ev = os.path.join(tmp, "ev.json")
json.dump(res, open(ev, "w"))
calls = []
orig = subprocess.run


def fake_run(cmd, *a, **kw):
    calls.append(cmd)
    raise AssertionError("cached 경로가 외부 프로세스를 불렀다: %r" % (cmd,))


scs.EVID = ev
subprocess.run = fake_run
mec.subprocess.run = fake_run
try:
    lines = mec.source_axis_sweep_note(max_age_h=12)
finally:
    subprocess.run = orig
    mec.subprocess.run = orig
check("캐시 읽고 psql 미호출", not calls, repr(calls[:1]))
check("캐시 문구", any("캐시" in l for l in lines), str(lines))

print("⑦ 틱 배선 존재")
src = open(os.path.join(HERE, "model_engineer_cycle.py"), encoding="utf-8").read()
check("tick 이 스윕 노트를 부른다", "for line in source_axis_sweep_note():" in src)

print("\n%d/%d PASS" % (0 if fails else 7, 7))
sys.exit(1 if fails else 0)

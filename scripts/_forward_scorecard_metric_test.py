#!/usr/bin/env python3
"""CG75 전방 성적표(forward_scorecard) metric 배선 자체점검 — 호스트에서 실행(pytest 불필요).

무엇을 지키는가(실측 함정 회피):
  · CG43/CG10 함정: 새 metric 을 백로그에만 등록하고 파서·판정·ingest 배선을 빼먹으면,
    실행은 rc=0 으로 끝나고 구동기가 '판정불가'로 원장에 남긴 뒤 항목을 done 으로 닫아
    그 항목의 유일한 산출물이 사라진다 → parse_by_metric/judge_by_metric 라우팅을 검사한다.
  · CG31 함정: 실측(기준선·창) 값을 per_exp 로 실으면 스코어보드가 'arm 최고 AUC'로 오독한다
    → 파서가 per_exp 를 만들지 **않음**을 검사한다.
  · CG75 전제: 표본(n_dates)이 안 쌓인 상태를 '노이즈'로 오독해 축을 조기에 닫으면 안 된다
    → n_dates 미달은 '표본부족'(판정 보류)이어야 한다.

실행: python3 scripts/_forward_scorecard_metric_test.py
"""
import json
import os
import sys
import tempfile
import time

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__))))
import model_engineer_cycle as m  # noqa: E402

fails = []


def check(name, cond, detail=""):
    print(f"{'PASS' if cond else 'FAIL'}  {name}{(' — ' + detail) if detail else ''}")
    if not cond:
        fails.append(name)


def _write(payload, mtime=None):
    fd, p = tempfile.mkstemp(suffix=".json")
    os.close(fd)
    with open(p, "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False)
    if mtime is not None:
        os.utime(p, (mtime, mtime))
    return p


def _scorecard(n_dates=5, auc=0.5437, dmean=0.5290, top10=-0.00257, allr=0.00794):
    return {
        "generated_at": "2026-10-03T07:06:05",
        "predictions_rows": 37378,
        "model_versions": {"v1.0": 37378},
        "result": {
            "h1": {"n_pairs": 17938, "n_dates": n_dates, "skipped": 0,
                   "pooled_auc": auc, "daily_auc_mean": dmean, "daily_auc_list": [0.5, 0.6],
                   "base_rate_up": 0.5284, "top10_ret_mean": top10, "all_ret_mean": allr},
            "h5": {"n_pairs": 6404, "n_dates": n_dates, "skipped": 0,
                   "pooled_auc": auc, "daily_auc_mean": dmean, "daily_auc_list": [0.5, 0.61],
                   "base_rate_up": 0.5498, "top10_ret_mean": top10, "all_ret_mean": allr},
        },
    }


ITEM = {"id": "CG75", "metric": "forward_scorecard", "forward_horizon": 5,
        "min_dates": 10, "min_auc": 0.52,
        "command": ("docker exec -e PYTHONPATH=/app stock_xgboost_ml python "
                    "/app/scripts/forward_scorecard.py --out /app/reports/overnight/fw.json")}

# ── 1) summary_path ───────────────────────────────────────────────────────────
p_def = m.summary_path("forward_scorecard", "docker exec x python /app/scripts/forward_scorecard.py")
check("summary_path 기본 경로", p_def.endswith("services/xgboost-ml/reports/overnight/forward_scorecard.json"),
      p_def)
p_out = m.summary_path("forward_scorecard", ITEM["command"])
check("summary_path 가 --out 을 호스트 경로로 변환",
      p_out == os.path.join(m.PROJ, "services/xgboost-ml/reports/overnight/fw.json"), p_out)

# ── 2) parse 실패 경로 ────────────────────────────────────────────────────────
check("parse: 빈 경로 → 오류", "요약 경로 없음" in m.parse_forward_scorecard("", 0.0).get("error", ""))
check("parse: 파일 없음 → 오류", "요약 파일 없음" in m.parse_forward_scorecard("/no/such.json", 0.0).get("error", ""))

p = _write(_scorecard(), mtime=time.time() - 3600)
parsed_stale = m.parse_forward_scorecard(p, time.time())
check("parse: mtime <= floor → 미갱신 오류", "요약 미갱신" in parsed_stale.get("error", ""), parsed_stale.get("error", ""))
os.remove(p)

p = _write({"generated_at": "x", "result": {}})
parsed_empty = m.parse_forward_scorecard(p, 0.0)
check("parse: result 창 없음 → 오류", "result 창" in parsed_empty.get("error", ""), parsed_empty.get("error", ""))
os.remove(p)

# ── 3) parse 성공 경로 ────────────────────────────────────────────────────────
p = _write(_scorecard())
parsed = m.parse_forward_scorecard(p, 0.0)
check("parse: h1/h5 창 파싱", set(parsed.get("windows", {})) == {"h1", "h5"}, str(sorted(parsed.get("windows", {}))))
check("parse: per_exp 를 만들지 않는다(scoreboard 오독 방지)", "per_exp" not in parsed)
check("parse: predictions_rows 보존", parsed.get("predictions_rows") == 37378)

# ── 4) judge 경로 ─────────────────────────────────────────────────────────────
v, d, delta = m.judge_by_metric(ITEM, parsed)
check("judge: n_dates 5 < 10 → '표본부족'(조기 종결 금지)", v == "표본부족", f"{v} · {d}")
check("judge: 표본부족은 delta=None", delta is None, repr(delta))
os.remove(p)

# n_dates 충족 + 조건 동시 충족 → 신호
p = _write(_scorecard(n_dates=12, auc=0.54, top10=0.03, allr=0.008))
parsed = m.parse_forward_scorecard(p, 0.0)
v, d, delta = m.judge_by_metric(ITEM, parsed)
check("judge: AUC≥0.52 & top10≥평균 & n_dates≥10 → '신호있음'", v == "신호있음", f"{v} · {d}")
check("judge: delta = AUC − min_auc", abs((delta or 0) - 0.02) < 1e-9, repr(delta))
os.remove(p)

# AUC 충족, top10 미달 → 노이즈
p = _write(_scorecard(n_dates=12, auc=0.54, top10=-0.02, allr=0.008))
parsed = m.parse_forward_scorecard(p, 0.0)
v, d, _ = m.judge_by_metric(ITEM, parsed)
check("judge: top10 < 전체평균 → '노이즈'", v == "노이즈", f"{v} · {d}")
os.remove(p)

# AUC 미달 → 노이즈
p = _write(_scorecard(n_dates=12, auc=0.49, top10=0.03, allr=0.008))
parsed = m.parse_forward_scorecard(p, 0.0)
v, d, _ = m.judge_by_metric(ITEM, parsed)
check("judge: AUC<0.52 → '노이즈'", v == "노이즈", f"{v} · {d}")
os.remove(p)

# 창 없음 → 판정불가
v, d, _ = m.judge_by_metric({"id": "CG75", "metric": "forward_scorecard", "forward_horizon": 8},
                            {"windows": {"h1": {}, "h5": {}}})
check("judge: 요청 창(h8) 없음 → '판정불가'", v == "판정불가", f"{v} · {d}")

# ── 5) 라우팅 (CG43/CG10 함정) ────────────────────────────────────────────────
routed_parse = m.parse_by_metric(ITEM, p_def, 0.0)
check("parse_by_metric: forward_scorecard 가 'parser 없음' 이 아니다",
      "parser 없음" not in str(routed_parse.get("error", "")), str(routed_parse.get("error", ""))[:60])
check("parse_by_metric: 미지의 metric 은 여전히 'parser 없음'",
      "parser 없음" in m.parse_by_metric({"metric": "존재하지않음"}, "", 0.0).get("error", ""))

# judge_by_metric 이 judge_forward_scorecard 로 분기하는지(직접 호출과 동일 결과)
p = _write(_scorecard(n_dates=12, auc=0.54, top10=0.03, allr=0.008))
pr = m.parse_by_metric(ITEM, p, 0.0)
check("judge_by_metric: 전용 판정기로 분기(judge_per 아님)",
      m.judge_by_metric(ITEM, pr)[0] == m.judge_forward_scorecard(ITEM, pr)[0])
os.remove(p)

# ── 6) CG75 백로그 항목 정합(등록되면 검사) ────────────────────────────────────
try:
    it = next((i for i in m.load_backlog()["items"] if i.get("id") == "CG75"), None)
    if it and it.get("metric") == "forward_scorecard":
        check("CG75: metric=forward_scorecard 이고 command 에 --out 이 있다",
              "--out" in (it.get("command") or ""), str(it.get("command"))[:80])
    else:
        print("NOTE  CG75 metric 미등록(backlog 단계) — 배선 검사는 승격 시 적용")
except Exception as e:  # noqa: BLE001
    check("CG75 백로그 조회", False, f"{type(e).__name__}: {e}")

print(f"\n{'ALL PASS' if not fails else 'FAILURES: ' + ', '.join(fails)}")
sys.exit(1 if fails else 0)

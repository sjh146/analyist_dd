#!/usr/bin/env python3
"""_factor_signals_parse_test — metric 'factor_signals' 파서/판정기 회귀 검증.

배경(2026-11-25 FAC-01): 구동기에 metric 'factor_signals' 의 경로·파서가 없어 rc=0 완주가
원장에 `판정불가(parser 없음)`으로만 남았다. 게다가 항목이 근거로 삼은 signals 포맷 파일은
현행 run_factor_job 이 쓰지 않는 유령 지표였고, 실제 측정(scripts/factor_signal_probe.py)에서
유니버스는 768~837종목(비어 있지 않음)이고 신호 0은 **리밸런싱 게이트 밖**일 때의 정상 동작이었다.

따라서 파서·판정기는 ①게이트 정보를 반드시 읽고 ②게이트 밖 0을 실패로 오독하지 않아야 한다.

실행: python3 scripts/_factor_signals_parse_test.py
"""
import json
import os
import sys
import tempfile
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import model_engineer_cycle as m  # noqa: E402

FAIL = []


def check(name, cond):
    print(f"  [{'PASS' if cond else 'FAIL'}] {name}")
    if not cond:
        FAIL.append(name)


def write_tmp(payload):
    fd, path = tempfile.mkstemp(suffix=".json")
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False)
    return path


def probe(asof, universe_size, per):
    return {"generated_at": "2026-11-25T20:00:00+09:00", "asof": asof,
            "n_stocks": 4355, "universe_size": universe_size, "strategies": per}


def strat(gate, buys, sells=0):
    return {"is_rebalance_day": gate, "rebalance_interval_days": 63,
            "n_signals": buys + sells, "n_buys": buys, "n_sells": sells,
            "top_buys": [], "error": None}


print("[1] 요약 파일 없음 → 판정불가")
parsed = m.parse_factor_signals("/nonexistent/x.json", 0.0)
v, d, _ = m.judge_by_metric({"metric": "factor_signals"}, parsed)
check("error 로 기록", bool(parsed.get("error")))
check("판정불가", v == "판정불가")

print("[2] 낡은 요약(mtime <= 실행 시작) → 판정불가")
p = write_tmp(probe("2026-11-25", 768, {"value_factor": strat(False, 0)}))
future = time.time() + 60
parsed = m.parse_factor_signals(p, future)
check("미갱신 감지", "미갱신" in str(parsed.get("error")))
os.unlink(p)

print("[3] 게이트 밖(전 전략 비리밸런싱일) 신호 0 → '게이트 밖 — 정상'")
p = write_tmp(probe("2026-11-25", 768, {n: strat(False, 0) for n in
                                        ("value_factor", "quality_factor", "momentum_factor",
                                         "lowvol_factor", "multifactor")}))
parsed = m.parse_factor_signals(p, 0.0)
v, d, _ = m.judge_by_metric({"metric": "factor_signals"}, parsed)
check("게이트 열린 전략 0", parsed.get("n_gate_open") == 0)
check("실패로 오독하지 않음", v.startswith("게이트 밖"))
os.unlink(p)

print("[4] 게이트일 5전략 신호 20~30 → '신호 정상'")
p = write_tmp(probe("2026-11-01", 837, {"value_factor": strat(True, 30),
                                        "quality_factor": strat(True, 30),
                                        "momentum_factor": strat(True, 30),
                                        "lowvol_factor": strat(True, 30),
                                        "multifactor": strat(True, 20)}))
parsed = m.parse_factor_signals(p, 0.0)
v, d, _ = m.judge_by_metric({"metric": "factor_signals"}, parsed)
check("게이트 열린 전략 5", parsed.get("n_gate_open") == 5)
check("신호>0 전략 5", parsed.get("n_signal_positive") == 5)
check("신호 정상", v.startswith("신호 정상"))
os.unlink(p)

print("[5] 게이트일인데 신호 0 → '신호 부족'(실패)")
p = write_tmp(probe("2026-11-01", 837, {"value_factor": strat(True, 0),
                                        "quality_factor": strat(True, 0)}))
parsed = m.parse_factor_signals(p, 0.0)
v, d, _ = m.judge_by_metric({"metric": "factor_signals"}, parsed)
check("게이트일 신호 부족", v.startswith("게이트일인데 신호 부족"))
os.unlink(p)

print("[6] 유니버스 0 → '유니버스 공백 — 진짜 실패'")
p = write_tmp(probe("2026-11-25", 0, {"value_factor": strat(True, 0)}))
parsed = m.parse_factor_signals(p, 0.0)
v, d, _ = m.judge_by_metric({"metric": "factor_signals"}, parsed)
check("유니버스 공백", v.startswith("유니버스 공백"))
os.unlink(p)

print("[7] 요약 경로 배선 — --out 을 컨테이너 경로에서 호스트 경로로 옮긴다")
sp = m.summary_path("factor_signals",
                    command="docker exec x python /opt/scripts/factor_signal_probe.py "
                            "--out /app/data/reports/fs.json")
check("호스트 경로 변환", bool(sp) and sp.startswith("/") and sp.endswith("fs.json"))
sp2 = m.summary_path("factor_signals", command="docker exec x python x.py")
check("기본 경로 = reports/factor_signal_probe.json(컨테이너 /app/reports 바인드)",
      bool(sp2) and sp2.endswith("reports/factor_signal_probe.json"))

print("[8] 리밸런싱 간격 불일치(전략 vs 백테스트) → 판정 detail 에 경고가 붙는다")
payload = probe("2026-11-25", 768, {"momentum_factor": strat(False, 0)})
payload["interval_audit"] = {"momentum_factor": {"strategy": 63, "backtest": 21, "match": False}}
payload["n_interval_mismatch"] = 1
p = write_tmp(payload)
parsed = m.parse_factor_signals(p, 0.0)
v, d, _ = m.judge_by_metric({"metric": "factor_signals"}, parsed)
check("불일치 경고 포함", "리밸런싱 간격 불일치" in d and "63≠21" in d)
os.unlink(p)

print()
if FAIL:
    print(f"FAIL {len(FAIL)}건: {FAIL}")
    sys.exit(1)
print("전부 PASS")

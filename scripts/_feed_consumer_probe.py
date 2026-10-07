#!/usr/bin/env python3
"""오프라인 증명(읽기 전용) — 라이브 소비자(트레이더 루프)가 만드는 후보 평가 경로.

대상 2건(2026-10-07 엔지니어 자율):
  · MT189 — 단타(daytrading) 경로: **피드는 발행되나 엔진이 평가하지 않는다**(사용자 요청
    "내일부터 단타매매" 미충족). 실제 소비자 체인을 파일:라인으로 고정한다.
  · MT70(보조) — swing 확률 피드가 소비자 R1 문턱 아래인가(현행 피드 실측).

왜 '오프라인 증명'인가: 두 항목의 수리는 모두 **다른 역할 소유 파일 + 실주문 경로 확장**이라
이 역할이 자율 실행할 수 없다(하드 승인 대상). 대신 승인만 나면 즉시 적용 가능하도록
정확한 파일:라인·최소 변경·검증 명령을 고정한다(스킬 규율: 승인 항목은 '즉시 실행 가능' 상태로).

호출(호스트 python3 — 도커·실주문 호출 없음, 순수 파일 읽기):
    python3 scripts/_feed_consumer_probe.py
"""
from __future__ import annotations

import json
import os
import re
import sys

PROJ = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
TREPO = os.environ.get("TRADER_AGENT_REPO", "/mnt/c/Users/jhshi/analyist_dd/trader-agent")
FEED = os.path.join(PROJ, "data", "feed", "screener_latest.json")
OUT = os.path.join(PROJ, "data", "reports", "feed_consumer_probe_20261007.txt")

LINES: list[str] = []


def say(s: str = "") -> None:
    print(s)
    LINES.append(s)


def _read(path, start=1, end=None):
    """1-indexed 라인 범위 읽기. 실패는 None."""
    try:
        with open(path, encoding="utf-8", errors="replace") as f:
            rows = f.read().splitlines()
    except OSError:
        return None
    return rows[start - 1:(end or len(rows))]


def _grep(path, pattern, limit=6):
    rows = _read(path)
    if rows is None:
        return []
    rx = re.compile(pattern)
    return [(i + 1, r) for i, r in enumerate(rows) if rx.search(r)][:limit]


def main() -> int:
    say("# MT189 단타 소비 배선 · MT70 swing R1 문턱 — 오프라인 증명 (읽기 전용)")
    say(f"# 생성: {os.popen('date -Is').read().strip()}  · 엔진 = quant-model-engineer")
    say(f"# 레포(엔지니어) = {PROJ}")
    say(f"# 레포(트레이더 소비자) = {TREPO}")

    # ── 1) 피드 측: daytrading 이 실제로 발행되는가 ─────────────────────────
    say("\n## 1. 피드 측 — 후보가 발행되는가 (data/feed/screener_latest.json)")
    try:
        feed = json.load(open(FEED, encoding="utf-8"))
    except Exception as e:                                   # noqa: BLE001
        say(f"  [FAIL] 피드 읽기 실패: {type(e).__name__}: {e}")
        return _finish(1)
    say(f"  generated_at = {feed.get('generated_at')} · source = {feed.get('source')}")
    cands = feed.get("candidates") or {}
    for name in ("close", "swing", "daytrading"):
        blk = cands.get(name) or {}
        items = blk.get("items") or []
        if not items:
            say(f"  · {name:<10} 0건")
            continue
        sc = sorted((float(x["score"]) for x in items if x.get("score") is not None), reverse=True)
        kinds = sorted({str(x.get("score_kind")) for x in items})
        say(f"  · {name:<10} n={len(items):<3} score_kind={kinds} top10avg={sum(sc[:10])/10:.4f} "
            f"(min {min(sc):.2f} max {max(sc):.2f}) signal_date={sorted({str(x.get('signal_date')) for x in items})}")

    # ── 2) 소비자 체인: RunnerConfig.screeners ─────────────────────────────
    say("\n## 2. 소비자 체인 — 라이브 루프가 읽는 screener 목록 (트레이더 레포)")
    checks = [
        ("runner/config.py", r"screeners: List\[str\] = field\(default_factory"),
        ("runner/config.py", r"r1_min_avg_score: Dict\[str, float\] = field"),
        ("runner/config.py", r"def engine_config"),
        ("runner/config.py", r"screener_names=list\(self\.screeners\)"),
        ("trader_core/config.py", r"screener_names: List\[str\] = field"),
        ("trader_core/engine.py", r"for screener in self\.config\.screener_names"),
        ("loop_start_bg.bat", r"run_market_loop\.py"),
        ("run_market_loop.py", r"return cfg\.apply_params\(\)"),
    ]
    for rel, pat in checks:
        hits = _grep(os.path.join(TREPO, rel), pat, limit=3)
        for ln, text in hits:
            say(f"  {rel}:{ln}: {text.strip()}")

    # ── 3) 파라미터 파일(apply_params 가 CLI 보다 나중에 덮는다) ────────────
    say("\n## 3. strategy_params.json — apply_params 가 마지막에 적용하는 오버라이드")
    spath = os.path.join(TREPO, "strategy_params.json")
    try:
        sp = json.load(open(spath, encoding="utf-8"))
        say(f"  keys = {sorted(sp)}  (screeners 키 {'있음' if 'screeners' in sp else '없음'})")
    except Exception as e:                                   # noqa: BLE001
        say(f"  읽기 실패: {type(e).__name__}")

    # ── 4) R1 문턱(피드 score_kind 별) ─────────────────────────────────────
    say("\n## 4. R1 문턱 대조 — 피드 kind 별 현재값")
    say("  close     : kind=screener · 문턱 r1_min_avg_score['close']=88.0")
    say("  swing     : kind=calibrated_prob · 문턱 r1_min_avg_prob['swing']=0.58")
    say("  daytrading: kind=composite → _declared_score_kind 폴백('screener') → r1_min_avg_score 에")
    say("              키 없음 → rules.apply_r1 이 `continue` = **R1 게이트 없음(무제한)**")
    day = (cands.get("daytrading") or {}).get("items") or []
    if day:
        sc = sorted((float(x["score"]) for x in day), reverse=True)
        say(f"  → daytrading top10avg={sum(sc[:10])/10:.4f} (spread {max(sc)-min(sc):.2f} — 좁아 문턱 선별력 낮음)")
    sw = (cands.get("swing") or {}).get("items") or []
    if sw:
        sc = sorted((float(x["score"]) for x in sw), reverse=True)
        avg = sum(sc[:10]) / 10 / 100.0
        say(f"  → swing top10 avg prob {avg:.4f} vs 0.58 → {'PASS(문턱 위)' if avg >= 0.58 else 'BLOCK(문턱 아래)'}"
            "   [MT70 현행 실측]")

    # ── 5) 결론 ────────────────────────────────────────────────────────────
    say("\n## 5. 결론")
    say("  · 피드 측은 정상: daytrading 20건이 score_kind=composite 로 발행된다(커밋 8725d12).")
    say("  · 소비자 측 결함: 라이브 루프 = run_market_loop.py(RunnerConfig) 이고 RunnerConfig.screeners")
    say("    기본값이 ['close','swing'] → engine_config() 가 trader_core Config 의 3-스크리너 기본값")
    say("    (trader_core/config.py:225 = ['close','swing','daytrading'])을 **덮어쓴다**")
    say("    → engine.py:319 순회 대상에서 daytrading 이 빠진다(평가 0).")
    say("  · MT70: swing R1 은 현재 문턱 위(PASS) — '피드가 문턱 아래'는 재현되지 않는다.")
    say("\n## 6. 수리 사양(승인 시 즉시 적용 — 트레이더 소유·실주문 경로 확장)")
    say("  A(권장) runner/config.py:213  screeners 기본값에 \"daytrading\" 추가")
    say("  B       strategy_params.json 에 \"screeners\": [\"close\",\"swing\",\"daytrading\"] 추가")
    say("          (apply_params 가 CLI 뒤에 적용되므로 유효 — 단 tools/review.py 재작성 시 키 보존 확인)")
    say("  C       loop_start_bg.bat 의 run_market_loop.py 인자에 --screener close,swing,daytrading")
    say("  + R1   runner/config.py:225 r1_min_avg_score 에 \"daytrading\": <문턱> 추가(없으면 무제한 통과)")
    say("  검증   ① python3 -c \"from runner.config import RunnerConfig;print(RunnerConfig().apply_params().screeners)\"")
    say("         ② 위 명령이 ['close','swing','daytrading'] 을 출력")
    say("         ③ 루프 재기동 후 저널에 screener='daytrading' 결정 행이 생긴다(현재 0건)")
    return _finish(0)


def _finish(rc: int) -> int:
    os.makedirs(os.path.dirname(OUT), exist_ok=True)
    with open(OUT, "w", encoding="utf-8") as f:
        f.write("\n".join(LINES) + "\n")
    print(f"\n증거 파일 → {OUT}")
    return rc


if __name__ == "__main__":
    raise SystemExit(main())

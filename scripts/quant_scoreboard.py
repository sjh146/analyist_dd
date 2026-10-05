#!/usr/bin/env python3
"""
퀀트 3역할 **목표 사슬 스코어보드** (north-star scoreboard).

WHY: 2026-09-25 사용자 지시 — "리서처=양질 데이터가 잘 정제되어 모델에 학습되게,
엔지니어=AUC 향상, 트레이더=돈을 더 벌기. 이 최종 목표를 위해 궁극적으로 일하게 하라."
그런데 그때까지 각 역할의 보고는 **"사이클을 돌았다"** 뿐이었고, 목표 지표가 없었다.
여기서 세 역할의 북극성을 **산출물에서 직접** 읽어 한 화면에 묶는다.

  최종 목표: 순손익(₩)
     └─ 트레이더  : 실현 순손익(₩) · 건당 기대값(₩) · 승률 · 보유/회전
     └─ 엔지니어  : 로버스트 AUC(확장창 워크포워드 다중폴드 평균) ↑
     └─ 리서처    : 모델에 들어가는 데이터의 품질·커버리지 (DQ 상태, 살아있는 피처)

원칙(문서 docs/QUANT_AGENT_OBJECTIVES.md 참조):
  1) 판정은 **산출물**에서 직접 읽는다(자기신고 금지). 출처를 화면에 밝힌다.
  2) 돈은 **%가 아니라 원**으로 본다 — 실측: 평균수익률 +0.32% 인데 총액 -7,839원(포지션 크기 차이).
  3) 무개선이 N사이클 지속되면 **사람 개입 필요**로 올린다(조용한 공전 금지).

사용:
  python3 scripts/quant_scoreboard.py                 # 사슬 전체 출력
  python3 scripts/quant_scoreboard.py --stanza trader # 한 역할만(구동기가 틱마다 사용)
  python3 scripts/quant_scoreboard.py --json          # 기계 판독용
"""
from __future__ import annotations

import argparse
import glob
import json
import os
import shutil
import sqlite3
import subprocess
import tempfile
from datetime import datetime, timedelta, timezone

PROJ = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
KST = timezone(timedelta(hours=9))

JOURNAL = "/mnt/c/Users/jhshi/analyist_dd/trader-agent/journal/trade_journal.sqlite3"
ME_LEDGER = os.path.join(PROJ, "data/reports/model_engineer_ledger.jsonl")
RES_LEDGER = os.path.join(PROJ, "data/reports/researcher_ledger.jsonl")
DQ_GLOB = os.path.join(PROJ, "data/reports/dq_snapshots/dq_*.json")
OUT_JSON = os.path.join(PROJ, "data/reports/quant_scoreboard.json")
OUT_MD = os.path.join(PROJ, "docs/QUANT_SCOREBOARD.md")

# 챔피언(현행) 단일분할 AUC 와 **최고 로버스트 기준선**.
# 기준선은 라벨·유니버스 실험에서 실측된 최선값(LS_quant_q30_h5, h5·분위0.3·top30).
# 신호 판정은 폴드 표준편차(±0.03)를 감안해 **+0.02 이상**만 인정한다.
BASELINE_ROBUST = 0.5406
BASELINE_NAME = "LS_quant_q30_h5 (h5·분위0.3·top30)"
SIGNAL_DELTA = 0.02
NO_IMPROVE_CYCLES = 3      # 사이클 3회 연속 무개선 → 사람 개입 요청
NO_TRADE_DAYS = 3          # 3거래일 신규 진입 없음 → 회전 정지 경고

# ── CG104 (리뷰보드 승인 2026-10-05 22:4x): 대조 가능성(comparability) 필터 ────
# 종전 best_robust 는 **전 이력 모든 arm 의 최고 mean**(max-over-arms)이라, 라벨 kind·q·게이트가
# 기준선과 다른 arm 이 섞여 거짓 돌파를 만들었다. 실측(2026-10-05): 최고 arm = CG89 의
# Q5s_120_150 0.5870(q0.05 · 서로소 슬라이스 [120:150)) vs 기준선 0.5406(q0.30·게이트 OFF) →
# "Δ+0.0464 개선"으로 표기됐지만 q0.05 의 행 집합은 q0.30 의 **부분집합**이라 과제 정의가 다르고
# (스킬 하드규칙 ①), 유니버스 교체만으로 폴드 평균이 Δ0.0287 움직인다(하드규칙: 사전문턱 +0.02 는
# 잡음 바닥 아래) → 비교 자체가 성립하지 않는다. 그래서 기준선 태그와 라벨 kind·q·게이트(core_only)·
# 호라이즌이 일치하는 arm 만 best_robust·무개선 카운터에 계상한다. 태그를 해석할 수 없는 arm
# (옛 기록·미등록 config)은 종전대로 포함하되 '미분류'로 각주에 남긴다 — 소급 오탐을 완전히 막지는
# 못한다는 사실을 숨기지 않기 위해서다. 기준선 값(BASELINE_ROBUST)은 재계산 전후 보존된다.
BASELINE_ARM = "LS_quant_q30_h5"
BASELINE_TAGS = {"kind": "quantile", "q": 0.30, "core_only": False, "horizon": 5}
ARM_CONFIGS_PATH = os.path.join(PROJ, "scripts/wf_label_sweep.py")
_ARM_TAGS_CACHE: dict | None = None


# ── 공통 ────────────────────────────────────────────────────────────────────
def _docker_cat(path: str) -> str | None:
    for cname in ("stock_xgboost_ml",):
        try:
            r = subprocess.run(["docker", "exec", cname, "cat", path],
                               capture_output=True, text=True, timeout=25)
            if r.returncode == 0 and r.stdout.strip():
                return r.stdout.strip()
        except (OSError, subprocess.SubprocessError):
            pass
    return None


def _jsonl(path: str) -> list[dict]:
    out = []
    try:
        with open(path, encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if line:
                    try:
                        out.append(json.loads(line))
                    except json.JSONDecodeError:
                        pass
    except OSError:
        pass
    return out


def _num(v, spec: str = "+,") -> str:
    """None 안전 숫자 포맷. 값이 없으면 'n/a' — None 에 포맷을 걸면 TypeError 로 죽는다
    (실측 2026-09-28: 저널을 못 읽어 realized_krw=None 인데 :+, 를 걸어 크래시)."""
    return format(v, spec) if isinstance(v, (int, float)) else "n/a"


# ── 트레이더: 돈 ────────────────────────────────────────────────────────────
def _copy_journal_local() -> tuple[str, str]:
    """LIVE 저널을 로컬(/tmp)로 **복사**해서 읽는다. 원본은 절대 건드리지 않는다.

    WHY: 저널은 Windows drvfs(/mnt/c) 위에 있고 트레이더가 WAL 모드로 계속 쓴다.
    drvfs 는 파일 락·mmap 을 제대로 지원하지 않아 sqlite3 가 직접 읽으면
    'disk I/O error' 를 내고, 본체에 아직 체크포인트되지 않은(-wal 에만 있는) 행은
    아예 안 보인다. 실측 2026-09-28: 직접 열면 error='저널 읽기 실패: disk I/O error'.
    본체 + -wal + -shm 세 개를 함께 복사해 로컬에서 read-only 로 열면 정상(41행/청산 32건).

    반환: (tmpdir, 복사본 경로) — 호출자가 **반드시** finally 에서 tmpdir 를 지운다.
    """
    tmpdir = tempfile.mkdtemp(prefix="qsb_journal_")
    dst = os.path.join(tmpdir, os.path.basename(JOURNAL))
    for suf in ("", "-wal", "-shm"):
        src = JOURNAL + suf
        if os.path.exists(src):
            shutil.copy2(src, dst + suf)   # -wal/-shm 이 있어야 최신 미체크포인트 행이 보인다
    return tmpdir, dst


def trader_stanza() -> dict:
    """실현 순손익을 **원**으로. 청산된 거래만 돈이 확정된다."""
    st = {"role": "trader", "north": "실현 순손익(₩)", "source": JOURNAL,
          "realized_krw": None, "trades": 0, "wins": 0, "win_rate": None,
          "expectancy_krw": None, "avg_return_pct": None, "open_positions": 0,
          "days_since_entry": None, "alerts": [], "error": None}
    if not os.path.exists(JOURNAL):
        st["error"] = "저널 없음"
        return st
    # 저널이 안 읽혀도 **크래시하지 않고** None/0 으로 퇴화한다(판정 불가를 숨기지 않는다).
    # 복사본이 읽는 도중 어긋날 수 있어(쓰는 쪽과 동시성) 2회 시도한다.
    closed, opens, last_ts = None, None, None
    last_exc: Exception | None = None
    for _ in range(2):
        tmpdir = None
        try:
            tmpdir, local = _copy_journal_local()
            conn = sqlite3.connect(f"file:{local}?mode=ro", uri=True, timeout=10)
            try:
                conn.row_factory = sqlite3.Row
                closed = [dict(r) for r in conn.execute(
                    "SELECT * FROM trades WHERE exit_price IS NOT NULL AND exit_price > 0")]
                opens = [dict(r) for r in conn.execute(
                    "SELECT * FROM trades WHERE exit_price IS NULL OR exit_price = 0")]
                last_ts = conn.execute("SELECT MAX(ts) FROM trades").fetchone()[0]
            finally:
                conn.close()
            last_exc = None
            break
        except (sqlite3.Error, OSError) as exc:
            last_exc = exc
        finally:
            if tmpdir:
                shutil.rmtree(tmpdir, ignore_errors=True)
    if last_exc is not None or closed is None or opens is None:
        st["error"] = f"저널 읽기 실패 (drvfs+WAL — journal unreadable): {last_exc}"
        return st

    total, wins, rets = 0.0, 0, []
    for t in closed:
        entry, exit_ = float(t["price"] or 0), float(t["exit_price"] or 0)
        qty = abs(float(t["qty"] or 0))
        sign = 1.0 if (t.get("side") or "buy") == "buy" else -1.0
        pnl = (exit_ - entry) * qty * sign
        total += pnl
        if pnl > 0:
            wins += 1
        if entry:
            rets.append((exit_ - entry) / entry * 100 * sign)
    st.update({
        "realized_krw": round(total),
        "trades": len(closed),
        "wins": wins,
        "win_rate": round(wins / len(closed) * 100, 1) if closed else None,
        "expectancy_krw": round(total / len(closed)) if closed else None,
        "avg_return_pct": round(sum(rets) / len(rets), 2) if rets else None,
        "open_positions": len(opens),
    })
    if last_ts:
        try:
            t = datetime.fromisoformat(str(last_ts).replace("Z", "+00:00"))
            if t.tzinfo is None:
                t = t.replace(tzinfo=KST)
            st["days_since_entry"] = round((datetime.now(KST) - t).total_seconds() / 86400, 1)
        except ValueError:
            pass
    # ⚠ 돈 기준 판정: 수익률 평균이 +여도 총액이 -면 진다(포지션 크기 비대칭).
    if st["realized_krw"] is not None and st["realized_krw"] < 0:
        st["alerts"].append(f"순손익 마이너스 {st['realized_krw']:,}원 — 기대값 "
                            f"{st['expectancy_krw']:,}원/건")
    if st["days_since_entry"] is not None and st["days_since_entry"] >= NO_TRADE_DAYS:
        st["alerts"].append(f"신규 진입 {st['days_since_entry']}일 없음 "
                            f"(보유 {st['open_positions']}종목) — 돈이 도는 회전이 멈춤")
    return st


# ── 엔지니어: 로버스트 AUC ──────────────────────────────────────────────────
def engineer_stanza() -> dict:
    """로버스트(다중폴드 평균) AUC. 단일분할은 참고로만 병기한다."""
    st = {"role": "engineer", "north": "로버스트 AUC (다중폴드 평균)",
          "champion_single": None, "best_robust": None, "best_robust_std": None,
          "best_exp": None, "best_rec_id": None, "best_rec_ts": None,
          "best_rec_verdict": None, "best_validated": None,
          "baseline": BASELINE_ROBUST, "baseline_name": BASELINE_NAME,
          "delta": None, "last_verdict": None, "no_improve_cycles": 0,
          "no_improve_streak": 0, "last_improve": None,
          "best_excluded": None, "comparability": None,
          "source": f"{ME_LEDGER} + /app/app/models/champion/auc.txt", "alerts": []}

    auc = _docker_cat("/app/app/models/champion/auc.txt")
    if auc:
        try:
            st["champion_single"] = round(float(auc.split()[0]), 6)
        except (ValueError, IndexError):
            pass

    # ⚠ 'best' 는 **전 이력의 모든 arm 중 최댓값**이다(max-over-arms) → 선택편향이 있고,
    # 어떤 arm 이 노이즈로 판정된 실행에서 나왔는지도 함께 봐야 한다. 실측(2026-09-29 22:40):
    # CG24(03:27, 판정 '노이즈' · 구간 짝 Δ+0.0176 3/5)의 arm Q5s_30_60 0.5720 이 최댓값이라
    # 스코어보드가 Δ+0.0321 [신호] 로 표기했지만 **엔지니어의 판정은 노이즈**였다. 그대로 두면
    # '검증된 신호'로 오독된다 → 출처(원장 id·ts·판정)를 함께 들고 다니고, 판정이 개선이 아닌
    # 실행에서 온 최댓값은 [미검증 최고 arm] 으로 표기한다(0/신호로 위장하지 않는다).
    best, best_std, best_exp = None, None, None
    best_rec = None
    best_x = None          # 최고 '대조 불가' arm — best_robust 에서 제외하되 각주로 남긴다(CG104)
    n_cmp, n_incmp, n_unclass = 0, 0, 0
    cmp_panels: set = set()
    best_panel = None
    for rec in _jsonl(ME_LEDGER):
        _panel = str(((rec.get("parsed") or {}).get("config") or {}).get("panel") or "?").rsplit("/", 1)[-1]
        for arm, val, ok, why in _rec_arm_entries(rec):
            mean = float(val["mean"])
            if ok is True:
                n_cmp += 1
                cmp_panels.add(_panel)
            elif ok is None:
                n_unclass += 1
            else:
                n_incmp += 1
                if best_x is None or mean > best_x["mean"]:
                    best_x = {"mean": round(mean, 4), "arm": arm, "id": rec.get("id"),
                              "ts": rec.get("ts"), "reason": why}
                continue
            if best is None or mean > best:
                best, best_std, best_exp = mean, val.get("std"), arm
                best_rec = rec
                best_panel = _panel
    st.update({"comparability": {
        "filter": "kind·q·core_only·horizon·유니버스슬라이스 (기준선 태그와 일치하는 arm 만 계상)",
        "baseline_arm": BASELINE_ARM, "baseline_tags": dict(BASELINE_TAGS),
        "arms_comparable": n_cmp, "arms_incomparable": n_incmp, "arms_unclassified": n_unclass,
        "panels_comparable": sorted(cmp_panels), "best_panel": best_panel,
        "best_excluded": best_x}})
    if best_x is not None:
        st["best_excluded"] = best_x
    if best_panel:
        st["comparability"]["note"] = ("패널(창)은 기준선 패널이 원장에 기록돼 있지 않아 필터하지 않고 "
                                       "각주로만 남긴다 — 창은 실측상 레버가 아니다(2026-09-30 U3b/d).")
    if best is not None:
        st.update({"best_robust": round(best, 4),
                   "best_robust_std": round(float(best_std), 4) if isinstance(best_std, (int, float)) else None,
                   "best_exp": best_exp,
                   "delta": round(best - BASELINE_ROBUST, 4)})
        if best_rec is not None:
            verdict = str(best_rec.get("verdict") or "")
            st.update({"best_rec_id": best_rec.get("id"),
                       "best_rec_ts": best_rec.get("ts"),
                       "best_rec_verdict": verdict,
                       # 판정 문구가 개선('신호있음'·'신호 확인'·'신호')일 때만 검증된 신호로 본다.
                       "best_validated": "신호" in verdict})
    recs = _jsonl(ME_LEDGER)
    if recs:
        st["last_verdict"] = recs[-1].get("verdict")
    # 무개선 카운터는 **측정이 성립한 사이클**만 센다. rc!=0(소실·타임아웃)·요약 미갱신은
    # '측정'이 아니라서, 소실을 노이즈로 세면 '무개선 3사이클 → 새 레버 필요' 경보가 헛돈다
    # (실측 2026-09-25: U1 이 평일 20:00 컨테이너 재생성으로 소실됐는데 옛 L2 요약을 읽어
    #  Δ+0.0000 '노이즈'로 기록 → 무효 사이클이 카운터에 포함됐다).
    measured = [r for r in recs
                if r.get("rc") == 0
                and _rec_best_mean(r) is not None
                and not (isinstance(r.get("parsed"), dict) and r["parsed"].get("error"))]
    improved = [r for r in measured
                if (_rec_best_mean(r) or 0.0) - BASELINE_ROBUST >= SIGNAL_DELTA]
    st["no_improve_cycles"] = max(0, len(measured) - len(improved))
    st["measured_cycles"] = len(measured)
    st["invalid_cycles"] = len(recs) - len(measured)
    # **'연속'과 '누적'을 구분하라**(실측 2026-09-28): 종전 구현은 '측정 − 개선' 누적 개수를
    # 그대로 '연속 N사이클'로 보고했다. 그래서 그날 CG20 이 Δ+0.0236 으로 사전 문턱(+0.02)을
    # 넘겨 새 레버가 실제로 나왔는데도 "33사이클 연속 미달 — 새 레버 필요(사람 승인)" 경보가 남아
    # **없는 사람 단계**를 만들었다. 경보는 꼬리 연속(tail)으로만 판정하고 누적은 정보로 남긴다.
    tail = 0
    for r in reversed(measured):
        if (_rec_best_mean(r) or 0.0) - BASELINE_ROBUST >= SIGNAL_DELTA:
            break
        tail += 1
    st["no_improve_streak"] = tail
    st["last_improve"] = ({"ts": improved[-1].get("ts"), "id": improved[-1].get("id")}
                          if improved else None)
    if tail >= NO_IMPROVE_CYCLES:
        st["alerts"].append(f"로버스트 AUC 가 {tail}사이클 연속 기준선 ({BASELINE_ROBUST}) 대비 "
                            f"+{SIGNAL_DELTA} 미달 — 새 레버 필요 (사람 승인 대상)")
    return st


def _arm_tags_registry() -> dict:
    """wf_label_sweep.CONFIGS 를 **실행하지 않고** AST 로 읽어 {arm_id: tags} 를 만든다.

    왜 AST 인가: 스코어보드는 호스트 python3 로 돌고 이 호스트엔 numpy 가 없어
    `import wf_label_sweep` 이 `ModuleNotFoundError` 로 죽는다(실측 2026-10-05).
    파싱 실패(비리터럴 항목·파일 부재)면 빈 dict → 필터는 '미분류'로 폴백한다.
    """
    global _ARM_TAGS_CACHE
    if _ARM_TAGS_CACHE is not None:
        return _ARM_TAGS_CACHE
    reg: dict = {}
    try:
        import ast
        with open(ARM_CONFIGS_PATH, encoding="utf-8") as f:
            tree = ast.parse(f.read())
        node = None
        for n in tree.body:
            if isinstance(n, ast.Assign) and any(
                    isinstance(t, ast.Name) and t.id == "CONFIGS" for t in n.targets):
                node = n.value
        for c in (ast.literal_eval(node) if node is not None else []):
            if isinstance(c, dict) and c.get("id"):
                sl = c.get("codes_slice")
                reg[c["id"]] = {
                    "kind": c.get("kind"), "q": c.get("q"),
                    "horizon": c.get("horizon"),
                    "core_only": bool(c.get("core_only")),
                    "select": c.get("select"),
                    "codes_slice": tuple(sl) if isinstance(sl, (list, tuple)) else None,
                }
    except Exception:
        reg = {}
    _ARM_TAGS_CACHE = reg
    return reg


def _baseline_universe() -> dict:
    """기준선(BASELINE_ROBUST·BASELINE_ARM)을 만든 기록의 유니버스(패널·days·limit)를 원장에서 찾는다.

    리뷰보드 기준에 '유니버스'가 포함된다 — 실측: 기준선 0.5406 은 L1/L2 의
    panel_420_asofpatch(d420·limit50)에서 나왔는데, 최고 arm U3 은 같은 arm 이름을
    panel_995(d995)에서 재측정한 0.5557 이다(창 교체만으로 Δ0.0151). 창이 레버가 아님은
    실측으로 확인됐지만(2026-09-30 U3b/d), 유니버스가 다르면 같은 과제가 아니므로 계상하지 않는다.
    파생 실패(기록 없음)면 빈 dict → 유니버스 검사는 건너뛰고 각주로만 남긴다(기준선 값은 보존).
    """
    for rec in _jsonl(ME_LEDGER):
        per = ((rec.get("parsed") or {}).get("per_exp") or {})
        v = per.get(BASELINE_ARM)
        if (isinstance(v, dict) and isinstance(v.get("mean"), (int, float))
                and abs(float(v["mean"]) - BASELINE_ROBUST) < 1e-9):
            cfg = (rec.get("parsed") or {}).get("config") or {}
            return {"panel": str(cfg.get("panel") or "?").rsplit("/", 1)[-1],
                    "days": cfg.get("days"), "limit": cfg.get("limit"), "source": rec.get("id")}
    return {}


_UNIV_CACHE: dict | None = None


def _record_universe(cfg: dict) -> dict:
    return {"panel": str(cfg.get("panel") or "?").rsplit("/", 1)[-1],
            "days": cfg.get("days"), "limit": cfg.get("limit")}


def arm_comparability(arm: str, val: dict, cfg: dict | None = None) -> tuple[bool | None, str]:
    """기준선과 대조 가능한 arm 인가 → (True/False/None, 사유).

    None = 태그 미해석(미등록 config) → 종전대로 계상하고 각주에 '미분류'로 남긴다.
    라벨 kind·horizon 은 per_exp 항목(그 런의 실측값)을 우선하고, q·게이트·유니버스는 config 에서 온다.
    cfg(그 기록의 실행 인자)가 주어지면 패널·days·limit 도 기준선과 대조한다.
    """
    global _UNIV_CACHE
    t = _arm_tags_registry().get(arm)
    if t is None:
        return None, "config 미등록(태그 미해석)"
    kind = val.get("kind", t.get("kind"))
    horizon = val.get("horizon", t.get("horizon"))
    bad = []
    if kind != BASELINE_TAGS["kind"]:
        bad.append(f"라벨kind {kind}≠{BASELINE_TAGS['kind']}")
    if t.get("q") != BASELINE_TAGS["q"]:
        bad.append(f"q {t.get('q')}≠{BASELINE_TAGS['q']}")
    if t.get("core_only") != BASELINE_TAGS["core_only"]:
        bad.append(f"게이트 {'ON' if t.get('core_only') else 'OFF'}≠"
                   f"{'ON' if BASELINE_TAGS['core_only'] else 'OFF'}")
    if horizon != BASELINE_TAGS["horizon"]:
        bad.append(f"h{horizon}≠h{BASELINE_TAGS['horizon']}")
    if t.get("codes_slice") is not None:
        bad.append(f"유니버스 슬라이스 {tuple(t['codes_slice'])}")
    if cfg:
        if _UNIV_CACHE is None:
            _UNIV_CACHE = _baseline_universe()
        bu = _UNIV_CACHE
        if bu.get("panel"):
            u = _record_universe(cfg)
            if (u["panel"], u["days"], u["limit"]) != (bu["panel"], bu["days"], bu["limit"]):
                bad.append(f"유니버스 {u['panel']}/d{u['days']}/L{u['limit']}"
                           f"≠{bu['panel']}/d{bu['days']}/L{bu['limit']}")
    return (not bad), ", ".join(bad)


def _rec_arm_entries(rec: dict) -> list[tuple[str, dict, bool | None, str]]:
    parsed = rec.get("parsed")
    per = (parsed.get("per_exp") or {}) if isinstance(parsed, dict) else {}
    cfg = (parsed.get("config") or {}) if isinstance(parsed, dict) else {}
    out = []
    for arm, val in per.items():
        if not isinstance(val, dict) or not isinstance(val.get("mean"), (int, float)):
            continue
        ok, why = arm_comparability(arm, val, cfg)
        out.append((arm, val, ok, why))
    return out


def _rec_best_mean(rec: dict) -> float | None:
    """그 기록의 **대조 가능한** arm 최고 mean. 대조 불가(False)만 제외, 미분류(None)는 포함."""
    vals = [float(v["mean"]) for _a, v, ok, _r in _rec_arm_entries(rec) if ok is not False]
    return max(vals) if vals else None


# ── 리서처: 데이터 품질 ─────────────────────────────────────────────────────
def researcher_stanza() -> dict:
    """모델에 들어가는 데이터의 품질·커버리지. '모았다'가 아니라 '쓸 수 있게 됐다'를 본다."""
    st = {"role": "researcher", "north": "모델 학습 데이터의 품질·커버리지",
          "sample": None, "status": None, "warns": [], "breaches": [],
          "alive_features": None, "alive_xsec_features": None, "dead_features": None,
          "stock_constant_ratio": None, "news_freshness_hours": None,
          "rows_delivered": None, "source": DQ_GLOB, "alerts": []}
    files = sorted(glob.glob(DQ_GLOB))
    if not files:
        st["alerts"].append("DQ 스냅샷 없음 — 리서처 사이클 미실행")
        return st
    st["sample"] = os.path.basename(files[-1])
    with open(files[-1], encoding="utf-8") as f:
        d = json.load(f)
    m = d.get("metrics") or {}
    st["warns"] = d.get("warns") or []
    st["breaches"] = d.get("breaches") or []
    st["status"] = "breach" if st["breaches"] else ("warn" if st["warns"] else "ok")

    def val(key):
        v = (m.get(key) or {}).get("value")
        return v

    st["alive_features"] = val("feature_alive_count")
    # 횡단면 변별력이 있는 살아있는 피처(시장레벨 제외). 계약 #6 위반분이 '진척'으로 보이지 않게
    # 북극성 줄에 함께 표시한다 — 없으면(구버전 스냅샷) 표시를 생략한다.
    st["alive_xsec_features"] = val("dq_feature_alive_xsec_count")
    st["dead_features"] = val("feature_dead_count")
    st["stock_constant_ratio"] = val("dq_feature_stock_constant_ratio")
    st["news_freshness_hours"] = val("news_analysis_freshness_hours")
    st["rows_delivered"] = val("dq_claim_source")
    if st["breaches"]:
        st["alerts"].append("DQ 위반: " + " / ".join(st["breaches"]))
    if st["warns"]:
        st["alerts"].append("DQ 경고: " + " / ".join(st["warns"]))
    dead = st["dead_features"] or 0
    alive = st["alive_features"] or 0
    if alive and dead and dead > alive:
        st["alerts"].append(f"죽은 피처 {dead:.0f}개 > 살아있는 피처 {alive:.0f}개 — "
                            f"모델이 쓸 수 있는 신호가 소수")
    return st


# ── 사슬 요약 ───────────────────────────────────────────────────────────────
def build() -> dict:
    t, e, r = trader_stanza(), engineer_stanza(), researcher_stanza()
    people = []
    for s in (t, e, r):
        people.extend(f"[{s['role']}] {a}" for a in s.get("alerts") or [])
    return {"ts": datetime.now(KST).isoformat(timespec="seconds"),
            "goal": "최종 목표: 순손익(₩) — 리서처 데이터 → 엔지니어 AUC → 트레이더 돈",
            "trader": t, "engineer": e, "researcher": r, "needs_human": people}


def fmt(st: dict, with_source: bool = True) -> str:
    kst = st["ts"][11:16]
    t, e, r = st["trader"], st["engineer"], st["researcher"]
    L = []
    L.append(f"[퀀트 목표 사슬] {kst} — 최종 목표: 돈(순손익)")
    if t.get("error"):
        L.append(f"💰 트레이더   : 측정 불가 ({t['error']}) — 저널 {t['source']}")
    else:
        L.append(f"💰 트레이더   : 순손익 {_num(t.get('realized_krw'))}원 | "
                 f"{_num(t.get('trades'), '.0f')}건 승률 {_num(t.get('win_rate'), '.1f')}% | "
                 f"기대값 {_num(t.get('expectancy_krw'))}원/건 | "
                 f"보유 {_num(t.get('open_positions'), '.0f')}종목 | "
                 f"마지막 진입 {_num(t.get('days_since_entry'), '.1f')}일 전")
    if e.get("best_robust") is None:
        L.append("📈 모델엔지니어: 로버스트 측정값 없음")
    else:
        d = e["delta"] or 0.0
        validated = e.get("best_validated", True)
        if d >= SIGNAL_DELTA and validated:
            mark = "신호"
        elif d >= SIGNAL_DELTA:
            # 최댓값이지만 그 실행의 판정은 개선이 아니다(노이즈/악화) → '신호'로 위장하지 않는다.
            mark = "미검증 최고 arm"
        else:
            mark = "유지" if d >= -0.005 else "악화"
        src = ""
        if e.get("best_rec_id"):
            src = f" · 출처 {e['best_rec_id']}"
            if not validated:
                src += f"({str(e.get('best_rec_ts') or '')[:16]} 판정 {e.get('best_rec_verdict') or '?'})"
        L.append(f"📈 모델엔지니어: 로버스트 {_num(e.get('best_robust'), '.4f')}"
                 f"±{e['best_robust_std'] if e['best_robust_std'] is not None else '?'}"
                 f" ({e['best_exp']}) vs 기준선 {_num(e.get('baseline'), '.4f')} → Δ{d:+.4f} [{mark}]"
                 f" | 챔피언 단일분할 {_num(e.get('champion_single'))}{src}")
    _cx = e.get("best_excluded")
    if _cx:
        L.append(f"   ↳ 대조 불가 제외(CG104): 최고 {_cx['arm']} {_cx['mean']:.4f} ({_cx['id']})"
                 f" — {_cx['reason']} (기준선 {_num(e.get('baseline'), '.4f')} 보존)")
    _ax = r.get("alive_xsec_features")
    _extra = f" (횡단면 {_num(_ax, '.0f')})" if isinstance(_ax, (int, float)) else ""
    L.append(f"🔬 퀀트리서처: DQ {r['status'] or 'n/a'} | 살아있는 피처 "
             f"{_num(r.get('alive_features'), '.0f')}{_extra} / 죽은 {_num(r.get('dead_features'), '.0f')} | "
             f"종목상수 {_num(r.get('stock_constant_ratio'), '.3f')} | 뉴스신선도 "
             f"{_num(r.get('news_freshness_hours'), '.2f')}h")
    if st["needs_human"]:
        L.append("[사람 개입 필요]")
        L.extend("  - " + x for x in st["needs_human"])
    if with_source:
        L.append("연쇄: 리서처(데이터) → 엔지니어(AUC) → 트레이더(₩)")
    return "\n".join(L)


def write_md(st: dict) -> None:
    os.makedirs(os.path.dirname(OUT_MD), exist_ok=True)
    body = ["# 퀀트 3역할 목표 스코어보드", "",
            f"- 갱신: {st['ts']}",
            f"- 목표: {st['goal']}", "",
            "```", fmt(st), "```", ""]
    if st["needs_human"]:
        body += ["## 🙋 사람 개입 필요", ""] + [f"- {x}" for x in st["needs_human"]] + [""]
    body += ["## 출처", "",
             f"- 트레이더: `{st['trader']['source']}` (실현 손익은 청산 거래에서만 계산)",
             f"- 엔지니어: `{st['engineer']['source']}`",
             f"- 리서처: `{st['researcher']['source']}` (최신 {st['researcher'].get('sample')})", ""]
    with open(OUT_MD, "w", encoding="utf-8") as f:
        f.write("\n".join(body))


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--stanza", choices=["trader", "engineer", "researcher", "all"], default="all")
    ap.add_argument("--json", action="store_true")
    a = ap.parse_args()

    if a.stanza == "trader":
        s = trader_stanza()
        if s["realized_krw"] is None:
            # 저널을 못 읽어도 여기서 죽지 않는다 — '판정 불가'를 명시하고 rc=0 으로 돌려준다.
            print(f"[북극성·트레이더] 측정 불가 — {s['error'] or '저널 판독값 없음'} | 저널 {s['source']}")
        else:
            print(f"[북극성·트레이더] 순손익 {s['realized_krw']:+,}원 | 기대값 {_num(s['expectancy_krw'])}원/건 | "
                  f"승률 {_num(s['win_rate'], '.1f')}% | 보유 {s['open_positions']}")
        for x in s["alerts"]:
            print(f"  ⚠ {x}")
        return 0
    if a.stanza == "engineer":
        s = engineer_stanza()
        _li = s.get("last_improve") or {}
        print(f"[북극성·엔지니어] 로버스트 {_num(s.get('best_robust'), '.4f')} vs 기준선 "
              f"{_num(s.get('baseline'), '.4f')} (Δ{s.get('delta')}) | 무개선 연속 "
              f"{s.get('no_improve_streak')}사이클 (누적 미달 {s['no_improve_cycles']}/{s.get('measured_cycles')}"
              f"·무효 {s.get('invalid_cycles')})"
              + (f" | 직전 개선 {_li.get('id')} @{_li.get('ts')}" if _li else ""))
        _cx = s.get("best_excluded")
        if _cx:
            _c = s["comparability"]
            print(f"  ↳ 대조 불가 제외(CG104): 최고 {_cx['arm']} {_cx['mean']:.4f} ({_cx['id']}) — "
                  f"{_cx['reason']} · 대조가능 arm {_c['arms_comparable']}"
                  f"/불가 {_c['arms_incomparable']}/미분류 {_c['arms_unclassified']}"
                  f" · 대조가능 패널 {','.join(_c.get('panels_comparable') or []) or '?'}")
        if s.get("best_robust") is None and (s.get("comparability") or {}).get("arms_incomparable"):
            print(f"  ↳ 대조 가능한 arm 없음 → best_robust 미산출 (기준선 값 보존: {s.get('baseline')})")
        for x in s["alerts"]:
            print(f"  ⚠ {x}")
        return 0
    if a.stanza == "researcher":
        s = researcher_stanza()
        _ax = s.get("alive_xsec_features")
        _extra = f" (횡단면 {_num(_ax, '.0f')})" if isinstance(_ax, (int, float)) else ""
        print(f"[북극성·리서처] DQ {s['status']} | 살아 {_num(s.get('alive_features'), '.0f')}{_extra}"
              f"/죽은 {_num(s.get('dead_features'), '.0f')} "
              f"| 뉴스 {_num(s.get('news_freshness_hours'), '.2f')}h")
        for x in s["alerts"]:
            print(f"  ⚠ {x}")
        return 0

    st = build()
    if a.json:
        print(json.dumps(st, ensure_ascii=False, indent=2))
    else:
        print(fmt(st))
    os.makedirs(os.path.dirname(OUT_JSON), exist_ok=True)
    tmp = OUT_JSON + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(st, f, ensure_ascii=False, indent=2)
    os.replace(tmp, OUT_JSON)
    write_md(st)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

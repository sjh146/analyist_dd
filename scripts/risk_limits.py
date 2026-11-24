#!/usr/bin/env python3
"""한도 단일 진실원 — 계기판 한도 축을 **실행 설정**(trader_core/config.py 의 Config)에서 읽는다.

WHY (2026-11-17, data/state/ceo_priority.json day=2026-11-16 escalations 에스컬레이션 인용):
  "[사람 승인·계기판 한도 값] money_scoreboard 는 종목 10%·총 30% 기준으로
  '한도 초과 +74,836원'을 출력하지만 실행 설정은 1/3·100%(11-09 지시)다. … 어느 쪽이
  정본인지 확정 전까지 노출 판정이 양방향으로 틀린다".
  실제로 주문을 내는 주체는 TraderEngine 이고 그 값은 trader_core/config.py 의 Config() 다.
  계기판이 그 값을 **직접 읽어** 표시하면 두 축이 어긋날 수 없고, 정본을 바꿀 때도 Config 한
  곳만 고치면 된다. 어긋남이 있으면 숨기지 말고 출처 메타(meta)를 출력에 실어 보인다.

실측 확정값 (2026-11-17 실행 — 재조사 금지):
  cd /mnt/c/Users/jhshi/analyist_dd/trader-agent && /usr/bin/python3 -c "import sys;sys.path.insert(0,'.');from trader_core.config import Config;c=Config();print(c.daily_max_trades,c.max_position_pct,c.max_open_positions,c.max_invested_pct)"
  → 3 0.3333333333333333 3 1.0
  (trader-agent 는 별도 저장소(github.com/sjh146/trader-agent) — 게시본이 단일 진실원)

계약:
  - candidates 기본 순서: TRADER_AGENT_DIR 환경변수 → Windows 라이브 사본 → 리포 내 사본.
    존재하는 **첫 사본**을 쓴다(라이브 사본이 실행 주체이므로 먼저). 없는 경로는 건너뛴다.
  - 각 후보는 sys.path.insert(0, 사본) 후 trader_core.config.Config() 를 읽고, import 한
    모듈·sys.modules 는 검사 후 **원상복구**한다(다른 후보·이후 import 오염 금지).
  - 어떤 후보도 읽지 못하면 LIMITS=FALLBACK, meta["source"]="fallback", loaded=False —
    **예외를 던지지 않는다**(계기판은 계속 돌아야 한다).
  - 매핑: max_daily_trades←daily_max_trades, per_stock_pct←max_position_pct,
    max_open_positions←max_open_positions, total_exposure_pct←max_invested_pct.
"""
from __future__ import annotations

import importlib
import json
import logging
import os
import subprocess
import sys

_WPY = os.environ.get("TRADER_WPY", r"C:\Users\jhshi\Python312-64\python.exe")
_LOG = logging.getLogger("risk_limits")


def _to_wsl_path(p):
    """C:\\Users\\... → /mnt/c/Users/... (WSL 쪽 존재 확인용). 변환 불가면 None."""
    if len(p) > 2 and p[1] == ":":
        rest = p[2:].replace("\\", "/").lstrip("/")
        return "/mnt/" + p[0].lower() + "/" + rest
    return None

# 정책 값이 아니라 **마지막 보루** — 어떤 Config 사본도 읽지 못할 때만 쓴다.
# 기존 계기판 하드코딩과 동일값(스펙 docs/spec_scoreboard_limits_source.md).
FALLBACK = {"max_daily_trades": 3, "per_stock_pct": 0.10,
            "max_open_positions": 3, "total_exposure_pct": 0.30}

# 계기판 한도 키 → Config 필드 이름
_MAP = (("max_daily_trades", "daily_max_trades"),
        ("per_stock_pct", "max_position_pct"),
        ("max_open_positions", "max_open_positions"),
        ("total_exposure_pct", "max_invested_pct"))

_REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _candidates(candidates):
    """기본 후보 순서: 환경변수 → Windows 라이브 사본 → 리포 내 사본 (빈 값 제거)."""
    if candidates is not None:
        return [c for c in candidates if c]
    return [p for p in (
        os.environ.get("TRADER_AGENT_DIR"),
        "/mnt/c/Users/jhshi/analyist_dd/trader-agent",
    ) if p]


def _load_config(cand):
    """cand 사본에서 Config() 를 읽어 매핑 4값을 돌려준다. 실패 시 None.

    sys.path·sys.modules 는 검사 후 원상복구한다(다른 후보·이후 import 오염 금지).
    """
    saved_path = list(sys.path)
    saved_mods = {k: v for k, v in sys.modules.items()
                  if k == "trader_core" or k.startswith("trader_core.")}
    try:
        for k in list(sys.modules):
            if k == "trader_core" or k.startswith("trader_core."):
                del sys.modules[k]
        sys.path.insert(0, cand)
        cfg = importlib.import_module("trader_core.config").Config()
        values = {limit_key: getattr(cfg, field) for limit_key, field in _MAP}
        if any(v is None for v in values.values()):
            return None
        return values
    except Exception as exc:
        _LOG.warning("Config import 실패(%s): %s: %s — win_python 폴백 시도", cand, type(exc).__name__, exc)
        return None
    finally:
        sys.path[:] = saved_path
        for k in list(sys.modules):
            if k == "trader_core" or k.startswith("trader_core."):
                del sys.modules[k]
        sys.modules.update(saved_mods)


def _win_path(p):
    """/mnt/c/... → C:\\... (Windows python 에 넘길 경로). 변환 불가면 None."""
    if p.startswith("/mnt/") and len(p) > 6 and p[5].isalpha():
        rest = p[6:].replace("/", "\\")
        return p[5].upper() + ":\\" + rest.lstrip("\\")
    return None


def _load_config_via_windows(cand, timeout=60):
    """WSL 에서 9p 디렉터리 목록이 비어(import 실패)일 때 Windows python 으로 같은 값을 읽는다.

    WHY (2026-11-24 실측): `os.listdir('/mnt/c/.../trader-agent')` 가 [] 를 돌려주는데
    `trader-agent/trader_core/config.py` 는 읽힌다(9p dentry 캐시가 재생성된 디렉터리에 stale —
    Windows cmd `dir` 은 정상). 그 상태에서 importlib 가 패키지를 못 찾아 계기판이 조용히
    폴백(10%/30%)으로 판정했다(= 실행값 1/3·100% 와 반대). 파일 단위 접근은 되므로
    Windows python 으로 같은 Config() 를 읽어 loaded=True 를 회복한다.
    실패하면 None — 호출부가 기존 폴백으로 내려간다(동작 변화 없음).
    """
    wp = _win_path(cand)
    wpy_wsl = _to_wsl_path(_WPY)
    if not wp or not wpy_wsl or not os.path.exists(wpy_wsl):
        return None
    code = ("import sys,json;sys.path.insert(0,r'%s');"
            "from trader_core.config import Config;c=Config();"
            "print(json.dumps([c.daily_max_trades,c.max_position_pct,"
            "c.max_open_positions,c.max_invested_pct]))" % wp)
    try:
        out = subprocess.run([wpy_wsl, "-c", code], capture_output=True, text=True,
                             timeout=timeout, cwd=_to_wsl_path(wp) or None)
        vals = json.loads((out.stdout or "").strip().splitlines()[-1])
        values = {k: v for (k, _f), v in zip(_MAP, vals)}
        if any(v is None for v in values.values()):
            return None
        return values
    except Exception as exc:
        _LOG.warning("win_python 폴백 실패(%s): %s: %s — 폴백 값 사용", cand, type(exc).__name__, exc)
        return None


def execution_limits(candidates=None):
    """(limits, meta) — 실행 설정(Config)에서 한도 4값과 출처 메타를 돌려준다.

    meta 필수 키: source("config:<절대경로>" 또는 "fallback"), loaded(bool),
    values(매핑 원본 4값 — fallback 이면 None), fallback(FALLBACK 사본),
    candidates_tried(시도한 후보 경로 목록). via("import"|"win_python") 는 참고용.
    """
    tried = _candidates(candidates)
    for cand in tried:
        if not os.path.isdir(cand):
            continue
        values = _load_config(cand)
        via = "import"
        if not values:
            values = _load_config_via_windows(cand)
            via = "win_python"
        if values:
            meta = {"source": "config:" + os.path.abspath(cand), "loaded": True,
                    "values": values, "fallback": dict(FALLBACK), "via": via,
                    "candidates_tried": list(tried)}
            return dict(values), meta
    meta = {"source": "fallback", "loaded": False, "values": None,
            "fallback": dict(FALLBACK), "via": None, "candidates_tried": list(tried)}
    return dict(FALLBACK), meta


def claim():
    """3분리 자기신고(소스 수신/파서 생성/실제 저장) — 소비자(계기판) 출력에 내장한다.

    dq_claim.record_claim 은 dq_runner_claim INSERT 라서 계기판의 읽기 전용 계약(DB 쓰기
    금지, money_scoreboard docstring)에 위배된다 — 대신 출력 json 의 limits_meta·
    claim.limits 로 남긴다. 재실행 시 같은 값(멱등).
    """
    return {
        "source_rows": {"config_values": LIMITS_META["values"]},
        "parsed_rows": {"mapping": dict(_MAP), "limits": dict(LIMITS)},
        "persisted_rows": {"output.limits_meta": 1,
                           "output.capital_utilization.limits": 1},
        "note": "dq_claim.record_claim 미사용 — 계기판 DB 쓰기 금지(파일 내장). 멱등.",
    }


LIMITS, LIMITS_META = execution_limits()

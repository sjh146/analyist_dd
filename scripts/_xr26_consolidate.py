#!/usr/bin/env python3
"""XR26 패치 단일화 — 경합하는 두 패치를 하나의 권위 있는 diff 로 합친다.

배경(2026-10-09 04:0x, 엔지니어 자율):
  `data/reports/xr26_minute_pagination_fix.patch`(10-08)와
  `data/reports/xr26_minute_pagination.patch`(10-09)가 **같은 파일을 고치는 서로 다른 diff** 로
  동시에 존재했다 — 승인자가 어느 것을 적용해야 하는지 알 수 없는 상태(적용 실수 위험).
  둘의 차이는 ① MAX_PAGES 14 vs 20 ② 종료 판정(직전 페이지 실측 크기 vs 개장 도달) ③ FID_CNT 상수.
  여기서는 **최신(10-09) 종료 로직**(완전성 우선: '개장 도달'로만 종료, 진행 없음은 무한루프 방어)을
  기준으로 ② MAX_PAGES=20 ③ FID_CNT_DEFAULT=30(실측 상한 문서화) 를 합쳐 하나로 만든다.

수집기 파일은 타 역할 소유이므로 이 스크립트는 **diff 산출물만** 만들고 원본을 즉시 원복한다.
사용: python3 scripts/_xr26_consolidate.py
"""
from __future__ import annotations

import pathlib
import subprocess

REPO = pathlib.Path(__file__).resolve().parents[1]
REL = "services/kis-collector/kis_app/collectors/minute_collector.py"
SRC = REPO / REL
OUT = REPO / "data/reports/xr26_minute_pagination.patch"

EDITS = [
    (
        """페이지네이션: 1회 응답 최대 100건(FID_CNT). 응답은 시간 내림차순(최신 우선)
이므로, ``FID_INPUT_HOUR=153000`` 시작 → 페이지의 최저 시각 −1분을 다음 요청
기준으로 과거로 진행. ``090000`` 도달 / 배치 < FID_CNT / max_pages 방어로 종료.""",
        """페이지네이션: 1회 응답은 **30건 고정**(``fid_cnt`` 파라미터는 이 엔드포인트에서 무시된다).
응답은 시간 내림차순(최신 우선)이므로, ``FID_INPUT_HOUR=153000`` 시작 → 페이지의 최저
시각 −1분을 다음 요청 기준으로 과거로 진행. ``090000`` 도달 / 진행 없음 / max_pages 방어로 종료.
⚠ 종료 판정에 '요청값(fid_cnt)'을 쓰면 안 된다 — 30 < 100 이 항상 참이라 1페이지에서
조기 종료되어 15:01~15:30 30봉만 적재된다(XR26 실측 결함).""",
    ),
    (
        """FID_CNT_DEFAULT = 100       # 1회 응답 최대 건수
MAX_PAGES_DEFAULT = 10      # 안전장치 (정규장 391분 → 100건×4페이지면 충분)""",
        """FID_CNT_DEFAULT = 30        # 1회 응답 실제 상한 — 이 엔드포인트는 fid_cnt 를 줘도 30행 고정(XR26)
MAX_PAGES_DEFAULT = 20      # 안전장치 (정규장 391분 ÷ 실측 30행 = 13.03 → 14페이지, 여유 20)""",
    ),
    (
        """            if len(page_bars) < int(fid_cnt) or oldest == MARKET_OPEN_TIME:
                break  # 마지막 페이지""",
        """            # XR26: KIS 당일분봉조회는 fid_cnt(=100)와 무관하게 1회 최대 30봉만 돌려준다 →
            # `len(page_bars) < fid_cnt` 로 종료를 판정하면 매일 1페이지(15:01~15:30)에서
            # 조기 종료된다. 종료는 '개장 도달'과 '진행 없음'으로만 판정한다.
            if oldest <= MARKET_OPEN_TIME:
                break  # 개장 시각 도달 = 마지막 페이지""",
    ),
]


def main() -> int:
    orig = SRC.read_text()
    if "len(page_bars) < int(fid_cnt)" not in orig:
        print("[SKIP] 원본이 이미 패치/변형 상태 — 손대지 않는다(백업 확인 필요)")
        return 1
    new = orig
    for old, rep in EDITS:
        if old not in new:
            print(f"[FAIL] 치환 대상 없음: {old.splitlines()[0][:60]}")
            return 1
        new = new.replace(old, rep, 1)
    try:
        SRC.write_text(new)
        diff = subprocess.run(["git", "diff", "--", REL], cwd=REPO,
                              capture_output=True, text=True).stdout
    finally:
        SRC.write_text(orig)                      # 즉시 원복(타 역할 파일 흔적 금지)
    assert SRC.read_text() == orig, "원복 실패!"
    if not diff.strip():
        print("[FAIL] diff 비어 있음")
        return 1
    OUT.write_text(diff)
    chk = subprocess.run(["git", "apply", "--check", "-p1", str(OUT)], cwd=REPO,
                         capture_output=True, text=True)
    print(f"[{'PASS' if chk.returncode == 0 else 'FAIL'}] git apply --check -p1 {OUT.relative_to(REPO)}")
    if chk.returncode:
        print(chk.stderr)
    print(f"원본 원복 확인: {SRC.read_text() == orig} · diff {len(diff.splitlines())}행")
    return 0 if chk.returncode == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())

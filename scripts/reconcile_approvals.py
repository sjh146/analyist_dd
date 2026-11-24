#!/usr/bin/env python3
"""승인된 항목을 실행 대기로 복원한다 (2026-11-24).

문제: 루프가 쓰는 일회성 백로그 패치 스크립트가 이미 승인된 항목을 needs_setup 으로
      되돌려 실행이 영구 정체된다(11-21 승인 3건이 11-24 에 다시 막혀 있었다).

규칙: approved_by 가 있는 항목은 needs_setup 일 수 없다 → pending 으로 올리고 setup_needed 를 비운다.
      승인 자체를 되돌리려면 approved_by 를 지워야 한다(명시적 행위).
야간 계약 크론에서 실행되어, 루프가 되돌려도 다음 새벽에 복원된다.

사용: python scripts/reconcile_approvals.py [--dry-run]
종료코드: 0 정상(복원 건수 출력)
"""
from __future__ import annotations
import argparse, json, os, re, sys
from datetime import datetime

PROJ = os.environ.get("PROJ_DIR", "/home/jhshi/analyist_dd")
BACKLOGS = ["docs/QUANT_MODEL_BACKLOG.json", "docs/QUANT_RESEARCH_BACKLOG.json",
            "docs/QUANT_TRADER_BACKLOG.json"]


APPROVAL_LIKE = re.compile(r"승인|리뷰보드|사용자")
TECHNICAL = re.compile(r"선행|의존|미구현|구현이 남|커버리지 게이트|표본 부족|기록 0건")


def classify(reason: str) -> str:
    if TECHNICAL.search(reason):
        if APPROVAL_LIKE.search(reason) and not TECHNICAL.search(reason):
            return "approval"
        return "technical"
    return "approval" if APPROVAL_LIKE.search(reason) else "technical"


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()
    changed, skipped = [], []
    for rel in BACKLOGS:
        path = os.path.join(PROJ, rel)
        if not os.path.exists(path):
            continue
        try:
            data = json.load(open(path, encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as e:
            print(f"[승인조정] {rel} 읽기 실패: {e}", file=sys.stderr)
            continue
        dirty = False
        for item in data.get("items", []):
            if item.get("approved_by") and item.get("status") == "needs_setup":
                kind = classify(str(item.get("setup_needed") or ""))
                if kind != "approval":
                    skipped.append((item["id"], str(item.get("setup_needed"))[:70]))
                    continue
                changed.append((item["id"], item.get("approved_by"), str(item.get("setup_needed"))[:60]))
                if not args.dry_run:
                    item["status"] = "pending"
                    item["setup_needed"] = None
                    item["status_note"] = f"{datetime.now():%Y-%m-%d} 승인 복원(reconcile_approvals)"
                    dirty = True
        if dirty:
            data["updated_at"] = datetime.now().isoformat(timespec="seconds")
            tmp = path + ".tmp"
            json.dump(data, open(tmp, "w", encoding="utf-8"), ensure_ascii=False, indent=2)
            os.replace(tmp, path)
    if skipped:
        print(f"[승인조정] 기술적 선행조건으로 **차단 유지** {len(skipped)}건(루프 판단 존중)")
        for iid, reason in skipped[:8]:
            print(f"  {iid}: {reason}")
    if not changed:
        sys.exit(0)
    tag = "DRY-RUN" if args.dry_run else "복원"
    print(f"[승인조정] {tag} {len(changed)}건 — 승인됐는데 차단돼 있던 항목")
    for iid, by, reason in changed[:10]:
        print(f"  {iid}: approved_by={by} / 해제된 사유: {reason}")
    sys.exit(0)


if __name__ == "__main__":
    main()

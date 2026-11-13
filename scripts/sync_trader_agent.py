#!/usr/bin/env python3
"""trader-agent 두 사본 동기화 — 저장소 사본 ↔ 라이브(Windows) 사본.

왜 필요한가
  2026-11-13 에 trader-agent 를 analyist_dd 저장소 안으로 통합했다. 그런데 매매 루프·브리지는
  Windows 사본(`C:\\Users\\<user>\\analyist_dd\\trader-agent`)에서 돌기 때문에 **물리적 사본이 두 개**다.
  한쪽에서 고친 코드가 다른 쪽에 없다는 사실은 조용히 지나가므로, 차이를 한 명령으로 보여준다.

사용
  python3 scripts/sync_trader_agent.py                 # 미리보기(양방향 차이만)
  python3 scripts/sync_trader_agent.py --push          # 저장소 → 라이브로 복사
  python3 scripts/sync_trader_agent.py --pull          # 라이브 → 저장소로 복사
  python3 scripts/sync_trader_agent.py --push --apply  # 실제 쓰기(기본은 미리보기)

규칙
  * 비교 대상은 **양쪽 git 이 추적하는 파일**의 합집합이다(journal·logs·data·.env·__pycache__ 같은
    런타임 산출물은 애초에 목록에 없어 안전하다).
  * 복사는 추가·변경만 한다. 삭제는 하지 않는다(라이브에서 지워진 파일을 지우는 건 사람 판단).
  * 벤더 샘플(`creon_extracted_v1/`)은 저장소 쪽에서만 제외돼 있으므로 기본 비교에서 뺀다
    (`--include-vendor` 로 포함).
  * 라이브 루프/브리지가 떠 있어도 파일 복사는 안전하다(실행 중 프로세스는 메모리 코드를 쓴다).
    다만 반영은 **다음 기동**부터이므로, 코드 변경 후에는 재기동 시점을 사람이 정한다.
"""
from __future__ import annotations

import argparse
import filecmp
import os
import subprocess
import sys

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DEFAULT_A = os.path.join(REPO_ROOT, "trader-agent")
DEFAULT_B = os.environ.get("TRADER_LIVE_DIR", "/mnt/c/Users/jhshi/analyist_dd/trader-agent")
VENDOR_PREFIXES = ("creon_extracted_v1/", "크레온 기본알고리즘/", "creon_src/")


def tracked(root: str) -> set:
    """git 이 추적하는 파일 목록(저장소 루트 기준 상대경로). 없으면 빈 집합.

    ``-z`` 로 받는다: 기본 출력은 비 ASCII 경로를 따옴표·이스케이프해서 돌려주므로
    (``"creon_extracted_v1/..."``) 경로 비교·벤더 판정이 어긋난다.
    """
    try:
        out = subprocess.run(["git", "-C", root, "ls-files", "-z"],
                             capture_output=True, timeout=60)
    except Exception:
        return set()
    if out.returncode != 0:
        return set()
    text = (out.stdout or b"").decode("utf-8", "replace")
    return {item for item in text.split("\0") if item}


def is_vendor(rel: str) -> bool:
    return rel.startswith(VENDOR_PREFIXES)


def digest(path: str) -> str:
    import hashlib

    h = hashlib.sha256()
    with open(path, "rb") as handle:
        for chunk in iter(lambda: handle.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def compare(a_root: str, b_root: str, files_a: set, files_b: set, include_vendor: bool = False) -> dict:
    """{'only_a': [...], 'only_b': [...], 'different': [...], 'same': n} 를 돌려준다."""
    universe = files_a | files_b
    if not include_vendor:
        universe = {rel for rel in universe if not is_vendor(rel)}
    only_a, only_b, different, same = [], [], [], 0
    for rel in sorted(universe):
        in_a, in_b = rel in files_a, rel in files_b
        if not include_vendor and is_vendor(rel):
            continue
        path_a, path_b = os.path.join(a_root, rel), os.path.join(b_root, rel)
        if in_a and not in_b:
            only_a.append(rel)
        elif in_b and not in_a:
            only_b.append(rel)
        elif os.path.isfile(path_a) and os.path.isfile(path_b):
            if filecmp.cmp(path_a, path_b, shallow=False):
                same += 1
            else:
                different.append(rel)
        else:
            different.append(rel)
    return {"only_a": only_a, "only_b": only_b, "different": different, "same": same}


def copy_files(src_root: str, dst_root: str, rels) -> int:
    copied = 0
    for rel in rels:
        src = os.path.join(src_root, rel)
        if not os.path.isfile(src):
            print("  [skip] 원본 없음: {0}".format(rel))
            continue
        dst = os.path.join(dst_root, rel)
        os.makedirs(os.path.dirname(dst), exist_ok=True)
        with open(src, "rb") as fsrc, open(dst, "wb") as fdst:
            fdst.write(fsrc.read())
        copied += 1
    return copied


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(prog="sync_trader_agent.py", description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--repo-dir", default=DEFAULT_A, help="저장소 사본(기본: <repo>/trader-agent)")
    parser.add_argument("--live-dir", default=DEFAULT_B, help="라이브 사본(기본: Windows 경로)")
    parser.add_argument("--push", action="store_true", help="저장소 → 라이브")
    parser.add_argument("--pull", action="store_true", help="라이브 → 저장소")
    parser.add_argument("--apply", action="store_true", help="실제로 복사(없으면 미리보기)")
    parser.add_argument("--include-vendor", action="store_true", help="벤더 샘플도 비교/복사")
    parser.add_argument("--max-list", type=int, default=20, help="차이 목록 출력 개수")
    args = parser.parse_args(argv)

    if args.push and args.pull:
        print("--push 와 --pull 은 함께 쓸 수 없다", file=sys.stderr)
        return 2
    for label, root in (("저장소", args.repo_dir), ("라이브", args.live_dir)):
        if not os.path.isdir(root):
            print("[error] {0} 사본이 없다: {1}".format(label, root), file=sys.stderr)
            return 2

    files_a, files_b = tracked(args.repo_dir), tracked(args.live_dir)
    if not files_a or not files_b:
        print("[warn] git 추적 목록을 못 읽었다 (repo={0}, live={1}) — 디렉터리 전체 비교로 대체하지 않고 중단".format(
            len(files_a), len(files_b)), file=sys.stderr)
        return 3

    diff = compare(args.repo_dir, args.live_dir, files_a, files_b, args.include_vendor)
    direction = "저장소 → 라이브" if args.push else ("라이브 → 저장소" if args.pull else None)
    print("trader-agent 동기화 {0}".format("(적용)" if (args.apply and direction) else "(미리보기)"))
    print("  저장소: {0}  ({1}파일 추적)".format(args.repo_dir, len(files_a)))
    print("  라이브: {0}  ({1}파일 추적)".format(args.live_dir, len(files_b)))
    print("  동일 {0} · 저장소만 {1} · 라이브만 {2} · 내용다름 {3}".format(
        diff["same"], len(diff["only_a"]), len(diff["only_b"]), len(diff["different"])))

    for label, key in (("저장소에만", "only_a"), ("라이브에만", "only_b"), ("내용 다름", "different")):
        if diff[key]:
            print("\n[{0}] {1}건".format(label, len(diff[key])))
            for rel in diff[key][:args.max_list]:
                print("  - {0}".format(rel))
            if len(diff[key]) > args.max_list:
                print("  … 외 {0}건".format(len(diff[key]) - args.max_list))

    if not direction:
        print("\n적용하려면 --push 또는 --pull 에 --apply 를 붙여라.")
        return 0

    if not args.apply:
        print("\n[{0}] 미리보기 — 실제 복사는 --apply 필요".format(direction))
        return 0

    if args.push:
        todo = diff["only_a"] + diff["different"]
        src, dst = args.repo_dir, args.live_dir
    else:
        todo = diff["only_b"] + diff["different"]
        src, dst = args.live_dir, args.repo_dir
    copied = copy_files(src, dst, todo)
    print("\n[{0}] {1}파일 복사 완료".format(direction, copied))
    print("주의: 실행 중인 루프·브리지는 다음 기동부터 새 코드를 쓴다.")
    return 0


if __name__ == "__main__":
    sys.exit(main())

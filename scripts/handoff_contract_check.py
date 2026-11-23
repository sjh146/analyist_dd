#!/usr/bin/env python3
"""역할 간 인계 계약 검증 (docs/HANDOFF_CONTRACT.json 기반).

검사:
  A. 백로그 항목 — 상태별 필수 필드(가설·재현명령·수치합격기준·근거·차단사유·결과)
  B. 원장 — 판정 근거(log/parsed) 누락
  C. 그래프 스키마 — writer/reader 관계 불일치(치명), 미등록 라벨·관계, 최소 개수
정상이면 완전 침묵(크론 친화). 문제가 있을 때만 요약을 출력한다.
종료코드: 0 정상 · 1 WARN만 · 2 FAIL 포함

사용: /usr/bin/python3 scripts/handoff_contract_check.py [--json out.json] [--verbose]
"""
from __future__ import annotations
import argparse, json, os, re, subprocess, sys
from datetime import datetime

PROJ = os.environ.get("PROJ_DIR", "/home/jhshi/analyist_dd")
CONTRACT = os.path.join(PROJ, "docs/HANDOFF_CONTRACT.json")


def load_json(path: str):
    try:
        return json.load(open(path, encoding="utf-8"))
    except Exception as e:
        return {"__error__": str(e)}


def load_jsonl(path: str) -> list[dict]:
    out = []
    try:
        with open(path, encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if line:
                    try:
                        out.append(json.loads(line))
                    except Exception:
                        pass
    except Exception:
        pass
    return out


def check_backlog(path: str, rules: dict) -> dict:
    """백로그 항목의 상태별 필수 필드 검사."""
    data = load_json(path)
    if "__error__" in data:
        return {"path": path, "error": data["__error__"], "warn": [], "fail": []}
    items = data.get("items") or []
    warn, fail = [], []
    all_req = rules["all_items"]["required"]
    id_re = re.compile(rules["all_items"]["id_pattern"])
    for it in items:
        iid = str(it.get("id") or "?")
        st = str(it.get("status") or "")
        # 공통 필수
        missing = [f for f in all_req if not it.get(f) and it.get(f) != 0]
        if missing:
            warn.append((iid, st, f"공통 필수 누락: {','.join(missing)}"))
        if not id_re.match(iid):
            warn.append((iid, st, "id 규칙 위반"))
        # 상태별
        if st in rules["actionable"]["statuses"]:
            if not it.get("hypothesis") and not it.get("detail"):
                warn.append((iid, st, "가설/상세 없음(반증 불가)"))
            if not it.get("command") and not it.get("summary_out"):
                warn.append((iid, st, "재현 명령 없음"))
            if not (it.get("success") or it.get("check") or it.get("check_target")):
                warn.append((iid, st, "수치 합격기준 없음"))
            if (it.get("priority") or 9) == 1 and not it.get("evidence") and not it.get("note"):
                warn.append((iid, st, "p1 인데 근거(evidence) 없음"))
        elif st in rules["blocked"]["statuses"]:
            if not it.get("setup_needed"):
                warn.append((iid, st, "차단 사유(setup_needed) 없음"))
        elif st in rules["finished"]["statuses"]:
            if not it.get("result"):
                warn.append((iid, st, "결과(result) 없음"))
    return {"path": path, "n": len(items), "warn": warn, "fail": fail}


def check_ledger(path: str, req: list[str], evidence_any: list[str] | None = None) -> dict:
    rows = load_jsonl(path)
    warn = []
    for r in rows[-300:]:
        missing = [f for f in req if r.get(f) in (None, "", [])]
        if missing:
            warn.append((str(r.get("id") or "?"), str(r.get("verdict") or "?"), f"원장 필수 누락: {','.join(missing)}"))
        elif evidence_any and not any(r.get(k) for k in evidence_any):
            warn.append((str(r.get("id") or "?"), str(r.get("verdict") or "?"),
                         f"원장 근거 누락: {'/'.join(evidence_any)} 중 없음"))
    return {"path": path, "n": len(rows), "warn": warn, "fail": []}


def _cypher(query: str) -> str | None:
    """Neo4j 질의 (자격증명은 .env 에서 읽고 출력하지 않는다)."""
    pw = os.environ.get("NEO4J_PASSWORD") or os.environ.get("NEO4J_AUTH_PASSWORD")
    if not pw:
        env = os.path.join(PROJ, ".env")
        try:
            for line in open(env, encoding="utf-8", errors="ignore"):
                if line.startswith(("NEO4J_PASSWORD=", "NEO4J_AUTH_PASSWORD=")):
                    pw = line.split("=", 1)[1].strip().strip('"').strip("'")
                    break
        except Exception:
            return None
    if not pw:
        return None
    try:
        r = subprocess.run(
            ["docker", "exec", "stock_neo4j", "cypher-shell", "-u", "neo4j", "-p", pw, query],
            capture_output=True, text=True, timeout=40)
        return r.stdout if r.returncode == 0 else None
    except Exception:
        return None


def check_graph(gcfg: dict) -> dict:
    warn, fail = [], []
    out = _cypher("MATCH ()-[r]->() RETURN type(r) AS rel, count(*) AS c")
    if out is None:
        return {"skipped": "Neo4j 접속 불가(자격증명 또는 컨테이너)", "warn": warn, "fail": fail}
    counts: dict[str, int] = {}
    for line in out.splitlines()[1:]:
        parts = [p.strip().strip('"') for p in line.split(",")]
        if len(parts) >= 2 and parts[1].isdigit():
            counts[parts[0]] = int(parts[1])
    allowed = set(gcfg["allowed_relations"])
    for rel in counts:
        if rel not in allowed:
            warn.append((rel, "graph", "계약에 없는 관계명(스키마 드리프트)"))
    for rel, minimum in (gcfg.get("min_relation_counts") or {}).items():
        if counts.get(rel, 0) < minimum:
            fail.append((rel, "graph", f"최소 {minimum} 필요 / 현재 {counts.get(rel, 0)}"))
    for pair in gcfg.get("reader_writer_pairs") or []:
        w, rd = pair["writer"], pair["reader"]
        if counts.get(w, 0) == 0 and counts.get(rd, 0) == 0:
            fail.append((f"{w}/{rd}", "graph", "writer·reader 관계 모두 0 — 피처가 조용히 0 이 된다"))
        elif counts.get(w, 0) == 0 or counts.get(rd, 0) == 0:
            warn.append((f"{w}/{rd}", "graph",
                         f"한쪽만 존재(writer {counts.get(w,0)} / reader {counts.get(rd,0)}) — 스키마 불일치 위험"))
    return {"counts": counts, "warn": warn, "fail": fail}


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--json", dest="json_out")
    ap.add_argument("--verbose", action="store_true")
    args = ap.parse_args()

    c = load_json(CONTRACT)
    if "__error__" in c:
        print(f"[계약검사] 계약 파일 읽기 실패: {c['__error__']}")
        sys.exit(2)

    reports = []
    for key in ("research_backlog", "model_backlog", "trader_backlog"):
        p = c["carriers"].get(key)
        if p and os.path.exists(os.path.join(PROJ, p)):
            reports.append(("backlog", check_backlog(os.path.join(PROJ, p), c["item_rules"])))
    for key in ("engineer_ledger", "researcher_ledger"):
        p = c["carriers"].get(key)
        if p and os.path.exists(os.path.join(PROJ, p)):
            reports.append(("ledger", check_ledger(
            os.path.join(PROJ, p),
            c["directions"]["engineer->trader"]["required_ledger_fields"],
            c["directions"]["engineer->trader"].get("evidence_any"))))
    reports.append(("graph", check_graph(c["graph"])))

    n_fail = sum(len(r["fail"]) for _, r in reports)
    n_warn = sum(len(r["warn"]) for _, r in reports)
    summary = {
        "checked_at": datetime.now().isoformat(timespec="seconds"),
        "fail": n_fail, "warn": n_warn,
        "details": [
            {"kind": k, **{kk: vv for kk, vv in r.items() if kk in ("path", "n", "counts", "skipped", "error")},
             "warn": r["warn"][:40], "fail": r["fail"]}
            for k, r in reports
        ],
    }
    if args.json_out:
        os.makedirs(os.path.dirname(args.json_out), exist_ok=True)
        json.dump(summary, open(args.json_out, "w", encoding="utf-8"), ensure_ascii=False, indent=2)

    # 정상이면 침묵
    if n_fail == 0 and n_warn == 0:
        if args.verbose:
            print("[계약검사] 위반 없음 ✓")
        sys.exit(0)

    print(f"[인계 계약검사] FAIL {n_fail} / WARN {n_warn}")
    for kind, r in reports:
        if r.get("fail"):
            print(f"  ✗ {kind}: {len(r['fail'])}건")
            for row in r["fail"][:5]:
                print(f"     {row[0]}: {row[2]}")
        if args.verbose and r.get("warn"):
            print(f"  ⚠ {kind}: {len(r['warn'])}건")
            for row in r["warn"][:8]:
                print(f"     {row[0]} [{row[1]}]: {row[2]}")
    # WARN 만 있으면 상위 3종만 요약
    if not args.verbose and n_fail == 0:
        from collections import Counter
        kinds = Counter(w[2].split(":")[0] for _, r in reports for w in r["warn"])
        print("  상위 유형:", dict(kinds.most_common(4)))
    sys.exit(2 if n_fail else 0)  # WARN 은 정상 종료(요약만 stdout)


if __name__ == "__main__":
    main()

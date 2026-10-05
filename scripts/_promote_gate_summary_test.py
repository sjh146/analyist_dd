#!/usr/bin/env python3
"""자체점검: champion_promote 의 **게이트 증거가 요약 JSON 에 남는가** + `--help` 정상.

실측 동기(2026-10-05 CG122): 후보(champion_cand)가 단일분할 val 0.5580 > 기준선 0.5513 으로
`would_promote` 였는데, 돈 기준 게이트(`--require-expectancy`)가 순기대 -0.2445%p 로 거부했다.
그런데 요약 JSON 은 `status`·`reason` 만 남기고 **auc·기준선·expectancy_gate 가 전부 null** 이었다
(`main()` 의 payload 가 fixed key 목록 + 거부 분기가 `result.update(...)` 앞이라서) → 원장/감사에서
'무엇을 막았는가'가 사라진다. 또 `--help` 는 help 문자열의 `%p` 를 argparse 가 %-보간해
`ValueError: unsupported format character 'p'` 로 죽었다(진단 불가).

검증(실측 대조군 포함):
  ① `--help` rc==0 (수리 전 rc!=0)
  ② 요약 payload 에 expectancy_gate·live_score_gate·candidate_robust_oos 키가 있다
  ③ 돈 기준 거부일 때 auc·champion_baseline 이 null 이 아니다(수리 전 null)
  ④ --require-expectancy 없이도 크래시하지 않는다

컨테이너에서 돈다: python3 scripts/_promote_gate_summary_test.py
"""
import json
import os
import subprocess
import sys

C = "stock_xgboost_ml"
CAND = "/app/app/models/champion_cand"
CHAMP = "/app/app/models/champion"
TMP = "/app/reports/overnight/_test_promote_gate.json"
TMP2 = "/app/reports/overnight/_test_promote_gate_naive.json"
HOST_DIR = "/home/jhshi/analyist_dd/services/xgboost-ml/reports/overnight"

FAIL = 0
PASS = 0


def check(name, cond, extra=""):
    global FAIL, PASS
    if cond:
        PASS += 1
        print(f"PASS  {name}")
    else:
        FAIL += 1
        print(f"FAIL  {name}  {extra}")


def dexec(args, **kw):
    return subprocess.run(["docker", "exec", C] + args, capture_output=True, text=True, **kw)


def main():
    if subprocess.run(["docker", "exec", C, "true"], capture_output=True).returncode != 0:
        print("SKIP  컨테이너 접근 불가 — 이 점검은 컨테이너에서 돈다")
        return 0

    # ① --help (수리 전 ValueError 로 rc!=0)
    h = dexec(["python", "/app/app/training/champion_promote.py", "--help"])
    check("① --help rc==0 (argparse %p 수리)", h.returncode == 0,
          f"rc={h.returncode} err={h.stderr[-200:]}")
    check("① usage 문구 출력", "usage:" in h.stdout, h.stdout[:120])
    check("① %p 가 리터럴로 렌더", "(%p)" in h.stdout, "")

    # 후보 존재 확인
    ls = dexec(["sh", "-c", f"ls {CAND}/training-result-*.json | tail -1"])
    if ls.returncode != 0 or not ls.stdout.strip():
        print("SKIP  후보(champion_cand) 없음 — 게이트 페이로드 점검 생략")
        return 0
    has_robust = dexec(["test", "-f", f"{CAND}/robust_oos.json"]).returncode == 0

    # ②③ 돈 기준 게이트 실행
    r = dexec(["python", "/app/app/training/champion_promote.py", "--candidate", CAND,
               "--champion", CHAMP, "--dry-run", "--require-expectancy", "--summary-out", TMP])
    if r.returncode == 5:
        print(f"SKIP  후보 무효(rc=5) — status=invalid_candidate: {r.stdout[-200:]}")
        return 0
    check("② 게이트 실행 rc==0", r.returncode == 0, f"rc={r.returncode} {r.stderr[-200:]}")
    hp = TMP.replace("/app/", HOST_DIR.rsplit("/", 0)[0] + "/", 1)
    host_tmp = os.path.join(HOST_DIR, os.path.basename(TMP))
    if not os.path.exists(host_tmp):
        check("② 요약 파일 생성", False, host_tmp)
        return 1
    d = json.load(open(host_tmp, encoding="utf-8"))
    for k in ("expectancy_gate", "live_score_gate", "candidate_robust_oos"):
        check(f"② payload 키 존재: {k}", k in d, f"keys={sorted(d.keys())}")
    check("③ auc(후보 단일분할) 비어있지 않음", d.get("auc") is not None, f"auc={d.get('auc')}")
    check("③ champion_baseline 비어있지 않음", d.get("champion_baseline") is not None,
          f"baseline={d.get('champion_baseline')}")
    if has_robust:
        eg = d.get("expectancy_gate") or {}
        if d.get("status") == "kept_incumbent" and str(d.get("reason", "")).startswith("돈 기준"):
            check("③ 돈 기준 거부 시 expectancy_gate.present=True", eg.get("present") is True, f"eg={eg}")
            check("③ 돈 기준 거부 시 순기대 수치 기록", eg.get("pct") is not None, f"eg={eg}")
            check("③ 후보 OOS robust_auc 기록",
                  (d.get("candidate_robust_oos") or {}).get("value") is not None, "")
        else:
            check("③ 돈 기준 거부가 아님이면 상태가 유효값", d.get("status") in
                  ("would_promote", "kept_incumbent", "promoted"), f"status={d.get('status')}")
    else:
        check("③ robust_oos.json 부재 → '돈 증거 없음' 거부", (d.get("expectancy_gate") or {}).get("present") is False,
              f"eg={d.get('expectancy_gate')}")

    # ④ --require-expectancy 없이도 크래시 없음
    r2 = dexec(["python", "/app/app/training/champion_promote.py", "--candidate", CAND,
                "--champion", CHAMP, "--dry-run", "--summary-out", TMP2])
    check("④ naive dry-run rc==0", r2.returncode in (0, 5), f"rc={r2.returncode} {r2.stderr[-200:]}")

    dexec(["rm", "-f", TMP, TMP2])
    print(f"\n{PASS} PASS / {FAIL} FAIL")
    return 1 if FAIL else 0


if __name__ == "__main__":
    sys.exit(main())

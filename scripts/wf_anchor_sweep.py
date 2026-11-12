#!/usr/bin/env python3
"""wf_anchor_sweep — 스윕 arm 의 **앵커 잡음**을 측정한다 (CG200, 2026-11-12 엔지니어 자율).

왜 필요한가 (실측 CG199, 2026-11-12 00:2x):
  `--end-date` 로 패널 끝을 2거래일 앞당겨도 폴드 AUC 가 4자리까지 동일했다
  (ctl 0.5106 [0.5293,0.4987,0.5029,0.4940,0.5283] = CG195·CG196 과 비트동일). 이유는 폴드 격자가
  **패널 왼쪽부터** `step=n//(folds+1)` 로 잘리기 때문 — 끝에서 1~2일을 잘라도 마지막 폴드 컷이
  dd[step*(i+1)-1] 에 고정된다. 즉 `--end-date` 는 앵커 이동이 아니었다(검정력 0 = 측정무효).
  반면 CG144/CG145 는 champion 경로(`champion_robust_eval.py --asof-date`)에서 같은 모델의 레벨이
  앵커에 따라 0.4531→0.5128(|Δ| 0.0597 = 사전문턱의 3배)로 요동함을 실측했다. 스윕 arm 델타
  (예: CG195_dsns +0.0056)는 그 잡음과 구분되지 않는데, 스윕에는 그 도구가 없었다.

무엇을 하나:
  앵커(마지막 테스트 라벨 날짜) N개에 대해 `wf_label_sweep.py --asof-date <앵커>` 를 순차 실행하고
  (격자를 앵커에서 역산 → 앵커가 움직이면 전 폴드 창이 함께 이동), 앵커별 짝 Δ(arm − 대조)의
  **평균±std** 를 낸다. 요약 스키마는 구동기의 `protocol_pair_anchor` 파서와 호환이다
  (`parse_protocol_pair_anchor`·`judge_protocol_pair_anchor` — 판정 delta=None 이라 무개선 카운터 무관).

사전등록 판정(변경 금지):
  ① 앵커별 폴드 테스트 구간이 실제로 달라야 한다(같으면 **측정무효** — CG199 재발 방지)
  ② |앵커평균 Δ| < 앵커 std → '앵커 잡음과 구분 불가' → 조정 축 종결 근거
  ③ 앵커평균 Δ ≥ +0.02 & 전 앵커 부호 양(+) → 신호 후보(그 arm 을 다음 실험 대조군으로)
  ④ 앵커평균 Δ ≤ −0.02 → 부호 불안정(축 종결)
  ⑤ rc≠0·요약 미갱신 → 실행실패(노이즈로 쓰지 말 것)

⚠ 이 스크립트는 **계측기**다. 판정 문턱을 '앵커 평균'으로 바꾸는 것은 프로토콜 변경 = 리뷰보드
  승인 대상(CG145). 여기서는 도구 구현·실측까지만 한다.

실행(컨테이너, /app = services/xgboost-ml):
  docker exec stock_xgboost_ml sh -c 'cd /app && OMP_NUM_THREADS=4 timeout 7200 python -u \\
    scripts/wf_anchor_sweep.py --panel /app/app/models/wf/panel_200liq_fresh.npz \\
    --arms CG199_ctl_h5,CG199_dsns_h5 --asofs 2026-11-05,2026-11-06,2026-11-09 \\
    --folds 5 --seeds 5 --summary-out /app/reports/overnight/cg200_anchor_summary.json'
"""
import argparse
import json
import os
import statistics as st
import subprocess
import sys
import time

THRESH = 0.02          # 사전등록 문턱(스킬 판정 하드규칙 2)
HERE = os.path.dirname(os.path.abspath(__file__))


def log(msg):
    print(f"[{time.strftime('%H:%M:%S')}] {msg}", flush=True)


def _tail(path, n=3):
    """진행 로그의 마지막 n줄(하트비트·실패 원인 출력용)."""
    try:
        with open(path, encoding="utf-8", errors="replace") as f:
            return "".join(f.readlines()[-n:]).strip()
    except OSError:
        return ""


def _summary_ok(path, arms):
    """기존 요약 JSON 이 이 arm 들을 **전부 ok** 로 담고 있으면 재사용(앵커 단위 재개)."""
    try:
        with open(path, encoding="utf-8") as f:
            d = json.load(f)
    except (OSError, json.JSONDecodeError):
        return None
    for a in arms:
        if not any(r.get("exp") == a and r.get("status") == "ok" for r in (d.get("results") or [])):
            return None
    return d


def run_anchor(anchor, arms, args, tag):
    """앵커 1개 = wf_label_sweep 1회. 반환: (요약 dict, 오류 문자열 or None).

    ⚠ 프로세스가 죽어도 앵커 단위로는 살아남게 두 가지를 지킨다:
      ① 자식의 stdout/stderr 를 **파일로 흘려보낸다**(work_dir/<tag>_<anchor>.log) — 이전에는
         capture_output 으로 메모리에 담아 9시간 정지 동안 사이클 로그에 진행 한 줄도 없었다
         (실측 2026-11-12 CG203: 로그 5줄·출력 파일 0개·rc=137). 이제 진행률을 실시간으로 볼 수 있다.
      ② 앵커별 요약 JSON 이 이미 완주해 있으면 **재실행하지 않는다** — 호스트 수면/재부팅으로
         중간에 죽어도 다음 시도는 남은 앵커부터 시작한다.
    """
    s_out = os.path.join(args.work_dir, f"{tag}_{anchor}.json")
    out_jsonl = os.path.join(args.work_dir, f"{tag}_{anchor}.jsonl")
    anchor_log = os.path.join(args.work_dir, f"{tag}_{anchor}.log")
    if not args.no_resume:
        done = _summary_ok(s_out, arms)
        if done is not None:
            log(f"anchor={anchor} 재개: 완주한 요약 재사용 {s_out}")
            return done, None
    cmd = [sys.executable, "-u", os.path.join(HERE, "wf_label_sweep.py"),
           "--panel", args.panel, "--asof-date", anchor,
           "--folds", str(args.folds), "--seeds", str(args.seeds),
           "--only", ",".join(arms),
           "--out", out_jsonl, "--summary-out", s_out]
    for k in ("days", "limit", "market", "since", "min_days", "min_value", "order"):
        v = getattr(args, k, None)
        if v is not None:
            cmd += ["--" + k.replace("_", "-"), str(v)]
    log(f"anchor={anchor} 실행(진행 로그 {anchor_log}): {' '.join(cmd)}")
    t0 = time.time()
    last_beat = 0.0
    with open(anchor_log, "w", encoding="utf-8") as lf:
        p = subprocess.Popen(cmd, cwd=os.path.dirname(HERE) or "/app",
                             stdout=lf, stderr=subprocess.STDOUT, text=True)
        rc = None
        while True:
            rc = p.poll()
            if rc is not None:
                break
            el = time.time() - t0
            if el > args.timeout_sec:
                p.kill()
                try:
                    p.wait(timeout=60)
                except subprocess.TimeoutExpired:
                    pass
                return None, f"timeout {args.timeout_sec}s (진행 로그 {anchor_log})"
            if el - last_beat >= 120:
                last_beat = el
                log(f"anchor={anchor} 진행 {el / 60.0:.1f}분 · {_tail(anchor_log, 1)}")
            time.sleep(5)
    dt = (time.time() - t0) / 60.0
    log(f"anchor={anchor} rc={rc} 소요 {dt:.1f}분")
    if rc != 0:
        log(_tail(anchor_log, 6))
        return None, f"rc={rc} (진행 로그 {anchor_log})"
    try:
        with open(s_out, encoding="utf-8") as f:
            d = json.load(f)
    except Exception as e:                                  # noqa: BLE001
        return None, f"요약 파싱 실패 {type(e).__name__}: {e}"
    return d, None


def arm_stats(summary, arm):
    """요약 JSON 에서 arm 의 폴드 평균·폴드 목록·테스트 창을 뽑는다."""
    for r in summary.get("results") or []:
        if r.get("exp") == arm and r.get("status") == "ok":
            folds = r.get("folds") or {}
            means = [folds[k]["mean"] for k in sorted(folds)]
            windows = [(folds[k].get("test_from"), folds[k].get("test_to"))
                       for k in sorted(folds)]
            return {"mean": r.get("auc_mean"), "std": r.get("auc_std"),
                    "folds": means, "windows": windows,
                    "n_folds": len(means)}
    return None


def main():
    ap = argparse.ArgumentParser(description="스윕 arm 앵커 잡음 측정(CG200)")
    ap.add_argument("--panel", required=True)
    ap.add_argument("--arms", required=True, help="대조군,가설arm (쉼표 구분, 순서 중요)")
    ap.add_argument("--asofs", required=True, help="앵커 날짜 쉼표 구분(YYYY-MM-DD)")
    ap.add_argument("--folds", type=int, default=5)
    ap.add_argument("--seeds", type=int, default=5)
    ap.add_argument("--summary-out", required=True)
    ap.add_argument("--work-dir", default="/app/reports/overnight/anchor_sweep")
    ap.add_argument("--tag", default="cg200")
    ap.add_argument("--timeout-sec", type=int, default=2400, help="앵커 1회 타임아웃")
    ap.add_argument("--no-resume", action="store_true",
                    help="앵커 단위 재개를 끈다(기존 앵커 요약 JSON 을 무시하고 전부 재실행)")
    # 유니버스 옵션 패스스루(패널 재사용 시엔 무시된다 — 새 패널 빌드에서만 유효)
    ap.add_argument("--days", type=int, default=None)
    ap.add_argument("--limit", type=int, default=None)
    ap.add_argument("--market", default=None)
    ap.add_argument("--since", default=None)
    ap.add_argument("--min-days", type=int, default=None)
    ap.add_argument("--min-value", type=float, default=None)
    ap.add_argument("--order", default=None)
    args = ap.parse_args()

    arms = [a.strip() for a in args.arms.split(",") if a.strip()]
    if len(arms) != 2:
        print(f"--arms 는 대조군,가설arm 2개여야 한다: {arms}", flush=True)
        return 2
    ctl, arm = arms
    anchors = [a.strip() for a in args.asofs.split(",") if a.strip()]
    os.makedirs(args.work_dir, exist_ok=True)

    out = {"metric": "protocol_pair_anchor", "family": "wf_sweep_anchor",
           "anchors": {f"a{i}": a for i, a in enumerate(anchors)},
           "arms": {"ctl": ctl, "arm": arm}, "pair": f"{arm}-{ctl}",
           "config": (f"wf_label_sweep --asof-date(오른쪽 역산 격자) folds={args.folds} "
                      f"seeds={args.seeds} panel={os.path.basename(args.panel)}"),
           "pre_registered_threshold": THRESH,
           "auc": {}, "folds": {}, "windows": {}, "errors": {}}
    stats = {}
    for a in anchors:
        summary, err = run_anchor(a, arms, args, args.tag)
        if err:
            out["errors"][a] = err
            continue
        for name, arm_id in (("ctl", ctl), ("arm", arm)):
            s = arm_stats(summary, arm_id)
            if s is None:
                out["errors"][f"{a}/{arm_id}"] = "arm 결과 없음(status!=ok)"
                continue
            stats[(a, name)] = s
            out["auc"][f"{a}/{name}"] = s["mean"]
            out["folds"][f"{a}/{name}"] = s["folds"]
            out["windows"][f"{a}/{name}"] = s["windows"]

    if out["errors"]:
        out["verdict"] = "판정불가(런 실패 — 로그 확인)"
        out["error_summary"] = out["errors"]
        _write(out, args.summary_out)
        return 1

    # ── ① 앵커가 실제로 움직였는가(전 폴드 창) ────────────────────────────────
    def _fresh(a, name):
        return [(str(x[0]), str(x[1])) for x in stats[(a, name)]["windows"]]
    ctl_sets = {a: _fresh(a, "ctl") for a in anchors}
    uniq = {tuple(v) for v in ctl_sets.values()}
    anchor_moved = len(uniq) == len(anchors)
    out["anchor_moved"] = anchor_moved
    out["anchor_windows"] = {a: ctl_sets[a] for a in anchors}
    # 실효 앵커 = 실행이 실제로 쓴 마지막 테스트 라벨 날짜. 요청 앵커가 **라벨 유효 마지막 날짜**
    # (패널 끝 − h 거래일)보다 뒤면 절단이 아무것도 하지 않아 실효 앵커가 같아진다(스모크 실측
    # 2026-11-12: 패널 끝 11-09·h=5 → 라벨 유효 11-02, 요청 11-05/11-06 둘 다 실효 11-02 = 동일).
    out["effective_anchor"] = {a: max((str(x[1]) for x in ctl_sets[a]), default=None)
                               for a in anchors}

    # ── ② 앵커별 레벨·짝 Δ ───────────────────────────────────────────────────
    level = {n: [stats[(a, n)]["mean"] for a in anchors] for n in ("ctl", "arm")}
    out["level_mean"] = {n: round(st.fmean(level[n]), 4) for n in level}
    out["level_std"] = {n: round(st.pstdev(level[n]), 4) for n in level if len(level[n]) > 1}
    pd_ = {a: round(stats[(a, "arm")]["mean"] - stats[(a, "ctl")]["mean"], 4) for a in anchors}
    pdm = st.fmean(pd_.values())
    pds = st.pstdev(pd_.values()) if len(pd_) > 1 else 0.0
    out["pair_delta"] = pd_
    out["pair_delta_mean"] = round(pdm, 4)
    out["pair_delta_std"] = round(pds, 4)
    out["anchor_threshold_pass"] = [f"{a}:{'O' if pd_[a] >= THRESH else 'x'}" for a in anchors]
    out["anchors_over_threshold"] = sum(1 for v in pd_.values() if v >= THRESH)
    out["sign_consistent"] = (all(v > 0 for v in pd_.values())
                              or all(v < 0 for v in pd_.values()))
    out["fold_win_rate"] = {
        a: round(sum(1 for x, y in zip(stats[(a, "arm")]["folds"],
                                      stats[(a, "ctl")]["folds"]) if x > y)
                 / max(1, len(stats[(a, "arm")]["folds"])), 2) for a in anchors}

    # ── ③ 사전등록 판정 ──────────────────────────────────────────────────────
    if not anchor_moved:
        out["verdict"] = ("측정무효(앵커 미이동 — 앵커별 폴드 테스트 창이 동일하다. "
                          "요청 앵커 → 실효 앵커 매핑이 전부 같은 값인지 확인하라: "
                          f"{out['effective_anchor']}. 라벨 유효 마지막 날짜(패널 끝 − h 거래일)보다 "
                          "뒤의 앵커는 절단이 무효다 = CG199 재발)")
    elif len(anchors) < 2:
        out["verdict"] = "판정불가(앵커 1개 — std 계산 불가)"
    elif abs(pdm) < pds:
        out["verdict"] = (f"앵커 잡음과 구분 불가(|Δ평균| {pdm:+.4f} < 앵커 std {pds:.4f}) "
                          f"→ 이 델타는 축 종결 근거로만 쓴다")
    elif pdm >= THRESH and out["sign_consistent"]:
        out["verdict"] = (f"신호 후보(앵커평균 Δ {pdm:+.4f} ≥ +{THRESH}, 부호 일관, "
                          f"문턱통과 {out['anchors_over_threshold']}/{len(anchors)})")
    elif pdm <= -THRESH:
        out["verdict"] = f"부호 불안정(앵커평균 Δ {pdm:+.4f} ≤ -{THRESH}) → 축 종결"
    else:
        out["verdict"] = (f"노이즈(앵커평균 Δ {pdm:+.4f} < +{THRESH}; "
                          f"|Δ| vs 앵커 std {pds:.4f})")
    _write(out, args.summary_out)
    return 0


def _write(out, path):
    d = os.path.dirname(path)
    if d:
        os.makedirs(d, exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, indent=2)
    print(json.dumps({k: out.get(k) for k in
                      ("verdict", "anchors", "pair_delta", "pair_delta_mean",
                       "pair_delta_std", "anchor_moved")}, ensure_ascii=False), flush=True)


if __name__ == "__main__":
    sys.exit(main())

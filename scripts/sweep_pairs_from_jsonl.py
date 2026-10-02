#!/usr/bin/env python3
"""구간 짝 검정 복원기 — wf_label_sweep.jsonl 에서 arm/대조군 짝 Δ 를 직접 계산한다.

WHY (2026-10-02 실측): CG69(배포가능 arm 5구간 짝 검정)는 250 cell 스윕인데, 외부 에이전트 4개가
같은 4코어를 쓰면서 cell 속도가 10 s → 60~90 s 로 10배 감속했다(실측 22:20). 컨테이너 `timeout 5400`
이 걸리면 summary JSON 이 없어 구동기는 '실행실패·측정값 없음'으로 기록하고, **완주한 config 의 값도
통째로 사라진다**(jsonl 은 config 단위로 append 되므로 데이터 자체는 살아 있다).

이 스크립트는 그 jsonl 을 읽어 **(exp, 대조군) 짝 Δ** 를 계산한다 — 요약 파일이 유실돼도 증거를
회수하기 위한 도구다. 판정 문턱은 구동기 judge_per 의 pairs 분기와 동일(짝 Δ 평균 ≥ +0.02 이고
양(+) 구간 ≥ n−1).

사용:  python3 scripts/sweep_pairs_from_jsonl.py \
         --jsonl services/xgboost-ml/reports/overnight/wf_label_sweep.jsonl \
         --since 2026-10-02T13:02 \
         --pair SD1s_00_30:US_00_30 --pair SD1s_30_60:US_30_60 ...
       (--pair 생략 시 --prefix 로 자동 짝지음: prefix 로 시작하는 arm/대조군을 구간 라벨로 매칭)
"""
import argparse
import json
import sys


def load(path, since=None):
    out = {}
    for line in open(path):
        line = line.strip()
        if not line:
            continue
        try:
            rec = json.loads(line)
        except json.JSONDecodeError:
            continue
        if since and (rec.get('ts') or '') < since:
            continue
        if rec.get('status') != 'ok' or rec.get('auc_mean') is None:
            continue
        out[rec['exp']] = rec          # 같은 exp 가 여러 번이면 마지막(=최신) 사용
    return out


def folds(rec):
    return {k: v['mean'] for k, v in (rec.get('folds') or {}).items() if isinstance(v, dict) and 'mean' in v}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--jsonl', required=True)
    ap.add_argument('--since', default=None, help='이 시각(UTC, ISO) 이후 기록만')
    ap.add_argument('--pair', action='append', default=[], help='arm:control')
    ap.add_argument('--arm-prefix', default=None)
    ap.add_argument('--ctl-prefix', default=None)
    ap.add_argument('--json-out', default=None)
    a = ap.parse_args()

    recs = load(a.jsonl, a.since)
    pairs = list(a.pair)
    if not pairs and a.arm_prefix and a.ctl_prefix:
        arms = sorted(k for k in recs if k.startswith(a.arm_prefix))
        for arm in arms:
            label = arm[len(a.arm_prefix):]
            ctl = a.ctl_prefix + label
            if ctl in recs:
                pairs.append('%s:%s' % (arm, ctl))

    if not pairs:
        print('짝 없음 — 완료된 config: %s' % sorted(recs))
        return 2

    deltas, rows = [], []
    for p in pairs:
        arm, ctl = p.split(':')
        if arm not in recs or ctl not in recs:
            rows.append((arm, ctl, None, None, None, '미완(한쪽 config 없음)'))
            continue
        fa, fc = folds(recs[arm]), folds(recs[ctl])
        common = sorted(set(fa) & set(fc))
        d = [round(fa[f] - fc[f], 4) for f in common]
        if not d:
            rows.append((arm, ctl, recs[arm]['auc_mean'], recs[ctl]['auc_mean'], None, '폴드 없음'))
            continue
        mean_d = sum(d) / len(d)
        deltas.append((mean_d, d))
        rows.append((arm, ctl, recs[arm]['auc_mean'], recs[ctl]['auc_mean'], d, 'ok'))

    print('%-16s %-16s %8s %8s  %s' % ('arm', 'control', 'arm_auc', 'ctl_auc', 'fold_deltas'))
    for arm, ctl, aa, cc, d, note in rows:
        print('%-16s %-16s %8s %8s  %s  %s' % (arm, ctl,
              '-' if aa is None else round(aa, 4), '-' if cc is None else round(cc, 4), d, '' if note == 'ok' else note))

    if not deltas:
        return 3
    means = [m for m, _ in deltas]
    avg = sum(means) / len(means)
    pos = sum(1 for m in means if m > 0)
    n = len(means)
    verdict = '신호있음' if (avg >= 0.02 and pos >= n - 1) else '노이즈/불충분'
    print('\n짝 Δ 평균 %.4f · 양(+) %d/%d · 구간별 %s → 판정: %s (사전문턱 +0.02, 양(+) ≥ n−1)'
          % (avg, pos, n, [round(m, 4) for m in means], verdict))
    print('주의: 완주한 짝만 반영됨(n=%d). 미완 짝이 있으면 그 수를 함께 보고하라.' % n)
    if a.json_out:
        json.dump({'pairs': [{'arm': r[0], 'control': r[1], 'arm_auc': r[2], 'ctl_auc': r[3],
                              'fold_deltas': r[4], 'note': r[5]} for r in rows],
                   'mean_delta': round(avg, 4), 'positive': pos, 'n_pairs': n, 'verdict': verdict},
                  open(a.json_out, 'w'), ensure_ascii=False, indent=1)
        print('wrote', a.json_out)
    return 0


if __name__ == '__main__':
    sys.exit(main())

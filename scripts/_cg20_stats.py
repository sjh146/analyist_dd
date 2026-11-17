#!/usr/bin/env python3
"""CG20 폴드 짝 Δ 통계(판정 보조 계산)."""
import json, statistics
from math import comb

rows = [json.loads(l) for l in open('/home/jhshi/analyist_dd/data/reports/model_engineer_ledger.jsonl') if l.strip()]
r = [x for x in rows if x.get('id') == 'CG20'][-1]
per = r['parsed']['per_exp']
a, c, off = per['CO_q05_h5']['folds'], per['CO_core30_h5']['folds'], per['LS_quant_q05_h5']['folds']
d = [round(x - y, 4) for x, y in zip(a, c)]
print('folds n =', len(a))
print('paired Δ(q05-q30) =', d)
print('mean %.4f | median %.4f | std %.4f | min %.4f | max %.4f | 양(+) %d/%d' % (
    statistics.mean(d), statistics.median(d), statistics.stdev(d), min(d), max(d),
    sum(1 for x in d if x > 0), len(d)))
p = sum(comb(10, k) for k in (9, 10)) / 2 ** 10 * 2
print('sign test (양측, 9/10) p = %.4f' % p)
print('레벨: q05 %.4f±%.4f | q30 %.4f±%.4f | Δ %.4f || 게이트 OFF q05 %.4f±%.4f (같은 런)' % (
    per['CO_q05_h5']['mean'], per['CO_q05_h5']['std'], per['CO_core30_h5']['mean'],
    per['CO_core30_h5']['std'], per['CO_q05_h5']['mean'] - per['CO_core30_h5']['mean'],
    per['LS_quant_q05_h5']['mean'], per['LS_quant_q05_h5']['std']))
print('등록 기준선 0.5406 대비 Δ =', round(per['CO_q05_h5']['mean'] - 0.5406, 4),
      '| 문턱(0.5606) 대비 =', round(per['CO_q05_h5']['mean'] - 0.5606, 4))
print('CG19(5폴드 0.5575) → CG20(10폴드) Δ =', round(per['CO_q05_h5']['mean'] - 0.5575, 4))
print('게이트 비용(같은 런, q0.05): %.4f' % (per['CO_q05_h5']['mean'] - per['LS_quant_q05_h5']['mean']))

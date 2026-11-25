import json
P = 'data/reports/model_engineer_ledger.jsonl'
rows = [json.loads(l) for l in open(P)]
n = 0
for r in rows:
    if not r.get('reported') and r.get('id') in ('CG466', 'CG197'):
        r['reported'] = True
        r['reported_at'] = '2026-11-25T07:48:00+09:00'
        n += 1
with open(P, 'w') as f:
    for r in rows:
        f.write(json.dumps(r, ensure_ascii=False) + '\n')
print('marked reported:', n, 'rows:', len(rows))

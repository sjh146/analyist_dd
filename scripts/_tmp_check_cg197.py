import json
led = [json.loads(l) for l in open('data/reports/model_engineer_ledger.jsonl')]
for r in led[-3:]:
    print(r['ts'], r['id'], 'rc=', r.get('rc'), r.get('verdict'), '|', str(r.get('detail'))[:400])
b = json.load(open('docs/QUANT_MODEL_BACKLOG.json'))
items = b['items'] if isinstance(b, dict) else b
for i in items:
    if i.get('id') in ('CG197', 'CG466'):
        print('==', i['id'], i.get('status'), i.get('result'))

import json, os
P='docs/QUANT_MODEL_BACKLOG.json'
b=json.load(open(P))
for it in b['items']:
    if it['id']=='CG418':
        it['command']=it['command'].replace('timeout 900 ','timeout 1200 ')
        print('fixed:', it['command'][:120], '...')
        assert 'timeout 1200 ' in it['command']
tmp=P+'.tmp'
json.dump(b, open(tmp,'w'), ensure_ascii=False, indent=1)
os.replace(tmp,P)

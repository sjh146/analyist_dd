import json, os, datetime
P='docs/QUANT_MODEL_BACKLOG.json'
b=json.load(open(P))
by={it['id']:it for it in b['items']}
it=by['CG418']
it['status']='done'
it['verdict']=("노이즈(타이밍 축 종결) — 사전 게이트 ON 61세션에서 Δ(풀) +1.258%p(t 1.71)로 문턱(Δ≥+1.0)은 넘고 "
               "t≥2 미달, 결정적으로 ON 대역의 **절대 순기대가 −1.478%p/세션**(t −2.76) — 게이트를 켜도 사면 잃는다. "
               "하락 국면의 상대 우위(+5.1%p)는 살 수 있는 규칙으로 바꾸면 사라진다.")
it['detail']=("trailing_pool d5 문턱 0% · 채점 110/125세션 · ON 61세션 풀 −2.736(t −3.31) → top5 −1.478(t −2.76, Δ(풀) +1.258 t 1.71, "
              "분할 −1.48/−1.48 neither) · OFF 49세션 풀 −1.416 → top5 +1.695(t 1.79, Δ(풀) +3.111) · "
              "게이트가 반대로 작동(과거 하락 뒤에도 계속 하락) · 사전문턱 ON Δ≥+1.0 & t≥2 & ON≥40 → t 미달 · "
              "→ '국면 방어'는 사후 분해에서만 보이는 성질. 롱 진입 규칙으로 쓸 수 없다.")
ts=datetime.datetime.now().astimezone().isoformat(timespec='seconds')
with open('data/reports/model_engineer_ledger.jsonl','a',encoding='utf-8') as f:
    f.write(json.dumps({"ts":ts,"id":"CG418","title":it['title'],"rc":0,"elapsed_min":0.1,
        "metric":"fillable_topk_vs_pool","verdict":it['verdict'],"detail":it['detail'],
        "mode":"manual-followup","reported":True,
        "summary_out":"/app/reports/overnight/cg418_gate_full.json"}, ensure_ascii=False)+"\n")
tmp=P+'.tmp'
json.dump(b, open(tmp,'w'), ensure_ascii=False, indent=1)
os.replace(tmp,P)
print(it['status'], it['verdict'][:90])

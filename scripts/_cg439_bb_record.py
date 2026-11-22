import json

PATH = "docs/QUANT_MODEL_BACKLOG.json"
d = json.load(open(PATH, encoding="utf-8"))
items = d["items"] if isinstance(d, dict) and "items" in d else d
cg = next(i for i in items if i["id"] == "CG439")
cg["result"]["per_exp"]["block_boot_t_by_seed"] = [1.01, -0.20, 0.56, 3.54, 1.31]
cg["result"]["per_exp"]["block_boot_p_one_sided"] = [0.1653, 0.5603, 0.3045, 0.0, 0.1125]
cg["result"]["per_exp"]["block_boot_delta_vs_control_t"] = [0.50, 0.80, 1.23, 1.96, 1.28]
cg["evidence"] += (
    " | 겹침 보정(이동블록 부트스트랩, 블록=h=5, 4,000회): vs 풀평균 t 1.01/−0.20/0.56/3.54/1.31"
    "(seed3 만 p<0.001, 나머지 p 0.11~0.56) · vs retrain Δ평균 +1.297 이나 블록 t≥2 시드 0/5 → "
    "iid t 를 써도 결론(단일 시드 지배)은 바뀌지 않는다. 단 stride=5 대안은 세션 18→4 로 검정력이 붕괴하므로 "
    "앞으로 h>1 돈 지표는 블록 부트스트랩 t 를 기본 병기한다(fillable_topk_expectancy 'overlap_boot')."
)
with open(PATH, "w", encoding="utf-8") as f:
    json.dump(d, f, ensure_ascii=False, indent=2)
    f.write("\n")
print("CG439 evidence 갱신 · 항목", len(items))

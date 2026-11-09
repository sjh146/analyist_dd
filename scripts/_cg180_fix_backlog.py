import json, io, sys

P = "/home/jhshi/analyist_dd/docs/QUANT_MODEL_BACKLOG.json"
d = json.load(open(P, encoding="utf-8"))
items = d["items"] if isinstance(d, dict) else d

CMD = ("docker exec stock_xgboost_ml sh -c 'cd /app && timeout 5400 sh scripts/cg180_rebaseline.sh'")

changed = []
for it in items:
    if it.get("id") == "CG180":
        it["metric"] = "robust_oos_audit"
        it["model_label"] = "champ"
        it["command"] = CMD
        it["status"] = "pending"
        it["est_minutes"] = 60
        it["command_verified_at"] = ("2026-11-10T00:10+09:00 (셸 4단 인용 문제 수리: 래퍼 "
                                     "scripts/cg180_rebaseline.sh · --skip a,b,c 프로브 rc=0 · "
                                     "metric kind robust_oos_audit 배선)")
        it["note"] = (it.get("note", "") +
                      " | 2026-11-10 00:0x 수리: 종전 command 는 --models JSON 을 4단 셸에 직접 넣어 "
                      "`[[` 가 셸 문법으로 해석 → json.JSONDecodeError(char 2) rc=1 즉사(원장 실행실패). "
                      "①래퍼 스크립트로 인용 단일화 ②구동기에 metric kind 'robust_oos_audit' 신설"
                      "(--out 파서·judge_champion_baseline+돈지표). 프로브: --skip a,b,c → rc=0, "
                      "champ 173피처·train 2026-07-03~2026-10-01 로드 확인.")
        changed.append("CG180")
    if it.get("id") == "CG183":
        est = it.get("est_minutes") or 0
        need = int(est * 60 * 1.15) + 600
        cmd = it.get("command", "")
        if "timeout 5400" in cmd and need > 5400:
            it["command"] = cmd.replace("timeout 5400", f"timeout {need}")
            it["timeout_fixed_at"] = "2026-11-10T00:10+09:00"
            changed.append(f"CG183 timeout 5400→{need} (est {est}분)")

json.dump(d, open(P, "w", encoding="utf-8"), ensure_ascii=False, indent=2)
print("변경:", changed)

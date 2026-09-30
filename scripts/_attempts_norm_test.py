"""회귀 테스트: 백로그 `attempts` 스키마(int)가 사이클을 죽이지 않는가.

실측(2026-10-01 00:02 CG38): `"attempts": 0`(정수)인 항목에서
`AttributeError: 'int' object has no attribute 'append'` 로 `--run` 프로세스가 사망 —
원장에는 rc=0 기록이 남았는데 백로그는 pending 인 '반쪽 상태'(다음 틱이 이미 끝난
46분짜리 실험을 재실행)가 됐다. 대책 2종: ①`_attempts_list()` 정규화
②백로그 갱신 블록을 try/except 로 감싸 원장 기록을 보존.

pytest 는 이 스택에 없다(2026-09-29 확인) — PASS/FAIL 자체점검 + sys.exit 형태로 쓴다.
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import model_engineer_cycle as m  # noqa: E402

HERE = os.path.dirname(os.path.abspath(__file__))
fails = []


def check(name, cond, extra=""):
    print(("PASS " if cond else "FAIL ") + name + (f" — {extra}" if extra else ""))
    if not cond:
        fails.append(name)


# ① 정규화: int 0 (실측 사고의 입력값 그대로)
it = {"id": "X", "attempts": 0}
got = m._attempts_list(it)
check("int 0 → 빈 리스트", isinstance(it["attempts"], list) and it["attempts"] == []
      and got is it["attempts"])

# ② None → 빈 리스트
it = {"id": "X"}
m._attempts_list(it)
check("None → 빈 리스트", it["attempts"] == [])

# ③ 기존 리스트는 보존(정상 항목 회귀 없음)
prev = [{"ts": "2026-01-01T00:00:00+09:00", "rc": 0}]
it = {"id": "X", "attempts": prev}
check("리스트 보존(원소 그대로)", m._attempts_list(it) == prev and it["attempts"] is prev)

# ④ int>0 은 유실하지 않고 보존 기록
it = {"id": "X", "attempts": 2}
m._attempts_list(it)
check("int 2 → 리스트화 + attempts_legacy 보존",
      it["attempts"] == [] and it.get("attempts_legacy") == 2)

# ⑤ 사고 재현: int 스키마 항목에 append 가 실제로 성공해야 한다
it = {"id": "X", "attempts": 0}
m._attempts_list(it).append({"ts": "now"})
check("int 스키마에서 append 성공(사고 재현 불가)", len(it["attempts"]) == 1)

# ⑥ RETRY_MAX 판정에 쓰이는 len() 이 정상 동작(정규화 후)
it = {"id": "X", "attempts": 0}
check("len(attempts) 사용 가능", len(m._attempts_list(it)) == 0)

src = open(os.path.join(HERE, "model_engineer_cycle.py"), encoding="utf-8").read()
import re  # noqa: E402

# ⑦ 회귀 방지: 코드 위치에 setdefault("attempts") 패턴이 남아 있지 않아야 한다
#    (int 스키마에서 다시 터진다). docstring 안의 인용(백틱으로 시작)은 제외한다.
code_use = re.search(r'^\s*[a-z_][a-z_0-9]*\s*\.setdefault\(\s*["\']attempts', src, re.M)
check('코드에 setdefault("attempts" 없음', code_use is None,
      code_use.group(0) if code_use else "")

# ⑧ 백로그 갱신 블록이 감싸져 있는가(원장만 남고 pending 되는 반쪽 상태 방지)
check("백로그 갱신 실패 경고 문구 존재", "경고: 백로그 갱신 실패" in src)

# ⑨ ingest 경로도 같은 정규화를 쓰는가
check("ingest 경로도 _attempts_list 사용", "            _attempts_list(x).append({" in src)

print("--- 전부 PASS" if not fails else f"--- {len(fails)} FAIL: {fails}")
sys.exit(1 if fails else 0)

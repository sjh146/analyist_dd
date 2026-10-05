"""CCPROBE — Claude Code 단독 저작(파일 쓰기) 검증 프로브.

WHY (실측 근거): 2026-10-05 build 모드에서 Claude Code 비대화형(-p) 실행이 파일을
만들지 못해 위임 소득 0 이었다(docs/spec_CCPROBE.md, 실제 조회: cat docs/spec_CCPROBE.md;
커밋 901194d "위임 실패 원인 수리: build 시 Claude Code 비대화형 Write 를 실제로
실행(권한 스킵)" — 세션 git 스냅샷에서 확인). ask_claude.sh 권한 스킵 후 opencode 폴백
차단 상태에서, 이 파일의 생성 자체가 Claude Code 단독 저작의 증거가 된다.

실행 검증: python3 -m py_compile scripts/_cc_write_probe.py && python3 scripts/_cc_write_probe.py
표준 라이브러리만 사용하며 부수 효과는 stdout 한 줄뿐이다(재실행 안전·멱등).
"""

print("CCPROBE_OK")

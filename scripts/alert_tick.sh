#!/usr/bin/env bash
# ~/.hermes/scripts/alert_tick.sh — Prometheus 알림 전달 틱(크론, no_agent).
#
# Alertmanager 가 없어 규칙 20개가 평가만 되고 전달되지 않던 문제의 해결(R15).
# 변화가 있을 때만 출력 → 크론이 그때만 사용자에게 보낸다(소음 없음).
# 종료코드는 항상 0 — 점검 스크립트의 코드를 그대로 두면 크론이 잡 실패로 오해한다(위생과 동일 규칙).
/usr/bin/python3 /home/jhshi/analyist_dd/scripts/alert_dispatch.py
exit 0

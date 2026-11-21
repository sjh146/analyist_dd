#!/bin/bash
# 추가 스왑 보장 (2026-11-21) — WSL 기본 swap 2GB 만으로는 ML 주기 실행 때 꽉 차서
# 게이트웨이 이벤트 루프가 굶고 liveness watchdog 이 exit 75 로 죽는다(실측: mem_available 162MB).
# .wslconfig(memory=12GB/swap=8GB)는 wsl --shutdown 후에만 적용되므로, 그 전에도 버티도록 4GB 를 즉시 붙인다.
set -u
SWAPFILE=/swapfile2
if swapon --show=NAME --noheadings 2>/dev/null | grep -qx "$SWAPFILE"; then
  exit 0
fi
if [ ! -f "$SWAPFILE" ]; then
  fallocate -l 4G "$SWAPFILE" 2>/dev/null || dd if=/dev/zero of="$SWAPFILE" bs=1M count=4096 status=none
  chmod 600 "$SWAPFILE"
  mkswap "$SWAPFILE" >/dev/null 2>&1
fi
swapon "$SWAPFILE" 2>/dev/null && echo "swap added: $SWAPFILE"
exit 0

#!/bin/sh

# WSL2 休眠恢复后尝试用硬件时钟校正系统时钟。
# 无 RTC、无 hwclock 或权限不足时保持 best-effort，不让 oneshot 进入失败态。
/sbin/hwclock --hctosys || hwclock -s || true

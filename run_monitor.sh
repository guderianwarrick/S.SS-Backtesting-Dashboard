#!/bin/bash
# Cron wrapper: 确保 xray 代理在跑 → 跑监控脚本
# xray 不在就拉起，已在跑就跳过

set -euo pipefail

# 检查 SOCKS5 端口是否在监听
if ! ss -tlnp 2>/dev/null | grep -q ':10808'; then
    echo "[startup] xray not running, starting..." >&2
    nohup /usr/local/bin/xray run -config /usr/local/etc/xray/config.json \
        > /var/log/xray-cron.log 2>&1 &
    sleep 3
    if ss -tlnp 2>/dev/null | grep -q ':10808'; then
        echo "[startup] xray started OK" >&2
    else
        echo "[startup] xray failed to start, abort" >&2
        exit 1
    fi
fi

# 跑监控（stderr 含 debug 日志，丢弃；stdout = 飞书消息）
cd /root/stock-sentiment
python3 monitor.py 2>/dev/null

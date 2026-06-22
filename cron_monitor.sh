#!/bin/bash
# 每日监控 @aleabitoreddit 新推文

set -e

cd /root/stock-sentiment

# 启动代理（如果没运行）
if ! ss -tlnp | grep -q ":10808"; then
    xray run -config /usr/local/etc/xray/config.json >/tmp/xray_monitor.log 2>&1 &
    sleep 3
fi

# 抓取最新推文
python3 scrape_cookie.py aleabitoreddit

# 导入新增并分析
python3 import_search_timeline.py aleabitoreddit

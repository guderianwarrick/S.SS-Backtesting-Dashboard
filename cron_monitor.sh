#!/bin/bash
# 每日监控 @aleabitoreddit 新推文 + 重新生成仪表盘

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

# 运行完整管线（增量更新）
python3 pipeline.py --step 2,3,4

# 重新生成静态仪表盘
python3 generate_site.py

# 提交到 GitHub 触发 EdgeOne 重新部署
git add index.html
git commit -m "Auto-update dashboard $(date +%Y-%m-%d)" || true
git push origin main 2>/dev/null || echo "Push skipped (maybe no changes)"
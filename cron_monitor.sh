#!/bin/bash
# 每日监控 @aleabitoreddit 新推文 + 重新生成仪表盘
# 由 crontab 触发

set -e

# Cron 环境需要显式设置 PATH
export PATH="/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin:/root/.local/bin:/root/.hermes/node/bin"

cd /root/stock-sentiment

# 加载环境变量（API Key 等）
set -a; source /root/.hermes/.env 2>/dev/null; set +a

# 启动代理（如果没运行）
if ! ss -tlnp | grep -q ":10808"; then
    xray run -config /usr/local/etc/xray/config.json >/tmp/xray_monitor.log 2>&1 &
    sleep 3
fi

# 抓取最新推文
python3 scrape_cookie.py aleabitoreddit || true

# 导入新增并分析
python3 import_search_timeline.py aleabitoreddit || true

# 运行完整管线（增量更新）
python3 pipeline.py --step 2,3,4 || true

# 更新 QQQ 基准数据
python3 -c "from portfolio.price_fetcher import PriceFetcher; from datetime import date; PriceFetcher().extend_cache('QQQ', target_start=date(2025,6,1))" || true

# 重新生成静态仪表盘
python3 generate_site.py || true

# 提交到 GitHub 触发 EdgeOne 重新部署
git add index.html
git add -A 2>/dev/null
git commit -m "Auto-update dashboard $(date +%Y-%m-%d)" 2>/dev/null || true
git push origin main 2>/dev/null || echo "Push skipped"
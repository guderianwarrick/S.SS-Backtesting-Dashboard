"""全局配置管理"""
import os
from pathlib import Path
from dotenv import load_dotenv

# 加载环境变量
load_dotenv()

# 项目根目录
BASE_DIR = Path(__file__).parent.resolve()
DATA_DIR = BASE_DIR / "data"
DATA_DIR.mkdir(exist_ok=True)

# 代理配置（访问外网）
HTTPS_PROXY = os.getenv("HTTPS_PROXY", "")
HTTP_PROXY = os.getenv("HTTP_PROXY", "")

# SOCKS5 代理（yfinance/yahoo chart 访问墙外 API）
SOCKS5_PROXY = os.getenv("SOCKS5_PROXY", "socks5h://127.0.0.1:10808")
SOCKS5_ENABLED = os.getenv("SOCKS5_ENABLED", "1") == "1"

# X API 配置
X_BEARER_TOKEN = os.getenv("X_BEARER_TOKEN", "")
X_API_BASE = "https://api.x.com/2"
X_USER_AGENT = "v2UserTweetsPython"

# 业务参数
TARGET_USERNAME = "aleabitoreddit"   # 目标博主用户名
RECENT_DAYS = 3          # 最近几天内提及的股票纳入分析
MAX_RESULTS_PER_REQUEST = 100
DEFAULT_MAX_TWEETS = 200

# Agent Browser 配置
AB_SESSION_NAME = "x-stock-scraper"  # 持久化会话名（保存 cookie）
AB_PROFILE_DIR = str(DATA_DIR / "browser_profile")  # 浏览器 profile 目录

# AI 配置
LLM_MODEL = "gpt-4o"
TEMPERATURE = 0.2

# 存储
DB_PATH = DATA_DIR / "tweets.db"

# Alpha Vantage API（多源价格拉取备用）
ALPHA_VANTAGE_API_KEY = os.getenv("ALPHA_VANTAGE_API_KEY", "")

# Browserbase 云端浏览器（X 推文抓取备用）
# 注册地址: https://browserbase.com
BROWSERBASE_API_KEY = os.getenv("BROWSERBASE_API_KEY", "")
BROWSERBASE_PROJECT_ID = os.getenv("BROWSERBASE_PROJECT_ID", "")

# 投资组合配置
PORTFOLIO_INITIAL_CASH = 1_000_000.0      # 初始资金（$1,000,000）
PORTFOLIO_START_DATE = "2025-09-01"        # 回测起始日期（覆盖最早推文）
PORTFOLIO_MIN_STOCKS = 1                   # 最少持仓数
PORTFOLIO_MAX_WEIGHT = 0.40                # 单只股票最大权重（防止过度集中）
SCORE_HALF_LIFE_DAYS = 14                  # 情绪分指数衰减半衰期（天）

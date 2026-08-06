"""全局配置：从 .env 加载密钥与参数，统一提供路径常量。"""

import os
from pathlib import Path

from dotenv import load_dotenv

# 项目根目录（app/ 的上一级）
BASE_DIR = Path(__file__).resolve().parent.parent
# override=True：确保项目 .env 优先于系统环境变量（避免旧的环境变量遮蔽新密钥）
load_dotenv(BASE_DIR / ".env", override=True)

# ---- DeepSeek ----
DEEPSEEK_API_KEY = os.getenv("DEEPSEEK_API_KEY", "").strip()
DEEPSEEK_BASE_URL = os.getenv("DEEPSEEK_BASE_URL", "https://api.deepseek.com").strip()
DEEPSEEK_MODEL = os.getenv("DEEPSEEK_MODEL", "deepseek-chat").strip()
# 总结报告使用的模型（可选）：留空则用 DEEPSEEK_MODEL；如 deepseek-reasoner 更深入但更慢更贵
REPORT_MODEL = os.getenv("REPORT_MODEL", "").strip()
LLM_TIMEOUT_SECONDS = float(os.getenv("LLM_TIMEOUT_SECONDS", "120"))
LLM_MAX_RETRIES = int(os.getenv("LLM_MAX_RETRIES", "3"))

# ---- 数据 ----
DATA_DIR = BASE_DIR / "data"
DB_PATH = DATA_DIR / "questions.db"
DB_TIMEOUT_SECONDS = int(os.getenv("DB_TIMEOUT_SECONDS", "10"))

# ---- 定时爬取 ----
CRAWL_TIME = os.getenv("CRAWL_TIME", "02:00").strip()  # 每天几点抓取
CRAWL_INTERVAL_HOURS = int(os.getenv("CRAWL_INTERVAL_HOURS", "24"))  # 间隔小时，0=仅启动时抓一次
SCHEDULER_TZ = os.getenv("SCHEDULER_TZ", "Asia/Shanghai").strip()

# ---- 爬虫 ----
CRAWL_PAGES_PER_CATEGORY = int(os.getenv("CRAWL_PAGES_PER_CATEGORY", "10"))
CRAWL_REQUEST_DELAY = float(os.getenv("CRAWL_REQUEST_DELAY", "0.3"))
CRAWL_WORKERS = int(os.getenv("CRAWL_WORKERS", "3"))
LEETCODE_CACHE_HOURS = int(os.getenv("LEETCODE_CACHE_HOURS", "72"))

# ---- 语音通话服务 ----
VOICE_HOST = os.getenv("VOICE_HOST", "127.0.0.1").strip()
VOICE_PORT = int(os.getenv("VOICE_PORT", "8765"))
VOICE_NAME = os.getenv("VOICE_NAME", "zh-CN-XiaoxiaoNeural").strip()
VOICE_RATE = os.getenv("VOICE_RATE", "+0%").strip()
VOICE_PITCH = os.getenv("VOICE_PITCH", "+2Hz").strip()
VOICE_VAD_THRESHOLD = float(os.getenv("VOICE_VAD_THRESHOLD", "0.045"))
VOICE_TTS = (
    os.getenv("VOICE_TTS", "edge").strip().lower()
)  # edge=微软edge-tts在线神经语音 / cosyvoice=阿里云百炼CosyVoice / local=浏览器本地语音

# ---- 阿里云百炼 CosyVoice（VOICE_TTS=cosyvoice 时使用）----
DASHSCOPE_API_KEY = os.getenv("DASHSCOPE_API_KEY", "").strip()
COSYVOICE_MODEL = os.getenv("COSYVOICE_MODEL", "cosyvoice-v2").strip()
# 龙小淳 v2：温暖甜美的女声；其他可用音色见百炼文档音色列表
COSYVOICE_VOICE = os.getenv("COSYVOICE_VOICE", "longxiaochun_v2").strip()
COSYVOICE_FORMAT = os.getenv("COSYVOICE_FORMAT", "mp3").strip().lower()
COSYVOICE_SAMPLE_RATE = int(os.getenv("COSYVOICE_SAMPLE_RATE", "24000"))
COSYVOICE_RATE = float(os.getenv("COSYVOICE_RATE", "1.0"))
COSYVOICE_PITCH = float(os.getenv("COSYVOICE_PITCH", "1.0"))

# ---- Web 文字版入口（语音通话页"返回文字版"链接用）----
WEB_URL = os.getenv("WEB_URL", "http://localhost:8501").strip().rstrip("/")


def ensure_data_dir() -> None:
    """确保数据目录存在。"""
    DATA_DIR.mkdir(parents=True, exist_ok=True)

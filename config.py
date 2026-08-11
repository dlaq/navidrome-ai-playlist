"""
Navidrome AI 智能歌单生成器 - 配置
"""
import os

# ========== Navidrome 服务器配置 ==========
NAVIDROME_URL = os.environ["NAVIDROME_URL"]
NAVIDROME_USER = os.environ["NAVIDROME_USER"]
NAVIDROME_PASS = os.environ["NAVIDROME_PASS"]

# ========== Web UI 登录密码 ==========
LOGIN_PASSWORD = os.environ["LOGIN_PASSWORD"]

# ========== 服务配置 ==========
HOST = os.getenv("HOST", "0.0.0.0")
PORT = int(os.getenv("PORT", "8899"))

# ========== 搜索配置 ==========
SEARCH_TIMEOUT = int(os.getenv("SEARCH_TIMEOUT", "10"))
MAX_RESULTS_PER_SOURCE = 30

# ========== Navidrome 曲库缓存 ==========
# 默认放在 Docker 的 /data 目录；部署时将该目录挂载到宿主机即可跨容器
# 重建保留曲库索引。
LIBRARY_CACHE_PATH = os.getenv("LIBRARY_CACHE_PATH", "/data/navidrome_library.json")
LIBRARY_CACHE_TTL = int(os.getenv("LIBRARY_CACHE_TTL", "3600"))
LIBRARY_SCAN_PAGE_SIZE = int(os.getenv("LIBRARY_SCAN_PAGE_SIZE", "500"))

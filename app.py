"""
Navidrome AI 智能歌单生成器 - 主应用
"""
import os
import json
import time
import logging
import secrets
import threading
import tempfile
from typing import Any, List, Optional
from pathlib import Path

from fastapi import FastAPI, Request, Response, HTTPException, Form
from fastapi.responses import HTMLResponse, RedirectResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from pydantic import BaseModel

import config
from searchers import search_all, search_all_merged, Song
from navidrome_client import NavidromeClient, NavidromeSong
from cover_generator import generate_cover, THEME_COLORS
from playlist_parser import fetch_playlist_from_url, parse_playlist_url
from matching import deduplicate_songs, match_songs

# 日志配置
logging.basicConfig(level=logging.INFO, format='%(asctime)s [%(levelname)s] %(message)s')
logger = logging.getLogger(__name__)

app = FastAPI(title="Navidrome AI Playlist Generator")

# 模板和静态文件
BASE_DIR = Path(__file__).parent
templates = Jinja2Templates(directory=str(BASE_DIR / "templates"))

# 会话存储（简单 token 方式）
active_sessions = {}

# Navidrome 客户端
navidrome = NavidromeClient(config.NAVIDROME_URL, config.NAVIDROME_USER, config.NAVIDROME_PASS)

# 歌曲库缓存。歌曲元数据保存到 Docker 的 /data 目录，容器重启后可以直接
# 使用旧索引；后台刷新完成前仍允许使用旧索引，避免再次出现“曲库为空”的
# 竞态。首次启动且没有缓存时，匹配接口会明确返回“加载中”，不会伪装成
# 0 首匹配。
library_cache = {
    "songs": [],
    "last_update": 0,
    "loading": False,
    "error": None,
    "source": None,
}
library_lock = threading.RLock()
CACHE_TTL = config.LIBRARY_CACHE_TTL
LIBRARY_CACHE_PATH = Path(config.LIBRARY_CACHE_PATH)


class LibraryNotReadyError(RuntimeError):
    """Raised when matching is requested before the first library scan ends."""


# ==================== 中间件 ====================
def get_session_token(request: Request) -> Optional[str]:
    return request.cookies.get("session_token")

def is_authenticated(request: Request) -> bool:
    token = get_session_token(request)
    if token and token in active_sessions:
        # 检查是否过期（24小时）
        if time.time() - active_sessions[token] < 86400:
            return True
        del active_sessions[token]
    return False

def require_auth(request: Request):
    if not is_authenticated(request):
        raise HTTPException(status_code=401, detail="未登录")


# ==================== 页面路由 ====================
@app.get("/", response_class=HTMLResponse)
async def index(request: Request):
    if is_authenticated(request):
        return RedirectResponse(url="/app")
    return RedirectResponse(url="/login")

@app.get("/login", response_class=HTMLResponse)
async def login_page(request: Request, error: str = ""):
    return templates.TemplateResponse("login.html", {"request": request, "error": error})

@app.post("/login")
async def do_login(request: Request, password: str = Form(...)):
    if password == config.LOGIN_PASSWORD:
        token = secrets.token_hex(32)
        active_sessions[token] = time.time()
        response = RedirectResponse(url="/app", status_code=302)
        response.set_cookie("session_token", token, httponly=True, max_age=86400)
        return response
    return templates.TemplateResponse("login.html", {
        "request": request,
        "error": "密码错误，请重试"
    })

@app.get("/app", response_class=HTMLResponse)
async def app_page(request: Request):
    if not is_authenticated(request):
        return RedirectResponse(url="/login")
    return templates.TemplateResponse("app.html", {"request": request})

@app.get("/logout")
async def logout(request: Request):
    token = get_session_token(request)
    if token and token in active_sessions:
        del active_sessions[token]
    response = RedirectResponse(url="/login")
    response.delete_cookie("session_token")
    return response


# ==================== API 路由 ====================
class SearchRequest(BaseModel):
    query: str
    sources: list = []  # 空列表表示全部

class CreatePlaylistRequest(BaseModel):
    name: str
    song_ids: list
    cover_theme: str = ""  # 封面主题（可选）
    cover_enabled: bool = True  # 是否生成封面

class MatchRequest(BaseModel):
    query: str
    sources: list = []

class PlaylistUrlRequest(BaseModel):
    url: str

@app.get("/api/status")
async def api_status(request: Request):
    require_auth(request)
    connected = navidrome.ping()
    with library_lock:
        library_size = len(library_cache.get("songs", []))
        library_loading = library_cache.get("loading", False)
        last_update = library_cache.get("last_update", 0)
        library_error = library_cache.get("error")
    return {
        "navidrome_connected": connected,
        "library_size": library_size,
        "library_loading": library_loading,
        "library_ready": bool(last_update),
        "library_last_update": last_update,
        "library_error": library_error,
    }

@app.post("/api/search")
async def api_search(req: SearchRequest, request: Request):
    require_auth(request)
    if not req.query.strip():
        raise HTTPException(400, "搜索关键词不能为空")

    logger.info(f"搜索: {req.query}, 来源: {req.sources or '全部'}")

    if req.sources:
        # 指定来源搜索
        from searchers import ALL_SEARCHERS
        results = {}
        for name, searcher in ALL_SEARCHERS:
            if name in req.sources:
                try:
                    songs = searcher(req.query, config.MAX_RESULTS_PER_SOURCE)
                    results[name] = [s.to_dict() for s in songs]
                except Exception as e:
                    logger.error(f"[{name}] 搜索失败: {e}")
                    results[name] = []
        total = sum(len(v) for v in results.values())
        return {"results": results, "total": total, "query": req.query}
    else:
        # 全平台搜索
        from searchers import ALL_SEARCHERS
        results = {}
        for name, searcher in ALL_SEARCHERS:
            try:
                songs = searcher(req.query, config.MAX_RESULTS_PER_SOURCE)
                results[name] = [s.to_dict() for s in songs]
            except Exception as e:
                logger.error(f"[{name}] 搜索失败: {e}")
                results[name] = []
        total = sum(len(v) for v in results.values())
        return {"results": results, "total": total, "query": req.query}

@app.post("/api/match")
async def api_match(req: MatchRequest, request: Request):
    require_auth(request)
    if not req.query.strip():
        raise HTTPException(400, "搜索关键词不能为空")

    # 先确认曲库已可用，首次扫描期间直接提示用户，不浪费时间请求各平台。
    try:
        _get_library()
    except LibraryNotReadyError as e:
        raise HTTPException(status_code=409, detail=str(e))

    # 1. 从各平台搜索
    logger.info(f"匹配搜索: {req.query}")
    from searchers import ALL_SEARCHERS, Song
    all_search_songs = []
    source_stats = {}

    searchers_to_use = ALL_SEARCHERS
    if req.sources:
        searchers_to_use = [(n, s) for n, s in ALL_SEARCHERS if n in req.sources]

    for name, searcher in searchers_to_use:
        try:
            songs = searcher(req.query, config.MAX_RESULTS_PER_SOURCE)
            source_stats[name] = len(songs)
            all_search_songs.extend(songs)
        except Exception as e:
            logger.error(f"[{name}] 搜索失败: {e}")
            source_stats[name] = 0

    # 2. 与 Navidrome 库匹配
    try:
        matched, unmatched, unique_songs = _match_source_songs(all_search_songs)
    except LibraryNotReadyError as e:
        raise HTTPException(status_code=409, detail=str(e))

    return {
        "query": req.query,
        "source_stats": source_stats,
        "search_total": len(unique_songs),
        "matched": matched,
        "matched_count": len(matched),
        "unmatched": unmatched[:50],  # 最多返回50个未匹配
        "unmatched_count": len(unmatched),
    }

@app.post("/api/playlist/from-url")
async def api_playlist_from_url(req: PlaylistUrlRequest, request: Request):
    """从歌单链接获取歌曲并匹配曲库"""
    require_auth(request)
    if not req.url.strip():
        raise HTTPException(400, "链接不能为空")

    try:
        _get_library()
    except LibraryNotReadyError as e:
        raise HTTPException(status_code=409, detail=str(e))

    logger.info(f"解析歌单链接: {req.url}")

    # 1. 从URL获取歌单歌曲
    try:
        playlist_name, url_songs = fetch_playlist_from_url(req.url)
    except ValueError as e:
        raise HTTPException(400, str(e))
    except Exception as e:
        logger.error(f"获取歌单失败: {e}")
        raise HTTPException(500, f"获取歌单失败: {e}")

    if not url_songs:
        raise HTTPException(400, "未能从该链接获取到歌曲")

    # 2. 与 Navidrome 库匹配
    try:
        matched, unmatched, unique_songs = _match_source_songs(url_songs)
    except LibraryNotReadyError as e:
        raise HTTPException(status_code=409, detail=str(e))

    return {
        "playlist_name": playlist_name,
        "source": url_songs[0].source if url_songs else "unknown",
        "search_total": len(unique_songs),
        "matched": matched,
        "matched_count": len(matched),
        "unmatched": unmatched[:50],
        "unmatched_count": len(unmatched),
    }

@app.post("/api/playlist/create")
async def api_create_playlist(req: CreatePlaylistRequest, request: Request):
    require_auth(request)
    if not req.name.strip():
        raise HTTPException(400, "歌单名称不能为空")
    if not req.song_ids:
        raise HTTPException(400, "歌曲列表不能为空")

    logger.info(f"创建歌单: {req.name}, 歌曲数: {len(req.song_ids)}, 封面: {req.cover_enabled}")

    # 生成封面
    cover_data = None
    if req.cover_enabled:
        try:
            theme = req.cover_theme if req.cover_theme else None
            subtitle = f"{len(req.song_ids)} 首歌曲 · AI 生成"
            cover_data = generate_cover(req.name, subtitle=subtitle, theme=theme)
            logger.info(f"封面已生成: {len(cover_data)} bytes")
        except Exception as e:
            logger.warning(f"封面生成失败: {e}")

    result = navidrome.create_playlist(req.name, req.song_ids, cover_data=cover_data)
    if result:
        added_song_ids = result.get("_added_song_ids")
        failed_song_ids = result.get("_failed_song_ids")
        if not isinstance(added_song_ids, list):
            added_song_ids = list(req.song_ids)
        if not isinstance(failed_song_ids, list):
            failed_song_ids = []
        cover_uploaded = bool(result.get("cover_uploaded", False))
        if cover_data is not None and not cover_uploaded:
            logger.warning("歌单已创建，但封面未能上传")
        return {
            "success": True,
            "playlist_id": result.get("id"),
            "playlist_name": result.get("name"),
            "song_count": len(added_song_ids),
            "requested_song_count": len(req.song_ids),
            "failed_song_count": len(failed_song_ids),
            "cover_generated": cover_data is not None,
            "cover_uploaded": cover_uploaded,
        }
    else:
        raise HTTPException(500, "创建歌单失败")

@app.post("/api/cover/preview")
async def api_cover_preview(request: Request):
    """预览封面生成效果"""
    require_auth(request)
    try:
        body = await request.json()
        title = body.get("title", "歌单")
        theme = body.get("theme", "")
        song_count = body.get("song_count", 0)
        subtitle = f"{song_count} 首歌曲 · AI 生成" if song_count else ""
        cover_data = generate_cover(title, subtitle=subtitle, theme=theme or None)
        return Response(content=cover_data, media_type="image/jpeg")
    except Exception as e:
        logger.error(f"封面预览失败: {e}")
        raise HTTPException(500, "封面生成失败")

@app.get("/api/cover/themes")
async def api_cover_themes(request: Request):
    """获取可用的封面主题列表"""
    require_auth(request)
    return {"themes": list(THEME_COLORS.keys())}

@app.get("/api/playlists")
async def api_playlists(request: Request):
    require_auth(request)
    playlists = navidrome.get_playlists()
    return {"playlists": playlists}

@app.post("/api/library/refresh")
async def api_refresh_library(request: Request):
    require_auth(request)
    started = _refresh_library(force=True)
    with library_lock:
        count = len(library_cache.get("songs", []))
        loading = library_cache.get("loading", False)
        ready = bool(library_cache.get("last_update", 0))
        error = library_cache.get("error")
    return {
        "status": "loading" if loading else "ok",
        "started": started,
        "count": count,
        "library_ready": ready,
        "error": error,
    }


# ==================== 辅助函数 ====================
def _match_source_songs(source_songs: List[Any]):
    """Match source songs against the current persistent/in-memory library."""

    unique_songs = deduplicate_songs(source_songs)
    library = _get_library()
    results, unmatched_songs = match_songs(unique_songs, library)

    matched = []
    for result in results:
        source_song = result.source_song
        source_name = getattr(source_song, "source", "") if source_song else ""
        source_name = source_name or "匹配"
        label = source_name if result.method in {"title+artist", "title"} else f"{source_name}(模糊)"
        nav_song = result.library_song
        matched.append({
            "title": nav_song.title,
            "artist": nav_song.artist,
            "album": nav_song.album,
            "id": nav_song.id,
            "source": label,
            "match_score": round(result.score * 100, 1),
            "match_method": result.method,
        })

    unmatched = [
        {
            "title": song.title,
            "artist": song.artist,
            "album": getattr(song, "album", ""),
            "source": getattr(song, "source", "") or "未知来源",
        }
        for song in unmatched_songs
    ]
    return matched, unmatched, unique_songs


def _get_library():
    """获取歌曲库（支持持久化、后台刷新和 stale-while-refresh）。"""

    now = time.time()
    with library_lock:
        songs = list(library_cache.get("songs", []))
        last_update = library_cache.get("last_update", 0)
        loading = library_cache.get("loading", False)
        error = library_cache.get("error")

    if not last_update:
        _refresh_library()
        with library_lock:
            songs = list(library_cache.get("songs", []))
            last_update = library_cache.get("last_update", 0)
            loading = library_cache.get("loading", False)
            error = library_cache.get("error")
        if not songs and not last_update:
            detail = "Navidrome 曲库正在首次加载，请等待扫描完成后再试"
            if error:
                detail += f"（最近一次错误：{error}）"
            raise LibraryNotReadyError(detail)
        return songs

    # 有旧缓存时返回旧数据，同时后台刷新；用户不会再看到短暂的空曲库。
    if CACHE_TTL > 0 and now - last_update > CACHE_TTL and not loading:
        _refresh_library()
    return songs


def _persist_library(songs: List[NavidromeSong], updated_at: float) -> None:
    """Atomically write the library cache so an interrupted scan is harmless."""

    payload = {
        "version": 1,
        "updated_at": updated_at,
        "songs": [song.to_dict() for song in songs],
    }
    try:
        LIBRARY_CACHE_PATH.parent.mkdir(parents=True, exist_ok=True)
        temp_name = None
        try:
            with tempfile.NamedTemporaryFile(
                mode="w",
                encoding="utf-8",
                dir=str(LIBRARY_CACHE_PATH.parent),
                prefix=f".{LIBRARY_CACHE_PATH.name}.",
                suffix=".tmp",
                delete=False,
            ) as handle:
                temp_name = handle.name
                json.dump(payload, handle, ensure_ascii=False, separators=(",", ":"))
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temp_name, LIBRARY_CACHE_PATH)
        finally:
            if temp_name and os.path.exists(temp_name):
                os.unlink(temp_name)
        logger.info(f"曲库缓存已持久化: {LIBRARY_CACHE_PATH} ({len(songs)} 首)")
    except Exception as e:
        # Cache persistence must never make a successful Navidrome scan fail.
        logger.warning(f"曲库缓存持久化失败，将继续使用内存缓存: {e}")


def _load_persisted_library() -> None:
    """Load a previous cache before the web server starts."""

    if not LIBRARY_CACHE_PATH.exists():
        return
    try:
        with LIBRARY_CACHE_PATH.open("r", encoding="utf-8") as handle:
            payload = json.load(handle)
        if isinstance(payload, list):
            raw_songs = payload
            updated_at = LIBRARY_CACHE_PATH.stat().st_mtime
        else:
            raw_songs = payload.get("songs", [])
            updated_at = float(payload.get("updated_at", 0))
        songs = [NavidromeSong.from_dict(item) for item in raw_songs if isinstance(item, dict)]
        with library_lock:
            library_cache["songs"] = songs
            library_cache["last_update"] = updated_at
            library_cache["source"] = "disk"
            library_cache["error"] = None
        logger.info(f"已加载持久化曲库缓存: {len(songs)} 首歌曲")
    except Exception as e:
        logger.warning(f"读取曲库缓存失败，将重新扫描 Navidrome: {e}")


def _refresh_library(force: bool = False) -> bool:
    """Start one background library refresh and return whether it started."""

    now = time.time()
    with library_lock:
        if library_cache.get("loading"):
            return False
        if not force and library_cache.get("last_update", 0):
            if CACHE_TTL <= 0 or now - library_cache["last_update"] <= CACHE_TTL:
                return False
        library_cache["loading"] = True
        library_cache["error"] = None

    def _do_refresh():
        try:
            logger.info("正在刷新歌曲库（search3 分页扫描）...")
            songs = navidrome.get_all_songs(page_size=config.LIBRARY_SCAN_PAGE_SIZE)
            updated_at = time.time()
            with library_lock:
                library_cache["songs"] = songs
                library_cache["last_update"] = updated_at
                library_cache["source"] = "navidrome"
                library_cache["error"] = None
            _persist_library(songs, updated_at)
            logger.info(f"歌曲库已更新: {len(songs)} 首歌曲")
        except Exception as e:
            with library_lock:
                library_cache["error"] = str(e)
            logger.error(f"刷新歌曲库失败: {e}", exc_info=True)
        finally:
            with library_lock:
                library_cache["loading"] = False

    threading.Thread(target=_do_refresh, name="library-refresh", daemon=True).start()
    return True


# 让通过 uvicorn 导入 app 模块的部署方式也能立即使用已有缓存。
_load_persisted_library()


# ==================== 启动 ====================
if __name__ == "__main__":
    import uvicorn
    # 启动时在后台预加载歌曲库
    try:
        logger.info(f"正在连接 Navidrome: {config.NAVIDROME_URL}")
        if navidrome.ping():
            if _refresh_library():
                logger.info("Navidrome 连接成功！正在后台刷新歌曲库...")
            else:
                with library_lock:
                    cached_count = len(library_cache.get("songs", []))
                logger.info(f"Navidrome 连接成功！使用已有曲库缓存（{cached_count} 首）")
        else:
            logger.warning("Navidrome 连接失败，将在首次请求时重试")
    except Exception as e:
        logger.error(f"启动时连接 Navidrome 失败: {e}")

    uvicorn.run(app, host=config.HOST, port=config.PORT)

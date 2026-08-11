"""
Navidrome (Subsonic API) 客户端
"""
import re
import hashlib
import random
import string
import logging
import os
import time
from typing import List, Optional, Sequence, Tuple, Union
from dataclasses import dataclass
from urllib.parse import urlsplit, urlunsplit
import requests

from matching import song_dedupe_key

logger = logging.getLogger(__name__)


# Songloft 的 Subsonic 代理对一次 updatePlaylist 的参数量和坏歌曲 ID
# 的容错并不完全一致。与 playlist-matcher 保持相同的安全边界：先分批，
# 批次失败后重试，仍失败再递归二分，避免整张歌单因一个 ID 失败。
PLAYLIST_ADD_BATCH_SIZE = 50
PLAYLIST_ADD_RETRIES = 2
PLAYLIST_ADD_RETRY_DELAY = 0.5

SubsonicParams = Union[dict, Sequence[Tuple[str, object]]]


@dataclass
class NavidromeSong:
    id: str
    title: str
    artist: str
    album: str = ""

    @property
    def match_key(self) -> str:
        return song_dedupe_key(self)

    def to_dict(self) -> dict:
        """Serialize the fields needed by the persistent library cache."""
        return {
            'id': self.id,
            'title': self.title,
            'artist': self.artist,
            'album': self.album,
        }

    @classmethod
    def from_dict(cls, data: dict) -> "NavidromeSong":
        return cls(
            id=str(data.get('id', '')),
            title=str(data.get('title', '')),
            artist=str(data.get('artist', '')),
            album=str(data.get('album', '')),
        )


class NavidromeClient:
    def __init__(self, base_url: str, username: str, password: str):
        self.base_url = base_url.rstrip('/')
        self.username = username
        self.password = password
        self._api_base = f"{self.base_url}/rest"
        # Songloft 的原生 REST API 与 Subsonic JS 插件共用同一组账号，
        # 但封面上传不属于 Subsonic createPlaylist，因此需要单独调用。
        # 可通过 SONGLOFT_API_URL 覆盖自动推断结果，令牌也可通过
        # SONGLOFT_ACCESS_TOKEN 注入；默认仍使用已有账号登录获取令牌。
        self._songloft_api_base = self._resolve_songloft_api_base(self.base_url)
        self._songloft_access_token = os.getenv('SONGLOFT_ACCESS_TOKEN', '').strip()

    @staticmethod
    def _resolve_songloft_api_base(base_url: str) -> Optional[str]:
        """从 Subsonic 插件地址推断 Songloft 原生 ``/api/v1`` 地址。

        例如：
        ``http://host/api/v1/jsplugin/subsonic`` -> ``http://host/api/v1``。
        通过环境变量提供的地址优先，便于反向代理使用非标准路径。
        """
        override = os.getenv('SONGLOFT_API_URL', '').strip()
        candidate = override or base_url
        try:
            parsed = urlsplit(candidate)
        except ValueError:
            return None
        if parsed.scheme not in {'http', 'https'} or not parsed.netloc:
            return None

        path = parsed.path.rstrip('/')
        if override:
            if path.endswith('/api/v1'):
                api_path = path
            else:
                api_path = f"{path}/api/v1" if path else '/api/v1'
            return urlunsplit((parsed.scheme, parsed.netloc, api_path, '', ''))

        marker = '/api/v1/jsplugin/'
        marker_index = path.find(marker)
        if marker_index < 0:
            return None
        proxy_prefix = path[:marker_index].rstrip('/')
        api_path = f"{proxy_prefix}/api/v1" if proxy_prefix else '/api/v1'
        return urlunsplit((parsed.scheme, parsed.netloc, api_path, '', ''))

    def _make_params(self, extra: dict = None) -> dict:
        """构建认证参数"""
        salt = ''.join(random.choices(string.ascii_lowercase + string.digits, k=8))
        token = hashlib.md5((self.password + salt).encode('utf-8')).hexdigest()
        params = {
            'u': self.username,
            't': token,
            's': salt,
            'v': '1.16.1',
            'c': 'navidrome-ai-playlist',
            'f': 'json',
        }
        if extra:
            params.update(extra)
        return params

    def _get(self, endpoint: str, extra_params: SubsonicParams = None) -> Optional[dict]:
        url = f"{self._api_base}/{endpoint}"
        auth_params = self._make_params()
        if isinstance(extra_params, dict) or extra_params is None:
            params = auth_params
            if extra_params:
                params.update(extra_params)
        else:
            # 使用键值对列表保留 songIdToAdd 的重复参数；某些代理会把
            # dict/list 压成一个字符串，导致批量添加只收到一首歌。
            params = list(auth_params.items()) + list(extra_params)
        try:
            resp = requests.get(url, params=params, timeout=30, verify=False)
            resp.raise_for_status()
            data = resp.json()
            response = data.get('subsonic-response', {})
            if response.get('status') == 'ok':
                return response
            else:
                error = response.get('error', {})
                logger.error(f"Navidrome API error: {error}")
                return None
        except requests.exceptions.HTTPError as e:
            response = getattr(e, 'response', None)
            status = getattr(response, 'status_code', 'unknown')
            body = (getattr(response, 'text', '') or '').strip()
            if len(body) > 500:
                body = body[:500] + '...'
            detail = f": {body}" if body else ''
            logger.error(f"Navidrome request failed ({endpoint}) HTTP {status}{detail}")
            return None
        except requests.exceptions.RequestException as e:
            logger.error(f"Navidrome request failed: {e}")
            return None
        except ValueError as e:
            logger.error(f"Navidrome response decode failed ({endpoint}): {e}")
            return None
        except Exception as e:
            logger.error(f"Navidrome request failed ({endpoint}): {e}")
            return None

    def ping(self) -> bool:
        result = self._get('ping')
        return result is not None

    def get_all_artists(self) -> List[dict]:
        """获取所有艺术家"""
        result = self._get('getArtists')
        if not result:
            return []
        artists = []
        for index in result.get('artists', {}).get('index', []):
            for artist in index.get('artist', []):
                artists.append(artist)
        return artists

    def get_artist_songs(self, artist_id: str) -> List[NavidromeSong]:
        """获取某艺术家的所有歌曲"""
        result = self._get('getArtist', {'id': artist_id})
        if not result:
            return []
        songs = []
        artist_data = result.get('artist', {})
        artist_name = artist_data.get('name', '')
        for album in artist_data.get('album', []):
            album_result = self._get('getAlbum', {'id': album.get('id', '')})
            if album_result:
                for song_data in album_result.get('album', {}).get('song', []):
                    songs.append(NavidromeSong(
                        id=song_data.get('id', ''),
                        title=song_data.get('title', ''),
                        artist=song_data.get('artist', artist_name),
                        album=song_data.get('album', ''),
                    ))
        return songs

    def search_songs(self, query: str, count: int = 50, offset: int = 0) -> List[NavidromeSong]:
        """搜索歌曲，支持 Subsonic search3 的分页偏移量。"""
        result = self._get('search3', {
            'query': query,
            'songCount': count,
            'songOffset': offset,
            'artistCount': 0,
            'albumCount': 0,
        })
        if not result:
            return []
        songs = []
        song_data_list = result.get('searchResult3', {}).get('song', [])
        if isinstance(song_data_list, dict):
            song_data_list = [song_data_list]
        for song_data in song_data_list:
            songs.append(NavidromeSong(
                id=song_data.get('id', ''),
                title=song_data.get('title', ''),
                artist=song_data.get('artist', ''),
                album=song_data.get('album', ''),
            ))
        return songs

    def get_all_songs(self, page_size: int = 500) -> List[NavidromeSong]:
        """获取全库歌曲。

        Navidrome 支持用 ``search3`` 搭配空 query 分页返回全库歌曲。相比
        原先逐个请求艺术家和专辑，这只需要几十次请求甚至更少。若当前
        Subsonic 兼容层不支持这种调用，则自动回退到旧扫描方式。
        """
        all_songs: List[NavidromeSong] = []
        seen_ids = set()
        offset = 0
        page_number = 0

        while True:
            page_number += 1
            result = self._get('search3', {
                'query': '',
                'songCount': page_size,
                'songOffset': offset,
                'artistCount': 0,
                'albumCount': 0,
            })
            if result is None:
                logger.warning("search3 全库分页调用失败，回退到艺术家/专辑扫描")
                return self._get_all_songs_by_artist()

            search_result = result.get('searchResult3')
            if not isinstance(search_result, dict):
                logger.warning("search3 未返回 searchResult3，回退到艺术家/专辑扫描")
                return self._get_all_songs_by_artist()

            raw_songs = search_result.get('song', [])
            if isinstance(raw_songs, dict):
                raw_songs = [raw_songs]
            if not raw_songs:
                if offset == 0:
                    # Some Subsonic-compatible plugins return status=ok with
                    # an empty searchResult3 when they do not support an empty
                    # search query. Check artists once before accepting an
                    # empty library as a real result.
                    artists = self.get_all_artists()
                    if artists:
                        logger.warning("search3 空查询未返回歌曲，回退到艺术家/专辑扫描")
                        return self._get_all_songs_by_artist(artists)
                break

            for song_data in raw_songs:
                song = NavidromeSong(
                    id=str(song_data.get('id', '')),
                    title=song_data.get('title', ''),
                    artist=song_data.get('artist', ''),
                    album=song_data.get('album', ''),
                )
                if not song.id or song.id in seen_ids:
                    continue
                seen_ids.add(song.id)
                all_songs.append(song)

            logger.info(
                f"已扫描第 {page_number} 页，当前累计 {len(all_songs)} 首歌曲"
            )
            if len(raw_songs) < page_size:
                break
            offset += page_size

        logger.info(f"共获取 {len(all_songs)} 首歌曲（search3 分页）")
        return all_songs

    def _get_all_songs_by_artist(self, artists: Optional[List[dict]] = None) -> List[NavidromeSong]:
        """兼容不支持空 query search3 的 Subsonic 实现。"""
        all_songs = []
        artists = artists if artists is not None else self.get_all_artists()
        seen_ids = set()
        logger.info(f"正在使用兼容模式获取 {len(artists)} 位艺术家的歌曲...")
        for i, artist in enumerate(artists):
            songs = self.get_artist_songs(artist.get('id', ''))
            for song in songs:
                if song.id and song.id not in seen_ids:
                    seen_ids.add(song.id)
                    all_songs.append(song)
            if (i + 1) % 20 == 0:
                logger.info(f"已处理 {i+1}/{len(artists)} 位艺术家，累计 {len(all_songs)} 首歌曲")
        logger.info(f"共获取 {len(all_songs)} 首歌曲（艺术家兼容模式）")
        return all_songs

    def get_playlists(self) -> List[dict]:
        """获取所有歌单"""
        result = self._get('getPlaylists')
        if not result:
            return []
        playlists = result.get('playlists', {}).get('playlist', [])
        if isinstance(playlists, dict):
            playlists = [playlists]
        return playlists if isinstance(playlists, list) else []

    def _find_playlist_by_name(
        self,
        name: str,
        attempts: int = 3,
        delay: float = PLAYLIST_ADD_RETRY_DELAY,
    ) -> Optional[dict]:
        """通过歌单名称回查 createPlaylist 未返回的 ID。"""
        attempts = max(1, attempts)
        for attempt in range(attempts):
            playlists = self.get_playlists()
            for playlist in playlists:
                playlist_name = str(playlist.get('name', '')).strip()
                playlist_id = str(playlist.get('id', '')).strip()
                if playlist_name == name.strip() and playlist_id:
                    return playlist
            if attempt < attempts - 1:
                time.sleep(delay)
        return None

    @staticmethod
    def _normalize_song_ids(song_ids: List[str]) -> List[str]:
        """按输入顺序去重歌曲 ID，并过滤空值。"""
        normalized: List[str] = []
        seen = set()
        for raw_id in song_ids:
            if raw_id is None or isinstance(raw_id, bool):
                continue
            song_id = str(raw_id).strip()
            if not song_id or song_id in seen:
                continue
            seen.add(song_id)
            normalized.append(song_id)
        return normalized

    def _add_playlist_song_batch(self, playlist_id: str, song_ids: List[str]) -> bool:
        """使用重复 songIdToAdd 参数添加一批歌曲，并进行有限重试。"""
        params: List[Tuple[str, object]] = [('playlistId', playlist_id)]
        params.extend(('songIdToAdd', song_id) for song_id in song_ids)

        for attempt in range(PLAYLIST_ADD_RETRIES + 1):
            result = self._get('updatePlaylist', params)
            if result is not None:
                return True
            if attempt < PLAYLIST_ADD_RETRIES:
                delay = PLAYLIST_ADD_RETRY_DELAY * (2 ** attempt)
                logger.warning(
                    f"批量添加 {len(song_ids)} 首歌曲失败，{delay:.1f} 秒后重试 "
                    f"({attempt + 1}/{PLAYLIST_ADD_RETRIES})..."
                )
                time.sleep(delay)
        return False

    def _add_playlist_batch_with_recovery(
        self,
        playlist_id: str,
        song_ids: List[str],
    ) -> Tuple[List[str], List[str]]:
        """失败时二分批次，隔离坏 ID，并继续处理其它歌曲。"""
        if not song_ids:
            return [], []
        if self._add_playlist_song_batch(playlist_id, song_ids):
            return list(song_ids), []

        if len(song_ids) == 1:
            logger.error(f"歌曲 ID {song_ids[0]} 添加失败，已跳过该歌曲。")
            return [], [song_ids[0]]

        midpoint = len(song_ids) // 2
        logger.warning(
            f"批次 ({len(song_ids)} 首) 添加失败，正在拆分为 "
            f"{midpoint} + {len(song_ids) - midpoint} 首重试..."
        )
        added_left, failed_left = self._add_playlist_batch_with_recovery(
            playlist_id, song_ids[:midpoint]
        )
        added_right, failed_right = self._add_playlist_batch_with_recovery(
            playlist_id, song_ids[midpoint:]
        )
        return added_left + added_right, failed_left + failed_right

    def _add_playlist_songs_batched(
        self,
        playlist_id: str,
        song_ids: List[str],
        batch_size: int = PLAYLIST_ADD_BATCH_SIZE,
    ) -> Tuple[List[str], List[str]]:
        """分批添加歌曲，返回 (成功 ID, 失败 ID)。"""
        normalized_ids = self._normalize_song_ids(song_ids)
        if not normalized_ids:
            return [], []

        try:
            batch_size = max(1, int(batch_size))
        except (TypeError, ValueError):
            batch_size = PLAYLIST_ADD_BATCH_SIZE

        total_batches = (len(normalized_ids) + batch_size - 1) // batch_size
        logger.info(
            f"准备将 {len(normalized_ids)} 首歌曲分 {total_batches} 个批次添加到歌单 "
            f"(ID: {playlist_id})..."
        )
        added_ids: List[str] = []
        failed_ids: List[str] = []
        for batch_number, start in enumerate(range(0, len(normalized_ids), batch_size), start=1):
            current_batch = normalized_ids[start:start + batch_size]
            logger.info(
                f"正在处理歌单歌曲批次 {batch_number}/{total_batches} "
                f"(歌曲数: {len(current_batch)})..."
            )
            added, failed = self._add_playlist_batch_with_recovery(playlist_id, current_batch)
            added_ids.extend(added)
            failed_ids.extend(failed)
            if failed:
                logger.warning(
                    f"歌单歌曲批次 {batch_number} 有 {len(failed)} 首歌曲最终未添加。"
                )
            else:
                logger.info(f"歌单歌曲批次 {batch_number} 添加成功。")
            if batch_number < total_batches:
                time.sleep(PLAYLIST_ADD_RETRY_DELAY)

        if failed_ids:
            logger.error(
                f"歌单添加完成，但有 {len(failed_ids)}/{len(normalized_ids)} 个歌曲 ID 失败: "
                f"{failed_ids[:20]}{'...' if len(failed_ids) > 20 else ''}"
            )
        else:
            logger.info(f"歌单歌曲添加完成，共添加 {len(added_ids)} 首。")
        return added_ids, failed_ids

    def _songloft_login(self, force: bool = False) -> bool:
        """登录 Songloft 原生 API，获取封面上传所需的 Access Token。"""
        if not self._songloft_api_base:
            return False
        if self._songloft_access_token and not force:
            return True

        try:
            response = requests.post(
                f"{self._songloft_api_base}/auth/login",
                json={'username': self.username, 'password': self.password},
                timeout=30,
                verify=False,
            )
            response.raise_for_status()
            payload = response.json()
            token = payload.get('access_token', '')
            if not token:
                logger.error("Songloft 登录成功但响应中没有 access_token。")
                return False
            self._songloft_access_token = str(token)
            return True
        except requests.exceptions.HTTPError as e:
            response = getattr(e, 'response', None)
            status = getattr(response, 'status_code', 'unknown')
            logger.error(f"Songloft 原生 API 登录失败，HTTP {status}。")
        except (requests.exceptions.RequestException, ValueError) as e:
            logger.error(f"Songloft 原生 API 登录失败: {e}")
        except Exception as e:
            logger.error(f"Songloft 原生 API 登录失败: {e}")
        return False

    def _upload_playlist_cover(self, playlist_id: str, cover_data: bytes) -> bool:
        """通过 Songloft 原生 ``POST /playlists/{id}/cover`` 上传封面。"""
        if not self._songloft_api_base:
            logger.warning(
                "当前地址不是可推断的 Songloft JS 插件地址，歌单已创建但无法自动上传封面。"
            )
            return False

        url = f"{self._songloft_api_base}/playlists/{playlist_id}/cover"
        for attempt in range(2):
            if not self._songloft_login(force=attempt > 0):
                return False
            try:
                response = requests.post(
                    url,
                    headers={'Authorization': f"Bearer {self._songloft_access_token}"},
                    files={'file': ('cover.jpg', cover_data, 'image/jpeg')},
                    timeout=30,
                    verify=False,
                )
                if response.status_code == 401 and attempt == 0:
                    self._songloft_access_token = ''
                    continue
                response.raise_for_status()
                logger.info(f"歌单封面上传成功 (ID: {playlist_id})。")
                return True
            except requests.exceptions.HTTPError as e:
                response = getattr(e, 'response', None)
                status = getattr(response, 'status_code', 'unknown')
                body = (getattr(response, 'text', '') or '').strip()
                if len(body) > 500:
                    body = body[:500] + '...'
                detail = f": {body}" if body else ''
                logger.error(f"歌单封面上传失败，HTTP {status}{detail}")
            except requests.exceptions.RequestException as e:
                logger.error(f"歌单封面上传失败: {e}")
            except Exception as e:
                logger.error(f"歌单封面上传失败: {e}")
            return False
        return False

    def create_playlist(self, name: str, song_ids: List[str], cover_data: bytes = None) -> Optional[dict]:
        """创建歌单、批量添加歌曲，并在最后单独上传封面。

        Songloft 的 Subsonic 代理不支持将 ``coverArt`` multipart 数据与
        createPlaylist 混发；同时它可能只返回 ``status=ok``。因此必须先
        创建无封面的歌单，再通过 getPlaylists 回查 ID，完成歌曲添加后调用
        Songloft 原生封面接口。
        """
        result = self._get('createPlaylist', {'name': name})
        playlist = dict((result or {}).get('playlist') or {})
        playlist_id = str(playlist.get('id', '')).strip()

        if not playlist_id:
            if result is not None:
                logger.warning(
                    "createPlaylist 已成功，但响应中没有 playlist.id；正在刷新歌单列表获取新 ID..."
                )
            else:
                logger.warning(
                    "createPlaylist 未返回有效响应；正在刷新歌单列表确认歌单是否已创建..."
                )
            found = self._find_playlist_by_name(name)
            if not found:
                logger.error(f"未能获取新建歌单 '{name}' 的 ID。")
                return None
            playlist = {**found, **playlist}
            playlist_id = str(playlist.get('id', '')).strip()

        if not playlist_id:
            logger.error(f"歌单 '{name}' 的 ID 为空，无法继续添加歌曲。")
            return None

        playlist.setdefault('id', playlist_id)
        playlist.setdefault('name', name)

        added_ids, failed_ids = self._add_playlist_songs_batched(playlist_id, song_ids)
        # 下划线字段供 Web 层计算准确的结果，不直接依赖代理返回的 songCount。
        playlist['_added_song_ids'] = added_ids
        playlist['_failed_song_ids'] = failed_ids

        if cover_data:
            playlist['cover_uploaded'] = self._upload_playlist_cover(playlist_id, cover_data)
        else:
            playlist['cover_uploaded'] = False
        return playlist

    def delete_playlist(self, playlist_id: str) -> bool:
        """删除歌单"""
        result = self._get('deletePlaylist', {'id': playlist_id})
        return result is not None

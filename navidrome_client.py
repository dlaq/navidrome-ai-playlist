"""
Navidrome (Subsonic API) 客户端
"""
import re
import hashlib
import random
import string
import logging
from typing import List, Dict, Optional, Tuple
from dataclasses import dataclass
import requests

from matching import song_dedupe_key

logger = logging.getLogger(__name__)


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

    def _get(self, endpoint: str, extra_params: dict = None) -> Optional[dict]:
        url = f"{self._api_base}/{endpoint}"
        params = self._make_params(extra_params)
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
        except Exception as e:
            logger.error(f"Navidrome request failed: {e}")
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
        return result.get('playlists', {}).get('playlist', [])

    def create_playlist(self, name: str, song_ids: List[str], cover_data: bytes = None) -> Optional[dict]:
        """创建歌单，可选附带封面图"""
        if cover_data:
            # 使用 multipart form 上传带封面的歌单
            url = f"{self._api_base}/createPlaylist"
            params = self._make_params({'name': name})
            files = {'coverArt': ('cover.jpg', cover_data, 'image/jpeg')}
            try:
                resp = requests.post(url, params=params, files=files, timeout=30, verify=False)
                resp.raise_for_status()
                data = resp.json()
                response = data.get('subsonic-response', {})
                if response.get('status') != 'ok':
                    logger.error(f"Create playlist with cover failed: {response.get('error')}")
                    return None
                playlist = response.get('playlist', {})
            except Exception as e:
                logger.error(f"Create playlist with cover failed: {e}")
                return None
        else:
            result = self._get('createPlaylist', {'name': name})
            if not result:
                return None
            playlist = result.get('playlist', {})

        playlist_id = playlist.get('id', '')
        if song_ids:
            self._get('updatePlaylist', {
                'playlistId': playlist_id,
                'songIdToAdd': song_ids,
            })
        return playlist

    def delete_playlist(self, playlist_id: str) -> bool:
        """删除歌单"""
        result = self._get('deletePlaylist', {'id': playlist_id})
        return result is not None

import unittest
from unittest.mock import Mock, patch

from playlist_parser import fetch_netease_playlist, parse_playlist_url


class NeteasePlaylistTests(unittest.TestCase):
    def test_mobile_hash_url_is_recognized(self):
        url = "https://music.163.com/#/my/m/music/playlist?id=13641085"
        self.assertEqual(parse_playlist_url(url), ("netease", "13641085"))

    @patch("playlist_parser.requests.get")
    def test_track_ids_are_fetched_in_batches_and_order_is_preserved(self, get):
        track_ids = list(range(1, 52))
        playlist_response = Mock()
        playlist_response.json.return_value = {
            "code": 200,
            "playlist": {
                "name": "测试歌单",
                "trackIds": [{"id": song_id} for song_id in track_ids],
            },
        }

        first_batch = Mock()
        first_batch.json.return_value = {
            "code": 200,
            "songs": [
                {
                    "id": song_id,
                    "name": f"歌曲{song_id}",
                    "artists": [{"name": f"歌手{song_id}"}],
                    "album": {"name": f"专辑{song_id}"},
                }
                for song_id in range(1, 51)
            ],
        }
        second_batch = Mock()
        second_batch.json.return_value = {
            "code": 200,
            "songs": [
                {
                    "id": 51,
                    "name": "歌曲51",
                    "artists": [{"name": "歌手51"}],
                    "album": {"name": "专辑51"},
                }
            ],
        }
        get.side_effect = [playlist_response, first_batch, second_batch]

        name, songs = fetch_netease_playlist("13641085")

        self.assertEqual(name, "测试歌单")
        self.assertEqual(len(songs), 51)
        self.assertEqual([song.title for song in songs], [f"歌曲{i}" for i in track_ids])
        self.assertIn("/api/v6/playlist/detail?id=13641085", get.call_args_list[0].args[0])
        self.assertEqual(get.call_count, 3)


if __name__ == "__main__":
    unittest.main()

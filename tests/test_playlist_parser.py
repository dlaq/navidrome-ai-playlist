import unittest
from unittest.mock import Mock, patch

from playlist_parser import fetch_netease_playlist, parse_playlist_url


class NeteasePlaylistTests(unittest.TestCase):
    def test_mobile_playlist_url_is_recognized(self):
        url = "https://music.163.com/#/my/m/music/playlist?id=13641085"

        self.assertEqual(("netease", "13641085"), parse_playlist_url(url))

    @patch("playlist_parser.requests.get")
    def test_fetches_all_track_ids_in_batches_and_preserves_order(self, get):
        track_ids = list(range(1, 52))
        playlist_response = Mock()
        playlist_response.json.return_value = {
            "code": 200,
            "playlist": {
                "name": "测试歌单",
                "trackIds": [{"id": song_id} for song_id in track_ids],
            },
        }

        first_batch_response = Mock()
        first_batch_response.json.return_value = {
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

        second_batch_response = Mock()
        second_batch_response.json.return_value = {
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
        get.side_effect = [
            playlist_response,
            first_batch_response,
            second_batch_response,
        ]

        name, songs = fetch_netease_playlist("13641085")

        self.assertEqual("测试歌单", name)
        self.assertEqual(51, len(songs))
        self.assertEqual(["歌曲1", "歌曲51"], [songs[0].title, songs[-1].title])
        self.assertEqual(3, get.call_count)
        self.assertEqual(
            "https://music.163.com/api/v6/playlist/detail?id=13641085",
            get.call_args_list[0].args[0],
        )
        self.assertIn("ids=[1,2,3", get.call_args_list[1].args[0])
        self.assertTrue(get.call_args_list[2].args[0].endswith("ids=[51]"))


if __name__ == "__main__":
    unittest.main()

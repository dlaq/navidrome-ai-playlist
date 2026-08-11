import unittest
from unittest.mock import Mock

from navidrome_client import NavidromeClient


class NavidromeLibraryScanTests(unittest.TestCase):
    def test_get_all_songs_uses_search3_pagination(self):
        client = NavidromeClient("http://navidrome", "user", "pass")
        client._get = Mock(side_effect=[
            {
                "searchResult3": {
                    "song": [
                        {"id": "1", "title": "歌曲1", "artist": "歌手1", "album": "专辑"},
                        {"id": "2", "title": "歌曲2", "artist": "歌手2", "album": "专辑"},
                    ]
                }
            },
            {"searchResult3": {"song": []}},
        ])

        songs = client.get_all_songs(page_size=2)

        self.assertEqual(["1", "2"], [song.id for song in songs])
        self.assertEqual(2, client._get.call_count)
        self.assertEqual(
            {
                "query": "",
                "songCount": 2,
                "songOffset": 0,
                "artistCount": 0,
                "albumCount": 0,
            },
            client._get.call_args_list[0].args[1],
        )
        self.assertEqual(2, client._get.call_args_list[1].args[1]["songOffset"])

    def test_get_all_songs_falls_back_when_search3_is_unavailable(self):
        client = NavidromeClient("http://navidrome", "user", "pass")
        client._get = Mock(return_value=None)
        fallback_song = Mock(id="fallback", title="歌曲", artist="歌手", album="专辑")
        client._get_all_songs_by_artist = Mock(return_value=[fallback_song])

        songs = client.get_all_songs(page_size=500)

        self.assertEqual([fallback_song], songs)
        client._get_all_songs_by_artist.assert_called_once_with()


if __name__ == "__main__":
    unittest.main()

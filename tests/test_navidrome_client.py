import unittest
from unittest.mock import Mock, patch

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


class NavidromePlaylistTests(unittest.TestCase):
    def test_create_playlist_recovers_missing_id_and_adds_in_batches(self):
        client = NavidromeClient("http://navidrome", "user", "pass")
        client._get = Mock(side_effect=[
            {"status": "ok"},
            {"playlists": {"playlist": [{"id": "77", "name": "测试歌单"}]}},
            {"status": "ok"},
            {"status": "ok"},
        ])

        with patch("navidrome_client.time.sleep"):
            playlist = client.create_playlist(
                "测试歌单", [str(index) for index in range(1, 52)]
            )

        self.assertIsNotNone(playlist)
        self.assertEqual("77", playlist["id"])
        self.assertEqual(51, len(playlist["_added_song_ids"]))
        self.assertEqual([], playlist["_failed_song_ids"])
        self.assertEqual("createPlaylist", client._get.call_args_list[0].args[0])
        self.assertEqual("getPlaylists", client._get.call_args_list[1].args[0])

        first_batch = client._get.call_args_list[2].args[1]
        second_batch = client._get.call_args_list[3].args[1]
        self.assertEqual("updatePlaylist", client._get.call_args_list[2].args[0])
        self.assertEqual("playlistId", first_batch[0][0])
        self.assertEqual(50, len([v for k, v in first_batch if k == "songIdToAdd"]))
        self.assertEqual(["51"], [v for k, v in second_batch if k == "songIdToAdd"])

    def test_failed_batch_is_split_and_bad_song_isolated(self):
        client = NavidromeClient("http://navidrome", "user", "pass")
        calls = []

        def fake_get(endpoint, params=None):
            if endpoint != "updatePlaylist":
                return {"status": "ok"}
            ids = [value for key, value in params if key == "songIdToAdd"]
            calls.append(ids)
            if len(ids) > 1 or ids == ["bad"]:
                return None
            return {"status": "ok"}

        client._get = Mock(side_effect=fake_get)
        with patch("navidrome_client.time.sleep"):
            added, failed = client._add_playlist_songs_batched(
                "7", ["ok-1", "bad", "ok-2"], batch_size=3
            )

        self.assertEqual(["ok-1", "ok-2"], added)
        self.assertEqual(["bad"], failed)
        self.assertIn(["bad"], calls)
        self.assertIn(["ok-1"], calls)
        self.assertIn(["ok-2"], calls)

    @patch("navidrome_client.requests.post")
    def test_cover_is_uploaded_after_creation_through_songloft_api(self, post):
        client = NavidromeClient(
            "http://songloft/api/v1/jsplugin/subsonic", "admin", "secret"
        )
        client._get = Mock(side_effect=[
            {"status": "ok", "playlist": {"id": "9", "name": "封面歌单"}},
            {"status": "ok"},
        ])

        login_response = Mock()
        login_response.json.return_value = {"access_token": "test-token"}
        upload_response = Mock(status_code=200)
        post.side_effect = [login_response, upload_response]

        playlist = client.create_playlist("封面歌单", ["1"], cover_data=b"jpeg")

        self.assertTrue(playlist["cover_uploaded"])
        self.assertEqual(2, post.call_count)
        self.assertEqual(
            "http://songloft/api/v1/auth/login",
            post.call_args_list[0].args[0],
        )
        upload_call = post.call_args_list[1]
        self.assertEqual(
            "http://songloft/api/v1/playlists/9/cover",
            upload_call.args[0],
        )
        self.assertEqual(
            "Bearer test-token",
            upload_call.kwargs["headers"]["Authorization"],
        )
        self.assertIn("file", upload_call.kwargs["files"])


if __name__ == "__main__":
    unittest.main()

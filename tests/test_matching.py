import unittest
from dataclasses import dataclass

from matching import SongMatcher, deduplicate_songs, match_songs


@dataclass
class TestSong:
    title: str
    artist: str
    album: str = ""
    id: str = ""
    source: str = ""


class SongMatchingTests(unittest.TestCase):
    def test_edition_and_artist_separators_are_compatible(self):
        source = TestSong("夜曲 (Live版)", "周杰伦/杨瑞代", source="网易云")
        library = [TestSong("夜曲 - 现场", "周杰伦 & 杨瑞代", id="1")]

        result = SongMatcher(library).match(source)

        self.assertIsNotNone(result)
        self.assertEqual("1", result.library_song.id)
        self.assertIn(result.method, {"title+artist", "title"})

    def test_featured_artist_is_not_lost(self):
        source = TestSong("平凡之路 - Remix", "朴树 feat. 某某", source="QQ")
        library = [TestSong("平凡之路", "朴树/某某", id="2")]

        result = SongMatcher(library).match(source)

        self.assertIsNotNone(result)
        self.assertEqual("2", result.library_song.id)

    def test_artist_alias_and_accents_are_normalized(self):
        source = TestSong("Halo", "Beyoncé (Beyonce)", source="网易云")
        library = [TestSong("Halo", "Beyonce", id="alias")]

        result = SongMatcher(library).match(source)

        self.assertIsNotNone(result)
        self.assertEqual("alias", result.library_song.id)

    def test_same_title_prefers_matching_artist(self):
        source = TestSong("后来（现场版）", "刘若英", source="网易云")
        library = [
            TestSong("后来", "其他歌手", id="wrong"),
            TestSong("后来", "刘若英", id="right"),
        ]

        result = SongMatcher(library).match(source)

        self.assertIsNotNone(result)
        self.assertEqual("right", result.library_song.id)

    def test_fuzzy_title_with_artist_match_is_supported(self):
        source = TestSong("Somewhere Only We Know - Live", "Keane", source="酷我")
        library = [TestSong("Somewhere Only We Know (Live at Berlin)", "Keane", id="3")]

        result = SongMatcher(library).match(source)

        self.assertIsNotNone(result)
        self.assertEqual("3", result.library_song.id)

    def test_live_and_remix_are_not_collapsed_during_source_deduplication(self):
        songs = [
            TestSong("歌曲 (Live)", "歌手"),
            TestSong("歌曲 - Live版", "歌手"),
            TestSong("歌曲 (Remix)", "歌手"),
        ]

        unique = deduplicate_songs(songs)

        self.assertEqual(2, len(unique))

    def test_match_result_keeps_source_song_and_does_not_match_wrong_artist(self):
        source = TestSong("唯一歌曲", "甲", source="网易云")
        matches, unmatched = match_songs(
            [source],
            [TestSong("唯一歌曲", "乙", id="wrong")],
        )

        self.assertEqual([], matches)
        self.assertEqual([source], unmatched)


if __name__ == "__main__":
    unittest.main()

"""Tests for tvrename.py. Run with:  python3 -m unittest -v"""
import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import tvrename as tr


class ParseTests(unittest.TestCase):
    def p(self, rel):
        return tr.parse_file(Path("/tv") / rel)

    def test_standard_sxxexx(self):
        pf = self.p("The.Office.US.S02E05.720p.HDTV.x264-GRP.mkv")
        self.assertEqual((pf.show_guess, pf.season, pf.episodes, pf.ext),
                         ("The Office US", 2, [5], ".mkv"))

    def test_year_in_name(self):
        pf = self.p("Doctor.Who.2005.S01E01.Rose.mp4")
        self.assertEqual((pf.show_guess, pf.year_guess), ("Doctor Who", 2005))

    def test_multi_episode(self):
        self.assertEqual(self.p("Show.S01E01E02.mkv").episodes, [1, 2])
        self.assertEqual(self.p("Show - S01E01-E03.mkv").episodes, [1, 2, 3])

    def test_1x02_style(self):
        pf = self.p("Friends 3x07 The One With.avi")
        self.assertEqual((pf.show_guess, pf.season, pf.episodes), ("Friends", 3, [7]))

    def test_resolution_is_not_episode(self):
        self.assertIsNone(self.p("Some Movie 1920x1080.mkv"))

    def test_show_from_folder(self):
        pf = self.p("Breaking Bad/Season 2/S02E03.mkv")
        self.assertEqual(pf.show_guess, "Breaking Bad")
        pf = self.p("Breaking Bad/Season 2/02x03.mkv")
        self.assertEqual(pf.show_guess, "Breaking Bad")

    def test_season_episode_words(self):
        pf = self.p("Lost Season 1 Episode 4.mkv")
        self.assertEqual((pf.show_guess, pf.season, pf.episodes), ("Lost", 1, [4]))

    def test_subtitle_language_kept(self):
        pf = self.p("Show.S01E01.WEB.en.forced.srt")
        self.assertEqual(pf.ext, ".en.forced.srt")
        self.assertTrue(pf.is_subtitle)
        self.assertEqual(self.p("Show.S01E01.WEB.srt").ext, ".srt")


class NamingTests(unittest.TestCase):
    series = {"id": 73244, "name": "The Office (US)", "year": 2005}

    def test_plex_name(self):
        pf = tr.parse_file(Path("The.Office.S02E05.mkv"))
        self.assertEqual(tr.build_new_name(self.series, pf, ["Halloween"]),
                         "The Office (US) (2005) - s02e05 - Halloween.mkv")

    def test_illegal_characters(self):
        s = {"id": 1, "name": "Star Trek: Discovery", "year": 2017}
        pf = tr.parse_file(Path("x.S01E01.mkv"))
        self.assertEqual(tr.build_new_name(s, pf, ['What? "Now"/Then']),
                         "Star Trek - Discovery (2017) - s01e01 - What Now-Then.mkv")

    def test_year_not_doubled(self):
        s = {"id": 1, "name": "Doctor Who (2005)", "year": 2005}
        self.assertEqual(tr.show_label(s), "Doctor Who (2005)")

    def test_multi_episode_name(self):
        pf = tr.parse_file(Path("x.S01E01E02.mkv"))
        self.assertEqual(tr.build_new_name(self.series, pf, ["Pilot", "Pilot"]),
                         "The Office (US) (2005) - s01e01-e02 - Pilot.mkv")


class FakeTVDB:
    def __init__(self, *a, **k):
        pass

    def login(self):
        pass

    def search_series(self, q, year=None):
        return [{"id": 73244, "name": "The Office (US)", "year": 2005, "network": "NBC",
                 "overview": "A mockumentary."},
                {"id": 78107, "name": "The Office", "year": 2001, "network": "BBC",
                 "overview": "UK original."}]

    def get_series(self, sid):
        return {"id": sid, "name": "By Id", "year": 2000, "network": "", "overview": ""}

    def get_episodes(self, sid, order="default"):
        return {(2, 5): {"name": "Halloween"}, (2, 6): {"name": "The Fight"},
                (1, 1): {"name": "Pilot"}}


class EndToEndTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        show = self.root / "The Office" / "Season 2"
        show.mkdir(parents=True)
        for n in ["The.Office.S02E05.720p.mkv", "The.Office.S02E05.720p.en.srt",
                  "The.Office.S02E06.720p.mkv", "The.Office.S02E99.mkv", "notes.txt"]:
            (show / n).write_text("x")
        self.show = show

    def tearDown(self):
        self.tmp.cleanup()

    def run_tool(self, argv, answers):
        with mock.patch.object(tr, "TVDB", FakeTVDB), \
             mock.patch("builtins.input", side_effect=list(answers)), \
             mock.patch("builtins.print"):
            return tr.main(argv + ["--api-key", "dummy"])

    def names(self, folder=None):
        return sorted(p.name for p in (folder or self.show).iterdir())

    def test_rename_one_at_a_time(self):
        # pick show 1; files come up as: E05 subtitle (y), E05 video (y), E06 video (n)
        self.run_tool([str(self.root)], ["1", "y", "y", "n"])
        self.assertEqual(self.names(), [
            "The Office (US) (2005) - s02e05 - Halloween.en.srt",
            "The Office (US) (2005) - s02e05 - Halloween.mkv",
            "The.Office.S02E06.720p.mkv",  # answered no
            "The.Office.S02E99.mkv",       # not on TVDB, skipped
            "notes.txt",
        ])

    def test_dry_run_changes_nothing(self):
        before = self.names()
        self.run_tool([str(self.root), "--dry-run"], ["1"])
        self.assertEqual(self.names(), before)

    def test_all_then_undo(self):
        self.run_tool([str(self.root), "--log", str(self.root / "log.jsonl")], ["1", "a"])
        self.assertIn("The Office (US) (2005) - s02e06 - The Fight.mkv", self.names())
        log = [json.loads(l) for l in (self.root / "log.jsonl").read_text().splitlines()]
        self.assertEqual(len(log), 3)
        self.run_tool(["--undo", str(self.root / "log.jsonl")], [])
        self.assertIn("The.Office.S02E06.720p.mkv", self.names())
        self.assertIn("The.Office.S02E05.720p.en.srt", self.names())

    def test_organize(self):
        dest = self.root / "Plex TV"
        self.run_tool([str(self.show), "--organize", str(dest), "--yes", "--tvdb-id-folder"], ["1"])
        season = dest / "The Office (US) (2005) {tvdb-73244}" / "Season 02"
        self.assertIn("The Office (US) (2005) - s02e06 - The Fight.mkv", self.names(season))

    def test_search_again_and_pick_by_id(self):
        self.run_tool([str(self.root), "--yes"], ["id 999"])
        self.assertIn("By Id (2000) - s02e05 - Halloween.mkv", self.names())

    def test_skip_show(self):
        before = self.names()
        self.run_tool([str(self.root)], ["s"])
        self.assertEqual(self.names(), before)


class ApiClientTests(unittest.TestCase):
    """Checks the client reads TheTVDB v4 response shapes correctly (no network)."""

    def test_search_and_episodes_paging(self):
        client = tr.TVDB("key")
        client.token = "t"
        pages = {
            0: {"data": {"episodes": [{"seasonNumber": 1, "number": 1, "name": "Pilot"}]},
                "links": {"next": "more"}},
            1: {"data": {"episodes": [{"seasonNumber": 1, "number": 2, "name": "Two"},
                                      {"seasonNumber": None, "number": 3}]},
                "links": {"next": None}},
        }

        def fake(method, path, params=None, body=None, auth=True, _retry=True):
            if path == "/search":
                return {"data": [{"tvdb_id": "73244", "name": "The Office",
                                  "translations": {"eng": "The Office (US)"}, "year": "2005"}]}
            return pages[params["page"]]

        with mock.patch.object(client, "_request", side_effect=fake):
            r = client.search_series("office")
            self.assertEqual((r[0]["id"], r[0]["name"], r[0]["year"]), (73244, "The Office (US)", 2005))
            eps = client.get_episodes(73244)
            self.assertEqual(eps[(1, 2)]["name"], "Two")
            self.assertEqual(len(eps), 2)


if __name__ == "__main__":
    unittest.main()

"""Tests for tvrename.py. Run with:  python3 -m unittest -v"""
import json
import tempfile
import urllib.error
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
                         "The Office (US) (2005) - S02E05 - Halloween.mkv")

    def test_illegal_characters(self):
        s = {"id": 1, "name": "Star Trek: Discovery", "year": 2017}
        pf = tr.parse_file(Path("x.S01E01.mkv"))
        self.assertEqual(tr.build_new_name(s, pf, ['What? "Now"/Then']),
                         "Star Trek - Discovery (2017) - S01E01 - What Now-Then.mkv")

    def test_year_not_doubled(self):
        s = {"id": 1, "name": "Doctor Who (2005)", "year": 2005}
        self.assertEqual(tr.show_label(s), "Doctor Who (2005)")

    def test_multi_episode_name(self):
        pf = tr.parse_file(Path("x.S01E01E02.mkv"))
        self.assertEqual(tr.build_new_name(self.series, pf, ["Pilot", "Pilot"]),
                         "The Office (US) (2005) - S01E01-E02 - Pilot.mkv")


class ExtensionTests(unittest.TestCase):
    series = {"id": 1, "name": "American Restoration", "year": 2010}

    def test_every_extension_is_kept_exactly(self):
        exts = sorted(tr.VIDEO_EXTS | tr.SUBTITLE_EXTS)
        exts += [e.upper() for e in exts] + [".Mkv"]
        titles = ["Plain", "Mr. Rogers' Pinball", "Part 1...", "Version 2.0", "Ends With.mp4"]
        for ext in exts:
            for title in titles:
                original = Path(f"American.Restoration.S01E01.720p{ext}")
                pf = tr.parse_file(original)
                new = tr.build_new_name(self.series, pf, [title])
                self.assertTrue(new.endswith(ext), f"{original.name} -> {new}")
                self.assertEqual(Path(new).suffix, ext, f"{original.name} -> {new}")

    def fix(self, original, typed):
        pf = tr.parse_file(Path(original))
        return tr.keep_original_extension(Path(typed), pf).name

    def test_typed_name_without_extension(self):
        self.assertEqual(self.fix("Show.S01E01.avi", "My Name"), "My Name.avi")

    def test_typed_name_with_wrong_extension_is_replaced(self):
        self.assertEqual(self.fix("Show.S01E01.mkv", "My Name.mp4"), "My Name.mkv")
        self.assertEqual(self.fix("Show.S01E01.MKV", "My Name.mkv"), "My Name.MKV")

    def test_typed_subtitle_keeps_language_tag(self):
        self.assertEqual(self.fix("Show.S01E01.en.srt", "My Name"), "My Name.en.srt")
        self.assertEqual(self.fix("Show.S01E01.en.srt", "My Name.srt"), "My Name.en.srt")
        self.assertEqual(self.fix("Show.S01E01.en.srt", "My Name.en.srt"), "My Name.en.srt")


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
        self.run_tool([str(self.root), "--no-folders"], ["1", "y", "y", "n"])
        self.assertEqual(self.names(), [
            "The Office (US) (2005) - S02E05 - Halloween.en.srt",
            "The Office (US) (2005) - S02E05 - Halloween.mkv",
            "The.Office.S02E06.720p.mkv",  # answered no
            "The.Office.S02E99.mkv",       # not on TVDB, skipped
            "notes.txt",
        ])

    def test_dry_run_changes_nothing(self):
        before = self.names()
        self.run_tool([str(self.root), "--dry-run"], ["1"])
        self.assertEqual(self.names(), before)

    def test_all_then_undo(self):
        self.run_tool([str(self.root), "--no-folders", "--log", str(self.root / "log.jsonl")], ["1", "a"])
        self.assertIn("The Office (US) (2005) - S02E06 - The Fight.mkv", self.names())
        log = [json.loads(l) for l in (self.root / "log.jsonl").read_text().splitlines()]
        self.assertEqual(len(log), 3)
        self.run_tool(["--undo", str(self.root / "log.jsonl")], [])
        self.assertIn("The.Office.S02E06.720p.mkv", self.names())
        self.assertIn("The.Office.S02E05.720p.en.srt", self.names())

    def test_organize(self):
        dest = self.root / "Plex TV"
        self.run_tool([str(self.show), "--organize", str(dest), "--yes", "--tvdb-id-folder"], ["1"])
        season = dest / "The Office (US) (2005) {tvdb-73244}" / "Season 02"
        self.assertIn("The Office (US) (2005) - S02E06 - The Fight.mkv", self.names(season))

    def test_search_again_and_pick_by_id(self):
        self.run_tool([str(self.root), "--yes", "--no-folders"], ["id 999"])
        self.assertIn("By Id (2000) - S02E05 - Halloween.mkv", self.names())

    def test_lowercase_name_upgraded_to_capitals(self):
        old = self.show / "The Office (US) (2005) - s01e01 - Pilot.mkv"
        old.write_text("x")
        self.run_tool([str(old), "--yes", "--show", "The Office"], ["1"])
        self.assertIn("The Office (US) (2005) - S01E01 - Pilot.mkv", self.names())
        self.assertNotIn(old.name, self.names())

    def test_edit_with_wrong_extension_keeps_original(self):
        # pick show, then for each file: edit and type a name ending in .mp4
        self.run_tool([str(self.show / "The.Office.S02E06.720p.mkv")],
                      ["1", "e", "Custom Name.mp4"])
        self.assertIn("Custom Name.mkv", self.names())
        self.assertNotIn("Custom Name.mp4", self.names())
        self.assertNotIn("Custom Name.mp4.mkv", self.names())

    def test_skip_show(self):
        before = self.names()
        self.run_tool([str(self.root)], ["s"])
        self.assertEqual(self.names(), before)


class FolderTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)

    def tearDown(self):
        self.tmp.cleanup()

    def make(self, *rels):
        for rel in rels:
            f = self.root / rel
            f.parent.mkdir(parents=True, exist_ok=True)
            f.write_text("x")

    def run_tool(self, argv, answers):
        with mock.patch.object(tr, "TVDB", FakeTVDB), \
             mock.patch("builtins.input", side_effect=list(answers)), \
             mock.patch("builtins.print"):
            return tr.main(argv + ["--api-key", "dummy"])

    def tree(self):
        return sorted(str(p.relative_to(self.root)) for p in self.root.rglob("*")
                      if not p.name.startswith("tvrename-log") and p.name != "log.jsonl")

    def test_show_and_season_folders_renamed_then_undone(self):
        self.make("The Office/Season 2/The.Office.S02E05.mkv",
                  "The Office/S1/The.Office.S01E01.mkv")
        before = self.tree()
        log = self.root / "log.jsonl"
        self.run_tool([str(self.root), "--yes", "--log", str(log)], ["1"])
        self.assertEqual(self.tree(), [
            "The Office (US) (2005)",
            "The Office (US) (2005)/Season 01",
            "The Office (US) (2005)/Season 01/The Office (US) (2005) - S01E01 - Pilot.mkv",
            "The Office (US) (2005)/Season 02",
            "The Office (US) (2005)/Season 02/The Office (US) (2005) - S02E05 - Halloween.mkv",
        ])
        self.run_tool(["--undo", str(log)], [])
        self.assertEqual(self.tree(), before)

    def test_asks_before_each_folder(self):
        self.make("The Office/Season 2/The.Office.S02E05.mkv")
        # pick show, rename file: yes, season folder: no, show folder: yes
        self.run_tool([str(self.root)], ["1", "y", "n", "y"])
        self.assertTrue((self.root / "The Office (US) (2005)" / "Season 2").is_dir())

    def test_mixed_folder_is_never_renamed(self):
        # Loose episodes from two shows straight inside "TV" - "TV" must stay "TV".
        self.make("TV/The.Office.S02E05.mkv", "TV/Other.Show.S01E01.mkv")
        self.run_tool([str(self.root / "TV"), "--yes"], ["1", "1"])
        self.assertTrue((self.root / "TV").is_dir())
        self.assertEqual(len(list(self.root.iterdir())), 1)

    def test_unrelated_folder_name_is_not_renamed(self):
        self.make("Downloads/The.Office.S02E05.mkv")
        self.run_tool([str(self.root / "Downloads"), "--yes"], ["1"])
        self.assertTrue((self.root / "Downloads").is_dir())

    def test_per_episode_folders_left_alone(self):
        self.make("The Office/The.Office.S02E05.720p/The.Office.S02E05.720p.mkv",
                  "The Office/The.Office.S02E06.720p/The.Office.S02E06.720p.mkv")
        self.run_tool([str(self.root), "--yes"], ["1"])
        self.assertIn("The Office/The.Office.S02E05.720p", self.tree())

    def test_pointed_at_season_folder(self):
        self.make("The Office/Season 2/The.Office.S02E05.mkv")
        self.run_tool([str(self.root / "The Office" / "Season 2"), "--yes"], ["1"])
        # Season folder renamed; the show folder above it was not scanned, so it's left alone.
        self.assertTrue((self.root / "The Office" / "Season 02").is_dir())
        logs = list((self.root / "The Office" / "Season 02").glob("tvrename-log-*.jsonl"))
        self.assertEqual(len(logs), 1)

    def test_no_padding_and_specials(self):
        self.make("The Office/Season 2/The.Office.S02E05.mkv",
                  "The Office/Specials/The.Office.S00E01.mkv")
        with mock.patch.object(FakeTVDB, "get_episodes",
                               return_value={(2, 5): {"name": "Halloween"}, (0, 1): {"name": "Extra"}}):
            self.run_tool([str(self.root), "--yes", "--no-season-padding"], ["1"])
        show = self.root / "The Office (US) (2005)"
        self.assertTrue((show / "Season 2").is_dir())
        self.assertTrue((show / "Specials").is_dir())

    def test_existing_target_folder_not_merged(self):
        self.make("The Office/Season 2/The.Office.S02E05.mkv",
                  "The Office/Season 02/keep.txt")
        self.run_tool([str(self.root), "--yes"], ["1"])
        show = self.root / "The Office (US) (2005)"
        self.assertTrue((show / "Season 2").is_dir())
        self.assertEqual(sorted(p.name for p in (show / "Season 02").iterdir()), ["keep.txt"])

    def test_no_folders_flag(self):
        self.make("The Office/Season 2/The.Office.S02E05.mkv")
        self.run_tool([str(self.root), "--yes", "--no-folders"], ["1"])
        self.assertTrue((self.root / "The Office" / "Season 2").is_dir())


class GuiApiTests(unittest.TestCase):
    """Drives the browser app's server functions directly (no browser, no network)."""

    def setUp(self):
        import tvrename_gui as gui
        self.gui = gui
        gui.SESSION = gui.Session()
        gui.SESSION.tvdb = FakeTVDB()
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name) / "The Office"
        for rel in ["Season 2/The.Office.S02E05.mkv", "Season 2/The.Office.S02E06.avi",
                    "Season 2/The.Office.S02E99.mkv"]:
            (self.root / rel).parent.mkdir(parents=True, exist_ok=True)
            (self.root / rel).write_text("x")

    def tearDown(self):
        self.tmp.cleanup()

    def tree(self):
        base = self.root.parent
        return sorted(str(p.relative_to(base)) for p in base.rglob("*") if "tvrename-log" not in p.name)

    def test_scan_plan_apply_undo(self):
        before = self.tree()
        scan = self.gui.api_scan({"path": str(self.root)})
        self.assertEqual(len(scan["groups"]), 1)
        group = scan["groups"][0]["key"]
        series = FakeTVDB().search_series("x")[0]
        plan = self.gui.api_plan({"group": group, "series": series, "options": {}})
        status = {r["old"]: r["status"] for r in plan["files"]}
        self.assertEqual(status["The.Office.S02E99.mkv"], "missing")
        self.assertEqual([f["new"] for f in plan["folders"]], ["Season 02", "The Office (US) (2005)"])

        rows = [{"id": r["id"], "new": r["new"]} for r in plan["files"] + plan["folders"]
                if r["status"] == "ok"]
        # User edits one name and types the wrong extension: it must stay .avi
        for r in rows:
            if r["new"].endswith(".avi"):
                r["new"] = "Edited Name.mkv"
        done = self.gui.api_apply({"plan_id": plan["plan_id"], "rows": rows})
        self.assertEqual(done["failed"], 0)
        self.assertEqual(self.tree(), [
            "The Office (US) (2005)",
            "The Office (US) (2005)/Season 02",
            "The Office (US) (2005)/Season 02/Edited Name.avi",
            "The Office (US) (2005)/Season 02/The Office (US) (2005) - S02E05 - Halloween.mkv",
            "The Office (US) (2005)/Season 02/The.Office.S02E99.mkv",
        ])
        self.assertTrue(done["root"].endswith("The Office (US) (2005)"))

        undone = self.gui.api_undo({"log": done["log"]})
        self.assertEqual(undone["restored"], 4)
        self.assertEqual(self.tree(), before)
        self.assertEqual(undone["root"], str(self.root))

    def test_unticked_rows_are_left_alone(self):
        scan = self.gui.api_scan({"path": str(self.root)})
        plan = self.gui.api_plan({"group": scan["groups"][0]["key"],
                                  "series": FakeTVDB().search_series("x")[0], "options": {"no_folders": True}})
        self.assertEqual(plan["folders"], [])
        done = self.gui.api_apply({"plan_id": plan["plan_id"], "rows": []})
        self.assertEqual(done["renamed"], 0)
        self.assertIn("Season 2/The.Office.S02E05.mkv", [str(p.relative_to(self.root)) for p in self.root.rglob("*")])

    def test_requests_need_the_secret_token(self):
        import threading, urllib.request
        from http.server import ThreadingHTTPServer
        server = ThreadingHTTPServer(("127.0.0.1", 0), self.gui.Handler)
        threading.Thread(target=server.serve_forever, daemon=True).start()
        try:
            url = f"http://127.0.0.1:{server.server_address[1]}"
            req = urllib.request.Request(url + "/api/scan", data=b"{}", method="POST")
            with self.assertRaises(urllib.error.HTTPError) as cm:
                urllib.request.urlopen(req, timeout=5)
            self.assertEqual(cm.exception.code, 403)
            with self.assertRaises(urllib.error.HTTPError) as cm:
                urllib.request.urlopen(url + "/", timeout=5)
            self.assertEqual(cm.exception.code, 403)
            with urllib.request.urlopen(f"{url}/?t={self.gui.TOKEN}", timeout=5) as resp:
                self.assertIn(b"TV Renamer", resp.read())
        finally:
            server.shutdown()


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


# --------------------------------------------------------------------------- #
# Movies + clean-up
# --------------------------------------------------------------------------- #
import movierename as mr


class FakeMovieTVDB(FakeTVDB):
    DB = [
        {"id": 169, "name": "The Matrix", "year": 1999, "network": "", "overview": "", "image": ""},
        {"id": 170, "name": "The Matrix Reloaded", "year": 2003, "network": "", "overview": "", "image": ""},
        {"id": 300, "name": "Inception", "year": 2010, "network": "", "overview": "", "image": ""},
        {"id": 400, "name": "Avatar", "year": 2009, "network": "", "overview": "", "image": ""},
        {"id": 500, "name": "1917", "year": 2019, "network": "", "overview": "", "image": ""},
    ]

    def search_movies(self, q, year=None):
        q = mr._norm(q)
        hits = [m for m in self.DB if q and q in mr._norm(m["name"])]
        return [m for m in hits if m["year"] == year] if year else hits

    def get_movie(self, mid):
        return next(m for m in self.DB if m["id"] == mid)


class MovieParseTests(unittest.TestCase):
    def test_titles(self):
        cases = {
            "The.Matrix.1999.1080p.BluRay.x264-GRP": ("The Matrix", 1999, None),
            "Blade.Runner.2049.2017.2160p.UHD": ("Blade Runner 2049", 2017, None),
            "1917.2019.1080p.WEBRip": ("1917", 2019, None),
            "Avatar.2009.CD2.XviD": ("Avatar", 2009, 2),
            "Movie.Name.1080p.BluRay": ("Movie Name", None, None),
            "Up (2009)": ("Up", 2009, None),
        }
        for raw, want in cases.items():
            self.assertEqual(mr.parse_title(raw), want, raw)

    def test_ranking(self):
        ranked = mr.rank_matches("The Matrix", 1999, list(reversed(FakeMovieTVDB.DB)))
        self.assertEqual((ranked[0]["id"], ranked[0]["confidence"]), (169, "sure"))
        self.assertEqual(mr.rank_matches("Matrix", None, FakeMovieTVDB.DB)[0]["confidence"], "check")


class MovieGuiTests(unittest.TestCase):
    def setUp(self):
        import tvrename_gui as gui
        self.gui = gui
        gui.SESSION = gui.Session()
        gui.SESSION.tvdb = FakeMovieTVDB()
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name) / "Movies"
        files = {
            # own release folder, obfuscated file name, subtitles in a Subs folder, junk
            "The.Matrix.1999.1080p.BluRay.x264-GRP/a8f7e6d5c4b3a2f1.mkv": "video",
            "The.Matrix.1999.1080p.BluRay.x264-GRP/Subs/2_English.srt": "sub",
            "The.Matrix.1999.1080p.BluRay.x264-GRP/Subs/3_Spanish.srt": "sub",
            "The.Matrix.1999.1080p.BluRay.x264-GRP/The.Matrix.1999.nfo": "junk",
            "The.Matrix.1999.1080p.BluRay.x264-GRP/Sample/sample-matrix.mkv": "junk",
            # loose files in the Movies folder
            "Inception.2010.1080p.BrRip.x264.mp4": "video",
            "Inception.2010.1080p.BrRip.x264.en.srt": "sub",
            "Avatar.2009.CD1.avi": "video",
            "Avatar.2009.CD2.avi": "video",
            "poster.jpg": "art",
            "Unpack.Me.rar": "archive",
        }
        for rel in files:
            f = self.root / rel
            f.parent.mkdir(parents=True, exist_ok=True)
            f.write_text("x")

    def tearDown(self):
        self.tmp.cleanup()

    def tree(self):
        return sorted(str(p.relative_to(self.root)) for p in self.root.rglob("*")
                      if not p.name.startswith("tvrename-"))

    def match_all(self):
        scan = self.gui.api_scan({"path": str(self.root), "mode": "movies"})
        return {m["title"]: self.gui.api_movie_match({"id": m["id"]}) for m in scan["movies"]}

    def test_scan_groups_movies_and_subtitles(self):
        scan = self.gui.api_scan({"path": str(self.root), "mode": "movies"})
        titles = sorted((m["title"], m["year"], len(m["files"])) for m in scan["movies"])
        self.assertEqual(titles, [("Avatar", 2009, 2), ("Inception", 2010, 2), ("The Matrix", 1999, 3)])

    def test_rename_then_undo(self):
        before = self.tree()
        rows = self.match_all()
        self.assertEqual({t: r["confidence"] for t, r in rows.items()},
                         {"Avatar": "sure", "Inception": "sure", "The Matrix": "sure"})
        done = self.gui.api_movie_apply({"ids": [r["id"] for r in rows.values()]})
        self.assertEqual(done["failed"], 0, done)
        after = self.tree()
        for expected in [
            "Avatar (2009)/Avatar (2009) - pt1.avi",
            "Avatar (2009)/Avatar (2009) - pt2.avi",
            "Inception (2010)/Inception (2010).mp4",
            "Inception (2010)/Inception (2010).en.srt",
            "The Matrix (1999)/The Matrix (1999).mkv",
            "The Matrix (1999)/The Matrix (1999).en.srt",
            "The Matrix (1999)/The Matrix (1999).es.srt",
            "The Matrix (1999)/The.Matrix.1999.nfo",       # junk untouched by renaming
            "poster.jpg",
        ]:
            self.assertIn(expected, after)
        self.assertNotIn("The Matrix (1999)/Subs", after)  # emptied Subs folder tidied away
        self.gui.api_undo({"log": done["log"]})
        self.assertEqual(self.tree(), before)

    def test_extensions_kept_with_hand_typed_name(self):
        rows = self.match_all()
        mid = rows["Inception"]["id"]
        row = self.gui.api_movie_set({"id": mid, "label": "My Movie (2010).mkv"})
        self.assertEqual([c["new"] for c in row["changes"]][1:], ["My Movie (2010).mp4", "My Movie (2010).en.srt"])
        row = self.gui.api_movie_set({"id": mid, "label": "My Movie (2010)"})
        self.gui.api_movie_apply({"ids": [mid]})
        self.assertIn("My Movie (2010)/My Movie (2010).mp4", self.tree())

    def test_choose_different_match(self):
        rows = self.match_all()
        mid = rows["The Matrix"]["id"]
        row = self.gui.api_movie_set({"id": mid, "match": FakeMovieTVDB.DB[1]})
        self.assertEqual(row["label"], "The Matrix Reloaded (2003)")

    def test_cleanup_lists_and_deletes_only_extras(self):
        self.gui.api_scan({"path": str(self.root), "mode": "movies"})
        extras = self.gui.api_extras({})
        found = {i["rel"]: (i["reason"], i["default"]) for i in extras["items"]}
        self.assertEqual(found, {
            "The.Matrix.1999.1080p.BluRay.x264-GRP/Sample/sample-matrix.mkv": ("Sample video", True),
            "The.Matrix.1999.1080p.BluRay.x264-GRP/The.Matrix.1999.nfo": ("Info file", True),
            "Unpack.Me.rar": ("Archive (may still hold a video that wasn't unpacked)", False),
            "poster.jpg": ("Artwork Plex can use", False),
        })
        ids = [i["id"] for i in extras["items"] if i["default"]]
        res = self.gui.api_clean({"ids": ids})
        self.assertEqual((res["deleted"], res["folders"], res["failed"]), (2, 1, []))
        tree = self.tree()
        self.assertNotIn("The.Matrix.1999.1080p.BluRay.x264-GRP/Sample", tree)  # empty folder removed
        self.assertIn("poster.jpg", tree)
        self.assertIn("Unpack.Me.rar", tree)
        self.assertIn("The.Matrix.1999.1080p.BluRay.x264-GRP/a8f7e6d5c4b3a2f1.mkv", tree)
        self.assertEqual(len(list(self.root.glob("tvrename-cleanup-*.jsonl"))), 1)

    def test_cleanup_refuses_media_files(self):
        self.gui.api_scan({"path": str(self.root), "mode": "movies"})
        self.gui.api_extras({})
        # Someone tampers with the list: a real movie must never be deleted
        movie = self.root / "Inception.2010.1080p.BrRip.x264.mp4"
        self.gui.SESSION.extras["x99"] = movie
        res = self.gui.api_clean({"ids": ["x99"]})
        self.assertEqual(res["deleted"], 0)
        self.assertTrue(movie.exists())

    def test_tv_cleanup_too(self):
        show = Path(self.tmp.name) / "Show"
        (show / "Season 1").mkdir(parents=True)
        (show / "Season 1" / "Show.S01E01.mkv").write_text("x")
        (show / "Season 1" / "Show.S01E01.nfo").write_text("x")
        (show / "Season 1" / "Show.S01E01.en.srt").write_text("x")
        self.gui.api_scan({"path": str(show)})
        extras = self.gui.api_extras({})
        self.assertEqual([i["rel"] for i in extras["items"]], ["Season 1/Show.S01E01.nfo"])

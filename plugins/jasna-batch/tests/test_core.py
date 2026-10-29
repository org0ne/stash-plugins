"""Tests for jasna_batch_core (no Stash needed):
python3 -m unittest -v plugins.jasna-batch.tests.test_core  -- or from this
folder: python3 -m unittest discover -s tests"""
import os
import sys
import tempfile
import unittest
import xml.etree.ElementTree as ET

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import jasna_batch_core as core  # noqa: E402

NFO = """<?xml version='1.0' encoding='utf-8'?>
<movie>
  <plot>Rio &amp; co. &lt;br&gt;Second line</plot>
  <lockdata>false</lockdata>
  <dateadded>2020-09-07 16:12:36</dateadded>
  <title>Rio Hamazaki - Huge-tits Creampie Paradise: Rio Hamasaki [MIGD-100]</title>
  <title_translated>2026-09-13T11:06:17</title_translated>
  <originaltitle>爆乳中出し天国 浜崎りお</originaltitle>
  <year>2008</year>
  <stashid endpoint="javstash">34298c4e</stashid>
  <URL>https://www.dmm.co.jp/mono/dvd/-/detail/=/cid=migd100/</URL>
  <genre>Big Tits</genre>
  <genre>Orgy</genre>
  <genre>LTX</genre>
  <studio>MOODYZ</studio>
  <art>
    <poster>/storage/pool/old/folder.jpg</poster>
    <fanart>/storage/pool/old/fanart.jpg</fanart>
  </art>
  <playcount>0</playcount>
  <resume><position>0</position></resume>
  <actor>
    <name>Rio Hamazaki</name>
    <role>Actress</role>
    <thumb>/var/lib/jellyfin/metadata/People/R/Rio Hamazaki/poster.jpg</thumb>
  </actor>
  <fileinfo><streamdetails><video><codec>mpeg4</codec></video></streamdetails></fileinfo>
</movie>
"""


class Presets(unittest.TestCase):
    def test_parse(self):
        presets, problems = core.parse_presets(
            "LTX = --restoration-model-name ltx --codec hevc --cq 24 # standard; "
            "LTX hq = --restoration-model-name ltx-8step --lut '/p/My LUT.cube'")
        self.assertEqual(problems, [])
        self.assertEqual([p.name for p in presets], ["LTX", "LTX hq"])
        self.assertEqual(presets[0].flags, ["--restoration-model-name", "ltx", "--codec", "hevc", "--cq", "24"])
        self.assertEqual(presets[0].comment, "standard")
        self.assertEqual(presets[0].tag, "Jasna: LTX")
        self.assertEqual(presets[1].flags[-1], "/p/My LUT.cube")

    def test_problems(self):
        presets, problems = core.parse_presets(
            "bad = --output /x; = --cq 3; Done = --cq 3; ok = --cq 3; ok = --cq 4; noflag = cq 3; empty = ")
        self.assertEqual([p.name for p in presets], ["ok"])
        self.assertEqual(len(problems), 6)
        self.assertTrue(any("--output" in p for p in problems))
        self.assertTrue(any("reserved" in p for p in problems))
        self.assertTrue(any("duplicate" in p for p in problems))


class Naming(unittest.TestCase):
    def test_marker(self):
        self.assertEqual(core.insert_marker("ABP-241 Ai Yuzuki - Title (2015-01-10)", "[LTX]"),
                         "ABP-241 Ai Yuzuki - [LTX] Title (2015-01-10)")
        self.assertEqual(core.insert_marker("A - B - C", "[LTX]"), "A - [LTX] B - C")
        self.assertEqual(core.insert_marker("No dash here", "[LTX]"), "[LTX] No dash here")
        self.assertEqual(core.insert_marker("A - [LTX] B", "[LTX]"), "A - [LTX] B")
        self.assertEqual(core.insert_marker("A - B", ""), "A - B")

    def test_output_plan(self):
        plan = core.plan_output("/lib/Censored/Censored HD/MIGD-100 Rio - Title (2008-02-01)/MIGD-100.mkv",
                                "/lib/LTX/", "LTX", "[LTX]")
        self.assertEqual(plan.dest_dir, "/lib/LTX/MIGD-100 Rio - [LTX] Title (2008-02-01)")
        self.assertEqual(plan.new_stem, "MIGD-100_LTX_jasna")
        self.assertEqual(plan.video, "/lib/LTX/MIGD-100 Rio - [LTX] Title (2008-02-01)/MIGD-100_LTX_jasna.mp4")
        self.assertEqual(core.output_stem("SNIS-317", "av1 fast"), "SNIS-317_av1-fast_jasna")
        # a second part of the same release lands in the same folder
        plan2 = core.plan_output("/lib/Censored/Censored HD/MIGD-100 Rio - Title (2008-02-01)/MIGD-100-pt2.mkv",
                                 "/lib/LTX", "LTX", "[LTX]")
        self.assertEqual(plan2.dest_dir, plan.dest_dir)
        self.assertEqual(plan2.new_stem, "MIGD-100-pt2_LTX_jasna")


class OutputNames(unittest.TestCase):
    def test_round_trip(self):
        for stem, preset in [("MIGD-100", "LTX"), ("ABC-1_part2", "LTX hq"), ("x", "v1.2+fast")]:
            name = core.output_stem(stem, preset) + core.OUTPUT_EXT
            self.assertEqual(core.parse_output_name("/root/dir/" + name), (stem, core.preset_slug(preset)))

    def test_other_files(self):
        for name in ["MIGD-100.mkv", "MIGD-100_LTX_jasna.nfo", "MIGD-100_LTX_jasna-jasna-command.txt",
                     "MIGD-100_jasna.mp4", "_LTX_jasna.mp4", "fanart.jpg", "MIGD-100_LTX_jasna.mp4.bak"]:
            self.assertIsNone(core.parse_output_name(name), name)

    def test_preset_with_underscore_needs_the_preset_list(self):
        name = core.output_stem("MIGD-100", "LTX_hq") + ".mp4"
        self.assertEqual(core.parse_output_name(name, ["LTX", "LTX_hq"]), ("MIGD-100", "LTX_hq"))
        self.assertEqual(core.parse_output_name(name), ("MIGD-100_LTX", "hq"))   # best guess without it
        self.assertEqual(core.parse_output_name("A_LTX_jasna.mp4", ["LTX_jasna"]), ("A", "LTX"))


class Sidecars(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.src_dir = os.path.join(self.tmp, "MKD-S147 Ayumi - Title (2018)")
        os.makedirs(os.path.join(self.src_dir, "extrafanart"))
        os.makedirs(os.path.join(self.src_dir, ".actors"))
        for name in ["MKD-S147-pt1.mp4", "MKD-S147-pt1.nfo", "MKD-S147-pt1.en.srt", "MKD-S147-pt1.vtt",
                     "MKD-S147-pt1-whisper-command.txt", "MKD-S147-pt1_CAPS.jpg", "MKD-S147-pt1.nfo.bak",
                     "MKD-S147-pt10.mp4", "MKD-S147-pt10.en.srt",
                     "MKD-S147-pt2.mp4", "MKD-S147-pt2.nfo", "MKD-S147-pt2.en.srt",
                     "fanart.jpg", "folder.jpg", "cover.jpg", ".lada_completed", "x.webloc"]:
            with open(os.path.join(self.src_dir, name), "w") as fh:
                fh.write(name)

    def test_plan_for_one_part(self):
        plan = core.plan_output(os.path.join(self.src_dir, "MKD-S147-pt1.mp4"), os.path.join(self.tmp, "LTX"),
                                "LTX", "[LTX]")
        side = core.plan_sidecars(plan, ["*.bak", ".lada_*"])
        renamed = sorted(os.path.basename(d) for _, d in side.renames)
        self.assertEqual(renamed, ["MKD-S147-pt1_LTX_jasna-whisper-command.txt", "MKD-S147-pt1_LTX_jasna.en.srt",
                                   "MKD-S147-pt1_LTX_jasna.vtt", "MKD-S147-pt1_LTX_jasna_CAPS.jpg"])
        self.assertEqual(sorted(os.path.basename(d) for _, d in side.copies), ["cover.jpg", "fanart.jpg", "folder.jpg", "x.webloc"])
        self.assertEqual(sorted(os.path.basename(d) for _, d in side.trees), [".actors", "extrafanart"])
        self.assertEqual(os.path.basename(side.nfo[1]), "MKD-S147-pt1_LTX_jasna.nfo")
        skipped = dict(side.skipped)
        self.assertEqual(skipped["MKD-S147-pt2.en.srt"], "belongs to MKD-S147-pt2")
        self.assertEqual(skipped["MKD-S147-pt10.en.srt"], "belongs to MKD-S147-pt10")
        self.assertEqual(skipped["MKD-S147-pt1.nfo.bak"], "ignore pattern")
        self.assertEqual(skipped[".lada_completed"], "ignore pattern")
        self.assertEqual(skipped["MKD-S147-pt1.mp4"], "video file")
        self.assertNotIn("MKD-S147-pt2.nfo", renamed)

    def test_stem_bound(self):
        self.assertTrue(core.stem_bound("CODE-1.en.srt", "CODE-1"))
        self.assertTrue(core.stem_bound("CODE-1-whisper-command.txt", "CODE-1"))
        self.assertTrue(core.stem_bound("CODE-1_CAPS.jpg", "CODE-1"))
        self.assertFalse(core.stem_bound("CODE-10.en.srt", "CODE-1"))
        self.assertFalse(core.stem_bound("fanart.jpg", "CODE-1"))


class Nfo(unittest.TestCase):
    def test_edit(self):
        r = core.edit_nfo(NFO, "[LTX]", ["LTX", "Jasna"])
        self.assertEqual(r.title_after, "Rio Hamazaki - [LTX] Huge-tits Creampie Paradise: Rio Hamasaki [MIGD-100]")
        self.assertEqual(r.genres_added, ["Jasna"])     # LTX was already there
        root = ET.fromstring(r.text)
        self.assertEqual([g.text for g in root.findall("genre")], ["Big Tits", "Orgy", "LTX", "Jasna"])
        for gone in ("art", "lockdata", "dateadded", "playcount", "resume", "fileinfo"):
            self.assertIsNone(root.find(gone), gone)
        self.assertIsNone(root.find("actor/thumb"))
        self.assertEqual(root.find("actor/name").text, "Rio Hamazaki")
        self.assertEqual(root.find("stashid").get("endpoint"), "javstash")
        self.assertEqual(root.find("originaltitle").text, "爆乳中出し天国 浜崎りお")
        self.assertTrue(r.text.startswith("<?xml version='1.0' encoding='utf-8'?>\n<movie>"))
        self.assertNotIn("\n\n", r.text)
        # genres sit right after the last original genre, before <studio>
        tags = [c.tag for c in root]
        self.assertEqual(tags[tags.index("studio") - 1], "genre")
        # plot kept its escaped HTML without clean_html
        self.assertIn("&lt;br&gt;", r.text)

    def test_clean_html_and_no_genres(self):
        r = core.edit_nfo(NFO, "[LTX]", [], clean_html=True)
        root = ET.fromstring(r.text)
        self.assertEqual(root.find("plot").text, "Rio & co. Second line")
        self.assertEqual(r.genres_added, [])

    def test_no_genre_block_goes_before_actor(self):
        text = "<?xml version='1.0'?><movie><title>A - B</title><actor><name>X</name></actor></movie>"
        r = core.edit_nfo(text, "[LTX]", ["LTX"])
        self.assertEqual([c.tag for c in ET.fromstring(r.text)], ["title", "genre", "actor"])

    def test_bare_ampersand_is_fixed(self):
        text = "<movie><title>A - B</title><URL>https://x/?a=1&b=2</URL></movie>"
        r = core.edit_nfo(text, "[LTX]", [])
        self.assertTrue(r.fixed_ampersands)
        self.assertEqual(ET.fromstring(r.text).find("URL").text, "https://x/?a=1&b=2")

    def test_idempotent(self):
        once = core.edit_nfo(NFO, "[LTX]", ["LTX", "Jasna"]).text
        twice = core.edit_nfo(once, "[LTX]", ["LTX", "Jasna"]).text
        self.assertEqual(once, twice)


class Sidecar(unittest.TestCase):
    def test_command_sidecar(self):
        job = {"id": "abc", "scene_id": "5", "preset": "LTX", "flags": ["--codec", "hevc"],
               "command": ["/opt/jasna", "--input", "/a b/x.mkv", "--output", "/s/abc.mp4"],
               "stash_source": "/a b/x.mkv", "stash_output": "/out/x_LTX_jasna.mp4", "created": 1700000000,
               "started": 1700000100, "finished": 1700007300, "duration_s": 7200.0, "exit_code": 0,
               "attempts": 1, "jasna_version": "0.11.3", "source_bytes": 3e9, "output_bytes": 12e9,
               "notes": ["interrupted once"]}
        text = core.command_sidecar(job, core.Preset("LTX", ["--codec", "hevc"], "standard"), "0.1.0")
        self.assertIn("Duration         : 2h 00m 00s", text)
        self.assertIn("'/a b/x.mkv'", text)
        self.assertIn("# standard", text)
        self.assertIn("Jasna version    : 0.11.3", text)
        self.assertIn("  - interrupted once", text)


if __name__ == "__main__":
    unittest.main()


class NodeTests(unittest.TestCase):
    """Bridge URL setting -> nodes, and the scheduler over their loads."""

    def test_empty_setting_is_the_default_single_node(self):
        nodes, problems = core.parse_nodes("", "http://127.0.0.1:8770/jasna/")
        self.assertEqual(problems, [])
        self.assertEqual([(n.name, n.url, n.speed, n.only) for n in nodes],
                         [("bridge", "http://127.0.0.1:8770/jasna", 1.0, [])])

    def test_one_bare_url_keeps_the_old_behaviour(self):
        nodes, problems = core.parse_nodes("http://host:8770/jasna/", "x")
        self.assertEqual(problems, [])
        self.assertEqual((nodes[0].name, nodes[0].url), ("bridge", "http://host:8770/jasna"))

    def test_named_nodes_with_speed_and_only(self):
        text = ("debeast = http://127.0.0.1:8770/jasna speed=3;\n"
                "rtx3060=http://192.168.11.157:8770/jasna only=LTX, LTX hq speed=1")
        nodes, problems = core.parse_nodes(text, "x")
        self.assertEqual(problems, [])
        self.assertEqual([n.name for n in nodes], ["debeast", "rtx3060"])
        self.assertEqual(nodes[0].speed, 3.0)
        self.assertEqual(nodes[1].only, ["LTX", "LTX hq"])
        self.assertTrue(nodes[0].accepts("anything"))
        self.assertTrue(nodes[1].accepts("ltx hq"))
        self.assertFalse(nodes[1].accepts("fast"))
        self.assertEqual(nodes[1].describe(), "http://192.168.11.157:8770/jasna only=LTX, LTX hq")

    def test_bare_urls_among_several_are_named_by_host(self):
        nodes, problems = core.parse_nodes("http://127.0.0.1:8770/jasna; http://gpu2:8770/jasna", "x")
        self.assertEqual(problems, [])
        self.assertEqual([n.name for n in nodes], ["local", "gpu2"])

    def test_bad_entries_are_skipped_and_described(self):
        text = "x = nope; a = http://a:1 speed=-1; b = http://b:1 foo=1; a = http://a:1; a = http://a:2"
        nodes, problems = core.parse_nodes(text, "x")
        self.assertEqual([n.name for n in nodes], ["a"])
        self.assertEqual(len(problems), 4)
        self.assertIn("expected a bridge URL", problems[0])
        self.assertIn("speed must be a positive number", problems[1])
        self.assertIn("unknown option 'foo'", problems[2])
        self.assertIn("duplicate node name", problems[3])

    def test_estimate_load_counts_queued_and_the_rest_of_running(self):
        jobs = [{"state": "queued", "scene_id": "1"},
                {"state": "running", "scene_id": "2", "progress": {"percent": 75}},
                {"state": "queued", "scene_id": "unknown"},
                {"state": "done", "scene_id": "1"}]
        load = core.estimate_load(jobs, {"1": 3600.0, "2": 4000.0}.get, default_s=600.0)
        self.assertEqual(load, 3600.0 + 1000.0 + 600.0)

    def test_rank_nodes_divides_by_speed_and_keeps_order_on_a_tie(self):
        fast, slow = core.Node("fast", "u", speed=3), core.Node("slow", "u")
        self.assertEqual([n.name for n, _ in core.rank_nodes([(fast, 9000.0), (slow, 3100.0)])], ["fast", "slow"])
        self.assertEqual([n.name for n, _ in core.rank_nodes([(fast, 9000.0), (slow, 2900.0)])], ["slow", "fast"])
        self.assertEqual([n.name for n, _ in core.rank_nodes([(slow, 0.0), (fast, 0.0)])], ["slow", "fast"])

    def test_command_sidecar_names_the_node(self):
        text = core.command_sidecar({"id": "j1", "node": "rtx3060", "node_url": "http://h:8770/jasna"}, None, "0.2.0")
        self.assertIn("Bridge node      : rtx3060 (http://h:8770/jasna)", text)

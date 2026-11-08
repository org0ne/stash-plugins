"""Tests for jasna_batch_core (no Stash needed):
python3 -m unittest -v plugins.jasna-batch.tests.test_core  -- or from this
folder: python3 -m unittest discover -s tests"""
import json
import os
import sys
import tempfile
import unittest
import unittest.mock
import xml.etree.ElementTree as ET

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import jasna_batch_core as core  # noqa: E402
import jasna_batch_upscale as upscale  # noqa: E402

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
        self.assertEqual(presets[0].tag, "Jasna-Batch Queue: LTX")
        self.assertEqual(presets[1].flags[-1], "/p/My LUT.cube")

    def test_problems(self):
        presets, problems = core.parse_presets(
            "bad = --output /x; = --cq 3; LTX hq = --cq 3; LTX-hq = --cq 3; ok = --cq 3; ok = --cq 4; "
            "noflag = cq 3; empty = ")
        self.assertEqual([p.name for p in presets], ["LTX hq", "ok"])
        self.assertEqual(len(problems), 6)
        self.assertTrue(any("--output" in p for p in problems))
        self.assertTrue(any("'LTX-hq': names its output files like 'LTX hq'" in p for p in problems))
        self.assertTrue(any("duplicate" in p for p in problems))

    def test_status_names_are_free_for_presets(self):
        presets, problems = core.parse_presets("Done = --cq 3")
        self.assertEqual((problems, presets[0].tag), ([], "Jasna-Batch Queue: Done"))
        self.assertEqual(core.legacy_trigger_tag("LTX"), "Jasna: LTX")
        self.assertEqual(core.preset_child_tag("Jasna-Batch: Restored", "LTX"), "Jasna-Batch: Restored - LTX")


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


class Upscale(unittest.TestCase):
    def test_preset_flag(self):
        presets, problems = core.parse_presets("Plain = --codec hevc; A = --upscale --codec hevc; B = -upscale --codec hevc")
        self.assertEqual(problems, [])
        self.assertEqual([p.upscale for p in presets], [False, True, True])

    def test_sidecar_names_the_method(self):
        self.assertNotIn("Upscale", core.command_sidecar({"flags": ["--codec", "hevc"]}, None, "0"))
        self.assertIn(f"Upscale          : {core.UPSCALE_METHOD}", core.command_sidecar({"flags": ["--upscale"]}, None, "0"))

    def test_jasna_command_swaps_the_input(self):
        for flag in core.UPSCALE_FLAGS:
            argv = ["jasna", "--input", "a.avi", "--output", "o.mp4", flag, "--cq", "24"]
            self.assertEqual(upscale.jasna_command(argv, "up.mkv"),
                             ["jasna", "--input", "up.mkv", "--output", "o.mp4", "--cq", "24"])

    def test_container_rate_skips_clock_ticks(self):
        self.assertEqual(str(upscale.source_rate({"avg_frame_rate": "30000/1001", "r_frame_rate": "60/1"})), "30000/1001")
        # HPK-004.avi: the average is a 1 ms clock, the base rate is right
        self.assertEqual(str(upscale.source_rate({"avg_frame_rate": "1000/1", "r_frame_rate": "30000/1001"})), "30000/1001")
        # WMV: no average, a 1 ms base
        self.assertIsNone(upscale.source_rate({"avg_frame_rate": "0/0", "r_frame_rate": "1000/1"}))
        self.assertIsNone(upscale.source_rate({}))

    def test_space_estimate_counts_the_filled_gaps(self):
        # DJSK-004.wmv: 1626 packets over 180 s become 5400 frames
        times = [i * 0.1107 for i in range(1626)]
        self.assertGreaterEqual(upscale.estimate_frames(times, None), 5390)
        self.assertEqual(upscale.estimate_frames([i / 30 for i in range(300)], None), 300)

    def test_pipeline_retimes_from_decoded_frame_times(self):
        stream = {"avg_frame_rate": "30000/1001", "height": 480, "sample_aspect_ratio": "8:9"}
        cmd = upscale.pipeline_command("in.avi", "out.mkv", stream, 900, "r.json")
        for flag, value in (("--fps", "auto"), ("--expect-frames", "900"), ("--report", "r.json"),
                            ("--source-rate", "30000/1001"), ("--sar", "8/9")):
            self.assertEqual(cmd[cmd.index(flag) + 1], value)
        self.assertIn("--retime", cmd); self.assertNotIn("--cfr", cmd)
        self.assertNotIn("--source-rate", upscale.pipeline_command("in.wmv", "o.mkv", {"height": 400}, 9, "r"))

    def test_color_follows_the_source(self):
        self.assertEqual(upscale.source_matrix({"height": 480}), "170m")
        self.assertEqual(upscale.source_matrix({"height": 720}), "709")
        self.assertEqual(upscale.source_matrix({"height": 480, "color_space": "bt709"}), "709")
        self.assertEqual(upscale.color_flags({"height": 480, "color_primaries": "unknown"}), ["-colorspace", "smpte170m"])

    def test_only_audio_an_mp4_refuses_is_converted(self):
        streams = '{"streams": [{"codec_name": "aac", "channels": 2}, {"codec_name": "wmapro", "channels": 6}]}'
        with unittest.mock.patch.object(upscale, "ffprobe", return_value=streams), \
                unittest.mock.patch.object(upscale, "fits_mp4", side_effect=lambda p, i: i == 0), \
                unittest.mock.patch("builtins.print"):
            self.assertEqual(upscale.audio_args("s.wmv"),
                             ["-c:a:0", "copy", "-c:a:1", "aac", "-b:a:1", "512k"])
        with unittest.mock.patch.object(upscale, "ffprobe", return_value=streams), \
                unittest.mock.patch.object(upscale, "fits_mp4", return_value=True):
            self.assertIsNone(upscale.audio_args("s.mp4"))

    def test_wrapper_check_names_what_is_missing(self):
        with unittest.mock.patch.object(upscale, "TOOLKIT", "/nonexistent"), \
                unittest.mock.patch("builtins.print") as printed:
            self.assertEqual(upscale.check(), 1)
        self.assertIn("/nonexistent/vs_pipeline.py", printed.call_args[0][0])


class AutoLut(unittest.TestCase):
    def test_preset_flag(self):
        presets, _ = core.parse_presets("LTX = --codec hevc; LTX auto = -auto-lut --codec hevc; "
                                        "LTX 2x auto = --upscale --auto-lut")
        self.assertEqual([p.auto_lut for p in presets], [False, True, True])
        self.assertEqual([p.wrapper_flags for p in presets], [[], ["--auto-lut"], ["--upscale", "--auto-lut"]])

    def test_sidecar_points_at_the_lut_record(self):
        text = core.command_sidecar({"preset": "LTX auto", "flags": ["--auto-lut"], "command": []}, None, "x")
        self.assertIn(".lut.json", text)

    def test_lut_sidecars_follow_the_restored_file(self):
        with tempfile.TemporaryDirectory() as d:
            src = os.path.join(d, "Src")
            os.makedirs(src)
            for n in ("MIGD-100.mkv", "MIGD-100.lut.cube", "MIGD-100.lut.json"):
                open(os.path.join(src, n), "w").close()
            plan = core.plan_output(os.path.join(src, "MIGD-100.mkv"), os.path.join(d, "LTX"), "LTX auto", "[LTX]")
            names = sorted(os.path.basename(b) for _, b in core.plan_sidecars(plan).renames)
        self.assertEqual(names, ["MIGD-100_LTX-auto_jasna.lut.cube", "MIGD-100_LTX-auto_jasna.lut.json"])

    def run_wrapper_step(self, argv, result):
        lines = ["[lut] measuring\n", "RESULT " + json.dumps(result) + "\n"]
        proc = unittest.mock.MagicMock(stdout=iter(lines), returncode=0)
        with unittest.mock.patch.object(upscale, "lut_python", return_value="/py"), \
                unittest.mock.patch.object(upscale.subprocess, "Popen", return_value=proc) as popen, \
                unittest.mock.patch("builtins.print"):
            out = upscale.with_auto_lut(argv)
        return out, popen.call_args[0][0]

    ARGV = ["/jasna", "--input", "/lib/A.avi", "--output", "/o.mp4", "--working-directory", "/w", "--auto-lut", "--cq", "24"]

    def test_wrapper_swaps_the_flag_for_the_lut(self):
        out, cmd = self.run_wrapper_step(self.ARGV, {"lut": "/lib/A.lut.cube", "blend": 1.0})
        self.assertEqual(out, ["/jasna", "--input", "/lib/A.avi", "--output", "/o.mp4", "--working-directory", "/w",
                               "--cq", "24", "--lut", "/lib/A.lut.cube"])
        self.assertEqual(cmd[2:], ["/lib/A.avi", "--fallback-dir", "/w/lut"])

    def test_wrapper_drops_the_flag_when_no_lut_is_needed(self):
        out, _ = self.run_wrapper_step(self.ARGV, {"lut": None, "blend": 0.0})
        self.assertNotIn("--auto-lut", out)
        self.assertNotIn("--lut", out)

    def test_wrapper_fails_on_a_lut_step_error(self):
        with self.assertRaises(upscale.UpscaleError):
            self.run_wrapper_step(self.ARGV, {"error": "only 3 of 80 frames could be measured"})

    def test_wrapper_refuses_both_flags(self):
        with self.assertRaises(upscale.UpscaleError):
            upscale.with_auto_lut(self.ARGV + ["--lut", "/x.cube"])


try:
    import numpy as np
    import jasna_batch_lut as lut
except ImportError:   # the LUT step runs under a Python with numpy; skip its tests elsewhere
    lut = None


@unittest.skipIf(lut is None, "numpy not installed")
class LutBlend(unittest.TestCase):
    def test_cutoffs_match_the_videos_judged_by_eye(self):
        # (warmth, skin R/G) measured 2026-11-06 -> what looked right
        full = {"EMU-089": (0.290, 0.250), "BBI-147": (0.256, 0.218), "KISD-019": (0.260, 0.274)}
        none = {"MEYD-057": (0.285, 0.312), "MIDA-817": (0.060, 0.303), "WANZ-322": (0.248, 0.304),
                "XVSR-552": (0.300, 0.416), "IH-43": (0.061, 0.187)}
        for name, (w, s) in full.items():
            self.assertEqual(lut.blend_for(w, s), 1.0, name)
        for name, (w, s) in none.items():
            self.assertLess(lut.blend_for(w, s), 0.02, name)
        self.assertGreater(lut.blend_for(0.240, 0.293), 0, "ONED-913")

    def test_blend_ends(self):
        base = np.random.default_rng(1).random((4, 4, 4, 3)).astype(np.float32)
        ident = lut.blend_lut(base, 0.0)
        self.assertTrue(np.allclose(ident[1, 2, 3], [3 / 3, 2 / 3, 1 / 3]))   # [b][g][r] -> (r, g, b)
        self.assertTrue(np.allclose(lut.blend_lut(base, 1.0), base))

    def test_cube_round_trip(self):
        base = np.random.default_rng(2).random((5, 5, 5, 3)).astype(np.float32)
        with tempfile.TemporaryDirectory() as d:
            path = os.path.join(d, "x.cube")
            lut.save_cube(path, base)
            with open(path) as f:
                self.assertEqual(f.readline(), "LUT_3D_SIZE 5\n")
            self.assertTrue(np.allclose(lut.read_cube(path), base, atol=1e-6))

    def test_manual_record_is_applied_without_measuring(self):
        with tempfile.TemporaryDirectory() as d:
            src = os.path.join(d, "A.avi")
            open(src, "w").close()
            with open(os.path.join(d, "A.lut.json"), "w") as f:
                json.dump({"blend": 0.4, "manual": True}, f)
            base = os.path.join(d, "base.cube")
            lut.save_cube(base, lut.blend_lut(np.zeros((3, 3, 3, 3), np.float32), 0.0))
            with unittest.mock.patch.object(lut, "BASE_LUT", base), \
                    unittest.mock.patch.object(lut, "measure") as measure, \
                    unittest.mock.patch("builtins.print"):
                result = lut.run(src)
            measure.assert_not_called()
            self.assertEqual(result["lut"], os.path.join(d, "A.lut.cube"))
            with open(os.path.join(d, "A.lut.json")) as f:
                self.assertEqual(json.load(f)["lut"], "A.lut.cube")

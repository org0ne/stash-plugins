"""Tests for censored_archiver_core (no Stash needed): from the plugin
folder, python3 -m unittest discover -s tests"""
import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import censored_archiver_core as core  # noqa: E402

R = "/lib/Sorted"
CEN = [f"{R}/Censored", f"{R}/Web Censored"]
REF = [f"{R}/LTX", f"{R}/Jasna DL"]


def scene(sid, code, *paths):
    return {"id": sid, "code": code, "files": [{"id": f"f{sid}{i}", "path": p} for i, p in enumerate(paths)]}


class Paths(unittest.TestCase):
    def test_parse_roots(self):
        self.assertEqual(core.parse_roots(" /a/Web Censored/ , //b,, /a/Web Censored"), ["/a/Web Censored", "/b"])
        self.assertEqual(core.parse_roots(""), [])

    def test_destination_mirrors_tree(self):
        folder = f"{R}/Censored/Censored HD/ABP-984 A - T (2020-06-12)"
        dest, why = core.archive_destination(folder, CEN, f"{R}/Archived")
        self.assertEqual(dest, f"{R}/Archived/Censored/Censored HD/ABP-984 A - T (2020-06-12)")
        dest, _ = core.archive_destination(f"{R}/Web Censored/Web Censored SD/X/", CEN, f"{R}/Archived")
        self.assertEqual(dest, f"{R}/Archived/Web Censored/Web Censored SD/X")

    def test_shallow_folders_never_move(self):
        for folder in (f"{R}/Censored", f"{R}/Censored/Censored HD"):
            dest, why = core.archive_destination(folder, CEN, f"{R}/Archived")
            self.assertIsNone(dest)
            self.assertIn("level", why)

    def test_already_archived_and_outside(self):
        self.assertEqual(core.archive_destination(f"{R}/Archived/Censored/HD/X", CEN, f"{R}/Archived")[1],
                         "already in the archive")
        self.assertIsNone(core.archive_destination(f"{R}/Censoredx/HD/X", CEN, f"{R}/Archived")[0])

    def test_nesting(self):
        self.assertEqual(core.nesting_problems({"c": CEN, "r": REF, "a": [f"{R}/Archived"]}), [])
        self.assertTrue(core.nesting_problems({"c": CEN, "a": [f"{R}/Censored/Archived"]}))


class Matching(unittest.TestCase):
    def setUp(self):
        self.scenes = [
            scene("1", " abp-984 ", f"{R}/Censored/Censored HD/ABP-984 x/ABP-984.mp4"),
            scene("2", "ABP-984", f"{R}/Jasna DL/ABP-984 y/ABP-984.mp4"),
            scene("3", "MIDA-844", f"{R}/Censored/Censored SD/MIDA-844 x/MIDA-844.mp4",
                  f"{R}/Uncensored/MIDA-844/MIDA-844.mp4"),
            scene("4", "mida-844", f"{R}/LTX/MIDA-844 [LTX]/MIDA-844.mp4"),
            scene("5", "SSIS-001", f"{R}/Censored/Censored HD/SSIS-001 x/SSIS-001.mp4"),
            scene("6", "", f"{R}/Censored/Censored HD/nocode/a.mp4"),
            scene("7", None, f"{R}/LTX/nocode/a.mp4"),
            scene("8", "M50RIO", f"{R}/Jasna-Test/M50RIO/a.mp4"),
            scene("9", "M50RIO", f"{R}/Censored/Censored HD/M50RIO x/a.mp4"),
        ]

    def test_reference_codes(self):
        self.assertEqual(core.reference_codes(self.scenes, REF), {"ABP-984": {"Jasna DL"}, "MIDA-844": {"LTX"}})

    def test_candidates(self):
        found = core.candidate_folders(self.scenes, CEN, core.reference_codes(self.scenes, REF))
        self.assertEqual(sorted(found), [f"{R}/Censored/Censored HD/ABP-984 x", f"{R}/Censored/Censored SD/MIDA-844 x"])
        mida = found[f"{R}/Censored/Censored SD/MIDA-844 x"]
        # Only the censored file of a multi-file scene moves.
        self.assertEqual([c["path"] for c in mida], [f"{R}/Censored/Censored SD/MIDA-844 x/MIDA-844.mp4"])
        self.assertEqual(mida[0]["matched"], ["LTX"])

    def test_merge_nested(self):
        a = [{"path": "/c/HD/X/X.mp4"}]
        b = [{"path": "/c/HD/X/extras/X-2.mp4"}]
        c = [{"path": "/c/HD/Y/Y.mp4"}]
        merged = core.merge_nested({"/c/HD/X/extras": b, "/c/HD/X": a, "/c/HD/Y": c})
        self.assertEqual(merged, {"/c/HD/X": a + b, "/c/HD/Y": c})
        self.assertEqual(core.merge_nested({"/c/HD/X1": a, "/c/HD/X": c}), {"/c/HD/X1": a, "/c/HD/X": c})


class OnDisk(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.src = os.path.join(self.tmp.name, "src")
        self.dst = os.path.join(self.tmp.name, "dst")
        os.makedirs(os.path.join(self.src, "extrafanart"))
        for name in ("ABP-984.mp4", "ABP-984.nfo", "ABP-984.en.srt", "fanart.jpg", "extrafanart/1.jpg"):
            open(os.path.join(self.src, name), "w").close()

    def tearDown(self):
        self.tmp.cleanup()

    def test_plan_and_collisions(self):
        moves, taken = core.plan_archive(self.src, self.dst)
        self.assertEqual(len(moves), 5)
        self.assertIn((os.path.join(self.src, "extrafanart", "1.jpg"),
                       os.path.join(self.dst, "extrafanart", "1.jpg")), moves)
        self.assertEqual(taken, [])
        os.makedirs(self.dst)
        open(os.path.join(self.dst, "fanart.jpg"), "w").close()
        self.assertEqual(core.plan_archive(self.src, self.dst)[1], ["fanart.jpg"])

    def test_stray_videos(self):
        video = os.path.join(self.src, "ABP-984.mp4")
        cands = [{"path": video}]
        self.assertEqual(core.stray_videos(self.src, cands, {video: {"id": "1"}}), [])
        other = os.path.join(self.src, "ABP-984-2.mp4")
        open(other, "w").close()
        self.assertEqual(core.stray_videos(self.src, cands, {}), ["ABP-984-2.mp4 (not a scene in Stash)"])
        self.assertEqual(core.stray_videos(self.src, cands, {other: {"id": "9", "code": "xyz-1"}}),
                         ["ABP-984-2.mp4 (scene 9, code XYZ-1)"])

    def test_remove_empty_dirs(self):
        for dirpath, _d, files in os.walk(self.src):
            for f in files:
                os.remove(os.path.join(dirpath, f))
        self.assertEqual(core.remove_empty_dirs(self.src), [])
        self.assertFalse(os.path.exists(self.src))

    def test_log_record(self):
        cands = [{"scene_id": "10", "code": "ABP-984", "matched": ["Jasna DL"]},
                 {"scene_id": "9", "code": "ABP-984", "matched": ["LTX"]}]
        rec = core.log_record("/s", "/d", cands, [], [("/s/a", "/d/a")], "boom")
        self.assertEqual(rec["scenes"], ["9", "10"])
        self.assertEqual(rec["matched"], ["Jasna DL", "LTX"])
        self.assertEqual(rec["sidecars"], [["/s/a", "/d/a"]])
        self.assertEqual(rec["error"], "boom")


if __name__ == "__main__":
    unittest.main()

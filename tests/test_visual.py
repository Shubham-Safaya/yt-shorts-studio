"""Offline tests for the optional visual check. Standard library only."""

import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
import analyze  # noqa: E402

FIXTURES = Path(__file__).parent / "fixtures"


def candidates(n=4):
    return [{"start": 10.0 * i, "end": 10.0 * i + 30, "text": f"moment {i}", "score": 10.0 - i} for i in range(n)]


def visual_reply(moments, stop_reason="end_turn"):
    return {"stop_reason": stop_reason, "content": [{"type": "text", "text": json.dumps({"moments": moments})}]}


@unittest.skipUnless(shutil.which("ffmpeg"), "ffmpeg not installed")
class FrameExtractionTests(unittest.TestCase):
    def test_extracts_three_frames_per_window(self):
        with tempfile.TemporaryDirectory() as tmp:
            video = Path(tmp) / "v.mp4"
            subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-f", "lavfi", "-i",
                            "testsrc=size=640x360:rate=5:duration=40", str(video)], check=True)
            frames = analyze.extract_frames(video, {"start": 5.0, "end": 35.0}, Path(tmp))
            self.assertEqual(len(frames), 3)
            self.assertTrue(all(f.read_bytes()[:2] == b"\xff\xd8" for f in frames))  # JPEG

    def test_window_past_the_end_yields_no_frames(self):
        with tempfile.TemporaryDirectory() as tmp:
            video = Path(tmp) / "v.mp4"
            subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-f", "lavfi", "-i",
                            "testsrc=size=320x180:rate=5:duration=5", str(video)], check=True)
            self.assertEqual(analyze.extract_frames(video, {"start": 100.0, "end": 130.0}, Path(tmp)), [])


class VisualRequestTests(unittest.TestCase):
    def test_labels_each_candidate_then_its_images(self):
        with tempfile.TemporaryDirectory() as tmp:
            f = Path(tmp) / "a.jpg"
            f.write_bytes(b"\xff\xd8fake")
            req = analyze.build_visual_request({0: [f, f], 2: [f]}, candidates(), "claude-opus-5-5")
        content = req["messages"][0]["content"]
        kinds = [b["type"] for b in content]
        self.assertEqual(kinds, ["text", "image", "image", "text", "image", "text"])
        self.assertTrue(content[0]["text"].startswith("Candidate 0"))
        self.assertTrue(content[3]["text"].startswith("Candidate 2"))
        self.assertEqual(content[1]["source"]["media_type"], "image/jpeg")
        schema = req["output_config"]["format"]["schema"]
        self.assertEqual(schema["properties"]["moments"]["items"]["properties"]["i"]["enum"], [0, 1, 2, 3])
        self.assertEqual(req["fallbacks"], "default")


class VisualResponseTests(unittest.TestCase):
    def test_parses_clamps_and_filters(self):
        data = visual_reply([{"i": 0, "visual_score": 14, "crop_ok": True, "note": " speaker centered "},
                             {"i": 9, "visual_score": 5, "crop_ok": True, "note": "bad index"}])
        out = analyze.parse_visual_response(data, 4)
        self.assertEqual(out, {0: {"score": 10, "crop_ok": True, "note": "speaker centered"}})

    def test_refusal_returns_none(self):
        self.assertIsNone(analyze.parse_visual_response(visual_reply([], "refusal"), 4))


class VisualCheckTests(unittest.TestCase):
    def test_attaches_visual_to_candidates(self):
        cands = candidates()
        reply = visual_reply([{"i": 1, "visual_score": 8, "crop_ok": False, "note": "speaker on left"}])
        with tempfile.TemporaryDirectory() as tmp, \
             mock.patch.dict(os.environ, {"ANTHROPIC_API_KEY": "k"}), \
             mock.patch.object(analyze, "extract_frames", lambda v, c, d: [Path(__file__)]), \
             mock.patch.object(analyze, "build_visual_request", lambda *a: {}), \
             mock.patch.object(analyze, "call_claude", lambda req, label: reply):
            video = Path(tmp) / "v.mp4"
            video.write_bytes(b"x")
            self.assertEqual(analyze.visual_check(video, cands), 1)
        self.assertEqual(cands[1]["visual"]["note"], "speaker on left")
        self.assertNotIn("visual", cands[0])

    def test_skips_without_key_or_video(self):
        with mock.patch.dict(os.environ, {"ANTHROPIC_API_KEY": ""}):
            self.assertEqual(analyze.visual_check(Path(__file__), candidates()), 0)
        with mock.patch.dict(os.environ, {"ANTHROPIC_API_KEY": "k"}):
            self.assertEqual(analyze.visual_check(Path("/nonexistent.mp4"), candidates()), 0)

    def test_visual_adjusted_penalizes_bad_crop(self):
        good = {"score": 10.0, "visual": {"score": 8, "crop_ok": True, "note": ""}}
        bad = {"score": 10.0, "visual": {"score": 8, "crop_ok": False, "note": ""}}
        plain = {"score": 10.0}
        self.assertGreater(analyze.visual_adjusted(good), analyze.visual_adjusted(plain))
        self.assertLess(analyze.visual_adjusted(bad), analyze.visual_adjusted(plain))

    def test_visual_info_reaches_the_clip_picker(self):
        cands = candidates(2)
        cands[0]["visual"] = {"score": 3, "crop_ok": False, "note": "slide"}
        req = analyze.build_claude_request(cands, "T", "m")
        items = json.loads(req["messages"][0]["content"].split("Candidates:\n", 1)[1])
        self.assertEqual(items[0]["visual"]["note"], "slide")
        self.assertNotIn("visual", items[1])


class EndToEndVisualTests(unittest.TestCase):
    def test_plan_shows_visual_verdict_and_heuristic_uses_it(self):
        def fake_check(video, cands):
            for c in cands:
                c["visual"] = {"score": 2, "crop_ok": False, "note": "slide with tiny text"}
            cands[-1]["visual"] = {"score": 10, "crop_ok": True, "note": "speaker close-up"}
            return len(cands)

        with tempfile.TemporaryDirectory() as out, \
             mock.patch.dict(os.environ, {"ANTHROPIC_API_KEY": "", "SHORTS_VIDEO": "work/x.mp4"}), \
             mock.patch.object(analyze, "visual_check", fake_check), \
             mock.patch.object(sys, "argv", ["analyze.py", str(FIXTURES / "sample.vtt"),
                                             str(FIXTURES / "sample.info.json"), out]):
            with mock.patch("sys.stdout"):
                analyze.main()
            plan = (Path(out) / "sample123.md").read_text()
        self.assertIn("**Visual:** 10/10, centered 9:16 crop keeps the subject. speaker close-up", plan)
        self.assertIn("centered crop cuts the subject, reframe by hand", plan)


if __name__ == "__main__":
    unittest.main()

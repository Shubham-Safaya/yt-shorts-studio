"""Offline tests for analyze.py. Standard library only: python -m unittest -v"""

import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
import analyze  # noqa: E402

FIXTURES = Path(__file__).parent / "fixtures"


def candidates(n=6):
    return [{"start": 10.0 * i, "end": 10.0 * i + 40, "text": f"moment {i}", "score": 1.0} for i in range(n)]


def api_reply(picks, stop_reason="end_turn"):
    return {"stop_reason": stop_reason,
            "content": [{"type": "thinking", "thinking": ""},
                        {"type": "text", "text": json.dumps({"picks": picks})}]}


class TranscriptTests(unittest.TestCase):
    def setUp(self):
        self.cues = analyze.parse_vtt((FIXTURES / "sample.vtt").read_text())

    def test_parse_vtt(self):
        self.assertEqual(len(self.cues), 24)
        self.assertEqual(self.cues[0]["text"], "Most people think the resume is the problem.")
        self.assertAlmostEqual(self.cues[1]["start"], 10.0)

    def test_windows_respect_length_bounds(self):
        wins = analyze.windows(self.cues)
        self.assertTrue(wins)
        for w in wins:
            self.assertGreaterEqual(w["end"] - w["start"], 22.0)
            self.assertLess(w["end"] - w["start"], 60.0)

    def test_score_rewards_hooks(self):
        hooky = {"start": 0, "end": 40, "text": "The truth is most people fail. Why?"}
        flat = {"start": 0, "end": 40, "text": "and then we went to the store"}
        self.assertGreater(analyze.score(hooky), analyze.score(flat))


class ClaudeRequestTests(unittest.TestCase):
    def test_request_uses_schema_effort_and_fallback(self):
        req = analyze.build_claude_request(candidates(3), "T", "claude-opus-5-5")
        fmt = req["output_config"]["format"]
        self.assertEqual(fmt["type"], "json_schema")
        item = fmt["schema"]["properties"]["picks"]["items"]
        self.assertEqual(item["properties"]["i"]["enum"], [0, 1, 2])
        self.assertFalse(item["additionalProperties"])
        self.assertEqual(req["fallbacks"], "default")
        self.assertEqual(req["model"], "claude-opus-5-5")


class ClaudeResponseTests(unittest.TestCase):
    def test_parses_and_sorts_picks(self):
        data = api_reply([
            {"i": 4, "hook": "Hook B", "caption": "Cap B", "hashtags": "#b"},
            {"i": 1, "hook": "Hook A", "caption": "Cap A", "hashtags": "#a"},
        ])
        out = analyze.parse_claude_response(data, candidates())
        self.assertEqual([p["hook"] for p in out], ["Hook A", "Hook B"])
        self.assertEqual(out[0]["caption"], "Cap A\n\n#a")

    def test_drops_duplicate_and_out_of_range_indices(self):
        data = api_reply([
            {"i": 2, "hook": "x", "caption": "c", "hashtags": ""},
            {"i": 2, "hook": "dup", "caption": "c", "hashtags": ""},
            {"i": 99, "hook": "bad", "caption": "c", "hashtags": ""},
        ])
        out = analyze.parse_claude_response(data, candidates())
        self.assertEqual([p["hook"] for p in out], ["x"])

    def test_caps_at_max_picks(self):
        data = api_reply([{"i": i, "hook": str(i), "caption": "c", "hashtags": ""} for i in range(8)])
        self.assertEqual(len(analyze.parse_claude_response(data, candidates(8))), analyze.MAX_PICKS)

    def test_refusal_and_truncation_fall_back(self):
        for stop in ("refusal", "max_tokens"):
            self.assertIsNone(analyze.parse_claude_response(api_reply([], stop), candidates()))

    def test_empty_picks_fall_back(self):
        self.assertIsNone(analyze.parse_claude_response(api_reply([]), candidates()))

    def test_no_key_skips_network(self):
        with mock.patch.dict(os.environ, {"ANTHROPIC_API_KEY": ""}), \
             mock.patch("urllib.request.urlopen") as urlopen:
            self.assertIsNone(analyze.claude_enhance(candidates(), "T"))
            urlopen.assert_not_called()


class EndToEndTests(unittest.TestCase):
    def test_main_writes_plan_and_cut_list_without_key(self):
        with tempfile.TemporaryDirectory() as out, \
             mock.patch.dict(os.environ, {"ANTHROPIC_API_KEY": ""}), \
             mock.patch.object(sys, "argv", ["analyze.py", str(FIXTURES / "sample.vtt"),
                                             str(FIXTURES / "sample.info.json"), out]):
            analyze.main()
            plan = (Path(out) / "sample123.md").read_text()
            cuts = json.loads((Path(out) / "sample123.clips.json").read_text())
        self.assertIn("# Shorts Plan — Sample talk", plan)
        self.assertIn("heuristic", plan)
        self.assertTrue(cuts)
        self.assertEqual(cuts, sorted(cuts, key=lambda c: c["start"]))


if __name__ == "__main__":
    unittest.main()

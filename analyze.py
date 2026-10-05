"""
YouTube -> Shorts/Reels analyzer.

Reads a video's auto-caption transcript (VTT, produced by yt-dlp) plus its
metadata, finds the strongest ~30-50s moments, and writes a "Shorts Plan":
ready-to-cut clip timestamps with a hook, a caption, and hashtags for both
Instagram Reels and YouTube Shorts. Also writes a short review of the video.

Pure standard library. Claude is optional (ANTHROPIC_API_KEY). Built for the user's OWN videos.
If an optional clipper step runs (ffmpeg), these timestamps drive the cuts.
"""

from __future__ import annotations

import base64
import json
import os
import re
import subprocess
import sys
import tempfile
import urllib.error
import urllib.request
from pathlib import Path

# Signals that a sentence is "clippable" — hooks, stakes, specifics, emotion.
HOOK_OPENERS = (
    "the truth is", "most people", "nobody tells you", "here's the thing",
    "the secret", "the biggest mistake", "what i learned", "if you want",
    "the reason", "let me tell you", "the problem with", "the key is",
    "i used to", "stop", "never", "always", "the one thing", "this is why",
    "here is", "the hardest part", "what nobody", "the difference between",
)
STRONG_WORDS = (
    "mistake", "secret", "fail", "failed", "win", "money", "salary", "offer",
    "rejected", "fired", "visa", "h1b", "interview", "resume", "promotion",
    "regret", "fear", "honest", "truth", "real", "proof", "framework",
    "strategy", "lesson", "story", "growth", "career", "identity", "privacy",
    "data", "ai", "product", "free", "fast", "best", "worst", "first",
)
CTA_LINES = (
    "Save this for your next push.",
    "Follow for more on tech careers and building in public.",
    "Which one hits home? Tell me below.",
    "Full breakdown on my channel.",
    "Comment if you want the longer version.",
)
HASHTAG_BANK = [
    "#sundayswithsafaya", "#techcareers", "#productmanagement", "#careeradvice",
    "#faang", "#jobsearch", "#interviewtips", "#buildinpublic", "#ai",
    "#datacareers", "#h1b", "#mastersinus",
]

TS = re.compile(r"(\d{2}):(\d{2}):(\d{2})[.,](\d{3})")


def parse_vtt(text: str) -> list[dict]:
    """Parse VTT/SRT into [{start, end, text}] with de-duplicated rolling caption lines."""
    cues, cur = [], None
    for line in text.splitlines():
        line = line.strip()
        m = TS.search(line)
        if m and "-->" in line:
            a, b = line.split("-->")
            cur = {"start": to_sec(a), "end": to_sec(b), "text": ""}
        elif cur is not None and line and not line.isdigit() and "WEBVTT" not in line:
            clean = re.sub(r"<[^>]+>", "", line)  # strip inline timing tags
            clean = re.sub(r"\s+", " ", clean).strip()
            if clean and clean not in cur["text"]:
                cur["text"] = (cur["text"] + " " + clean).strip()
            if cur["text"]:
                cues.append(cur)
                cur = None
    # collapse consecutive identical caption text (auto-caption rolling effect)
    out = []
    for c in cues:
        if out and c["text"] == out[-1]["text"]:
            out[-1]["end"] = c["end"]
        else:
            out.append(c)
    return out


def to_sec(s: str) -> float:
    m = TS.search(s)
    if not m:
        return 0.0
    h, mn, sec, ms = (int(x) for x in m.groups())
    return h * 3600 + mn * 60 + sec + ms / 1000


def fmt(sec: float) -> str:
    sec = int(sec)
    return f"{sec // 60:02d}:{sec % 60:02d}"


def windows(cues: list[dict], target=42.0, lo=22.0, hi=58.0) -> list[dict]:
    """Greedily group caption cues into ~target-second windows on sentence-ish breaks."""
    out, i = [], 0
    while i < len(cues):
        start = cues[i]["start"]
        text = []
        j = i
        while j < len(cues) and cues[j]["end"] - start < hi:
            text.append(cues[j]["text"])
            if cues[j]["end"] - start >= target and cues[j]["text"].rstrip().endswith((".", "?", "!")):
                j += 1
                break
            j += 1
        end = cues[min(j, len(cues)) - 1]["end"]
        if end - start >= lo:
            out.append({"start": start, "end": end, "text": " ".join(text).strip()})
        i = max(j, i + 1)
    return out


def score(w: dict) -> float:
    t = w["text"].lower()
    s = 0.0
    for h in HOOK_OPENERS:
        if h in t:
            s += 6
    s += sum(2 for k in STRONG_WORDS if k in t)
    s += t.count("?") * 2.5            # questions hook viewers
    s += len(re.findall(r"\b\d+\b", t)) * 1.5  # concrete numbers
    dur = w["end"] - w["start"]
    s += 4 if 28 <= dur <= 50 else 0   # ideal short length
    words = len(t.split())
    s += 3 if 60 <= words <= 150 else 0
    return s


def hook_line(text: str) -> str:
    first = re.split(r"(?<=[.?!])\s+", text.strip())[0]
    return (first[:110] + "…") if len(first) > 110 else first


def caption(text: str, idx: int) -> str:
    hook = hook_line(text)
    cta = CTA_LINES[idx % len(CTA_LINES)]
    tags = " ".join(HASHTAG_BANK[:6] + [HASHTAG_BANK[6 + (idx % 6)]])
    return f"{hook}\n\n{cta}\n\n{tags}"


CLAUDE_URL = "https://api.anthropic.com/v1/messages"
DEFAULT_MODEL = "claude-opus-5-5"
FALLBACK_BETA = "server-side-fallback-2026-07-01"
MAX_PICKS = 5
SYSTEM_PROMPT = (
    "You are an elite short-form video editor who turns long talks into viral "
    "Shorts/Reels for a tech-career creator. From the candidate transcript moments, "
    f"choose the {MAX_PICKS} BEST standalone clips (each must make sense alone and have a strong "
    "hook in the first 2 seconds). For each, write a punchy on-screen hook (<=70 chars) "
    "and a caption (1-2 lines + a question), then 6-8 relevant hashtags. "
    "When a candidate has a `visual` review, prefer strong visuals and avoid crop_ok=false. "
    "Never use em dashes anywhere; use commas or periods."
)


def picks_schema(n_candidates: int) -> dict:
    """JSON schema for Claude's reply. `i` is limited to real candidate indices."""
    return {
        "type": "object",
        "properties": {
            "picks": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "i": {"type": "integer", "enum": list(range(n_candidates))},
                        "hook": {"type": "string"},
                        "caption": {"type": "string"},
                        "hashtags": {"type": "string"},
                    },
                    "required": ["i", "hook", "caption", "hashtags"],
                    "additionalProperties": False,
                },
            }
        },
        "required": ["picks"],
        "additionalProperties": False,
    }


def build_claude_request(candidates: list[dict], title: str, model: str) -> dict:
    items = [{"i": i, "start": round(c["start"], 1), "end": round(c["end"], 1),
              "text": c["text"][:600], **({"visual": c["visual"]} if c.get("visual") else {})}
             for i, c in enumerate(candidates)]
    return {
        "model": model,
        "max_tokens": 16000,
        "system": SYSTEM_PROMPT,
        "messages": [{"role": "user",
                      "content": f"Video: {title}\nCandidates:\n{json.dumps(items, ensure_ascii=False)}"}],
        "output_config": {
            "effort": "medium",
            "format": {"type": "json_schema", "schema": picks_schema(len(candidates))},
        },
        # A safety decline is re-run on Anthropic's recommended fallback model.
        "fallbacks": "default",
    }


def parse_claude_response(data: dict, candidates: list[dict]) -> list[dict] | None:
    """Turn an API response into picks, or None to fall back to the heuristic."""
    stop = data.get("stop_reason")
    if stop in ("refusal", "max_tokens"):
        print(f"(Claude enhance skipped: stop_reason={stop})")
        return None
    text = next((b.get("text", "") for b in data.get("content", []) if b.get("type") == "text"), "")
    parsed = json.loads(text)  # structured outputs guarantee valid JSON for the schema
    out, seen = [], set()
    for p in parsed.get("picks", []):
        i = p.get("i")
        if not isinstance(i, int) or not 0 <= i < len(candidates) or i in seen:
            continue
        seen.add(i)
        cap = p.get("caption", "").strip()
        tags = p.get("hashtags", "").strip()
        out.append({**candidates[i], "hook": p.get("hook", "").strip(),
                    "caption": (cap + ("\n\n" + tags if tags else "")).strip()})
        if len(out) == MAX_PICKS:
            break
    out.sort(key=lambda w: w["start"])
    return out or None


def model_name() -> str:
    return os.getenv("SHORTS_MODEL", "").strip() or DEFAULT_MODEL


def call_claude(request: dict, label: str) -> dict | None:
    """POST one Messages request. Pure stdlib HTTP so the Action needs no extra
    dependency. Returns the response JSON, or None on any failure."""
    key = os.getenv("ANTHROPIC_API_KEY", "").strip()
    if not key:
        return None
    req = urllib.request.Request(
        CLAUDE_URL, data=json.dumps(request).encode(),
        headers={"content-type": "application/json", "x-api-key": key,
                 "anthropic-version": "2023-06-01", "anthropic-beta": FALLBACK_BETA})
    try:
        with urllib.request.urlopen(req, timeout=300) as r:
            return json.loads(r.read())
    except urllib.error.HTTPError as e:
        detail = e.read().decode("utf-8", "ignore")[:300]
        print(f"({label} skipped: HTTP {e.code} {detail})")
    except Exception as e:
        print(f"({label} skipped: {e})")
    return None


def claude_enhance(candidates: list[dict], title: str) -> list[dict] | None:
    """Optional: if ANTHROPIC_API_KEY is set, let Claude pick the best clips and
    write punchier hooks + captions. Returns enhanced picks, or None to fall back
    to the heuristic."""
    if not os.getenv("ANTHROPIC_API_KEY", "").strip() or not candidates:
        return None
    data = call_claude(build_claude_request(candidates, title, model_name()), "Claude enhance")
    if data is None:
        return None
    try:
        return parse_claude_response(data, candidates)
    except (ValueError, KeyError, TypeError) as e:
        print(f"(Claude enhance skipped: {e})")
        return None


# ── Optional visual check (needs the video file + ANTHROPIC_API_KEY) ──
# Adapted from the multimodal video moment finder in awesome-llm-apps, using
# Claude's vision on a few sampled frames instead of an embedding index: it
# judges how each candidate LOOKS and whether clip.py's centered 9:16 crop
# keeps the speaker in frame, which a transcript cannot tell you.

FRAME_POSITIONS = (0.2, 0.5, 0.8)  # sample points inside each candidate window
VISUAL_SYSTEM = (
    "You review frames from candidate moments of a long video that will be cut into "
    "vertical 9:16 Shorts with a CENTERED crop (the middle ~56% of a 16:9 frame). "
    "For each candidate, score visual strength 0-10 (a clear, expressive, well-lit "
    "speaker or striking visual scores high; slides with small text, black or static "
    "frames, or an empty set score low). Set crop_ok to false if a centered vertical "
    "crop would cut off the main subject. Keep each note under 15 words."
)


def extract_frames(video: Path, cand: dict, out_dir: Path) -> list[Path]:
    """Grab small JPEG frames inside one candidate window with ffmpeg."""
    frames = []
    for k, pos in enumerate(FRAME_POSITIONS):
        t = cand["start"] + (cand["end"] - cand["start"]) * pos
        out = out_dir / f"{int(cand['start'])}_{k}.jpg"
        subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-ss", f"{t:.2f}", "-i", str(video),
                        "-frames:v", "1", "-vf", "scale=480:-2", "-q:v", "5", str(out)], check=False)
        if out.exists() and out.stat().st_size > 0:
            frames.append(out)
    return frames


def visual_schema(n_candidates: int) -> dict:
    return {
        "type": "object",
        "properties": {"moments": {"type": "array", "items": {
            "type": "object",
            "properties": {
                "i": {"type": "integer", "enum": list(range(n_candidates))},
                "visual_score": {"type": "integer"},
                "crop_ok": {"type": "boolean"},
                "note": {"type": "string"},
            },
            "required": ["i", "visual_score", "crop_ok", "note"],
            "additionalProperties": False,
        }}},
        "required": ["moments"],
        "additionalProperties": False,
    }


def build_visual_request(frames_by_i: dict[int, list[Path]], candidates: list[dict], model: str) -> dict:
    content = []
    for i, frames in sorted(frames_by_i.items()):
        c = candidates[i]
        content.append({"type": "text", "text": f"Candidate {i} ({fmt(c['start'])}-{fmt(c['end'])}):"})
        for f in frames:
            content.append({"type": "image", "source": {
                "type": "base64", "media_type": "image/jpeg",
                "data": base64.b64encode(f.read_bytes()).decode()}})
    content.append({"type": "text", "text": "Score every candidate above."})
    return {
        "model": model,
        "max_tokens": 16000,
        "system": VISUAL_SYSTEM,
        "messages": [{"role": "user", "content": content}],
        "output_config": {"effort": "low",
                          "format": {"type": "json_schema", "schema": visual_schema(len(candidates))}},
        "fallbacks": "default",
    }


def parse_visual_response(data: dict, n_candidates: int) -> dict[int, dict] | None:
    if data.get("stop_reason") in ("refusal", "max_tokens"):
        print(f"(Visual check skipped: stop_reason={data.get('stop_reason')})")
        return None
    text = next((b.get("text", "") for b in data.get("content", []) if b.get("type") == "text"), "")
    out = {}
    for m in json.loads(text).get("moments", []):
        i = m.get("i")
        if isinstance(i, int) and 0 <= i < n_candidates:
            out[i] = {"score": max(0, min(10, int(m.get("visual_score", 0)))),
                      "crop_ok": bool(m.get("crop_ok", True)), "note": str(m.get("note", "")).strip()}
    return out or None


def visual_check(video: Path, candidates: list[dict]) -> int:
    """Attach c['visual'] to candidates. Returns how many were scored."""
    if not candidates or not video.exists() or not os.getenv("ANTHROPIC_API_KEY", "").strip():
        return 0
    with tempfile.TemporaryDirectory() as tmp:
        frames_by_i = {i: f for i, c in enumerate(candidates) if (f := extract_frames(video, c, Path(tmp)))}
        if not frames_by_i:
            print("(Visual check skipped: no frames extracted)")
            return 0
        data = call_claude(build_visual_request(frames_by_i, candidates, model_name()), "Visual check")
    if data is None:
        return 0
    try:
        scores = parse_visual_response(data, len(candidates))
    except (ValueError, KeyError, TypeError) as e:
        print(f"(Visual check skipped: {e})")
        return 0
    for i, v in (scores or {}).items():
        candidates[i]["visual"] = v
    return len(scores or {})


def visual_adjusted(w: dict) -> float:
    """Heuristic score nudged by the visual check, when it ran."""
    v = w.get("visual")
    if not v:
        return w["score"]
    return w["score"] + (v["score"] - 5) * 1.5 - (0 if v["crop_ok"] else 8)


def build_plan(video_id: str, title: str, url: str, dur_s: float, picks: list[dict], smart: bool) -> str:
    L = [
        f"# Shorts Plan — {title}",
        "",
        f"- **Source:** [{url}]({url}) · `{video_id}`",
        f"- **Length:** {fmt(dur_s)} · **Candidate clips found:** {len(picks)}",
        f"- **Selection:** {'Claude-picked (best-quality)' if smart else 'heuristic (set ANTHROPIC_API_KEY for Claude-picked clips)'}.",
        "- **Use:** cut these 9:16 (1080x1920), burn captions, hook in the first 2 seconds.",
        "- *Generated from auto-captions; tighten the exact in/out points by eye.*",
        "",
        "---",
        "",
    ]
    for i, w in enumerate(picks, 1):
        L += [
            f"## Clip {i}  ·  {fmt(w['start'])} → {fmt(w['end'])}  ({int(w['end']-w['start'])}s)",
            "",
            f"**Hook (first 2s, big text):** {w.get('hook') or hook_line(w['text'])}",
            "",
            *([f"**Visual:** {w['visual']['score']}/10, "
               f"{'centered 9:16 crop keeps the subject' if w['visual']['crop_ok'] else 'centered crop cuts the subject, reframe by hand'}. "
               f"{w['visual']['note']}", ""] if w.get("visual") else []),
            f"> {w['text']}",
            "",
            "**Caption (paste to Reels + Shorts):**",
            "",
            "```",
            w.get("caption") or caption(w["text"], i - 1),
            "```",
            "",
            "---",
            "",
        ]
    L += [
        "## Posting checklist",
        "- [ ] Cut each clip 9:16, captions burned in, hook visible in first 2 seconds",
        "- [ ] Post 3/week (Mon/Wed/Fri); same clip to YouTube Shorts + Instagram Reels",
        "- [ ] First comment = the question from the hook (drives replies)",
        "- [ ] Pin the best performer to your profile",
        "",
        "*Auto-posting to Reels/Shorts needs each platform's API + OAuth (Instagram Graph API, "
        "YouTube Data API). Captions above are ready to paste so posting is a 1-minute job.*",
    ]
    return "\n".join(L)


def build_review(title: str, dur_s: float, cues: list[dict], picks: list[dict]) -> str:
    words = sum(len(c["text"].split()) for c in cues)
    wpm = words / (dur_s / 60) if dur_s else 0
    themes = {}
    for k in STRONG_WORDS:
        n = sum(c["text"].lower().count(k) for c in cues)
        if n:
            themes[k] = n
    top = sorted(themes.items(), key=lambda x: x[1], reverse=True)[:8]
    return "\n".join([
        f"# Review — {title}",
        "",
        f"- Length **{fmt(dur_s)}**, ~**{words:,} words**, ~**{wpm:.0f} wpm** "
        f"({'brisk, good for shorts' if wpm>150 else 'measured, trim dead air in clips'}).",
        f"- **{len(picks)} strong short-worthy moments** detected.",
        f"- Recurring themes: {', '.join(f'{k} ({n})' for k,n in top) or 'n/a'}.",
        "",
        "**Verdict:** "
        + ("Lots of clippable moments here, ship 3-5 shorts from it."
           if len(picks) >= 3 else
           "Fewer obvious hooks, pick the 1-2 best and add a strong on-screen opener."),
        "",
    ])


def main():
    transcript = Path(sys.argv[1])
    meta = json.loads(Path(sys.argv[2]).read_text()) if len(sys.argv) > 2 and Path(sys.argv[2]).exists() else {}
    out_dir = Path(sys.argv[3]) if len(sys.argv) > 3 else Path("reviews")
    out_dir.mkdir(parents=True, exist_ok=True)

    video_id = meta.get("id", transcript.stem)
    title = meta.get("title", video_id)
    url = meta.get("webpage_url", f"https://youtu.be/{video_id}")
    dur = float(meta.get("duration", 0))

    cues = parse_vtt(transcript.read_text(encoding="utf-8", errors="ignore"))
    if not cues:
        (out_dir / f"{video_id}.md").write_text(
            f"# {title}\n\nNo transcript/captions available for this video, so no clip plan. "
            "Enable captions on YouTube (or upload an SRT) and re-run.\n")
        print("No cues parsed.")
        return
    if not dur:
        dur = cues[-1]["end"]

    wins = windows(cues)
    for w in wins:
        w["score"] = score(w)
    ranked = sorted(wins, key=lambda w: w["score"], reverse=True)
    top = ranked[:10]

    # Optional: look at frames from the top candidates (needs SHORTS_VIDEO).
    video = os.getenv("SHORTS_VIDEO", "").strip()
    visual = visual_check(Path(video), top) if video else 0
    if visual:
        print(f"Visual check scored {visual} candidates.")

    # Optional: let Claude choose from the top candidates and write the copy.
    smart_picks = claude_enhance(top, title)
    smart = smart_picks is not None
    picks = smart_picks if smart else sorted(top, key=visual_adjusted, reverse=True)[:5]
    picks.sort(key=lambda w: w["start"])  # chronological in the plan

    plan = build_plan(video_id, title, url, dur, picks, smart)
    review = build_review(title, dur, cues, picks)
    (out_dir / f"{video_id}.md").write_text(review + "\n---\n\n" + plan)

    # machine-readable cut list for the optional ffmpeg clipper
    (out_dir / f"{video_id}.clips.json").write_text(json.dumps(
        [{"start": round(w["start"], 1), "end": round(w["end"], 1)} for w in picks], indent=1))
    print(f"Wrote {out_dir/f'{video_id}.md'} with {len(picks)} clips.")


if __name__ == "__main__":
    main()

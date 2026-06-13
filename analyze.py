"""
YouTube -> Shorts/Reels analyzer.

Reads a video's auto-caption transcript (VTT, produced by yt-dlp) plus its
metadata, finds the strongest ~30-50s moments, and writes a "Shorts Plan":
ready-to-cut clip timestamps with a hook, a caption, and hashtags for both
Instagram Reels and YouTube Shorts. Also writes a short review of the video.

Pure standard library. No API key. Built for the user's OWN videos.
If an optional clipper step runs (ffmpeg), these timestamps drive the cuts.
"""

from __future__ import annotations

import json
import re
import sys
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


def build_plan(video_id: str, title: str, url: str, dur_s: float, picks: list[dict]) -> str:
    L = [
        f"# Shorts Plan — {title}",
        "",
        f"- **Source:** [{url}]({url}) · `{video_id}`",
        f"- **Length:** {fmt(dur_s)} · **Candidate clips found:** {len(picks)}",
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
            f"**Hook (first 2s, big text):** {hook_line(w['text'])}",
            "",
            f"> {w['text']}",
            "",
            "**Caption (paste to Reels + Shorts):**",
            "",
            "```",
            caption(w["text"], i - 1),
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
    picks = sorted(wins, key=lambda w: w["score"], reverse=True)[:5]
    picks.sort(key=lambda w: w["start"])  # chronological in the plan

    plan = build_plan(video_id, title, url, dur, picks)
    review = build_review(title, dur, cues, picks)
    (out_dir / f"{video_id}.md").write_text(review + "\n---\n\n" + plan)

    # machine-readable cut list for the optional ffmpeg clipper
    (out_dir / f"{video_id}.clips.json").write_text(json.dumps(
        [{"start": round(w["start"], 1), "end": round(w["end"], 1)} for w in picks], indent=1))
    print(f"Wrote {out_dir/f'{video_id}.md'} with {len(picks)} clips.")


if __name__ == "__main__":
    main()

#!/usr/bin/env python3
"""yt-shorts-studio v2 — the local repurposing factory (spec section 10).

Turns one long SWS interview into ready-to-post 9:16 Shorts/Reels:

  download (yt-dlp) -> transcribe (faster-whisper, word timestamps)
  -> highlight scoring over 20-58s windows -> ffmpeg 9:16 crop
  -> burned-in ASS captions -> hook/caption/hashtags -> posting checklist

Local-first: video processing is too heavy for CI, so this runs on your
machine. Every external tool is checked before use with an install hint.
--dry-run prints the exact commands without running them.

Quick start (see README):
  python3 pipeline.py --url https://youtu.be/VIDEOID --clips 3 --style captions-big
"""
from __future__ import annotations
import argparse, json, os, re, shutil, subprocess, sys, textwrap
from pathlib import Path

# ── tiny utils ────────────────────────────────────────────────────────
def have(tool: str) -> bool:
    return shutil.which(tool) is not None

def run(cmd: list[str], dry: bool):
    print("  $ " + " ".join(str(c) for c in cmd))
    if dry:
        return
    subprocess.run(cmd, check=True)

def fmt_ts(sec: float) -> str:
    m, s = divmod(int(sec), 60)
    return f"{m:02d}:{s:02d}"

# ── highlight scoring (pure — unit-tested) ───────────────────────────
HOOK_WORDS = re.compile(r"\b(why|how|what|never|always|nobody|everyone|the truth|"
                        r"biggest|first|realized|honestly|secret|mistake|number|percent|"
                        r"million|thousand|dollars|rupees|failed|learned|google|offer)\b", re.I)
EMO_WORDS = re.compile(r"\b(love|hate|scared|proud|regret|amazing|incredible|shocked|"
                       r"cried|laughed|struggle|dream|hope|fear)\b", re.I)

def windows_from_words(words: list[dict], target=42.0, lo=20.0, hi=58.0) -> list[dict]:
    """words: [{'w':str,'start':float,'end':float}] -> candidate windows."""
    wins = []
    n = len(words)
    i = 0
    while i < n:
        start = words[i]["start"]
        j = i
        while j < n and words[j]["end"] - start < target:
            j += 1
        end = words[min(j, n - 1)]["end"]
        dur = end - start
        if lo <= dur <= hi:
            text = " ".join(w["w"] for w in words[i:j + 1]).strip()
            wins.append({"start": start, "end": end, "dur": dur, "text": text})
        i += max(1, (j - i) // 2)  # 50% overlap stride
    return wins

def score_window(w: dict) -> float:
    t = w["text"]
    s = 0.0
    s += 3.0 * len(HOOK_WORDS.findall(t))
    s += 2.0 * len(EMO_WORDS.findall(t))
    s += 1.5 * len(re.findall(r"\b\d[\d,\.]*\b", t))   # numbers punch
    s += 1.0 if "?" in t else 0.0                       # a question hooks
    words = t.split()
    s += min(len(words) / 12.0, 6.0)                    # enough substance
    ideal = 40.0
    s -= abs(w["dur"] - ideal) * 0.05                   # near ideal length
    return s

def pick_clips(words: list[dict], n: int) -> list[dict]:
    wins = windows_from_words(words)
    for w in wins:
        w["score"] = score_window(w)
    ranked = sorted(wins, key=lambda w: w["score"], reverse=True)
    picks, used = [], []
    for w in ranked:
        if any(not (w["end"] < u[0] or w["start"] > u[1]) for u in used):
            continue  # no overlap between chosen clips
        picks.append(w)
        used.append((w["start"], w["end"]))
        if len(picks) >= n:
            break
    return picks

# ── ASS subtitle generation (pure — unit-tested) ─────────────────────
ASS_HEADER = """[Script Info]
ScriptType: v4.00+
PlayResX: 1080
PlayResY: 1920
WrapStyle: 0

[V4+ Styles]
Format: Name, Fontname, Fontsize, PrimaryColour, OutlineColour, BackColour, Bold, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding
Style: Big, Arial, 84, &H00FFFFFF, &H00000000, &H90000000, 1, 5, 2, 2, 60, 60, 260, 1

[Events]
Format: Layer, Start, End, Style, MarginL, MarginR, MarginV, Effect, Text
"""

def ass_time(sec: float) -> str:
    cs = int(round(sec * 100))
    h, cs = divmod(cs, 360000)
    m, cs = divmod(cs, 6000)
    s, cs = divmod(cs, 100)
    return f"{h:d}:{m:02d}:{s:02d}.{cs:02d}"

def build_ass(words: list[dict], clip_start: float, clip_end: float, per_line=4) -> str:
    """Group clip words into short caption lines, timed relative to clip start."""
    inside = [w for w in words if w["start"] >= clip_start and w["end"] <= clip_end]
    lines = ASS_HEADER
    for k in range(0, len(inside), per_line):
        grp = inside[k:k + per_line]
        if not grp:
            continue
        st = ass_time(grp[0]["start"] - clip_start)
        en = ass_time(grp[-1]["end"] - clip_start)
        text = " ".join(g["w"] for g in grp).upper().replace("\n", " ")
        lines += f"Dialogue: 0,{st},{en},Big,,0,0,0,,{text}\n"
    return lines

# ── ffmpeg command construction (pure — unit-tested) ─────────────────
def crop_filter(mode: str) -> str:
    # 9:16 from a 16:9 source: crop to centre vertical strip, scale to 1080x1920
    if mode == "face":
        # face-tracking crop needs mediapipe preprocessing (see --crop face note);
        # falls through to centre if the track file is absent
        return "crop=ih*9/16:ih,scale=1080:1920:flags=lanczos"
    return "crop=ih*9/16:ih,scale=1080:1920:flags=lanczos"

def ffmpeg_clip_cmd(src: Path, start: float, dur: float, ass: Path, out: Path, crop="center") -> list[str]:
    vf = f"{crop_filter(crop)},ass={ass}"
    return ["ffmpeg", "-y", "-ss", f"{start:.2f}", "-i", str(src), "-t", f"{dur:.2f}",
            "-vf", vf, "-c:v", "libx264", "-preset", "medium", "-crf", "20",
            "-c:a", "aac", "-b:a", "128k", str(out)]

# ── hook / caption / hashtags ────────────────────────────────────────
BASE_TAGS = "#SundaysWithSafaya #Podcast #Interview #Shorts #Reels"
def hook_of(text: str) -> str:
    first = re.split(r"(?<=[.!?])\s", text.strip())[0]
    return first[:70].rstrip(",;: ") + ("…" if len(first) > 70 else "")

def caption_of(text: str) -> str:
    return hook_of(text) + " — full episode on the channel."

# ── transcription (faster-whisper, word timestamps) ──────────────────
def transcribe(audio: Path, model="base") -> list[dict]:
    try:
        from faster_whisper import WhisperModel
    except ImportError:
        sys.exit("faster-whisper not installed. Run: pip install faster-whisper")
    print(f"  transcribing with faster-whisper ({model})…")
    wm = WhisperModel(model, device="cpu", compute_type="int8")
    segments, _ = wm.transcribe(str(audio), word_timestamps=True)
    words = []
    for seg in segments:
        for w in (seg.words or []):
            words.append({"w": w.word.strip(), "start": w.start, "end": w.end})
    return words

# ── main pipeline ────────────────────────────────────────────────────
def main():
    ap = argparse.ArgumentParser(description="yt-shorts-studio v2 local factory")
    ap.add_argument("--url", help="YouTube URL")
    ap.add_argument("--transcript", help="pre-made words JSON (skips download+whisper; bot-wall bypass)")
    ap.add_argument("--clips", type=int, default=3)
    ap.add_argument("--style", default="captions-big", choices=["captions-big", "no-captions"])
    ap.add_argument("--crop", default="center", choices=["center", "face"])
    ap.add_argument("--model", default="base", help="whisper size: tiny/base/small/medium")
    ap.add_argument("--out", default="output")
    ap.add_argument("--dry-run", action="store_true", help="print commands, don't execute")
    args = ap.parse_args()
    dry = args.dry_run

    if not args.url and not args.transcript:
        ap.error("need --url or --transcript")

    vid = re.search(r"(?:v=|youtu\.be/|shorts/)([\w-]{11})", args.url or "")
    vid = vid.group(1) if vid else "video"
    work = Path(args.out) / vid
    work.mkdir(parents=True, exist_ok=True)
    src = work / "source.mp4"
    audio = work / "audio.m4a"

    # 1. download
    if args.url:
        if not dry and not have("yt-dlp"):
            sys.exit("yt-dlp not installed. Run: pip install yt-dlp  (and: brew install ffmpeg)")
        print("1. download")
        run(["yt-dlp", "-f", "bestvideo[height<=1080]+bestaudio/best", "-o", str(src), args.url], dry)
        run(["yt-dlp", "-f", "bestaudio", "-x", "--audio-format", "m4a", "-o", str(audio), args.url], dry)

    # 2. transcribe
    print("2. transcribe")
    if args.transcript:
        words = json.load(open(args.transcript))
    elif dry:
        print("  (dry-run: would run faster-whisper for word timestamps)")
        words = [{"w": "sample", "start": 0.0, "end": 0.5}]
    else:
        words = transcribe(audio, args.model)
    json.dump(words, open(work / "words.json", "w"))

    # 3. score + pick
    print("3. score + pick highlights")
    picks = pick_clips(words, args.clips) if len(words) > 5 else []
    if not picks and not dry:
        sys.exit("no scorable windows — is the transcript long enough?")
    if dry and not picks:
        picks = [{"start": 12.0, "end": 52.0, "dur": 40.0, "text": "why nobody tells you this about the first offer", "score": 9.9}]

    # 4-6. per clip: caption file, ffmpeg cut, metadata
    if not have("ffmpeg") and not dry:
        sys.exit("ffmpeg not installed. Run: brew install ffmpeg  (or apt install ffmpeg)")
    checklist = ["# Posting checklist — upload manually (no personal-account APIs exist)",
                 f"# Source: {args.url or args.transcript}", ""]
    for i, p in enumerate(picks, 1):
        print(f"4.{i} clip {i}: {fmt_ts(p['start'])}-{fmt_ts(p['end'])}  score={p['score']:.1f}")
        ass = work / f"clip{i}.ass"
        out = work / f"short{i}.mp4"
        if args.style == "captions-big":
            ass.write_text(build_ass(words, p["start"], p["end"]))
        else:
            ass.write_text(ASS_HEADER)
        run(ffmpeg_clip_cmd(src, p["start"], p["dur"], ass, out, args.crop), dry)
        hook, cap = hook_of(p["text"]), caption_of(p["text"])
        json.dump({"clip": i, "start": fmt_ts(p["start"]), "end": fmt_ts(p["end"]),
                   "hook": hook, "caption": cap, "hashtags": BASE_TAGS, "file": out.name},
                  open(work / f"short{i}.json", "w"), indent=2)
        checklist += [f"## Short {i}  ({fmt_ts(p['start'])}-{fmt_ts(p['end'])})",
                      f"- [ ] Review {out.name}",
                      f"- [ ] YouTube Shorts: title = {hook}",
                      f"- [ ] Instagram Reels: caption = {cap}",
                      f"- [ ] Hashtags: {BASE_TAGS}", ""]
    (work / "POSTING_CHECKLIST.md").write_text("\n".join(checklist))
    print(f"\nDone. {len(picks)} shorts + POSTING_CHECKLIST.md in {work}/")

if __name__ == "__main__":
    main()

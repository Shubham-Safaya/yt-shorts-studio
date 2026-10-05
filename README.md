# YouTube to Shorts Studio

Turn a long YouTube video (yours) into a **Shorts/Reels plan** — best moments, hooks, captions, and hashtags — plus optional 9:16 clips. Runs entirely in **GitHub Actions** (no local Python needed). Repeatable: share a link, get a plan.

## How to use it

**Option A — one-off (fastest):** Actions tab → **YouTube to Shorts** → *Run workflow* → paste a YouTube URL. Tick *make clips* if you also want cut 9:16 videos (downloaded as a run artifact). A `reviews/<id>.md` is committed with the plan. Tick *visual check* to have Claude look at frames from the top candidate moments before picking: each clip in the plan then shows a visual score and whether the centered 9:16 crop keeps you in frame (needs `ANTHROPIC_API_KEY`; downloads a 480p copy, or reuses the 1080p one when cutting clips).

**Option B — queue:** add your video URL to [`videos/queue.txt`](videos/queue.txt) and push. Every new link is processed and its plan committed.

**Option C — whole channel (batch):** Actions tab → **Channel to Shorts (batch)** → *Run workflow*. Defaults to `@sundayswithsafaya`; processes the most recent N videos that don't have a plan yet. Re-run anytime to catch up the back catalog N at a time.

## Best-quality mode (optional, recommended)

Add an `ANTHROPIC_API_KEY` repo secret (Settings → Secrets → Actions). When present, **Claude picks the best clips and writes the hooks + captions** (no em dashes) instead of the built-in heuristic — meaningfully better shorts. Without the key it still works, just heuristically. Model override via the `SHORTS_MODEL` env (default `claude-opus-5-5`). Claude returns JSON validated against a schema, and a safety decline is retried on Anthropic's recommended fallback model.

## What you get, per video

- **Review** — length, words-per-minute, recurring themes, and a ship/skip verdict.
- **Shorts Plan** — up to 5 clips, each with: exact `mm:ss → mm:ss` timestamps, a 2-second **hook**, the full quote, and a **caption + hashtags ready to paste** into both Instagram Reels and YouTube Shorts.
- **`<id>.clips.json`** — machine-readable cut list (drives the optional ffmpeg clipper).
- **Clips** (optional) — `clips/<id>_shortN.mp4`, center-cropped to vertical 1080×1920, as a downloadable artifact.

## How it works

`analyze.py` (pure standard library, no API key) parses the video's auto-captions, groups them into ~40-second windows, and scores each on hooks, specifics (numbers), questions, and emotion to pick the most clippable moments. The workflow fetches captions + metadata with `yt-dlp` and cuts clips with `ffmpeg`.

## YouTube blocks CI runners (important)

YouTube increasingly challenges GitHub Actions' datacenter IPs with *"Sign in to confirm you're not a bot,"* so `yt-dlp` can fail to fetch captions from the cloud. Two ways around it, in order of robustness:

1. **Transcript drop (bulletproof, for your own videos):** In YouTube Studio → your video → Subtitles → download the English `.srt`. Save it as `transcripts/<video_id>.srt` (the id is the 11 chars after `v=` or `youtu.be/`), commit, and run the workflow. The pipeline uses the dropped transcript and never touches YouTube — no bot wall.
2. **Cookies secret:** Export your YouTube cookies (a `cookies.txt` from a logged-in browser via a "Get cookies.txt" extension) and paste the file contents into a repo secret named `YOUTUBE_COOKIES`. The workflows pass `--cookies` automatically. Cookies expire, so refresh when fetches start failing.

If neither is set and YouTube blocks the runner, the run still succeeds but prints exactly what to do instead of silently producing nothing.

## Honest limits

- **Use it on your own videos.** Downloading and re-cutting content you own is fine; this is built for that.
- **Posting is still manual.** Auto-posting to Instagram Reels or YouTube Shorts needs each platform's API and OAuth (Instagram Graph API requires a Business/Creator account + a Facebook app; YouTube Data API requires OAuth). The captions are pre-written so posting is a one-minute paste. Wiring full auto-post is a future add once you set up those API credentials.
- Captions come from YouTube's auto-transcript; nudge exact in/out points by eye and let CapCut burn on-screen captions.

Built by [Shubham Safaya](https://shubham-safaya.github.io).

---

## v2 — the local repurposing factory (`pipeline.py`)

v1 (above) plans clips in GitHub Actions. **v2 produces the actual 9:16 clips** on your machine, because video processing is too heavy for CI.

### One-time setup
```bash
# macOS
brew install ffmpeg
pip install yt-dlp faster-whisper
# Linux: sudo apt install ffmpeg && pip install yt-dlp faster-whisper
```
Python 3.10+. First run downloads a small Whisper model (~150 MB for `base`).

### Make your first 3 shorts tonight
```bash
python3 pipeline.py --url https://youtu.be/VIDEOID --clips 3 --style captions-big
```
Pipeline: **download → faster-whisper word timestamps → highlight scoring (20–58s windows) → ffmpeg 9:16 crop → burned-in big captions → hook/caption/hashtags → posting checklist.**

Output: `output/<video-id>/` with `short1.mp4 … shortN.mp4`, per-clip `.json` metadata, and `POSTING_CHECKLIST.md` (upload to YT Shorts + IG Reels manually — no personal-account APIs exist; ~5 min).

### Flags
| Flag | Default | Notes |
|---|---|---|
| `--clips N` | 3 | how many shorts |
| `--style` | captions-big | or `no-captions` |
| `--crop` | center | or `face` (needs mediapipe; falls back to centre) |
| `--model` | base | whisper size: tiny/base/small/medium |
| `--transcript words.json` | — | skip download+whisper (bot-wall bypass; JSON = `[{"w","start","end"}]`) |
| `--dry-run` | — | print every command without running |

### Verify before the real batch
```bash
python3 pipeline.py --url https://youtu.be/VIDEOID --clips 2 --dry-run   # prints exact commands
python3 -c "import pipeline"                                             # imports clean
```

### The web command-builder
`index.html` (GitHub Pages) — paste a link, pick options, copy the exact `pipeline.py` command. No processing in the browser; it just builds the command you run locally.

### Batch the back catalog
The 13 weekly reel plans in `mission-control/content-bank/reels/` each name a real SWS video and the command to run. Start with the Maryland→Google episode plan.

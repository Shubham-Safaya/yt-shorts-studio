# YouTube to Shorts Studio

Turn a long YouTube video (yours) into a **Shorts/Reels plan** — best moments, hooks, captions, and hashtags — plus optional 9:16 clips. Runs entirely in **GitHub Actions** (no local Python needed). Repeatable: share a link, get a plan.

## How to use it

**Option A — one-off (fastest):** Actions tab → **YouTube to Shorts** → *Run workflow* → paste a YouTube URL. Tick *make clips* if you also want cut 9:16 videos (downloaded as a run artifact). A `reviews/<id>.md` is committed with the plan.

**Option B — queue:** add your video URL to [`videos/queue.txt`](videos/queue.txt) and push. Every new link is processed and its plan committed.

**Option C — whole channel (batch):** Actions tab → **Channel to Shorts (batch)** → *Run workflow*. Defaults to `@sundayswithsafaya`; processes the most recent N videos that don't have a plan yet. Re-run anytime to catch up the back catalog N at a time.

## Best-quality mode (optional, recommended)

Add an `ANTHROPIC_API_KEY` repo secret (Settings → Secrets → Actions). When present, **Claude picks the best clips and writes the hooks + captions** (no em dashes) instead of the built-in heuristic — meaningfully better shorts. Without the key it still works, just heuristically. Model override via the `SHORTS_MODEL` env (default `claude-opus-4-8`).

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

# YouTube to Shorts Studio

Turn a long YouTube video (yours) into a **Shorts/Reels plan** — best moments, hooks, captions, and hashtags — plus optional 9:16 clips. Runs entirely in **GitHub Actions** (no local Python needed). Repeatable: share a link, get a plan.

## How to use it

**Option A — one-off (fastest):** Actions tab → **YouTube to Shorts** → *Run workflow* → paste a YouTube URL. Tick *make clips* if you also want cut 9:16 videos (downloaded as a run artifact). A `reviews/<id>.md` is committed with the plan.

**Option B — queue:** add your video URL to [`videos/queue.txt`](videos/queue.txt) and push. Every new link is processed and its plan committed.

## What you get, per video

- **Review** — length, words-per-minute, recurring themes, and a ship/skip verdict.
- **Shorts Plan** — up to 5 clips, each with: exact `mm:ss → mm:ss` timestamps, a 2-second **hook**, the full quote, and a **caption + hashtags ready to paste** into both Instagram Reels and YouTube Shorts.
- **`<id>.clips.json`** — machine-readable cut list (drives the optional ffmpeg clipper).
- **Clips** (optional) — `clips/<id>_shortN.mp4`, center-cropped to vertical 1080×1920, as a downloadable artifact.

## How it works

`analyze.py` (pure standard library, no API key) parses the video's auto-captions, groups them into ~40-second windows, and scores each on hooks, specifics (numbers), questions, and emotion to pick the most clippable moments. The workflow fetches captions + metadata with `yt-dlp` and cuts clips with `ffmpeg`.

## Honest limits

- **Use it on your own videos.** Downloading and re-cutting content you own is fine; this is built for that.
- **Posting is still manual.** Auto-posting to Instagram Reels or YouTube Shorts needs each platform's API and OAuth (Instagram Graph API requires a Business/Creator account + a Facebook app; YouTube Data API requires OAuth). The captions are pre-written so posting is a one-minute paste. Wiring full auto-post is a future add once you set up those API credentials.
- Captions come from YouTube's auto-transcript; nudge exact in/out points by eye and let CapCut burn on-screen captions.

Built by [Shubham Safaya](https://shubham-safaya.github.io).

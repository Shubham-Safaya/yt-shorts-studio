#!/usr/bin/env bash
# Process one YouTube URL into a Shorts plan. Used by both workflows.
# Robust to YouTube's "confirm you're not a bot" wall on CI datacenter IPs:
#   1. If transcripts/<id>.(vtt|srt) exists, use it and SKIP yt-dlp entirely
#      (the bulletproof path for your own videos: export captions from YouTube
#      Studio -> Subtitles -> download .srt, drop it in transcripts/).
#   2. Else fetch captions with yt-dlp, passing --cookies cookies.txt when present
#      (set the YOUTUBE_COOKIES secret to defeat the bot wall).
# Env: MAKE_CLIPS=true to also cut 9:16 clips. ANTHROPIC_API_KEY for best clips.
#      VISUAL_CHECK=true (with ANTHROPIC_API_KEY) to let Claude look at frames from
#      the top candidates before picking: visual strength + whether the 9:16 crop works.
set -uo pipefail

url="$1"
mkdir -p reviews clips work transcripts
COOKIES=""; [ -f cookies.txt ] && COOKIES="--cookies cookies.txt"

# Extract the 11-char video id straight from the URL (no network, no bot wall).
id=$(printf '%s' "$url" | grep -oP '(?:v=|youtu\.be/|/shorts/|/embed/)\K[A-Za-z0-9_-]{11}' | head -1)
[ -z "$id" ] && id=$(printf '%s' "$url" | grep -oE '[A-Za-z0-9_-]{11}' | tail -1)

if [ -f "reviews/$id.md" ]; then echo "already reviewed: $id"; exit 0; fi

# Path 1: a dropped transcript wins — no yt-dlp needed.
vtt=$(ls "transcripts/$id".* 2>/dev/null | head -1)
if [ -n "$vtt" ]; then
  echo "Using dropped transcript: $vtt"
  printf '{"id":"%s","title":"%s","webpage_url":"%s","duration":0}\n' "$id" "$id" "$url" > work/meta.json
else
  # Path 2: yt-dlp (with cookies if available).
  if ! python -m yt_dlp $COOKIES --skip-download --dump-single-json "$url" > work/meta.json 2>work/err.log; then
    echo "!! Could not fetch $url. YouTube likely blocked the runner."
    echo "   Fix: add a YOUTUBE_COOKIES secret, OR drop transcripts/$id.srt from YouTube Studio."
    sed 's/^/   yt-dlp: /' work/err.log | tail -2
    exit 0
  fi
  python -m yt_dlp $COOKIES --skip-download --write-auto-subs --write-subs \
    --sub-langs "en.*" --sub-format vtt -o "work/$id.%(ext)s" "$url" 2>/dev/null || true
  vtt=$(ls work/"$id"*.vtt 2>/dev/null | head -1)
  [ -z "$vtt" ] && vtt=/dev/null
fi

# Download the video once, before analysis, if clips or the visual check need it.
# Clips need 1080p; the visual check alone is fine at 480p.
want_visual=false
[ "${VISUAL_CHECK:-false}" = "true" ] && [ -n "${ANTHROPIC_API_KEY:-}" ] && want_visual=true
if [ "${MAKE_CLIPS:-false}" = "true" ] || [ "$want_visual" = "true" ]; then
  height=480; [ "${MAKE_CLIPS:-false}" = "true" ] && height=1080
  python -m yt_dlp $COOKIES -f "bv*[height<=$height]+ba/b[height<=$height]" --merge-output-format mp4 \
    -o "work/$id.mp4" "$url" 2>/dev/null || echo "   (video download failed; continuing without it)"
fi

video=""
[ "$want_visual" = "true" ] && [ -f "work/$id.mp4" ] && video="work/$id.mp4"
SHORTS_VIDEO="$video" python analyze.py "$vtt" work/meta.json reviews

if [ "${MAKE_CLIPS:-false}" = "true" ] && [ -f "reviews/$id.clips.json" ] && [ -f "work/$id.mp4" ]; then
  python clip.py "$id"
fi

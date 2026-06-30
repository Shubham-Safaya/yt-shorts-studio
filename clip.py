"""Cut 9:16 vertical clips for one video from its reviews/<id>.clips.json cut list.
Called by the workflows (keeps the ffmpeg logic out of YAML). Needs work/<id>.mp4."""

import json
import pathlib
import subprocess
import sys

vid = sys.argv[1]
clips = json.load(open(f"reviews/{vid}.clips.json"))
pathlib.Path("clips").mkdir(exist_ok=True)

for i, c in enumerate(clips, 1):
    out = f"clips/{vid}_short{i}.mp4"
    # cut, then center-crop + scale to vertical 1080x1920
    subprocess.run([
        "ffmpeg", "-y", "-ss", str(c["start"]), "-to", str(c["end"]), "-i", f"work/{vid}.mp4",
        "-vf", "crop='min(iw,ih*9/16)':'min(ih,iw*16/9)',scale=1080:1920,setsar=1",
        "-c:v", "libx264", "-preset", "veryfast", "-c:a", "aac", out,
    ], check=False)
    print("cut", out)

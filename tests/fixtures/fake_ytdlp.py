"""Stand-in for yt-dlp that emits the same line protocol. Behaviour is chosen by the URL."""

import json
import sys
import time

url = sys.argv[-1]
out = sys.stdout


def emit(prefix, obj):
    out.write(f"{prefix} {json.dumps(obj)}\n")
    out.flush()


emit("[info]", {"id": url.rsplit("/", 1)[-1], "title": f"Title {url}", "uploader": "U",
                "extractor_key": "Fake", "format_id": "137+140"})

if "fail-permanent" in url:
    sys.stderr.write("ERROR: [fake] x: Private video\n")
    sys.exit(1)
if "fail-transient" in url:
    sys.stderr.write("ERROR: Unable to download: HTTP Error 503: Service Unavailable\n")
    sys.exit(1)

steps = 40 if "slow" in url else 4
for name in ("v.f137.mp4", "a.f140.m4a"):
    for i in range(1, steps + 1):
        emit("[dl]", {"status": "downloading", "downloaded_bytes": i * 100, "total_bytes": steps * 100,
                      "speed": 1000.0, "eta": steps - i, "filename": name})
        time.sleep(0.05 if "slow" in url else 0.005)
    emit("[dl]", {"status": "finished", "downloaded_bytes": steps * 100,
                  "total_bytes": steps * 100, "filename": name})
emit("[pp]", {"status": "started", "postprocessor": "Merger"})
emit("[done]", {"id": url.rsplit("/", 1)[-1], "title": "T", "filepath": "C:/out/T.mp4"})

"""Download a wheel with chunked streaming + sha256 verify + retries."""
import hashlib
import os
import sys
import time
import urllib.request


def download(url: str, out: str, expected: str, attempts: int = 12) -> int:
    for i in range(1, attempts + 1):
        print(f"attempt {i} -> {out}", flush=True)
        try:
            req = urllib.request.Request(url, headers={"User-Agent": "pip-installer/1.0"})
            with urllib.request.urlopen(req, timeout=180) as r, open(out, "wb") as f:
                total = 0
                while True:
                    chunk = r.read(1024 * 1024)
                    if not chunk:
                        break
                    f.write(chunk)
                    total += len(chunk)
            h = hashlib.sha256(open(out, "rb").read()).hexdigest()
            print(f"  size={os.path.getsize(out)} sha256={h}", flush=True)
            if h == expected:
                print("  OK", flush=True)
                return 0
            print(f"  hash mismatch (expected {expected}); retrying", flush=True)
        except Exception as e:  # noqa: BLE001
            print(f"  error: {e!r}", flush=True)
        time.sleep(2 + i)
    return 1


if __name__ == "__main__":
    url, out, expected = sys.argv[1], sys.argv[2], sys.argv[3]
    sys.exit(download(url, out, expected))

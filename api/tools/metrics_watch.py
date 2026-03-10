"""Watch /metrics in the terminal (no jq/watch required).

Run
  cd api
  python tools/metrics_watch.py --port 8001
"""

from __future__ import annotations

import argparse
import json
import time

import requests


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--port", type=int, default=8001)
    ap.add_argument("--interval", type=float, default=1.0)
    args = ap.parse_args()

    base = f"http://{args.host}:{args.port}"
    prev = None

    while True:
        try:
            r = requests.get(f"{base}/metrics", timeout=10)
            data = r.json()
            ctr = data.get("counters") or {}

            if prev is None:
                print(json.dumps({"counters": ctr}, ensure_ascii=False))
            else:
                # show only changed counters
                diff = {k: ctr.get(k, 0) - prev.get(k, 0) for k in set(prev) | set(ctr)}
                diff = {k: v for k, v in diff.items() if v != 0}
                print(json.dumps({"diff": diff, "counters": ctr}, ensure_ascii=False))

            prev = dict(ctr)
        except KeyboardInterrupt:
            break
        except Exception as e:
            print(f"error: {e}")

        time.sleep(args.interval)


if __name__ == "__main__":
    main()

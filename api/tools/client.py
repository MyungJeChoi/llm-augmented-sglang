"""Interactive CLI client (no browser needed).

Usage:
  # in another terminal, run uvicorn
  uvicorn app.main:app --host 127.0.0.1 --port 8001

  # then:
  python tools/client.py --port 8001

It will prompt:
  query> 다운타임 많은 장비 top10

This avoids writing curl commands repeatedly.
"""

from __future__ import annotations

import argparse
import json

import requests


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--port", type=int, default=8001)
    ap.add_argument("--actor", default="me")
    args = ap.parse_args()

    base = f"http://{args.host}:{args.port}"

    while True:
        q = input("query> ").strip()
        if not q:
            continue
        if q.lower() in {"exit", "quit"}:
            break

        r = requests.post(
            f"{base}/chat/query",
            json={"query": q, "actor": args.actor},
            timeout=60,
        )

        rid = r.headers.get("X-Request-ID")
        print(f"[http] status={r.status_code} request_id={rid}")
        try:
            print(json.dumps(r.json(), ensure_ascii=False, indent=2))
        except Exception:
            print(r.text)


if __name__ == "__main__":
    main()

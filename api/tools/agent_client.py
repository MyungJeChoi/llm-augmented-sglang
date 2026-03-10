"""Interactive CLI client for /agent/chat (multi-turn).

Usage:
  # 1) Run server
  uvicorn app.main:app --reload --host 127.0.0.1 --port 8001

  # 2) Run client
  python tools/agent_client.py --port 8001

Features:
- Maintains thread_id automatically
- Prints the assistant message
- Optional debug mode

Commands:
- /new : start a new thread
- /id  : show current thread_id
- /quit: exit
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
    ap.add_argument("--debug", action="store_true")
    args = ap.parse_args()

    base = f"http://{args.host}:{args.port}"
    thread_id: str | None = None

    print("[agent_client] type /new to start a new thread, /quit to exit")

    while True:
        q = input("you> ").strip()
        if not q:
            continue
        if q.lower() in {"/quit", "/exit"}:
            break
        if q.lower() == "/new":
            thread_id = None
            print("[agent_client] new thread will be created on next message")
            continue
        if q.lower() == "/id":
            print(f"[agent_client] thread_id={thread_id}")
            continue

        payload = {
            "message": q,
            "thread_id": thread_id,
            "actor": args.actor,
            "debug": args.debug,
        }

        r = requests.post(f"{base}/agent/chat", json=payload, timeout=120)
        rid = r.headers.get("X-Request-ID")
        print(f"[http] status={r.status_code} request_id={rid}")

        data = r.json()
        thread_id = data.get("thread_id") or thread_id

        print(f"assistant> {data.get('assistant_message')}")
        if args.debug:
            print(json.dumps(data, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()

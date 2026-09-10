from __future__ import annotations

import argparse
import json
import sys
import urllib.error
import urllib.parse
import urllib.request


def _stream_events(url: str) -> int:
    request = urllib.request.Request(url, method="GET")
    try:
        with urllib.request.urlopen(request, timeout=30) as response:
            print(f"Connected to {url}")
            print("Listening for SSE events. Press Ctrl+C to stop.")
            event_name = "message"
            for raw_line in response:
                line = raw_line.decode("utf-8", errors="replace").rstrip("\n")
                if not line:
                    continue
                if line.startswith("event:"):
                    event_name = line.split(":", 1)[1].strip() or "message"
                    continue
                if line.startswith("data:"):
                    payload_text = line.split(":", 1)[1].strip()
                    try:
                        payload = json.loads(payload_text)
                        payload_text = json.dumps(payload, indent=2, ensure_ascii=True)
                    except Exception:
                        pass
                    print(f"\n[{event_name}]\n{payload_text}")
    except KeyboardInterrupt:
        print("\nStopped.")
        return 0
    except urllib.error.HTTPError as exc:
        print(f"HTTP error: {exc.code} {exc.reason}")
        return 1
    except urllib.error.URLError as exc:
        print(f"Connection failed: {exc.reason}")
        return 1
    return 0


def _print_recent(url: str) -> int:
    request = urllib.request.Request(url, method="GET")
    try:
        with urllib.request.urlopen(request, timeout=30) as response:
            data = response.read().decode("utf-8", errors="replace")
            print(data)
    except urllib.error.HTTPError as exc:
        print(f"HTTP error: {exc.code} {exc.reason}")
        return 1
    except urllib.error.URLError as exc:
        print(f"Connection failed: {exc.reason}")
        return 1
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description="Tap rm-agent webhook SSE lifecycle events for local debugging.")
    parser.add_argument("--base-url", default="http://127.0.0.1:8000", help="Backend base URL")
    parser.add_argument("--chat-id", default="", help="Optional chat filter")
    parser.add_argument("--recent", action="store_true", help="Fetch recent events snapshot instead of live stream")
    parser.add_argument("--limit", type=int, default=50, help="Recent events limit (only with --recent)")
    args = parser.parse_args()

    base = args.base_url.rstrip("/")
    if args.recent:
        params = {"limit": str(max(1, min(args.limit, 500)))}
        if args.chat_id.strip():
            params["chat_id"] = args.chat_id.strip()
        query = urllib.parse.urlencode(params)
        url = f"{base}/relationship-manager/webhook/events/recent?{query}"
        return _print_recent(url)

    if args.chat_id.strip():
        query = urllib.parse.urlencode({"chat_id": args.chat_id.strip()})
        url = f"{base}/relationship-manager/webhook/events?{query}"
    else:
        url = f"{base}/relationship-manager/webhook/events"
    return _stream_events(url)


if __name__ == "__main__":
    raise SystemExit(main())

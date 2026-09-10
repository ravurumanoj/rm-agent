from __future__ import annotations

import argparse
import json
import sys
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path


def _load_env(path: str) -> dict[str, str]:
    values: dict[str, str] = {}
    env_path = Path(path)
    if not env_path.exists():
        return values
    for raw in env_path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        values[key.strip()] = value.strip().strip('"').strip("'")
    return values


def _derive_event_socket_url(unique_api_base_url: str) -> str:
    base = unique_api_base_url.strip().rstrip("/")
    if not base:
        return ""
    if base.endswith("/public/chat"):
        return base[: -len("/public/chat")] + "/public/event-socket/events/stream"
    if "/public/chat" in base:
        return base.replace("/public/chat", "/public/event-socket/events/stream")
    if base.endswith("/public"):
        return base + "/event-socket/events/stream"
    return base + "/public/event-socket/events/stream"


def _build_stream_url(base_url: str, subscriptions: str) -> str:
    query = urllib.parse.urlencode({"subscriptions": subscriptions})
    return f"{base_url}?{query}"


def _post_to_local_webhook(local_webhook_url: str, payload: str) -> tuple[int, str]:
    req = urllib.request.Request(
        local_webhook_url,
        data=payload.encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            body = resp.read().decode("utf-8", errors="replace")
            return int(resp.status), body
    except urllib.error.HTTPError as exc:
        body = exc.read().decode("utf-8", errors="replace") if exc.fp else exc.reason
        return int(exc.code), body


def _stream_dropin(
    *,
    stream_url: str,
    headers: dict[str, str],
    local_webhook_url: str,
    max_events: int,
) -> int:
    processed = 0
    while True:
        request = urllib.request.Request(stream_url, headers=headers, method="GET")
        print(f"Connecting to Event Socket: {stream_url}")
        try:
            with urllib.request.urlopen(request, timeout=620) as response:
                print("Connected. Forwarding events to local webhook...")
                event_name = "message"
                for raw_line in response:
                    line = raw_line.decode("utf-8", errors="replace").rstrip("\n")
                    if not line:
                        continue
                    if line.startswith("event:"):
                        event_name = line.split(":", 1)[1].strip() or "message"
                        continue
                    if not line.startswith("data:"):
                        continue

                    payload_text = line.split(":", 1)[1].strip()
                    if not payload_text:
                        continue

                    # Some SSE streams can send keep-alive payloads.
                    try:
                        event_obj = json.loads(payload_text)
                    except json.JSONDecodeError:
                        print(f"Skipping non-JSON SSE data: {payload_text[:120]}")
                        continue

                    event_type = str(event_obj.get("event") or event_name or "").strip()
                    event_id = str(event_obj.get("id") or "").strip()

                    status, response_text = _post_to_local_webhook(local_webhook_url, payload_text)
                    processed += 1
                    print(
                        f"[{processed}] event={event_type} id={event_id or '-'} "
                        f"local_status={status}"
                    )
                    if status >= 400:
                        print(f"  local_response={response_text[:300]}")

                    if max_events > 0 and processed >= max_events:
                        print("Max events reached; stopping.")
                        return 0
        except KeyboardInterrupt:
            print("\nStopped.")
            return 0
        except urllib.error.HTTPError as exc:
            body = exc.read().decode("utf-8", errors="replace") if exc.fp else exc.reason
            print(f"Stream HTTP error: {exc.code} {exc.reason} body={body[:300]}")
            return 1
        except urllib.error.URLError as exc:
            print(f"Stream connection error: {exc.reason}")
            return 1
        except Exception as exc:
            print(f"Stream ended ({exc}); reconnecting...")


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Unique Event Socket drop-in: consume Unique SSE and forward each event to local webhook."
    )
    parser.add_argument("--env-file", default=".env", help="Env file path for defaults")
    parser.add_argument("--stream-url", default="", help="Full Event Socket stream URL (overrides derived URL)")
    parser.add_argument("--subscriptions", default="unique.chat.external-module.chosen", help="Comma-separated event names")
    parser.add_argument(
        "--local-webhook-url",
        default="http://127.0.0.1:8000/relationship-manager/webhook",
        help="Local webhook endpoint URL",
    )
    parser.add_argument("--api-key", default="", help="Unique API key (Bearer token)")
    parser.add_argument("--app-id", default="", help="Unique app id")
    parser.add_argument("--company-id", default="", help="Unique company id")
    parser.add_argument("--unique-api-base-url", default="", help="Unique API base URL, e.g. https://gateway.<tenant>.unique.app/public/chat")
    parser.add_argument("--max-events", type=int, default=0, help="Stop after N forwarded events (0 means unlimited)")
    args = parser.parse_args()

    env_values = _load_env(args.env_file)

    api_key = (args.api_key or env_values.get("UNIQUE_APP_KEY", "")).strip()
    app_id = (args.app_id or env_values.get("UNIQUE_APP_ID", "")).strip()
    company_id = (args.company_id or env_values.get("UNIQUE_COMPANY_ID", "")).strip()
    unique_api_base_url = (args.unique_api_base_url or env_values.get("UNIQUE_API_BASE_URL", "")).strip()

    stream_base = (args.stream_url or "").strip()
    if not stream_base:
        stream_base = _derive_event_socket_url(unique_api_base_url)

    if not api_key or not app_id or not company_id or not stream_base:
        print("Missing required config.")
        print("Need: api key, app id, company id, and stream URL (or UNIQUE_API_BASE_URL).")
        return 2

    stream_url = _build_stream_url(stream_base, args.subscriptions)
    headers = {
        "Authorization": f"Bearer {api_key}",
        "x-app-id": app_id,
        "x-company-id": company_id,
        "Accept": "text/event-stream",
    }

    print("Using settings:")
    print(f"  stream_url={stream_url}")
    print(f"  local_webhook_url={args.local_webhook_url}")
    print(f"  subscriptions={args.subscriptions}")
    print("  note=Set UNIQUE_WEBHOOK_VERIFY_SIGNATURE=false for drop-in local testing")

    return _stream_dropin(
        stream_url=stream_url,
        headers=headers,
        local_webhook_url=args.local_webhook_url,
        max_events=max(0, args.max_events),
    )


if __name__ == "__main__":
    raise SystemExit(main())

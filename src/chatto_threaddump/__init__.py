from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path
from urllib.parse import urlsplit

import httpx

_THREAD_EVENTS_PATH = "/api/connect/chatto.api.v1.ThreadService/GetThreadEvents"


class ExportError(Exception):
    pass


def _parse_thread_url(value: str) -> tuple[str, str]:
    parsed = urlsplit(value)
    parts = parsed.path.split("/")
    if (
        parsed.scheme != "https"
        or parsed.netloc == ""
        or parsed.query
        or parsed.fragment
        or len(parts) != 7
        or parts[1] != "chat"
        or parts[2] == "-"
        or parts[5] != "m"
        or not parts[3]
        or not parts[4]
        or not parts[6]
    ):
        raise ExportError("invalid Chatto thread URL")
    return parts[3], parts[4]


def _render_page(page: dict[str, object]) -> str:
    events = page.get("events")
    includes = page.get("includes", {})
    users = includes.get("users", {}) if isinstance(includes, dict) else {}
    if not isinstance(events, list) or not isinstance(users, dict):
        raise ExportError("malformed thread response")

    rendered = ["# Chatto thread"]
    messages: list[dict[str, object]] = []
    for event in events:
        if not isinstance(event, dict):
            raise ExportError("malformed thread response")
        posted = event.get("messagePosted")
        if not isinstance(posted, dict):
            continue
        message = posted.get("message")
        if not isinstance(message, dict):
            raise ExportError("malformed thread response")
        messages.append(message)

    if not messages:
        raise ExportError("thread response contains no messages")
    created = messages[0].get("createdAt", messages[0].get("createTime"))
    if not isinstance(created, str):
        raise ExportError("malformed thread response")
    rendered.extend(("", f"Started: {created}"))
    for message in messages:
        actor_id = message.get("actorId")
        user = users.get(actor_id, {})
        if not isinstance(user, dict):
            user = {}
        author = user.get("displayName") or user.get("login") or "Unknown author"
        body = message.get("body", "")
        if not isinstance(author, str) or not isinstance(body, str):
            raise ExportError("malformed thread response")
        rendered.extend(("", f"## {author}", "", body))
    return "\n".join(rendered) + "\n"


def _export(url: str, output: Path) -> None:
    server_url = os.environ.get("CHATTO_THREADDUMP_SERVER_URL", "")
    api_key = os.environ.get("CHATTO_THREADDUMP_API_KEY", "")
    if not server_url or not api_key:
        raise ExportError("missing Chatto configuration")
    room_id, root_id = _parse_thread_url(url)
    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
        "Connect-Protocol-Version": "1",
    }
    request_url = server_url.rstrip("/") + _THREAD_EVENTS_PATH
    try:
        with httpx.Client(follow_redirects=False, timeout=5.0) as client:
            response = client.post(
                request_url,
                headers=headers,
                json={"roomId": room_id, "threadRootEventId": root_id, "limit": 500},
            )
            response.raise_for_status()
            page = response.json()
    except (httpx.HTTPError, json.JSONDecodeError) as exc:
        raise ExportError("Chatto request failed") from exc
    if not isinstance(page, dict):
        raise ExportError("malformed thread response")
    output.mkdir(parents=True, exist_ok=False)
    (output / "_index.md").write_text(_render_page(page), encoding="utf-8")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="chatto-threaddump")
    parser.add_argument("chatto_url")
    parser.add_argument("output_directory", type=Path)
    args = parser.parse_args(argv)
    try:
        _export(args.chatto_url, args.output_directory)
    except (ExportError, OSError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    return 0

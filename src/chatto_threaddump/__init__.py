from __future__ import annotations

import argparse
import json
import math
import os
import sys
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlsplit

import httpx
from dotenv import dotenv_values

_THREAD_EVENTS_PATH = "/api/connect/chatto.api.v1.ThreadService/GetThreadEvents"


class ExportError(Exception):
    pass


@dataclass(frozen=True)
class Settings:
    server_url: str
    api_key: str
    timeout: float
    force: bool


def _timeout(value: str) -> float:
    try:
        parsed = float(value)
    except ValueError as exc:
        raise argparse.ArgumentTypeError(
            "timeout must be a positive finite number"
        ) from exc
    if not math.isfinite(parsed) or parsed <= 0:
        raise argparse.ArgumentTypeError("timeout must be a positive finite number")
    return parsed


def _settings(timeout: float, force: bool) -> Settings:
    values = {
        "CHATTO_THREADDUMP_SERVER_URL": os.environ.get("CHATTO_THREADDUMP_SERVER_URL"),
        "CHATTO_THREADDUMP_API_KEY": os.environ.get("CHATTO_THREADDUMP_API_KEY"),
    }
    env_path = Path.cwd() / ".env"
    if not env_path.exists():
        env_path = Path.home() / ".chatto-threaddump.env"
    if env_path.is_file():
        for key, value in dotenv_values(env_path).items():
            if key in values and values[key] is None and isinstance(value, str):
                values[key] = value
    if (
        not values["CHATTO_THREADDUMP_SERVER_URL"]
        or not values["CHATTO_THREADDUMP_API_KEY"]
    ):
        raise ExportError("missing Chatto configuration")
    return Settings(
        server_url=values["CHATTO_THREADDUMP_SERVER_URL"],
        api_key=values["CHATTO_THREADDUMP_API_KEY"],
        timeout=timeout,
        force=force,
    )


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


def _export(url: str, output: Path, settings: Settings) -> None:
    room_id, root_id = _parse_thread_url(url)
    headers = {
        "Authorization": f"Bearer {settings.api_key}",
        "Content-Type": "application/json",
        "Connect-Protocol-Version": "1",
    }
    request_url = settings.server_url.rstrip("/") + _THREAD_EVENTS_PATH
    try:
        with httpx.Client(follow_redirects=False, timeout=settings.timeout) as client:
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
    parser.add_argument("-f", "--force", action="store_true")
    parser.add_argument("-t", "--timeout", type=_timeout, default=5.0)
    parser.add_argument("chatto_url")
    parser.add_argument("output_directory", type=Path)
    args = parser.parse_args(argv)
    try:
        settings = _settings(args.timeout, args.force)
        _export(args.chatto_url, args.output_directory, settings)
    except (ExportError, OSError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    return 0

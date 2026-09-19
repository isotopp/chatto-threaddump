from __future__ import annotations

import argparse
import json
import math
import os
import re
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


@dataclass(frozen=True)
class ChattoLink:
    room_id: str
    message_id: str
    thread_root_id: str | None


_ROOM_ID = re.compile(r"(?:R[A-Za-z0-9]{14}|[a-f0-9]{14})\Z")
_EVENT_ID = re.compile(r"E[A-Za-z0-9]{14}\Z")
_DNS_NAME = re.compile(
    r"[A-Za-z0-9](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?"
    r"(?:\.[A-Za-z0-9](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?)*\Z"
)


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


def _origin(value: str, *, require_root_path: bool) -> tuple[str, str, int]:
    try:
        parsed = urlsplit(value)
        port = parsed.port
    except ValueError as exc:
        raise ExportError("invalid Chatto server origin") from exc
    if (
        parsed.scheme.lower() != "https"
        or not parsed.netloc
        or parsed.username is not None
        or parsed.password is not None
        or parsed.hostname is None
        or parsed.query
        or parsed.fragment
        or (require_root_path and parsed.path not in ("", "/"))
    ):
        raise ExportError("invalid Chatto server origin")
    try:
        parsed.hostname.encode("ascii")
    except UnicodeEncodeError as exc:
        raise ExportError("invalid Chatto server origin") from exc
    return "https", parsed.hostname.lower(), port or 443


def _parse_chatto_url(value: str, server_url: str) -> ChattoLink:
    configured_origin = _origin(server_url, require_root_path=True)
    try:
        parsed = urlsplit(value)
        _ = parsed.port
    except ValueError as exc:
        raise ExportError("invalid Chatto URL") from exc
    if (
        parsed.scheme.lower() != "https"
        or not parsed.netloc
        or parsed.username is not None
        or parsed.password is not None
        or parsed.hostname is None
        or parsed.query
        or parsed.fragment
    ):
        raise ExportError("invalid Chatto URL")
    parts = parsed.path.split("/")
    if len(parts) not in (5, 6, 7) or parts[1] != "chat" or not parts[2]:
        raise ExportError("invalid Chatto URL")

    server_segment = parts[2]
    if server_segment == "-":
        input_origin = _origin(value, require_root_path=False)
        if input_origin != configured_origin:
            raise ExportError("Chatto URL does not match configured server")
    elif (
        not _DNS_NAME.fullmatch(server_segment)
        or configured_origin[2] != 443
        or server_segment.lower() != configured_origin[1]
    ):
        raise ExportError("Chatto URL does not match configured server")

    if len(parts) == 5 and parts[4]:
        room_id, message_id = parts[3], parts[4]
        thread_root_id = None
    elif len(parts) == 6 and parts[4] == "m" and parts[5]:
        room_id, message_id = parts[3], parts[5]
        thread_root_id = None
    elif len(parts) == 7 and parts[5] == "m" and parts[6]:
        room_id, thread_root_id, message_id = parts[3], parts[4], parts[6]
    else:
        raise ExportError("invalid Chatto URL")
    if not _ROOM_ID.fullmatch(room_id) or not _EVENT_ID.fullmatch(message_id):
        raise ExportError("invalid Chatto URL")
    if thread_root_id is not None and not _EVENT_ID.fullmatch(thread_root_id):
        raise ExportError("invalid Chatto URL")
    return ChattoLink(room_id, message_id, thread_root_id)


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
    link = _parse_chatto_url(url, settings.server_url)
    if link.thread_root_id is None:
        raise ExportError("room-message links are not supported yet")
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
                json={
                    "roomId": link.room_id,
                    "threadRootEventId": link.thread_root_id,
                    "limit": 500,
                },
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

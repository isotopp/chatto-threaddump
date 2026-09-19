from __future__ import annotations

import argparse
import json
import math
import os
import re
import shutil
import sys
import tempfile
import unicodedata
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from urllib.parse import quote, urlsplit

import httpx
from dotenv import dotenv_values

_THREAD_EVENTS_PATH = "/api/connect/chatto.api.v1.ThreadService/GetThreadEvents"
_GET_MESSAGE_PATH = "/api/connect/chatto.api.v1.MessageService/GetMessage"
_BATCH_USERS_PATH = "/api/connect/chatto.api.v1.UserService/BatchGetUsers"


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


@dataclass(frozen=True)
class ValidatedPage:
    messages: list[dict[str, object]]
    users: dict[str, object]
    event_ids: tuple[str, ...]
    event_times: tuple[datetime, ...]


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


def _render_messages(
    messages: list[dict[str, object]],
    users: object = None,
    attachments: dict[str, list[str]] | None = None,
) -> str:
    if users is None:
        users = {}
    if not isinstance(users, dict):
        raise ExportError("malformed thread response")

    rendered = ["# Chatto thread"]
    if not messages:
        raise ExportError("thread response contains no messages")
    rendered.extend(
        ("", f"Started: {_escape_metadata(_clean_text(_timestamp(messages[0])))}")
    )
    for message in messages:
        message_id = _required_string(message.get("id"), "message.id")
        actor_id = _required_string(message.get("actorId"), "message.actorId")
        user = users.get(actor_id, {})
        if not isinstance(user, dict):
            user = {}
        author = _author_label(user)
        rendered.extend(("", f"## {author}", "", _message_body(message)))
        if attachments is not None:
            attachment_lines = attachments.get(message_id, [])
            if attachment_lines:
                rendered.extend(("", *attachment_lines))
    return "\n".join(rendered) + "\n"


def _clean_text(value: str) -> str:
    value = value.replace("\r\n", "\n").replace("\r", "\n")
    return "".join(
        character
        for character in value
        if character in "\n\t" or not unicodedata.category(character).startswith("C")
    )


def _escape_metadata(value: str) -> str:
    value = value.replace("\\", "\\\\")
    return re.sub(r"([`*_\[\]()#+])", r"\\\1", value)


def _author_label(user: dict[str, object]) -> str:
    display_name = user.get("displayName")
    if isinstance(display_name, str):
        normalized = " ".join(_clean_text(display_name).split())
        if normalized:
            return _escape_metadata(normalized)
    login = user.get("login")
    if isinstance(login, str):
        normalized = " ".join(_clean_text(login).split()).lstrip("@")
        if normalized:
            return _escape_metadata(f"@{normalized}")
    return "Unknown author"


def _message_body(message: dict[str, object]) -> str:
    if "body" in message:
        body = message["body"]
        if not isinstance(body, str):
            raise ExportError("malformed message body")
        return _clean_text(body)
    deleted_at = message.get("deletedAt")
    if deleted_at is not None and not isinstance(deleted_at, str):
        raise ExportError("malformed deletion timestamp")
    if deleted_at:
        return "_[message deleted]_"
    return "_[message body unavailable]_"


def _required_string(value: object, field: str) -> str:
    if not isinstance(value, str) or not value:
        raise ExportError(f"malformed Chatto response field: {field}")
    return value


def _timestamp(value: dict[str, object]) -> str:
    for field in ("createdAt", "createTime"):
        if field in value:
            return _required_string(value[field], field)
    raise ExportError("malformed Chatto response field: creation timestamp")


def _timestamp_value(value: str) -> datetime:
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError as exc:
        raise ExportError("malformed Chatto response timestamp") from exc
    if parsed.tzinfo is None:
        raise ExportError("malformed Chatto response timestamp")
    return parsed


def _validate_includes(value: object) -> dict[str, object]:
    if value is None:
        return {}
    if not isinstance(value, dict):
        raise ExportError("malformed Chatto response includes")
    users = value.get("users", {})
    if not isinstance(users, dict):
        raise ExportError("malformed Chatto response users")
    for user_id, user in users.items():
        if not isinstance(user_id, str) or not isinstance(user, dict):
            raise ExportError("malformed Chatto response user map")
        if user.get("id") != user_id:
            raise ExportError("malformed Chatto response user identity")
    return users


def _validate_message(message: object) -> dict[str, object]:
    if not isinstance(message, dict):
        raise ExportError("malformed Chatto response message")
    _required_string(message.get("id"), "message.id")
    _required_string(message.get("roomId"), "message.roomId")
    _required_string(message.get("actorId"), "message.actorId")
    _timestamp(message)
    if "threadRootEventId" in message and not isinstance(
        message["threadRootEventId"], str
    ):
        raise ExportError("malformed Chatto response thread root")
    if "thread" in message and not isinstance(message["thread"], dict):
        raise ExportError("malformed Chatto response thread summary")
    return message


def _validate_thread_page(
    response: dict[str, object], room_id: str, root_id: str, *, initial: bool
) -> ValidatedPage:
    events = response.get("events")
    page = response.get("page")
    if not isinstance(events, list) or not isinstance(page, dict):
        raise ExportError("malformed thread response")
    if not isinstance(page.get("hasOlder"), bool):
        raise ExportError("malformed thread response page")
    if "startCursor" in page and not isinstance(page["startCursor"], str):
        raise ExportError("malformed thread response cursor")
    includes = _validate_includes(response.get("includes"))
    messages: list[dict[str, object]] = []
    event_ids: list[str] = []
    event_times: list[datetime] = []
    for event in events:
        if not isinstance(event, dict):
            raise ExportError("malformed thread event")
        event_id = _required_string(event.get("id"), "event.id")
        event_room = _required_string(event.get("roomId"), "event.roomId")
        event_actor = _required_string(event.get("actorId"), "event.actorId")
        event_timestamp = _timestamp(event)
        event_ids.append(event_id)
        event_times.append(_timestamp_value(event_timestamp))
        if len(event_times) > 1 and event_times[-2] > event_times[-1]:
            raise ExportError("thread events are out of order")
        if event_room != room_id:
            raise ExportError("malformed thread event room")
        posted = event.get("messagePosted")
        if posted is None:
            continue
        if not isinstance(posted, dict):
            raise ExportError("malformed message-posted event")
        message = _validate_message(posted.get("message"))
        if (
            message["id"] != event_id
            or message["roomId"] != event_room
            or message["actorId"] != event_actor
            or _timestamp(message) != event_timestamp
        ):
            raise ExportError("inconsistent message event")
        if message["id"] != root_id and message.get("threadRootEventId") != root_id:
            raise ExportError("inconsistent reply thread root")
        messages.append(message)

    root_positions = [
        index for index, message in enumerate(messages) if message["id"] == root_id
    ]
    if initial:
        if root_positions != [0]:
            raise ExportError("thread root is missing or misplaced")
    elif root_positions:
        raise ExportError("older thread page contains the root")
    return ValidatedPage(messages, includes, tuple(event_ids), tuple(event_times))


def _request_json(
    settings: Settings, path: str, payload: dict[str, object]
) -> dict[str, object]:
    headers = {
        "Authorization": f"Bearer {settings.api_key}",
        "Content-Type": "application/json",
        "Connect-Protocol-Version": "1",
    }
    request_url = settings.server_url.rstrip("/") + path
    try:
        with httpx.Client(follow_redirects=False, timeout=settings.timeout) as client:
            response = client.post(request_url, headers=headers, json=payload)
    except httpx.TimeoutException as exc:
        raise ExportError("network timeout") from exc
    except httpx.TransportError as exc:
        raise ExportError("transport or TLS failure") from exc
    if response.is_redirect:
        raise ExportError("redirect rejected")
    if response.status_code == 401:
        raise ExportError("authentication failure")
    if response.status_code == 403:
        raise ExportError("permission denied")
    if response.status_code == 404:
        raise ExportError("Chatto resource not found")
    if response.status_code >= 500:
        raise ExportError("Chatto server failure")
    if response.status_code >= 400:
        try:
            value = response.json()
        except json.JSONDecodeError as exc:
            raise ExportError("malformed JSON response") from exc
        if isinstance(value, dict) and isinstance(value.get("code"), str):
            raise ExportError("ConnectRPC failure")
        raise ExportError("Chatto request failed")
    try:
        value = response.json()
    except json.JSONDecodeError as exc:
        raise ExportError("malformed JSON response") from exc
    if isinstance(value, dict) and isinstance(value.get("code"), str):
        raise ExportError("ConnectRPC failure")
    if not isinstance(value, dict):
        raise ExportError("malformed Chatto response")
    return value


def _validate_lookup(
    response: dict[str, object], room_id: str, message_id: str
) -> tuple[dict[str, object], dict[str, object]]:
    message = _validate_message(response.get("message"))
    if message["id"] != message_id or message["roomId"] != room_id:
        raise ExportError("message lookup did not match the requested message")
    return message, _validate_includes(response.get("includes"))


def _load_thread(
    settings: Settings, room_id: str, root_id: str
) -> tuple[list[dict[str, object]], dict[str, object]]:
    page = _request_json(
        settings,
        _THREAD_EVENTS_PATH,
        {"roomId": room_id, "threadRootEventId": root_id, "limit": 500},
    )
    first_page = _validate_thread_page(page, room_id, root_id, initial=True)
    root_message, all_replies = first_page.messages[0], first_page.messages[1:]
    all_users = dict(first_page.users)
    seen_event_ids = set(first_page.event_ids)
    root_time = _timestamp_value(_timestamp(root_message))
    seen_cursors: set[str] = set()
    page_info = page["page"]
    while isinstance(page_info, dict) and page_info["hasOlder"]:
        cursor = page_info.get("startCursor")
        if not isinstance(cursor, str) or not cursor or cursor in seen_cursors:
            raise ExportError("invalid thread pagination cursor")
        seen_cursors.add(cursor)
        page = _request_json(
            settings,
            _THREAD_EVENTS_PATH,
            {
                "roomId": room_id,
                "threadRootEventId": root_id,
                "limit": 500,
                "before": cursor,
            },
        )
        older_page = _validate_thread_page(page, room_id, root_id, initial=False)
        if any(event_id in seen_event_ids for event_id in older_page.event_ids):
            raise ExportError("duplicate thread event")
        seen_event_ids.update(older_page.event_ids)
        older_messages, older_users = older_page.messages, older_page.users
        if older_messages:
            if all_replies:
                first_reply_time = _timestamp_value(_timestamp(all_replies[0]))
                if _timestamp_value(_timestamp(older_messages[-1])) > first_reply_time:
                    raise ExportError("older thread page is out of order")
            elif _timestamp_value(_timestamp(older_messages[0])) < root_time:
                raise ExportError("reply precedes thread root")
        all_replies = older_messages + all_replies
        all_users.update(older_users)
        page_info = page["page"]
    return [root_message, *all_replies], all_users


def _hydrate_authors(
    settings: Settings,
    messages: list[dict[str, object]],
    users: dict[str, object],
) -> dict[str, object]:
    unresolved: list[str] = []
    seen: set[str] = set()
    for message in messages:
        actor_id = _required_string(message.get("actorId"), "message.actorId")
        if actor_id not in users and actor_id not in seen:
            seen.add(actor_id)
            unresolved.append(actor_id)

    for start in range(0, len(unresolved), 100):
        batch = unresolved[start : start + 100]
        response = _request_json(settings, _BATCH_USERS_PATH, {"userIds": batch})
        records = response.get("users")
        if not isinstance(records, list):
            raise ExportError("malformed user response")
        returned: set[str] = set()
        for record in records:
            if not isinstance(record, dict):
                raise ExportError("malformed user response")
            user = record.get("user")
            if not isinstance(user, dict):
                raise ExportError("malformed user response")
            user_id = _required_string(user.get("id"), "user.id")
            if user_id not in batch or user_id in returned:
                raise ExportError("invalid user response")
            returned.add(user_id)
            users[user_id] = user
    return users


def _attachment_name(value: object) -> str:
    if not isinstance(value, str):
        return "attachment"
    cleaned = " ".join(_clean_text(value).split()).replace("\\", "/")
    name = cleaned.rsplit("/", 1)[-1]
    return name if name not in ("", ".", "..") else "attachment"


def _unique_attachment_name(name: str, used: set[str]) -> str:
    if name != "_index.md" and name not in used:
        used.add(name)
        return name
    path = Path(name)
    suffix = path.suffix
    stem = name[: -len(suffix)] if suffix else name
    index = 1
    while True:
        candidate = f"{stem}.{index}{suffix}"
        if candidate not in used and candidate != "_index.md":
            used.add(candidate)
            return candidate
        index += 1


def _mime_type(value: object) -> str:
    if not isinstance(value, str):
        raise ExportError("malformed attachment MIME type")
    return " ".join(_clean_text(value).split())


def _download_asset(settings: Settings, value: object) -> bytes:
    if not isinstance(value, dict) or not isinstance(value.get("url"), str):
        raise ExportError("attachment has no usable asset URL")
    url = value["url"]
    try:
        parsed = urlsplit(url)
        _ = parsed.port
        if (
            parsed.scheme.lower() != "https"
            or not parsed.netloc
            or parsed.hostname is None
            or parsed.username is not None
            or parsed.password is not None
            or parsed.fragment
        ):
            raise ValueError
        with httpx.Client(follow_redirects=False, timeout=settings.timeout) as client:
            response = client.get(url)
    except ValueError as exc:
        raise ExportError("attachment has no usable asset URL") from exc
    except httpx.TimeoutException as exc:
        raise ExportError("attachment network timeout") from exc
    except httpx.TransportError as exc:
        raise ExportError("attachment transport or TLS failure") from exc
    if response.is_redirect:
        raise ExportError("attachment redirect rejected")
    if response.status_code == 404:
        raise ExportError("attachment not found")
    if response.status_code >= 500:
        raise ExportError("attachment server failure")
    if response.status_code >= 400:
        raise ExportError("attachment download failed")
    return response.content


def _collect_attachments(
    settings: Settings, messages: list[dict[str, object]]
) -> tuple[dict[str, list[str]], list[tuple[str, bytes]]]:
    links: dict[str, list[str]] = {}
    files: list[tuple[str, bytes]] = []
    used_names = {"_index.md"}
    for message in messages:
        message_id = _required_string(message.get("id"), "message.id")
        raw_attachments = message.get("attachments", [])
        if not isinstance(raw_attachments, list):
            raise ExportError("malformed message attachments")
        for attachment in raw_attachments:
            if not isinstance(attachment, dict):
                raise ExportError("malformed attachment")
            filename = _unique_attachment_name(
                _attachment_name(attachment.get("filename")), used_names
            )
            mime_type = _mime_type(
                attachment.get("mimeType", "application/octet-stream")
            )
            description = attachment.get("description", "")
            if description is None:
                description = ""
            if not isinstance(description, str):
                raise ExportError("malformed attachment description")
            data = _download_asset(settings, attachment.get("assetUrl"))
            clean_description = _clean_text(description)
            target = quote(filename, safe="")
            label = clean_description or filename
            if mime_type.lower().startswith("image/"):
                link = f"![{_escape_metadata(label)}]({target})"
            else:
                link = f"[{_escape_metadata(filename)}]({target})"
                if clean_description:
                    link += f" — {_escape_metadata(clean_description)}"
            links.setdefault(message_id, []).append(link)
            files.append((filename, data))
    return links, files


def _check_output_path(output: Path, force: bool) -> None:
    if output.is_symlink() or output.exists():
        if not output.is_dir():
            raise ExportError("output path is not a directory")
        if not force:
            raise ExportError("output directory already exists; use --force")


def _publish(
    content: str,
    output: Path,
    force: bool,
    files: list[tuple[str, bytes]],
) -> None:
    existing_parent = output.parent
    while not existing_parent.exists():
        existing_parent = existing_parent.parent
    if not existing_parent.is_dir():
        raise ExportError("output parent is not a directory")

    staged = Path(tempfile.mkdtemp(prefix=f".{output.name}-", dir=existing_parent))
    backup: Path | None = None
    try:
        (staged / "_index.md").write_text(content, encoding="utf-8")
        for filename, data in files:
            (staged / filename).write_bytes(data)
        output.parent.mkdir(parents=True, exist_ok=True)
        if force and (output.is_symlink() or output.exists()):
            backup = Path(
                tempfile.mkdtemp(prefix=f".{output.name}-backup-", dir=output.parent)
            )
            backup.rmdir()
            os.replace(output, backup)
        try:
            os.replace(staged, output)
        except OSError:
            if backup is not None and not output.exists():
                os.replace(backup, output)
                backup = None
            raise
        if backup is not None:
            shutil.rmtree(backup)
            backup = None
    finally:
        if staged.exists():
            shutil.rmtree(staged)
        if backup is not None and backup.exists() and not output.exists():
            os.replace(backup, output)


def _export(url: str, output: Path, settings: Settings) -> None:
    _check_output_path(output, settings.force)
    link = _parse_chatto_url(url, settings.server_url)
    if link.thread_root_id is not None:
        messages, includes = _load_thread(settings, link.room_id, link.thread_root_id)
    else:
        lookup = _request_json(
            settings,
            _GET_MESSAGE_PATH,
            {"roomId": link.room_id, "eventId": link.message_id},
        )
        message, includes = _validate_lookup(lookup, link.room_id, link.message_id)
        thread_root_id = message.get("threadRootEventId") or link.message_id
        if not isinstance(thread_root_id, str):
            raise ExportError("malformed Chatto response thread root")
        if "thread" not in message or message.get("thread") is None:
            messages = [message]
        else:
            messages, includes = _load_thread(settings, link.room_id, thread_root_id)
    users = _hydrate_authors(settings, messages, includes)
    attachment_links, files = _collect_attachments(settings, messages)
    content = _render_messages(messages, users, attachment_links)
    _publish(content, output, settings.force, files)


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
    except ExportError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    except OSError:
        print("error: output failure", file=sys.stderr)
        return 1
    return 0

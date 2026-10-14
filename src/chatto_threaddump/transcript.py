from __future__ import annotations

import re
import unicodedata
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any, Protocol, cast

from chattolib._pb.chatto.api.v1 import user_service_pb2
from google.protobuf.message import Message

from chatto_threaddump.thread_reader import ThreadMessage, ThreadUser


class TranscriptError(Exception):
    """Raised when author hydration or transcript rendering fails."""


class UserServiceClient(Protocol):
    async def batch_get_users(
        self,
        request: Any,
        *,
        headers: Mapping[str, str] | None = None,
    ) -> Any: ...


async def write_transcript(
    messages: Sequence[ThreadMessage],
    included_users: Mapping[str, ThreadUser],
    client: UserServiceClient,
    *,
    api_key: str,
    output_dir: Path,
    attachment_lines: Mapping[str, Sequence[str]] | None = None,
) -> Path:
    """Hydrate missing authors and write the Chatto Markdown transcript."""
    if not messages:
        raise TranscriptError("thread contains no messages")

    users = await _hydrate_authors(client, api_key, messages, included_users)
    rendered = [
        "# Chatto thread",
        "",
        f"Started: {_escape_metadata(_timestamp(messages[0]))}",
    ]
    for message in messages:
        user = users.get(message.actor_id)
        rendered.extend(("", f"## {_author_label(user)}", "", _message_body(message)))
        if attachment_lines is not None:
            links = attachment_lines.get(message.id, ())
            if links:
                rendered.extend(("", *(_clean_text(line) for line in links)))

    path = output_dir / "_index.md"
    path.write_text("\n".join(rendered) + "\n", encoding="utf-8")
    return path


async def _hydrate_authors(
    client: UserServiceClient,
    api_key: str,
    messages: Sequence[ThreadMessage],
    included_users: Mapping[str, ThreadUser],
) -> dict[str, ThreadUser]:
    users = dict(included_users)
    unresolved: list[str] = []
    seen: set[str] = set()
    for message in messages:
        actor_id = message.actor_id
        if actor_id and actor_id not in users and actor_id not in seen:
            seen.add(actor_id)
            unresolved.append(actor_id)

    for start in range(0, len(unresolved), 100):
        batch = unresolved[start : start + 100]
        request_factory: Any = getattr(  # noqa: B009 - generated protobuf module is dynamic.
            user_service_pb2, "BatchGetUsersRequest"
        )
        request = cast(Message, request_factory(user_ids=batch))
        try:
            response = await client.batch_get_users(
                request, headers={"Authorization": f"Bearer {api_key}"}
            )
        except Exception:  # noqa: BLE001 - transport errors may include private bodies.
            raise TranscriptError("Chatto user lookup failed") from None
        if (
            not isinstance(response, Message)
            or response.DESCRIPTOR.full_name != "chatto.api.v1.BatchGetUsersResponse"
        ):
            raise TranscriptError("malformed Chatto user response")

        requested = set(batch)
        returned: set[str] = set()
        for member in response.users:
            if not member.HasField("user"):
                raise TranscriptError("malformed Chatto user response")
            user = member.user
            user_id = user.id
            if not user_id or user_id not in requested or user_id in returned:
                raise TranscriptError("invalid Chatto user response")
            returned.add(user_id)
            users[user_id] = cast(ThreadUser, user)

    return users


def _timestamp(message: ThreadMessage) -> str:
    if not message.HasField("created_at"):
        raise TranscriptError("malformed thread timestamp")
    try:
        return cast(Any, message.created_at).ToJsonString()
    except AttributeError, ValueError:
        raise TranscriptError("malformed thread timestamp") from None


def _author_label(user: ThreadUser | None) -> str:
    if user is None:
        return "Unknown author"
    display_name = user.display_name
    if isinstance(display_name, str):
        normalized = " ".join(_clean_text(display_name).split())
        if normalized:
            return _escape_metadata(normalized)
    login = user.login
    if isinstance(login, str):
        normalized = " ".join(_clean_text(login).split()).lstrip("@")
        if normalized:
            return _escape_metadata(f"@{normalized}")
    return "Unknown author"


def _message_body(message: ThreadMessage) -> str:
    if message.HasField("body"):
        body = message.body
        if not isinstance(body, str):
            raise TranscriptError("malformed thread message body")
        return _clean_text(body)
    if message.HasField("deleted_at"):
        return "_[message deleted]_"
    return "_[message body unavailable]_"


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

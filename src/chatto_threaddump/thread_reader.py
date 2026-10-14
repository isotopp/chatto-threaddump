from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any, Protocol, cast

from chattolib._pb.chatto.api.v1 import (
    room_timeline_pb2,
)
from google.protobuf.message import Message


class ThreadReadError(Exception):
    """Raised when a Chatto thread page cannot be read or validated."""


class _TimestampValue(Protocol):
    seconds: int
    nanos: int


class ThreadMessage(Protocol):
    id: str
    room_id: str
    actor_id: str
    created_at: _TimestampValue
    body: str
    thread_root_event_id: str

    def HasField(self, field: str) -> bool: ...


class ThreadUser(Protocol):
    id: str
    login: str
    display_name: str


class _MessagePosted(Protocol):
    message: ThreadMessage

    def HasField(self, field: str) -> bool: ...


class _TimelineEvent(Protocol):
    id: str
    actor_id: str
    created_at: _TimestampValue
    message_posted: _MessagePosted

    def HasField(self, field: str) -> bool: ...

    def WhichOneof(self, field: str) -> str | None: ...


class _TimelineIncludes(Protocol):
    users: Mapping[str, ThreadUser]


class _TimelinePage(Protocol):
    events: list[_TimelineEvent]
    includes: _TimelineIncludes
    has_older: bool
    start_cursor: str

    def HasField(self, field: str) -> bool: ...


class ThreadEventsClient(Protocol):
    async def get_thread_events(
        self,
        request: Any,
        *,
        headers: Mapping[str, str] | None = None,
    ) -> Any: ...


@dataclass(frozen=True)
class ThreadPage:
    """Validated thread data and pagination metadata."""

    starter: ThreadMessage
    replies: tuple[ThreadMessage, ...]
    included_users: Mapping[str, ThreadUser]
    has_older: bool
    start_cursor: str

    @property
    def messages(self) -> tuple[ThreadMessage, ...]:
        """Return the starter followed by chronological replies."""
        return (self.starter, *self.replies)


@dataclass(frozen=True)
class _ValidatedPage:
    starter: ThreadMessage | None
    replies: tuple[ThreadMessage, ...]
    included_users: Mapping[str, ThreadUser]
    has_older: bool
    start_cursor: str
    event_ids: tuple[str, ...]


async def read_thread_page(
    client: ThreadEventsClient,
    room_id: str,
    root_event_id: str,
    *,
    api_key: str,
) -> ThreadPage:
    """Read and validate the initial page for the requested thread."""
    page = await _read_page(
        client,
        room_id,
        root_event_id,
        api_key=api_key,
        before=None,
        initial=True,
    )
    if page.starter is None:
        raise ThreadReadError("thread root is missing or misplaced")
    return ThreadPage(
        starter=page.starter,
        replies=page.replies,
        included_users=page.included_users,
        has_older=page.has_older,
        start_cursor=page.start_cursor,
    )


async def read_thread(
    client: ThreadEventsClient,
    room_id: str,
    root_event_id: str,
    *,
    api_key: str,
) -> ThreadPage:
    """Read all pages and return the complete chronological thread."""
    current = await _read_page(
        client,
        room_id,
        root_event_id,
        api_key=api_key,
        before=None,
        initial=True,
    )
    if current.starter is None:
        raise ThreadReadError("thread root is missing or misplaced")

    starter = current.starter
    replies = list(current.replies)
    included_users = dict(current.included_users)
    seen_event_ids = set(current.event_ids)
    seen_cursors: set[str] = set()
    root_time = _timestamp(starter.created_at)
    root_time_key = (root_time.seconds, root_time.nanos)
    for message in current.replies:
        message_time = _timestamp(message.created_at)
        if (message_time.seconds, message_time.nanos) < root_time_key:
            raise ThreadReadError("reply precedes thread root")
    newer_reply_boundary = (
        _timestamp(current.replies[0].created_at) if current.replies else None
    )

    while current.has_older:
        cursor = current.start_cursor
        if not cursor or cursor in seen_cursors:
            raise ThreadReadError("invalid thread pagination cursor")
        seen_cursors.add(cursor)

        older = await _read_page(
            client,
            room_id,
            root_event_id,
            api_key=api_key,
            before=cursor,
            initial=False,
        )
        if any(event_id in seen_event_ids for event_id in older.event_ids):
            raise ThreadReadError("duplicate thread event")
        seen_event_ids.update(older.event_ids)

        if older.replies and newer_reply_boundary is not None:
            older_last_time = _timestamp(older.replies[-1].created_at)
            current_first_time = newer_reply_boundary
            if (older_last_time.seconds, older_last_time.nanos) > (
                current_first_time.seconds,
                current_first_time.nanos,
            ):
                raise ThreadReadError("older thread page is out of order")
        for message in older.replies:
            message_time = _timestamp(message.created_at)
            if (message_time.seconds, message_time.nanos) < root_time_key:
                raise ThreadReadError("reply precedes thread root")

        if older.replies:
            newer_reply_boundary = _timestamp(older.replies[0].created_at)

        replies = [*older.replies, *replies]
        included_users.update(older.included_users)
        current = older

    return ThreadPage(
        starter=starter,
        replies=tuple(replies),
        included_users=included_users,
        has_older=False,
        start_cursor="",
    )


async def _read_page(
    client: ThreadEventsClient,
    room_id: str,
    root_event_id: str,
    *,
    api_key: str,
    before: str | None,
    initial: bool,
) -> _ValidatedPage:
    if not room_id or not root_event_id:
        raise ThreadReadError("a room ID and thread root ID are required")

    request_factory: Any = getattr(  # noqa: B009 - generated protobuf module is dynamic.
        room_timeline_pb2, "GetThreadEventsRequest"
    )
    request_values: dict[str, Any] = {
        "room_id": room_id,
        "thread_root_event_id": root_event_id,
        "limit": 500,
    }
    if before is not None:
        request_values["before"] = before
    request = cast(Message, request_factory(**request_values))
    try:
        response = await client.get_thread_events(
            request, headers={"Authorization": f"Bearer {api_key}"}
        )
    except Exception:  # noqa: BLE001 - transport exceptions may expose private bodies.
        raise ThreadReadError("Chatto thread request failed") from None

    if not isinstance(response, Message):
        raise ThreadReadError("malformed Chatto thread response")
    descriptor = response.DESCRIPTOR
    if (
        descriptor is None
        or descriptor.full_name != "chatto.api.v1.GetThreadEventsResponse"
    ):
        raise ThreadReadError("malformed Chatto thread response")
    if not response.HasField("page"):
        raise ThreadReadError("malformed Chatto thread response")

    page: Any = response.page
    return _validate_page(page, room_id, root_event_id, initial=initial)


def _validate_page(
    page: _TimelinePage,
    room_id: str,
    root_event_id: str,
    *,
    initial: bool,
) -> _ValidatedPage:
    users = _validate_users(page)
    messages: list[ThreadMessage] = []
    event_ids: list[str] = []
    seen_event_ids: set[str] = set()
    previous_time: tuple[int, int] | None = None

    for event in page.events:
        event_id = _required_string(event.id, "event ID")
        if event_id in seen_event_ids:
            raise ThreadReadError("duplicate thread event")
        seen_event_ids.add(event_id)
        event_ids.append(event_id)
        actor_id = _required_string(event.actor_id, "event actor ID")
        if not event.HasField("created_at"):
            raise ThreadReadError("malformed Chatto thread timestamp")
        event_time = _timestamp(event.created_at)
        current_time = (event_time.seconds, event_time.nanos)
        if previous_time is not None and previous_time > current_time:
            raise ThreadReadError("Chatto thread events are out of order")
        previous_time = current_time

        event_kind = event.WhichOneof("event")
        if event_kind is None:
            raise ThreadReadError("malformed Chatto thread event")
        if event_kind != "message_posted":
            continue
        if not event.message_posted.HasField("message"):
            raise ThreadReadError("malformed Chatto thread message")

        message = event.message_posted.message
        message_id = _required_string(message.id, "message ID")
        message_room_id = _required_string(message.room_id, "message room ID")
        message_actor_id = _required_string(message.actor_id, "message actor ID")
        if not message.HasField("created_at"):
            raise ThreadReadError("malformed Chatto thread timestamp")
        message_time = _timestamp(message.created_at)
        if (
            message_id != event_id
            or message_room_id != room_id
            or message_actor_id != actor_id
            or (message_time.seconds, message_time.nanos) != current_time
        ):
            raise ThreadReadError("inconsistent Chatto thread message")

        if (
            message_id != root_event_id
            and message.thread_root_event_id != root_event_id
        ):
            raise ThreadReadError("inconsistent Chatto reply thread root")
        messages.append(message)

    root_positions = [
        index for index, message in enumerate(messages) if message.id == root_event_id
    ]
    if initial and root_positions != [0]:
        raise ThreadReadError("thread root is missing or misplaced")
    if not initial and root_positions:
        raise ThreadReadError("older thread page contains the root")

    return _ValidatedPage(
        starter=messages[0] if initial else None,
        replies=tuple(messages[1:] if initial else messages),
        included_users=users,
        has_older=page.has_older,
        start_cursor=page.start_cursor,
        event_ids=tuple(event_ids),
    )


def _validate_users(
    page: _TimelinePage,
) -> Mapping[str, ThreadUser]:
    if not page.HasField("includes"):
        return {}
    users: dict[str, ThreadUser] = {}
    for user_id, user in page.includes.users.items():
        if not user_id or not user.id or user.id != user_id:
            raise ThreadReadError("malformed Chatto included user")
        users[user_id] = user
    return users


def _required_string(value: str, field: str) -> str:
    if not value:
        raise ThreadReadError(f"malformed Chatto {field}")
    return value


def _timestamp(value: _TimestampValue) -> _TimestampValue:
    if (
        value.seconds < -62_135_596_800
        or value.seconds > 253_402_300_799
        or not 0 <= value.nanos <= 999_999_999
    ):
        raise ThreadReadError("malformed Chatto thread timestamp") from None
    return value

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
    """Validated thread data and continuation metadata from one API page."""

    starter: ThreadMessage
    replies: tuple[ThreadMessage, ...]
    included_users: Mapping[str, ThreadUser]
    has_older: bool
    start_cursor: str

    @property
    def messages(self) -> tuple[ThreadMessage, ...]:
        """Return the starter followed by this page's chronological replies."""
        return (self.starter, *self.replies)


async def read_thread_page(
    client: ThreadEventsClient,
    room_id: str,
    root_event_id: str,
    *,
    api_key: str,
) -> ThreadPage:
    """Read and validate the initial page for the requested thread."""
    if not room_id or not root_event_id:
        raise ThreadReadError("a room ID and thread root ID are required")

    request_factory: Any = getattr(  # noqa: B009 - generated protobuf module is dynamic.
        room_timeline_pb2, "GetThreadEventsRequest"
    )
    request = cast(
        Message,
        request_factory(
            room_id=room_id,
            thread_root_event_id=root_event_id,
            limit=500,
        ),
    )
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
    return _validate_page(page, room_id, root_event_id)


def _validate_page(
    page: _TimelinePage,
    room_id: str,
    root_event_id: str,
) -> ThreadPage:
    users = _validate_users(page)
    messages: list[ThreadMessage] = []
    previous_time: tuple[int, int] | None = None

    for event in page.events:
        event_id = _required_string(event.id, "event ID")
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

    if not messages or messages[0].id != root_event_id:
        raise ThreadReadError("thread root is missing or misplaced")

    return ThreadPage(
        starter=messages[0],
        replies=tuple(messages[1:]),
        included_users=users,
        has_older=page.has_older,
        start_cursor=page.start_cursor,
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

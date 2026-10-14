import asyncio
from datetime import UTC, datetime
from types import ModuleType
from typing import Any

import pytest
from chattolib._pb.chatto.api.v1 import (
    message_types_pb2 as _message_types_pb2,
)
from chattolib._pb.chatto.api.v1 import (
    room_timeline_pb2 as _room_timeline_pb2,
)
from chattolib._pb.chatto.api.v1 import (
    users_pb2 as _users_pb2,
)
from google.protobuf import timestamp_pb2

from chatto_threaddump.thread_reader import ThreadReadError, read_thread_page


class _ProtoTypes:
    def __init__(self, module: ModuleType) -> None:
        self.module = module

    def __getattr__(self, name: str) -> Any:
        return getattr(self.module, name)


timeline_types = _ProtoTypes(_room_timeline_pb2)
message_types = _ProtoTypes(_message_types_pb2)
user_types = _ProtoTypes(_users_pb2)
timestamp_types = _ProtoTypes(timestamp_pb2)
room_timeline_pb2 = timeline_types
message_types_pb2 = message_types
users_pb2 = user_types


def _timestamp(year: int, month: int, day: int, hour: int = 0) -> Any:
    value = timestamp_types.Timestamp()
    value.FromDatetime(datetime(year, month, day, hour, tzinfo=UTC))
    return value


def _message(
    event_id: str,
    room_id: str,
    actor_id: str,
    created_at: Any,
    *,
    thread_root_event_id: str = "",
) -> Any:
    return timeline_types.RoomMessagePosted(
        message=message_types.Message(
            id=event_id,
            room_id=room_id,
            actor_id=actor_id,
            created_at=created_at,
            thread_root_event_id=thread_root_event_id,
        )
    )


class _FakeThreadClient:
    def __init__(self, response: Any) -> None:
        self.response = response
        self.calls: list[tuple[object, dict[str, str] | None]] = []

    async def get_thread_events(self, request: Any, *, headers=None) -> Any:
        self.calls.append((request, headers))
        return self.response


def _event(
    event_id: str,
    actor_id: str,
    created_at: Any,
    *,
    thread_root_event_id: str = "",
    room_id: str = "R12345678901234",
) -> Any:
    return timeline_types.RoomTimelineEvent(
        id=event_id,
        actor_id=actor_id,
        created_at=created_at,
        message_posted=_message(
            event_id,
            room_id,
            actor_id,
            created_at,
            thread_root_event_id=thread_root_event_id,
        ),
    )


def _response(
    *events: Any,
) -> Any:
    return timeline_types.GetThreadEventsResponse(
        page=timeline_types.RoomTimelinePage(events=events)
    )


def test_reads_one_valid_thread_page_with_root_replies_and_included_authors() -> None:
    client = _FakeThreadClient(
        room_timeline_pb2.GetThreadEventsResponse(
            page=room_timeline_pb2.RoomTimelinePage(
                events=[
                    room_timeline_pb2.RoomTimelineEvent(
                        id="Eroot00000000001",
                        actor_id="Uroot00000000001",
                        created_at=_timestamp(2026, 1, 1),
                        message_posted=_message(
                            "Eroot00000000001",
                            "R12345678901234",
                            "Uroot00000000001",
                            _timestamp(2026, 1, 1),
                        ),
                    ),
                    room_timeline_pb2.RoomTimelineEvent(
                        id="Ereply0000000001",
                        actor_id="Ureply0000000001",
                        created_at=_timestamp(2026, 1, 2),
                        message_posted=_message(
                            "Ereply0000000001",
                            "R12345678901234",
                            "Ureply0000000001",
                            _timestamp(2026, 1, 2),
                            thread_root_event_id="Eroot00000000001",
                        ),
                    ),
                ],
                includes=room_timeline_pb2.RoomTimelineIncludes(
                    users={
                        "Uroot00000000001": users_pb2.User(
                            id="Uroot00000000001", display_name="Starter"
                        ),
                        "Ureply0000000001": users_pb2.User(
                            id="Ureply0000000001", login="replier"
                        ),
                    }
                ),
                start_cursor="opaque-before-cursor",
                has_older=True,
            )
        )
    )

    page = asyncio.run(
        read_thread_page(
            client,
            "R12345678901234",
            "Eroot00000000001",
            api_key="fake-api-key",
        )
    )

    assert [message.id for message in page.messages] == [
        "Eroot00000000001",
        "Ereply0000000001",
    ]
    assert page.starter.actor_id == "Uroot00000000001"
    assert page.replies[0].thread_root_event_id == "Eroot00000000001"
    assert page.included_users["Ureply0000000001"].login == "replier"
    assert page.has_older is True
    assert page.start_cursor == "opaque-before-cursor"
    assert client.calls[0][0] == room_timeline_pb2.GetThreadEventsRequest(
        room_id="R12345678901234",
        thread_root_event_id="Eroot00000000001",
        limit=500,
    )
    assert client.calls[0][1] == {"Authorization": "Bearer fake-api-key"}


def test_rejects_page_that_does_not_contain_the_requested_root() -> None:
    client = _FakeThreadClient(
        _response(
            _event(
                "Ereply0000000001",
                "Ureply0000000001",
                _timestamp(2026, 1, 2),
                thread_root_event_id="Eroot00000000001",
            )
        )
    )

    with pytest.raises(ThreadReadError, match="thread root is missing or misplaced"):
        asyncio.run(
            read_thread_page(
                client,
                "R12345678901234",
                "Eroot00000000001",
                api_key="fake-api-key",
            )
        )


def test_rejects_requested_root_after_a_reply() -> None:
    client = _FakeThreadClient(
        _response(
            _event(
                "Ereply0000000001",
                "Ureply0000000001",
                _timestamp(2026, 1, 2),
                thread_root_event_id="Eroot00000000001",
            ),
            _event(
                "Eroot00000000001",
                "Uroot00000000001",
                _timestamp(2026, 1, 3),
            ),
        )
    )

    with pytest.raises(ThreadReadError, match="thread root is missing or misplaced"):
        asyncio.run(
            read_thread_page(
                client,
                "R12345678901234",
                "Eroot00000000001",
                api_key="fake-api-key",
            )
        )


def test_rejects_message_from_a_different_room() -> None:
    timestamp = _timestamp(2026, 1, 1)
    event = room_timeline_pb2.RoomTimelineEvent(
        id="Eroot00000000001",
        actor_id="Uroot00000000001",
        created_at=timestamp,
        message_posted=_message(
            "Eroot00000000001",
            "Rwrong0000000001",
            "Uroot00000000001",
            timestamp,
        ),
    )
    client = _FakeThreadClient(_response(event))

    with pytest.raises(ThreadReadError, match="inconsistent Chatto thread message"):
        asyncio.run(
            read_thread_page(
                client,
                "R12345678901234",
                "Eroot00000000001",
                api_key="fake-api-key",
            )
        )


@pytest.mark.parametrize("mismatch", ["event ID", "actor ID", "timestamp"])
def test_rejects_message_identity_that_differs_from_its_event(mismatch: str) -> None:
    event_timestamp = _timestamp(2026, 1, 1)
    message_timestamp = (
        _timestamp(2026, 1, 2) if mismatch == "timestamp" else event_timestamp
    )
    event_id = "Eroot00000000001"
    message_id = "Eother0000000001" if mismatch == "event ID" else event_id
    event_actor = "Uroot00000000001"
    message_actor = "Uother000000001" if mismatch == "actor ID" else event_actor
    event = room_timeline_pb2.RoomTimelineEvent(
        id=event_id,
        actor_id=event_actor,
        created_at=event_timestamp,
        message_posted=_message(
            message_id,
            "R12345678901234",
            message_actor,
            message_timestamp,
        ),
    )
    client = _FakeThreadClient(_response(event))

    with pytest.raises(ThreadReadError, match="inconsistent Chatto thread message"):
        asyncio.run(
            read_thread_page(
                client,
                "R12345678901234",
                event_id,
                api_key="fake-api-key",
            )
        )


def test_rejects_reply_for_a_different_thread_root() -> None:
    client = _FakeThreadClient(
        _response(
            _event(
                "Eroot00000000001",
                "Uroot00000000001",
                _timestamp(2026, 1, 1),
            ),
            _event(
                "Ereply0000000001",
                "Ureply0000000001",
                _timestamp(2026, 1, 2),
                thread_root_event_id="Ewrong00000000001",
            ),
        )
    )

    with pytest.raises(ThreadReadError, match="inconsistent Chatto reply thread root"):
        asyncio.run(
            read_thread_page(
                client,
                "R12345678901234",
                "Eroot00000000001",
                api_key="fake-api-key",
            )
        )


def test_ignores_a_well_formed_non_message_event() -> None:
    root_time = _timestamp(2026, 1, 1)
    changed_time = _timestamp(2026, 1, 2)
    reply_time = _timestamp(2026, 1, 3)
    non_message = room_timeline_pb2.RoomTimelineEvent(
        id="Echange000000001",
        actor_id="Uchange000000001",
        created_at=changed_time,
        room_updated=room_timeline_pb2.RoomTimelineRoomEvent(room_id="R12345678901234"),
    )
    client = _FakeThreadClient(
        _response(
            _event("Eroot00000000001", "Uroot00000000001", root_time),
            non_message,
            _event(
                "Ereply0000000001",
                "Ureply0000000001",
                reply_time,
                thread_root_event_id="Eroot00000000001",
            ),
        )
    )

    page = asyncio.run(
        read_thread_page(
            client,
            "R12345678901234",
            "Eroot00000000001",
            api_key="fake-api-key",
        )
    )

    assert [message.id for message in page.messages] == [
        "Eroot00000000001",
        "Ereply0000000001",
    ]


def test_rejects_missing_event_and_message_timestamps() -> None:
    event = room_timeline_pb2.RoomTimelineEvent(
        id="Eroot00000000001",
        actor_id="Uroot00000000001",
        message_posted=room_timeline_pb2.RoomMessagePosted(
            message=message_types_pb2.Message(
                id="Eroot00000000001",
                room_id="R12345678901234",
                actor_id="Uroot00000000001",
            )
        ),
    )
    client = _FakeThreadClient(_response(event))

    with pytest.raises(ThreadReadError, match="malformed Chatto thread timestamp"):
        asyncio.run(
            read_thread_page(
                client,
                "R12345678901234",
                "Eroot00000000001",
                api_key="fake-api-key",
            )
        )


def test_rejects_response_with_the_wrong_generated_type() -> None:
    client = _FakeThreadClient({"private": "response body"})

    with pytest.raises(ThreadReadError, match="malformed Chatto thread response"):
        asyncio.run(
            read_thread_page(
                client,
                "R12345678901234",
                "Eroot00000000001",
                api_key="fake-api-key",
            )
        )


def test_rejects_response_without_a_thread_page() -> None:
    client = _FakeThreadClient(room_timeline_pb2.GetThreadEventsResponse())

    with pytest.raises(ThreadReadError, match="malformed Chatto thread response"):
        asyncio.run(
            read_thread_page(
                client,
                "R12345678901234",
                "Eroot00000000001",
                api_key="fake-api-key",
            )
        )


def test_rejects_included_user_whose_id_does_not_match_its_map_key() -> None:
    response = _response(
        _event(
            "Eroot00000000001",
            "Uroot00000000001",
            _timestamp(2026, 1, 1),
        )
    )
    response.page.includes.users["Uroot00000000001"].id = "Uwrong0000000001"
    client = _FakeThreadClient(response)

    with pytest.raises(ThreadReadError, match="malformed Chatto included user"):
        asyncio.run(
            read_thread_page(
                client,
                "R12345678901234",
                "Eroot00000000001",
                api_key="fake-api-key",
            )
        )


def test_rejects_event_with_no_known_event_payload() -> None:
    event = room_timeline_pb2.RoomTimelineEvent(
        id="Eroot00000000001",
        actor_id="Uroot00000000001",
        created_at=_timestamp(2026, 1, 1),
    )
    client = _FakeThreadClient(_response(event))

    with pytest.raises(ThreadReadError, match="malformed Chatto thread event"):
        asyncio.run(
            read_thread_page(
                client,
                "R12345678901234",
                "Eroot00000000001",
                api_key="fake-api-key",
            )
        )


def test_sanitizes_transport_error() -> None:
    class _FailingClient:
        async def get_thread_events(self, request, *, headers=None):
            raise RuntimeError("fake-api-key private response body")

    with pytest.raises(ThreadReadError, match="Chatto thread request failed") as error:
        asyncio.run(
            read_thread_page(
                _FailingClient(),
                "R12345678901234",
                "Eroot00000000001",
                api_key="fake-api-key",
            )
        )

    assert "fake-api-key" not in str(error.value)
    assert "private response body" not in str(error.value)

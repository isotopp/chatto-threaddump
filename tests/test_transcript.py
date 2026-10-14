import asyncio
from datetime import UTC, datetime
from pathlib import Path
from types import ModuleType
from typing import Any

import pytest
from chattolib._pb.chatto.api.v1 import (
    message_types_pb2 as _message_types_pb2,
)
from chattolib._pb.chatto.api.v1 import (
    user_service_pb2 as _user_service_pb2,
)
from chattolib._pb.chatto.api.v1 import (
    users_pb2 as _users_pb2,
)
from google.protobuf import timestamp_pb2 as _timestamp_pb2

from chatto_threaddump.transcript import TranscriptError, write_transcript


class _ProtoTypes:
    def __init__(self, module: ModuleType) -> None:
        self.module = module

    def __getattr__(self, name: str) -> Any:
        return getattr(self.module, name)


message_types_pb2 = _ProtoTypes(_message_types_pb2)
user_service_pb2 = _ProtoTypes(_user_service_pb2)
users_pb2 = _ProtoTypes(_users_pb2)
timestamp_pb2 = _ProtoTypes(_timestamp_pb2)


def _message(
    message_id: str,
    actor_id: str,
    *,
    body: str | None = None,
    deleted: bool = False,
    thread_root_event_id: str = "",
    second: int = 0,
) -> Any:
    timestamp = timestamp_pb2.Timestamp()
    timestamp.FromDatetime(datetime(2026, 9, 19, 10, 0, second, tzinfo=UTC))
    message = message_types_pb2.Message(
        id=message_id,
        room_id="R12345678901234",
        actor_id=actor_id,
        created_at=timestamp,
        thread_root_event_id=thread_root_event_id,
    )
    if body is not None:
        message.body = body
    if deleted:
        message.deleted_at.FromDatetime(datetime(2026, 9, 19, 10, 1, tzinfo=UTC))
    return message


class _FakeUsersClient:
    def __init__(self, responses: list[Any]) -> None:
        self.responses = responses
        self.requests: list[Any] = []

    async def batch_get_users(self, request: Any, *, headers=None) -> Any:
        self.requests.append((request, headers))
        return self.responses[len(self.requests) - 1]


def test_writes_transcript_with_included_and_hydrated_authors(tmp_path: Path) -> None:
    messages = [
        _message("Eroot00000000001", "Uincluded", body="starter\r\n*markdown*\x1b"),
        _message(
            "Ereply0000000001",
            "Umissing",
            body="reply",
            thread_root_event_id="Eroot00000000001",
            second=1,
        ),
    ]
    included = {
        "Uincluded": users_pb2.User(id="Uincluded", display_name="  Alice\n#  ")
    }
    users = _FakeUsersClient(
        [
            user_service_pb2.BatchGetUsersResponse(
                users=[
                    {
                        "user": {
                            "id": "Umissing",
                            "login": "  bob\t",
                        }
                    }
                ]
            )
        ]
    )

    path = asyncio.run(
        write_transcript(
            messages,
            included,
            users,
            api_key="fake-api-key",
            output_dir=tmp_path,
        )
    )

    assert path == tmp_path / "_index.md"
    assert path.read_text(encoding="utf-8") == (
        "# Chatto thread\n"
        "\nStarted: 2026-09-19T10:00:00Z\n"
        "\n## Alice \\#\n"
        "\nstarter\n*markdown*\n"
        "\n## @bob\n"
        "\nreply\n"
    )
    assert users.requests[0][0].user_ids == ["Umissing"]
    assert users.requests[0][1] == {"Authorization": "Bearer fake-api-key"}
    assert "Uincluded" not in path.read_text(encoding="utf-8")


def test_hydrates_unique_authors_in_first_seen_batches_of_at_most_100(
    tmp_path: Path,
) -> None:
    actor_ids = [f"U{index}" for index in range(101)]
    messages = [
        _message(f"E{index}", actor_id, body="message")
        for index, actor_id in enumerate([*actor_ids, actor_ids[0]])
    ]
    responses = [
        user_service_pb2.BatchGetUsersResponse(
            users=[{"user": {"id": actor_id}} for actor_id in batch]
        )
        for batch in (actor_ids[:100], actor_ids[100:])
    ]
    users = _FakeUsersClient(responses)

    asyncio.run(
        write_transcript(
            messages,
            {},
            users,
            api_key="fake-api-key",
            output_dir=tmp_path,
        )
    )

    assert [request.user_ids for request, _ in users.requests] == [
        actor_ids[:100],
        actor_ids[100:],
    ]
    assert all(len(request.user_ids) <= 100 for request, _ in users.requests)


def test_rejects_an_unrequested_hydrated_user(tmp_path: Path) -> None:
    users = _FakeUsersClient(
        [
            user_service_pb2.BatchGetUsersResponse(
                users=[{"user": {"id": "Uunexpected"}}]
            )
        ]
    )

    with pytest.raises(TranscriptError, match="invalid Chatto user response"):
        asyncio.run(
            write_transcript(
                [_message("Eroot00000000001", "Urequested", body="text")],
                {},
                users,
                api_key="fake-api-key",
                output_dir=tmp_path,
            )
        )


def test_rejects_duplicate_hydrated_user_records(tmp_path: Path) -> None:
    users = _FakeUsersClient(
        [
            user_service_pb2.BatchGetUsersResponse(
                users=[
                    {"user": {"id": "Urequested"}},
                    {"user": {"id": "Urequested"}},
                ]
            )
        ]
    )

    with pytest.raises(TranscriptError, match="invalid Chatto user response"):
        asyncio.run(
            write_transcript(
                [_message("Eroot00000000001", "Urequested", body="text")],
                {},
                users,
                api_key="fake-api-key",
                output_dir=tmp_path,
            )
        )


def test_preserves_empty_deleted_and_unavailable_bodies_with_author_fallbacks(
    tmp_path: Path,
) -> None:
    messages = [
        _message("Eroot00000000001", "Uroot", body=""),
        _message(
            "Ereply0000000001",
            "Udeleted",
            deleted=True,
            thread_root_event_id="Eroot00000000001",
            second=1,
        ),
        _message(
            "Ereply0000000002",
            "Uunknown",
            thread_root_event_id="Eroot00000000001",
            second=2,
        ),
    ]
    users = _FakeUsersClient([user_service_pb2.BatchGetUsersResponse()])

    path = asyncio.run(
        write_transcript(
            messages,
            {"Uroot": users_pb2.User(id="Uroot", login=" ")},
            users,
            api_key="fake-api-key",
            output_dir=tmp_path,
        )
    )

    content = path.read_text(encoding="utf-8")
    assert content.count("## Unknown author") == 3
    assert content.count("_[message deleted]_") == 1
    assert content.count("_[message body unavailable]_") == 1
    assert users.requests[0][0].user_ids == ["Udeleted", "Uunknown"]


def test_renders_provided_local_attachment_lines_after_their_message(
    tmp_path: Path,
) -> None:
    messages = [
        _message("Eroot00000000001", "Uauthor", body="with files"),
        _message(
            "Ereply0000000001",
            "Uauthor",
            body="second",
            thread_root_event_id="Eroot00000000001",
            second=1,
        ),
    ]
    attachment_lines = {
        "Eroot00000000001": [
            "![diagram](diagram.png)",
            "[notes.pdf](notes.pdf)",
        ]
    }
    users = _FakeUsersClient([])

    path = asyncio.run(
        write_transcript(
            messages,
            {"Uauthor": users_pb2.User(id="Uauthor", display_name="Author")},
            users,
            api_key="fake-api-key",
            output_dir=tmp_path,
            attachment_lines=attachment_lines,
        )
    )

    content = path.read_text(encoding="utf-8")
    assert content.index("with files") < content.index("![diagram](diagram.png)")
    assert content.index("![diagram](diagram.png)") < content.index(
        "[notes.pdf](notes.pdf)"
    )
    assert content.index("[notes.pdf](notes.pdf)") < content.index("\nsecond\n")


class _FailingUsersClient:
    async def batch_get_users(self, request: Any, *, headers=None) -> Any:
        raise RuntimeError("private response fake-api-key")


def test_hides_user_lookup_error_details(tmp_path: Path) -> None:
    with pytest.raises(TranscriptError) as error:
        asyncio.run(
            write_transcript(
                [_message("Eroot00000000001", "Umissing", body="text")],
                {},
                _FailingUsersClient(),
                api_key="fake-api-key",
                output_dir=tmp_path,
            )
        )

    assert str(error.value) == "Chatto user lookup failed"
    assert "fake-api-key" not in str(error.value)
    assert "private response" not in str(error.value)

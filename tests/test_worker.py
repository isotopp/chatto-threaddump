from __future__ import annotations

import asyncio
import logging
from datetime import UTC, datetime
from pathlib import Path
from types import ModuleType, SimpleNamespace
from typing import Any
from zipfile import ZipFile

import pytest
from chattolib import ChattoClient
from chattolib._pb.chatto.api.v1 import message_types_pb2 as _message_types_pb2
from chattolib._pb.chatto.api.v1 import room_timeline_pb2 as _room_timeline_pb2
from chattolib._pb.chatto.api.v1 import user_service_pb2 as _user_service_pb2
from chattolib._pb.chatto.api.v1 import users_pb2 as _users_pb2
from google.protobuf import timestamp_pb2 as _timestamp_pb2

from chatto_threaddump.contracts import DumpRequest
from chatto_threaddump.inbox import RequestInbox
from chatto_threaddump.worker import process_dump_request

ROOM_ID = "R12345678901234"
AUTHOR_ID = "Uauthor000000001"


class _ProtoTypes:
    def __init__(self, module: ModuleType) -> None:
        self.module = module

    def __getattr__(self, name: str) -> Any:
        return getattr(self.module, name)


message_types_pb2 = _ProtoTypes(_message_types_pb2)
room_timeline_pb2 = _ProtoTypes(_room_timeline_pb2)
users_pb2 = _ProtoTypes(_users_pb2)
user_service_pb2 = _ProtoTypes(_user_service_pb2)
timestamp_pb2 = _ProtoTypes(_timestamp_pb2)


@pytest.mark.parametrize("hydrate_author", [False, True])
def test_worker_uses_sdk_protobuf_services(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, hydrate_author: bool
) -> None:
    calls: list[str] = []
    replies: list[str] = []

    async def get_thread_events(request: Any, *, headers: dict[str, str]) -> Any:
        assert headers == {"Authorization": "Bearer fake-bot-key"}
        calls.append("thread")
        response = _thread_response(request.thread_root_event_id)
        if hydrate_author:
            response.page.includes.users.clear()
        return response

    async def batch_get_users(request: Any, *, headers: dict[str, str]) -> Any:
        assert headers == {"Authorization": "Bearer fake-bot-key"}
        assert list(request.user_ids) == [AUTHOR_ID]
        calls.append("authors")
        return user_service_pb2.BatchGetUsersResponse(
            users=[{"user": users_pb2.User(id=AUTHOR_ID, display_name="Alice")}]
        )

    async def upload_attachment(
        room_id: str, file_path: Path, **kwargs: object
    ) -> object:
        assert room_id == ROOM_ID
        assert kwargs["content_type"] == "application/zip"
        with ZipFile(file_path) as archive:
            assert b"Alice" in archive.read(archive.namelist()[0])
        calls.append("upload")
        return SimpleNamespace(id="fake-archive-asset")

    async def post_message(room_id: str, body: str, **kwargs: object) -> object:
        assert room_id == ROOM_ID
        if kwargs.get("in_reply_to"):
            replies.append(body)
        else:
            assert kwargs["attachment_asset_ids"] == ["fake-archive-asset"]
            calls.append("publish")
        return object()

    services: Any = SimpleNamespace(
        threads=SimpleNamespace(get_thread_events=get_thread_events),
        user=SimpleNamespace(batch_get_users=batch_get_users),
    )
    client = ChattoClient(token="fake-bot-key", service_clients=services)
    monkeypatch.setattr(client, "upload_attachment", upload_attachment)
    monkeypatch.setattr(client, "post_message", post_message)
    asyncio.run(
        process_dump_request(
            client,
            DumpRequest(ROOM_ID, "Ecommand00000001", "Eroot00000000001"),
            api_key="fake-bot-key",
            temp_root=tmp_path / "tmpdir",
        )
    )

    expected = (
        ["thread", "authors", "upload", "publish"]
        if hydrate_author
        else ["thread", "upload", "publish"]
    )
    assert calls == expected
    assert replies == []
    assert list((tmp_path / "tmpdir").iterdir()) == []


def _thread_response(root_id: str) -> Any:
    timestamp = timestamp_pb2.Timestamp()
    timestamp.FromDatetime(datetime(2026, 10, 14, 10, 0, tzinfo=UTC))
    message = message_types_pb2.Message(
        id=root_id,
        room_id=ROOM_ID,
        actor_id=AUTHOR_ID,
        created_at=timestamp,
        body=f"Starter for {root_id}",
    )
    event = room_timeline_pb2.RoomTimelineEvent(
        id=root_id,
        actor_id=AUTHOR_ID,
        created_at=timestamp,
        message_posted=room_timeline_pb2.RoomMessagePosted(message=message),
    )
    return room_timeline_pb2.GetThreadEventsResponse(
        page=room_timeline_pb2.RoomTimelinePage(
            events=[event],
            includes=room_timeline_pb2.RoomTimelineIncludes(
                users={AUTHOR_ID: users_pb2.User(id=AUTHOR_ID, display_name="Alice")}
            ),
        )
    )


class _FakeChattoClient:
    def __init__(self) -> None:
        self.services = SimpleNamespace(threads=self, user=self)
        self.top_level_posts = 0
        self.uploaded_archives: list[Path] = []
        self.replies: list[tuple[str, str]] = []
        self.thread_requests: list[str] = []

    async def get_thread_events(self, request: Any, *, headers=None) -> Any:
        assert headers == {"Authorization": "Bearer fake-bot-key"}
        self.thread_requests.append(request.thread_root_event_id)
        return _thread_response(request.thread_root_event_id)

    async def batch_get_users(self, _request: Any, *, headers=None) -> Any:
        raise AssertionError("included author should not need hydration")

    async def upload_attachment(
        self,
        _room_id: str,
        file_path: str | Path,
        *,
        content_type: str = "",
        filename: str | None = None,
    ) -> object:
        path = Path(file_path)
        assert path.is_file()
        assert content_type == "application/zip"
        assert filename == path.name
        with ZipFile(path) as archive:
            names = archive.namelist()
            assert len(names) == 1
            assert names[0].endswith("/_index.md")
            assert b"Starter for" in archive.read(names[0])
        self.uploaded_archives.append(path)
        return SimpleNamespace(id=f"asset-{len(self.uploaded_archives)}")

    async def post_message(
        self,
        _room_id: str,
        body: str = "",
        *,
        in_reply_to: str = "",
        **_kwargs: object,
    ) -> object:
        if in_reply_to:
            self.replies.append((in_reply_to, body))
            raise RuntimeError("private reply body fake-bot-key signed-url")
        self.top_level_posts += 1
        if self.top_level_posts == 1:
            raise RuntimeError("private post body fake-bot-key signed-url")
        return object()


def test_inbox_worker_isolates_post_failure_and_cleans_each_archive(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    client = _FakeChattoClient()
    inbox = RequestInbox(capacity=2)
    requests = [
        DumpRequest(ROOM_ID, "Ecommand00000001", "Eroot00000000001"),
        DumpRequest(ROOM_ID, "Ecommand00000002", "Eroot00000000002"),
    ]
    finished = asyncio.Event()
    completed: list[str] = []

    async def scenario() -> None:
        async def process(request: DumpRequest) -> None:
            await process_dump_request(
                client,
                request,
                api_key="fake-bot-key",
                temp_root=tmp_path / "tmpdir",
            )
            completed.append(request.command_event_id)
            if len(completed) == len(requests):
                finished.set()

        consumer = asyncio.create_task(inbox.run(process))
        try:
            assert all(inbox.admit(request) for request in requests)
            await finished.wait()
        finally:
            consumer.cancel()
            with pytest.raises(asyncio.CancelledError):
                await consumer

    with caplog.at_level(logging.WARNING, logger="chatto_threaddump.worker"):
        asyncio.run(scenario())

    assert completed == [request.command_event_id for request in requests]
    assert client.thread_requests == [
        request.thread_root_event_id for request in requests
    ]
    assert client.top_level_posts == 2
    assert len(client.uploaded_archives) == 2
    assert all(not archive.exists() for archive in client.uploaded_archives)
    assert list((tmp_path / "tmpdir").iterdir()) == []
    assert client.replies == [
        ("Ecommand00000001", "The thread dump could not be completed.")
    ]
    diagnostics = "\n".join(record.getMessage() for record in caplog.records)
    assert "Could not report failed thread dump request." in diagnostics
    for private_value in (
        "fake-bot-key",
        "signed-url",
        "private post body",
        "private reply body",
    ):
        assert private_value not in diagnostics
    assert client.replies[0][1] not in diagnostics


def test_archive_read_failure_gets_one_sanitized_reply_and_cleans_parent(
    tmp_path: Path,
) -> None:
    class ReadFailureClient:
        replies: list[tuple[str, str]]

        def __init__(self) -> None:
            self.replies = []
            self.services = SimpleNamespace(threads=self, user=self)

        async def get_thread_events(self, _request: Any, *, headers=None) -> Any:
            raise RuntimeError("private body fake-bot-key signed-url")

        async def post_message(
            self,
            _room_id: str,
            body: str = "",
            *,
            in_reply_to: str = "",
            **_kwargs: object,
        ) -> object:
            self.replies.append((in_reply_to, body))
            return object()

    client = ReadFailureClient()
    request = DumpRequest(ROOM_ID, "Ecommand00000003", "Eroot00000000003")

    asyncio.run(
        process_dump_request(
            client,
            request,
            api_key="fake-bot-key",
            temp_root=tmp_path / "tmpdir",
        )
    )

    assert client.replies == [
        ("Ecommand00000003", "The thread dump could not be completed.")
    ]
    assert list((tmp_path / "tmpdir").iterdir()) == []


def test_upload_failure_replies_once_and_cleans_archive_after_upload(
    tmp_path: Path,
) -> None:
    class UploadFailureClient(_FakeChattoClient):
        async def upload_attachment(
            self,
            _room_id: str,
            file_path: str | Path,
            *,
            content_type: str = "",
            filename: str | None = None,
        ) -> object:
            await super().upload_attachment(
                _room_id,
                file_path,
                content_type=content_type,
                filename=filename,
            )
            raise RuntimeError("private upload body fake-bot-key signed-url")

        async def post_message(
            self,
            _room_id: str,
            body: str = "",
            *,
            in_reply_to: str = "",
            **_kwargs: object,
        ) -> object:
            if not in_reply_to:
                raise AssertionError("upload failure must prevent publication")
            return await super().post_message(
                _room_id, body, in_reply_to=in_reply_to, **_kwargs
            )

    client = UploadFailureClient()
    request = DumpRequest(ROOM_ID, "Ecommand00000004", "Eroot00000000004")

    asyncio.run(
        process_dump_request(
            client,
            request,
            api_key="fake-bot-key",
            temp_root=tmp_path / "tmpdir",
        )
    )

    assert len(client.uploaded_archives) == 1
    assert not client.uploaded_archives[0].exists()
    assert client.replies == [
        ("Ecommand00000004", "The thread dump could not be completed.")
    ]
    assert list((tmp_path / "tmpdir").iterdir()) == []
    assert "fake-bot-key" not in client.replies[0][1]

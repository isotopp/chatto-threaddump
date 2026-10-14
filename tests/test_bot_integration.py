from __future__ import annotations

import asyncio
import os
import subprocess
import sysconfig
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from pathlib import Path
from types import ModuleType, SimpleNamespace
from typing import Any
from zipfile import ZipFile

import httpx
import pytest
from chattolib._pb.chatto.api.v1 import (
    member_directory_pb2 as _member_directory_pb2,
)
from chattolib._pb.chatto.api.v1 import (
    message_types_pb2 as _message_types_pb2,
)
from chattolib._pb.chatto.api.v1 import (
    room_timeline_pb2 as _room_timeline_pb2,
)
from chattolib._pb.chatto.api.v1 import (
    user_service_pb2 as _user_service_pb2,
)
from chattolib._pb.chatto.api.v1 import (
    users_pb2 as _users_pb2,
)
from chattolib._pb.chatto.realtime.v1 import realtime_pb2 as _realtime_pb2
from google.protobuf import timestamp_pb2 as _timestamp_pb2

from chatto_threaddump import chatto_events
from chatto_threaddump.config import Settings
from chatto_threaddump.contracts import DumpRequest
from chatto_threaddump.runtime import run_bot
from chatto_threaddump.worker import process_dump_request

BOT_ID = "Ubot00000000001"
ACTOR_ID = "Urequester00001"
AUTHOR_ID = "Uauthor0000001"
ROOM_ID = "Rroom0000000001"
ROOT_ID = "Eroot0000000001"
REPLY_ID = "Ereply000000001"
COMMAND_ID = "Ecommand0000001"
ROLE_NAME = "thread-exporter"
FAKE_KEY = "fake-bot-credential"
ASSET_BYTES = b"fake attachment bytes"
SIGNED_URL = "https://assets.example.test/diagram?signature=fake-signed-value"


class _ProtoTypes:
    def __init__(self, module: ModuleType) -> None:
        self.module = module

    def __getattr__(self, name: str) -> Any:
        return getattr(self.module, name)


member_directory_pb2 = _ProtoTypes(_member_directory_pb2)
message_types_pb2 = _ProtoTypes(_message_types_pb2)
room_timeline_pb2 = _ProtoTypes(_room_timeline_pb2)
user_service_pb2 = _ProtoTypes(_user_service_pb2)
users_pb2 = _ProtoTypes(_users_pb2)
realtime_pb2 = _ProtoTypes(_realtime_pb2)
timestamp_pb2 = _ProtoTypes(_timestamp_pb2)


def _frame() -> bytes:
    frame = realtime_pb2.RealtimeServerFrame()
    frame.event.id = COMMAND_ID
    frame.event.actor_id = ACTOR_ID
    message = frame.event.message_posted
    message.room_id = ROOM_ID
    message.thread_root_event_id = ROOT_ID
    message.body_plaintext = "@thread-dumper dump this thread"
    message.mentions.add().direct.user_id = BOT_ID
    return frame.SerializeToString()


def _thread_response() -> Any:
    root_time = timestamp_pb2.Timestamp()
    root_time.FromDatetime(datetime(2026, 10, 14, 10, 0, tzinfo=UTC))
    reply_time = timestamp_pb2.Timestamp()
    reply_time.FromDatetime(datetime(2026, 10, 14, 10, 5, tzinfo=UTC))
    root = message_types_pb2.Message(
        id=ROOT_ID,
        room_id=ROOM_ID,
        actor_id=AUTHOR_ID,
        created_at=root_time,
        body="Starter headline\nStarter details",
        attachments=[
            message_types_pb2.MessageAttachment(
                filename="diagram #1.png",
                content_type="image/png",
                description="Architecture diagram",
                asset_url=message_types_pb2.MessageAssetUrl(url=SIGNED_URL),
            )
        ],
    )
    reply = message_types_pb2.Message(
        id=REPLY_ID,
        room_id=ROOM_ID,
        actor_id=AUTHOR_ID,
        created_at=reply_time,
        body="A reply in the original thread.",
        thread_root_event_id=ROOT_ID,
    )
    return room_timeline_pb2.GetThreadEventsResponse(
        page=room_timeline_pb2.RoomTimelinePage(
            events=[
                room_timeline_pb2.RoomTimelineEvent(
                    id=ROOT_ID,
                    actor_id=AUTHOR_ID,
                    created_at=root_time,
                    message_posted=room_timeline_pb2.RoomMessagePosted(message=root),
                ),
                room_timeline_pb2.RoomTimelineEvent(
                    id=REPLY_ID,
                    actor_id=AUTHOR_ID,
                    created_at=reply_time,
                    message_posted=room_timeline_pb2.RoomMessagePosted(message=reply),
                ),
            ],
            includes=room_timeline_pb2.RoomTimelineIncludes(
                users={AUTHOR_ID: users_pb2.User(id=AUTHOR_ID, display_name="Alice")}
            ),
        )
    )


class _FakeClient:
    def __init__(self) -> None:
        self.base_url = "https://chat.example.test"
        self.token = FAKE_KEY
        self.session_cookie = None
        self.thread_calls: list[Any] = []
        self.role_calls: list[Any] = []
        self.replies: list[tuple[str, str]] = []
        self.top_level_posts: list[tuple[str, list[str], dict[str, object]]] = []
        self.uploads: list[tuple[str, str, str, bytes]] = []
        self.services = SimpleNamespace(user=SimpleNamespace(get_user=self.get_user))

    async def me(self) -> SimpleNamespace:
        return SimpleNamespace(id=BOT_ID)

    async def get_user(
        self,
        request: Any,
        *,
        headers: dict[str, str],
    ) -> Any:
        self.role_calls.append(request)
        assert headers == {"Authorization": f"Bearer {FAKE_KEY}"}
        return user_service_pb2.GetUserResponse(
            user=member_directory_pb2.DirectoryMember(
                user=users_pb2.User(id=ACTOR_ID), roles=[ROLE_NAME]
            )
        )

    async def get_thread_events(
        self,
        request: Any,
        *,
        headers: dict[str, str],
    ) -> Any:
        self.thread_calls.append(request)
        assert headers == {"Authorization": f"Bearer {FAKE_KEY}"}
        return _thread_response()

    async def batch_get_users(
        self,
        _request: Any,
        *,
        headers: dict[str, str],
    ) -> Any:
        raise AssertionError(f"unexpected author hydration call: {headers}")

    async def upload_attachment(
        self,
        room_id: str,
        file_path: str | Path,
        *,
        content_type: str = "",
        filename: str | None = None,
    ) -> SimpleNamespace:
        path = Path(file_path)
        assert path.is_file()
        payload = path.read_bytes()
        self.uploads.append((room_id, content_type, filename or "", payload))
        return SimpleNamespace(id="Aarchive0000001")

    async def post_message(
        self,
        room_id: str,
        body: str = "",
        *,
        attachment_asset_ids: list[str] | None = None,
        thread_root_event_id: str = "",
        in_reply_to: str = "",
        also_send_to_channel: bool = False,
        link_preview_token: str = "",
    ) -> object:
        assert room_id == ROOM_ID
        assert not also_send_to_channel
        assert not link_preview_token
        if in_reply_to:
            self.replies.append((in_reply_to, body))
            return object()
        self.top_level_posts.append(
            (
                body,
                attachment_asset_ids or [],
                {
                    "thread_root_event_id": thread_root_event_id,
                    "in_reply_to": in_reply_to,
                },
            )
        )
        return object()


async def _run_fake_bot(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    *,
    asset_status: int,
) -> tuple[_FakeClient, dict[str, Any], list[httpx.Request]]:
    finished = asyncio.Event()
    client = _FakeClient()
    capture: dict[str, Any] = {"sent": []}
    requests: list[httpx.Request] = []
    raw_frame = _frame()

    class FakeWebSocket:
        async def send(self, data: bytes) -> None:
            capture["sent"].append(data)

        async def __aiter__(self):
            yield raw_frame
            await asyncio.Future()

    class FakeRealtimeConnect:
        def __init__(self, uri: str, **kwargs: object) -> None:
            capture["uri"] = uri
            capture["connect_options"] = kwargs
            self.websocket = FakeWebSocket()

        async def __aenter__(self) -> FakeWebSocket:
            return self.websocket

        async def __aexit__(self, *_exc: object) -> None:
            return None

    monkeypatch.setattr(chatto_events, "RealtimeConnect", FakeRealtimeConnect)

    def respond(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        if asset_status == 200:
            return httpx.Response(200, content=ASSET_BYTES, request=request)
        return httpx.Response(
            asset_status, content=b"private fake asset response", request=request
        )

    transport = httpx.MockTransport(respond)
    settings = Settings(
        server_url=client.base_url,
        api_key=FAKE_KEY,
        role_name=ROLE_NAME,
        queue_length=1,
        temp_dir=tmp_path / "tmpdir",
    )

    @asynccontextmanager
    async def client_factory(**kwargs: object):
        assert kwargs == {"token": FAKE_KEY, "base_url": client.base_url}
        yield client

    async def event_stream(fake_client: _FakeClient, *, bot_user_id: str):
        async for mention in chatto_events.live_mentions(
            fake_client, bot_user_id=bot_user_id
        ):
            yield mention

    async def process_request(
        fake_client: _FakeClient,
        request: DumpRequest,
        *,
        api_key: str,
        temp_root: Path,
    ) -> None:
        try:
            await process_dump_request(
                fake_client,
                request,
                api_key=api_key,
                temp_root=temp_root,
                asset_transport=transport,
            )
        finally:
            finished.set()

    runtime = asyncio.create_task(
        run_bot(
            settings,
            client_factory=client_factory,
            event_stream_factory=event_stream,
            request_processor=process_request,
        )
    )
    try:
        await asyncio.wait_for(finished.wait(), timeout=3)
    finally:
        runtime.cancel()
        with pytest.raises(asyncio.CancelledError):
            await runtime

    return client, capture, requests


@pytest.mark.parametrize(
    ("asset_status", "expected_upload_count", "expected_post_count"),
    [(200, 1, 1), (404, 0, 0)],
)
def test_live_mention_role_check_and_worker_publish_only_complete_archive(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
    asset_status: int,
    expected_upload_count: int,
    expected_post_count: int,
) -> None:
    async def scenario() -> tuple[_FakeClient, dict[str, Any], list[httpx.Request]]:
        return await _run_fake_bot(tmp_path, monkeypatch, asset_status=asset_status)

    client, capture, asset_requests = asyncio.run(scenario())

    subscription = realtime_pb2.RealtimeSubscribe()
    assert len(capture["sent"]) == 1
    subscription.ParseFromString(capture["sent"][0])
    assert subscription.initial_state == realtime_pb2.REALTIME_INITIAL_STATE_LIVE_ONLY
    assert not subscription.resume_cursor
    assert subscription.bearer_token == FAKE_KEY
    assert capture["uri"] == "wss://chat.example.test/api/realtime"
    assert capture["connect_options"] == {
        "additional_headers": None,
        "open_timeout": 5,
    }

    assert [request.user_id for request in client.role_calls] == [ACTOR_ID]
    assert len(client.thread_calls) == 1
    assert client.thread_calls[0].room_id == ROOM_ID
    assert client.thread_calls[0].thread_root_event_id == ROOT_ID
    assert len(client.replies) >= 1
    assert client.replies[0] == (COMMAND_ID, "The dump was accepted.")
    assert len(client.uploads) == expected_upload_count
    assert len(client.top_level_posts) == expected_post_count
    assert list((tmp_path / "tmpdir").iterdir()) == []

    assert len(asset_requests) == 1
    assert str(asset_requests[0].url) == SIGNED_URL
    assert "Authorization" not in asset_requests[0].headers

    if asset_status == 404:
        assert client.replies == [
            (COMMAND_ID, "The dump was accepted."),
            (COMMAND_ID, "The thread dump could not be completed."),
        ]
        assert client.top_level_posts == []
        assert FAKE_KEY not in caplog.text
        assert "fake-signed-value" not in caplog.text
        assert "private fake asset response" not in caplog.text
        return

    assert client.replies == [(COMMAND_ID, "The dump was accepted.")]
    headline, attachment_ids, placement = client.top_level_posts[0]
    assert headline == 'Dump of Thread "Starter headline"'
    assert attachment_ids == ["Aarchive0000001"]
    assert placement == {"thread_root_event_id": "", "in_reply_to": ""}
    uploaded_room, content_type, filename, archive_bytes = client.uploads[0]
    assert uploaded_room == ROOM_ID
    assert content_type == "application/zip"
    assert filename.startswith("thread_") and filename.endswith(".zip")
    assert archive_bytes.startswith(b"PK")

    extraction = tmp_path / "extracted"
    extraction.mkdir()
    archive_path = tmp_path / "published.zip"
    archive_path.write_bytes(archive_bytes)
    with ZipFile(archive_path) as archive:
        names = archive.namelist()
        assert len(names) == 2
        index_name = next(name for name in names if name.endswith("/_index.md"))
        asset_name = next(name for name in names if name.endswith("diagram #1.png"))
        markdown = archive.read(index_name).decode("utf-8")
        assert "Starter headline\nStarter details" in markdown
        assert "A reply in the original thread." in markdown
        assert "![Architecture diagram](diagram%20%231.png)" in markdown
        assert archive.read(asset_name) == ASSET_BYTES
        archive.extractall(extraction)

    extracted_dir = extraction / index_name.split("/", maxsplit=1)[0]
    assert (extracted_dir / "_index.md").read_text(encoding="utf-8") == markdown
    assert (extracted_dir / "diagram #1.png").read_bytes() == ASSET_BYTES


def test_installed_entrypoint_help_is_local_and_does_not_need_configuration(
    tmp_path: Path,
) -> None:
    executable = Path(sysconfig.get_path("scripts")) / "chatto-threaddump"
    assert executable.is_file()
    environment = os.environ.copy()
    for key in (
        "CHATTO_THREADDUMP_SERVER_URL",
        "CHATTO_THREADDUMP_API_KEY",
        "CHATTO_THREADDUMP_ROLE",
        "CHATTO_THREADDUMP_QUEUE_LENGTH",
    ):
        environment.pop(key, None)

    result = subprocess.run(
        [str(executable), "--help"],
        cwd=tmp_path,
        env=environment,
        capture_output=True,
        check=False,
        text=True,
        timeout=3,
    )

    assert result.returncode == 0
    assert "long-running Chatto thread dump bot" in result.stdout
    assert "CHATTO_THREADDUMP_ROLE" in result.stdout
    assert "CHATTO_THREADDUMP_QUEUE_LENGTH" in result.stdout
    assert result.stderr == ""

import asyncio
from datetime import UTC, datetime
from pathlib import Path
from types import ModuleType
from typing import Any
from zipfile import ZipFile

import httpx
import pytest
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
from google.protobuf import timestamp_pb2 as _timestamp_pb2

import chatto_threaddump.archive as archive_module
from chatto_threaddump.archive import ArchiveError, create_thread_archive
from chatto_threaddump.assets import AssetCollectionError
from chatto_threaddump.thread_reader import ThreadReadError


class _GeneratedTypes:
    def __init__(self, module: ModuleType) -> None:
        self.module = module

    def __getattr__(self, name: str) -> Any:
        return getattr(self.module, name)


message_types_pb2 = _GeneratedTypes(_message_types_pb2)
room_timeline_pb2 = _GeneratedTypes(_room_timeline_pb2)
user_service_pb2 = _GeneratedTypes(_user_service_pb2)
users_pb2 = _GeneratedTypes(_users_pb2)
timestamp_pb2 = _GeneratedTypes(_timestamp_pb2)

ROOM_ID = "R12345678901234"
ROOT_ID = "Eroot00000000001"
ROOT_TIME = timestamp_pb2.Timestamp()
ROOT_TIME.FromDatetime(datetime(2026, 9, 19, 10, 0, tzinfo=UTC))


class _ChattoClient:
    def __init__(
        self,
        *,
        body: str | None = "DuMp this thread\nsecond line",
        deleted: bool = False,
    ) -> None:
        root = message_types_pb2.Message(
            id=ROOT_ID,
            room_id=ROOM_ID,
            actor_id="Uauthor0000000001",
            created_at=ROOT_TIME,
            attachments=[
                message_types_pb2.MessageAttachment(
                    filename="notes #1.txt",
                    content_type="text/plain",
                    asset_url=message_types_pb2.MessageAssetUrl(
                        url="https://assets.example.test/notes?signature=fake"
                    ),
                )
            ],
        )
        if body is not None:
            root.body = body
        if deleted:
            root.deleted_at.CopyFrom(ROOT_TIME)
        event = room_timeline_pb2.RoomTimelineEvent(
            id=ROOT_ID,
            actor_id=root.actor_id,
            created_at=ROOT_TIME,
            message_posted=room_timeline_pb2.RoomMessagePosted(message=root),
        )
        self.response = room_timeline_pb2.GetThreadEventsResponse(
            page=room_timeline_pb2.RoomTimelinePage(
                events=[event],
                includes=room_timeline_pb2.RoomTimelineIncludes(
                    users={
                        "Uauthor0000000001": users_pb2.User(
                            id="Uauthor0000000001", display_name="Alice"
                        )
                    }
                ),
            )
        )

    async def get_thread_events(self, request, *, headers=None):
        assert request.room_id == ROOM_ID
        assert request.thread_root_event_id == ROOT_ID
        assert headers == {"Authorization": "Bearer fake-key"}
        return self.response

    async def batch_get_users(self, request, *, headers=None):
        return user_service_pb2.BatchGetUsersResponse()


def test_archive_context_yields_complete_zip_and_cleans_owned_parent(
    tmp_path: Path,
) -> None:
    (tmp_path / "unrelated.txt").write_text("keep", encoding="utf-8")
    temporary_root = tmp_path / "temporary"
    extraction_root = tmp_path / "extracted"
    requests: list[httpx.Request] = []

    def respond(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(200, content=b"asset contents", request=request)

    client = _ChattoClient(body="DuMp this thread\r\nsecond line\x1b")
    expected_directory = f"thread_{ROOT_TIME.seconds}"

    async def exercise() -> None:
        async with create_thread_archive(
            client,
            ROOM_ID,
            ROOT_ID,
            api_key="fake-key",
            temp_root=temporary_root,
            asset_transport=httpx.MockTransport(respond),
        ) as archive:
            assert archive.filename == f"{expected_directory}.zip"
            assert archive.zip_path.name == archive.filename
            assert archive.zip_path.is_file()
            assert archive.starter_first_line == "DuMp this thread"
            with ZipFile(archive.zip_path) as bundle:
                assert set(bundle.namelist()) == {
                    f"{expected_directory}/_index.md",
                    f"{expected_directory}/notes #1.txt",
                }
                transcript = bundle.read(f"{expected_directory}/_index.md").decode(
                    "utf-8"
                )
                assert "DuMp this thread\nsecond line" in transcript
                assert "[notes \\#1.txt](notes%20%231.txt)" in transcript
                assert bundle.read(f"{expected_directory}/notes #1.txt") == (
                    b"asset contents"
                )
                bundle.extractall(extraction_root)
            extracted_bundle = extraction_root / expected_directory
            assert (extracted_bundle / "_index.md").read_text(
                encoding="utf-8"
            ) == transcript
            assert (extracted_bundle / "notes #1.txt").read_bytes() == (
                b"asset contents"
            )

    asyncio.run(exercise())

    assert len(requests) == 1
    assert "Authorization" not in requests[0].headers
    assert list(temporary_root.iterdir()) == []
    assert (tmp_path / "unrelated.txt").read_text(encoding="utf-8") == "keep"


def test_equal_starter_timestamps_use_distinct_parents(tmp_path: Path) -> None:
    client = _ChattoClient()

    def respond(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, content=b"asset", request=request)

    async def exercise() -> None:
        async with create_thread_archive(
            client,
            ROOM_ID,
            ROOT_ID,
            api_key="fake-key",
            temp_root=tmp_path,
            asset_transport=httpx.MockTransport(respond),
        ) as first:
            async with create_thread_archive(
                client,
                ROOM_ID,
                ROOT_ID,
                api_key="fake-key",
                temp_root=tmp_path,
                asset_transport=httpx.MockTransport(respond),
            ) as second:
                assert first.filename == second.filename
                assert first.zip_path != second.zip_path
                assert first.zip_path.is_file()
                assert second.zip_path.is_file()

            assert first.zip_path.is_file()
            assert not second.zip_path.exists()

        assert list(tmp_path.iterdir()) == []

    asyncio.run(exercise())


@pytest.mark.parametrize(
    ("body", "deleted", "expected"),
    [
        ("", False, ""),
        (None, True, "_[message deleted]_"),
        (None, False, "_[message body unavailable]_"),
    ],
)
def test_starter_first_line_preserves_empty_and_unavailable_states(
    body: str | None, deleted: bool, expected: str, tmp_path: Path
) -> None:
    def respond(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, content=b"asset", request=request)

    async def exercise() -> None:
        async with create_thread_archive(
            _ChattoClient(body=body, deleted=deleted),
            ROOM_ID,
            ROOT_ID,
            api_key="fake-key",
            temp_root=tmp_path,
            asset_transport=httpx.MockTransport(respond),
        ) as archive:
            assert archive.starter_first_line == expected

    asyncio.run(exercise())
    assert list(tmp_path.iterdir()) == []


def test_reader_failure_removes_owned_parent(tmp_path: Path) -> None:
    class BrokenReader(_ChattoClient):
        async def get_thread_events(self, request, *, headers=None):
            raise RuntimeError("private response body")

    async def exercise() -> None:
        with pytest.raises(ThreadReadError) as caught:
            async with create_thread_archive(
                BrokenReader(),
                ROOM_ID,
                ROOT_ID,
                api_key="fake-key",
                temp_root=tmp_path,
            ):
                pytest.fail("a failed thread read must not yield an archive")
        assert "private response body" not in str(caught.value)

    asyncio.run(exercise())
    assert list(tmp_path.iterdir()) == []


def test_asset_failure_removes_partial_bundle_and_archive(tmp_path: Path) -> None:
    def respond(request: httpx.Request) -> httpx.Response:
        return httpx.Response(404, content=b"private response body", request=request)

    async def exercise() -> None:
        with pytest.raises(AssetCollectionError) as caught:
            async with create_thread_archive(
                _ChattoClient(),
                ROOM_ID,
                ROOT_ID,
                api_key="fake-key",
                temp_root=tmp_path,
                asset_transport=httpx.MockTransport(respond),
            ):
                pytest.fail("a failed asset download must not yield an archive")
        assert "private response body" not in str(caught.value)

    asyncio.run(exercise())
    assert list(tmp_path.iterdir()) == []


def test_zip_packaging_failure_removes_partial_archive(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    def fail_zip(*args, **kwargs):
        raise OSError("private filesystem path")

    monkeypatch.setattr(archive_module, "ZipFile", fail_zip)

    def respond(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, content=b"asset", request=request)

    async def exercise() -> None:
        with pytest.raises(
            ArchiveError, match="cannot package thread archive"
        ) as caught:
            async with create_thread_archive(
                _ChattoClient(),
                ROOM_ID,
                ROOT_ID,
                api_key="fake-key",
                temp_root=tmp_path,
                asset_transport=httpx.MockTransport(respond),
            ):
                pytest.fail("a failed ZIP must not be yielded")
        assert "private filesystem path" not in str(caught.value)

    asyncio.run(exercise())
    assert list(tmp_path.iterdir()) == []


def test_cancellation_during_asset_download_removes_owned_parent(
    tmp_path: Path,
) -> None:
    async def exercise() -> None:
        entered_transport = asyncio.Event()
        hold_transport = asyncio.Event()

        async def respond(request: httpx.Request) -> httpx.Response:
            entered_transport.set()
            await hold_transport.wait()
            return httpx.Response(200, content=b"asset", request=request)

        async def build_archive() -> None:
            async with create_thread_archive(
                _ChattoClient(),
                ROOM_ID,
                ROOT_ID,
                api_key="fake-key",
                temp_root=tmp_path,
                asset_transport=httpx.MockTransport(respond),
            ):
                pytest.fail("canceled work must not yield an archive")

        task = asyncio.create_task(build_archive())
        await entered_transport.wait()
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task

    asyncio.run(exercise())
    assert list(tmp_path.iterdir()) == []


def test_zip_work_yields_to_event_loop_before_context_yields(tmp_path: Path) -> None:
    async def exercise() -> None:
        loop = asyncio.get_running_loop()
        heartbeat = asyncio.Event()
        observing = True

        def observe_zip() -> None:
            if any(tmp_path.glob("*/thread_*.zip")):
                heartbeat.set()
            elif observing:
                loop.call_soon(observe_zip)

        loop.call_soon(observe_zip)

        def respond(request: httpx.Request) -> httpx.Response:
            return httpx.Response(200, content=b"asset", request=request)

        try:
            async with create_thread_archive(
                _ChattoClient(body="large command " + "x" * (8 * 1024 * 1024)),
                ROOM_ID,
                ROOT_ID,
                api_key="fake-key",
                temp_root=tmp_path,
                asset_transport=httpx.MockTransport(respond),
            ):
                assert heartbeat.is_set()
        finally:
            observing = False

    asyncio.run(exercise())
    assert list(tmp_path.iterdir()) == []


def test_cancellation_during_zip_work_removes_partial_archive(tmp_path: Path) -> None:
    async def exercise() -> None:
        loop = asyncio.get_running_loop()
        zip_started = asyncio.Event()

        def cancel_when_zip_exists() -> None:
            if any(tmp_path.glob("*/thread_*.zip")):
                zip_started.set()
                task.cancel()
            else:
                loop.call_soon(cancel_when_zip_exists)

        def respond(request: httpx.Request) -> httpx.Response:
            return httpx.Response(200, content=b"asset", request=request)

        async def build_archive() -> None:
            async with create_thread_archive(
                _ChattoClient(body="large command " + "x" * (8 * 1024 * 1024)),
                ROOM_ID,
                ROOT_ID,
                api_key="fake-key",
                temp_root=tmp_path,
                asset_transport=httpx.MockTransport(respond),
            ):
                pytest.fail("canceled ZIP work must not yield an archive")

        task = asyncio.create_task(build_archive())
        loop.call_soon(cancel_when_zip_exists)
        with pytest.raises(asyncio.CancelledError):
            await task
        assert zip_started.is_set()

    asyncio.run(exercise())
    assert list(tmp_path.iterdir()) == []

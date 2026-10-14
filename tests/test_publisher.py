from __future__ import annotations

import asyncio
import hashlib
from pathlib import Path
from types import SimpleNamespace
from typing import Any
from zipfile import ZIP_DEFLATED, ZipFile

import httpx
import pytest
from chattolib import ChattoClient
from chattolib._pb.chatto.api.v1 import (
    asset_uploads_pb2,
    messages_pb2,
)

from chatto_threaddump.archive import ThreadArchive
from chatto_threaddump.publisher import (
    ThreadPublicationError,
    publish_thread_archive,
)

protobuf: Any = asset_uploads_pb2
messages: Any = messages_pb2


def _zip_file(path: Path) -> bytes:
    with ZipFile(path, "w", compression=ZIP_DEFLATED) as archive:
        archive.writestr("thread_123/_index.md", "# Thread\n\nContent\n")
        archive.writestr("thread_123/photo.png", b"fake image bytes")
    return path.read_bytes()


def test_publishes_complete_zip_at_room_top_level(tmp_path: Path) -> None:
    zip_path = tmp_path / "thread_123.zip"
    zip_bytes = _zip_file(zip_path)
    requests: list[httpx.Request] = []
    upload_chunks: list[bytes] = []
    published: list[object] = []

    def respond(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        method = request.url.path.rsplit("/", 1)[-1]
        if method == "CreateUpload":
            upload_request = protobuf.CreateUploadRequest()
            upload_request.ParseFromString(request.content)
            assert upload_request.room_id == "room-1"
            assert upload_request.filename == "thread_123.zip"
            assert upload_request.content_type == "application/zip"
            assert upload_request.size == len(zip_bytes)
            assert upload_request.sha256 == hashlib.sha256(zip_bytes).hexdigest()
            response = protobuf.CreateUploadResponse()
            response.upload.upload_id = "upload-1"
            response.upload.room_id = "room-1"
            response.upload.max_chunk_size = 13
            response.upload.size = len(zip_bytes)
            return httpx.Response(200, content=response.SerializeToString())
        if method == "UploadChunk":
            chunk_request = protobuf.UploadChunkRequest()
            chunk_request.ParseFromString(request.content)
            assert chunk_request.upload_id == "upload-1"
            assert chunk_request.offset == len(upload_chunks) * 13
            upload_chunks.append(chunk_request.content)
            response = protobuf.UploadChunkResponse()
            response.upload.upload_id = "upload-1"
            response.upload.room_id = "room-1"
            response.upload.committed_offset = chunk_request.offset + len(
                chunk_request.content
            )
            response.upload.size = len(zip_bytes)
            return httpx.Response(200, content=response.SerializeToString())
        if method == "CompleteUpload":
            complete_request = protobuf.CompleteUploadRequest()
            complete_request.ParseFromString(request.content)
            assert complete_request.upload_id == "upload-1"
            response = protobuf.CompleteUploadResponse()
            response.upload.upload_id = "upload-1"
            response.upload.room_id = "room-1"
            response.upload.size = len(zip_bytes)
            response.upload.asset_id = "asset-1"
            response.asset.id = "asset-1"
            response.asset.filename = "thread_123.zip"
            response.asset.content_type = "application/zip"
            response.asset.size = len(zip_bytes)
            return httpx.Response(200, content=response.SerializeToString())
        if method == "CreateMessage":
            message_request = messages.CreateMessageRequest()
            message_request.ParseFromString(request.content)
            published.append(message_request)
            assert message_request.room_id == "room-1"
            assert message_request.body == 'Dump of Thread "First line"'
            assert list(message_request.attachment_asset_ids) == ["asset-1"]
            assert not message_request.thread_root_event_id
            assert not message_request.in_reply_to
            response = messages.CreateMessageResponse()
            response.message.id = "posted-event"
            response.message.room_id = "room-1"
            return httpx.Response(200, content=response.SerializeToString())
        raise AssertionError(f"unexpected Chatto API call: {method}")

    async def run() -> None:
        client = ChattoClient(
            token="fake-bot-key", base_url="https://chat.example.test"
        )
        asset_service = client.services.asset_uploads
        message_service = client.services.messages
        original_asset_http = asset_service._http
        original_message_http = message_service._http
        transport = httpx.MockTransport(respond)
        asset_service._http = httpx.AsyncClient(transport=transport, timeout=5)
        message_service._http = httpx.AsyncClient(transport=transport, timeout=5)
        await original_asset_http.aclose()
        await original_message_http.aclose()
        try:
            await publish_thread_archive(
                client,
                "room-1",
                ThreadArchive(
                    zip_path=zip_path,
                    filename="thread_123.zip",
                    starter_first_line="First line",
                ),
            )
        finally:
            await client.close()

    asyncio.run(run())

    assert b"".join(upload_chunks) == zip_bytes
    assert len(published) == 1
    methods = [request.url.path.rsplit("/", 1)[-1] for request in requests]
    assert methods[0] == "CreateUpload"
    assert methods[-1] == "CreateMessage"
    assert "JoinRoom" not in methods
    assert all(
        request.headers["Authorization"] == "Bearer fake-bot-key"
        for request in requests
    )


def test_upload_failure_is_sanitized_and_prevents_publication(tmp_path: Path) -> None:
    class FailingUploadClient:
        uploads = 0
        posts = 0

        async def upload_attachment(self, *_args: object, **_kwargs: object) -> object:
            self.uploads += 1
            raise RuntimeError("private Chatto response body and fake-key")

        async def post_message(self, *_args: object, **_kwargs: object) -> object:
            self.posts += 1
            return object()

    client = FailingUploadClient()
    archive_path = tmp_path / "thread_123.zip"
    _zip_file(archive_path)

    async def run() -> None:
        await publish_thread_archive(
            client,
            "room-1",
            ThreadArchive(archive_path, "thread_123.zip", "First line"),
        )

    try:
        asyncio.run(run())
    except ThreadPublicationError as error:
        assert str(error) == "Could not upload the thread archive."
        assert "private Chatto" not in str(error)
        assert "fake-key" not in str(error)
    else:
        raise AssertionError("failed upload unexpectedly published")

    assert client.uploads == 1
    assert client.posts == 0


@pytest.mark.parametrize(
    ("starter_first_line", "expected_body"),
    [
        ("", 'Dump of Thread ""'),
        ("_[message deleted]_", 'Dump of Thread "_[message deleted]_"'),
        (
            "_[message body unavailable]_",
            'Dump of Thread "_[message body unavailable]_"',
        ),
    ],
)
def test_uses_starter_first_line_verbatim_in_publication_headline(
    tmp_path: Path,
    starter_first_line: str,
    expected_body: str,
) -> None:
    class RecordingClient:
        posted: list[tuple[str, str, dict[str, object]]]

        def __init__(self) -> None:
            self.posted = []

        async def upload_attachment(self, *_args: object, **_kwargs: object) -> object:
            return SimpleNamespace(id="asset-1")

        async def post_message(
            self,
            room_id: str,
            body: str = "",
            **kwargs: object,
        ) -> object:
            self.posted.append((room_id, body, kwargs))
            return object()

    client = RecordingClient()
    archive_path = tmp_path / "thread_123.zip"
    _zip_file(archive_path)

    asyncio.run(
        publish_thread_archive(
            client,
            "room-1",
            ThreadArchive(archive_path, "thread_123.zip", starter_first_line),
        )
    )

    assert client.posted == [
        ("room-1", expected_body, {"attachment_asset_ids": ["asset-1"]})
    ]


def test_uncertain_post_failure_is_attempted_once_and_sanitized(
    tmp_path: Path,
) -> None:
    class AmbiguousPostClient:
        posts = 0

        async def upload_attachment(self, *_args: object, **_kwargs: object) -> object:
            return SimpleNamespace(id="asset-1")

        async def post_message(self, *_args: object, **_kwargs: object) -> object:
            self.posts += 1
            raise RuntimeError("response lost after private server processing")

    client = AmbiguousPostClient()
    archive_path = tmp_path / "thread_123.zip"
    _zip_file(archive_path)

    async def run() -> None:
        await publish_thread_archive(
            client,
            "room-1",
            ThreadArchive(archive_path, "thread_123.zip", "First line"),
        )

    with pytest.raises(
        ThreadPublicationError,
        match="Could not publish the thread archive",
    ) as caught:
        asyncio.run(run())

    assert str(caught.value) == "Could not publish the thread archive."
    assert "private server processing" not in str(caught.value)
    assert client.posts == 1

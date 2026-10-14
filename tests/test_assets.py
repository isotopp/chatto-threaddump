import asyncio
from types import ModuleType
from typing import Any

import httpx
import pytest
from chattolib._pb.chatto.api.v1 import message_types_pb2 as _message_types_pb2

from chatto_threaddump.assets import AssetCollectionError, collect_assets


class _ProtoTypes:
    def __init__(self, module: ModuleType) -> None:
        self.module = module

    def __getattr__(self, name: str) -> Any:
        return getattr(self.module, name)


message_types_pb2 = _ProtoTypes(_message_types_pb2)


def test_downloads_attachment_and_returns_local_markdown_reference(tmp_path) -> None:
    requests: list[httpx.Request] = []

    def respond(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(200, content=b"asset contents", request=request)

    message = message_types_pb2.Message(
        id="Eroot00000000001",
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

    links = asyncio.run(
        collect_assets(
            [message],
            tmp_path,
            transport=httpx.MockTransport(respond),
        )
    )

    assert (tmp_path / "notes #1.txt").read_bytes() == b"asset contents"
    assert links == {"Eroot00000000001": [r"[notes \#1.txt](notes%20%231.txt)"]}
    assert len(requests) == 1
    assert "Authorization" not in requests[0].headers


def test_preserves_attachment_order_with_safe_unique_names_and_links(tmp_path) -> None:
    downloaded: list[str] = []

    def respond(request: httpx.Request) -> httpx.Response:
        downloaded.append(request.url.path)
        return httpx.Response(200, content=b"data", request=request)

    first = message_types_pb2.Message(
        id="Eroot00000000001",
        attachments=[
            message_types_pb2.MessageAttachment(
                filename="../photo #.png",
                content_type="image/png",
                description="A [photo]",
                asset_url=message_types_pb2.MessageAssetUrl(
                    url="https://assets.example.test/one"
                ),
            ),
            message_types_pb2.MessageAttachment(
                filename="_index.md",
                content_type="text/plain",
                description="Read me",
                asset_url=message_types_pb2.MessageAssetUrl(
                    url="https://assets.example.test/two"
                ),
            ),
        ],
    )
    second = message_types_pb2.Message(
        id="Ereply0000000001",
        attachments=[
            message_types_pb2.MessageAttachment(
                filename="photo #.png",
                content_type="image/png\r\n",
                asset_url=message_types_pb2.MessageAssetUrl(
                    url="https://assets.example.test/three"
                ),
            ),
            message_types_pb2.MessageAttachment(
                filename="dir\\attachment\n.txt",
                content_type="application/octet-stream",
                asset_url=message_types_pb2.MessageAssetUrl(
                    url="https://assets.example.test/four"
                ),
            ),
        ],
    )

    links = asyncio.run(
        collect_assets(
            [first, second],
            tmp_path,
            transport=httpx.MockTransport(respond),
        )
    )

    assert downloaded == ["/one", "/two", "/three", "/four"]
    assert list(links) == ["Eroot00000000001", "Ereply0000000001"]
    assert links["Eroot00000000001"] == [
        "![A \\[photo\\]](photo%20%23.png)",
        r"[\_index.1.md](_index.1.md) — Read me",
    ]
    assert links["Ereply0000000001"] == [
        r"![photo \#.1.png](photo%20%23.1.png)",
        "[attachment .txt](attachment%20.txt)",
    ]
    assert (tmp_path / "photo #.png").read_bytes() == b"data"
    assert (tmp_path / "photo #.1.png").read_bytes() == b"data"
    assert (tmp_path / "_index.1.md").read_bytes() == b"data"
    assert (tmp_path / "attachment .txt").read_bytes() == b"data"


def test_late_asset_failure_removes_downloads_and_sanitizes_signed_url(
    tmp_path,
) -> None:
    def respond(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("first"):
            return httpx.Response(200, content=b"first asset", request=request)
        return httpx.Response(404, content=b"private response body", request=request)

    message = message_types_pb2.Message(
        id="Eroot00000000001",
        attachments=[
            message_types_pb2.MessageAttachment(
                filename="first.txt",
                content_type="text/plain",
                asset_url=message_types_pb2.MessageAssetUrl(
                    url="https://assets.example.test/first?signature=private-secret"
                ),
            ),
            message_types_pb2.MessageAttachment(
                filename="missing.txt",
                content_type="text/plain",
                asset_url=message_types_pb2.MessageAssetUrl(
                    url="https://assets.example.test/missing?signature=private-secret"
                ),
            ),
        ],
    )

    with pytest.raises(AssetCollectionError) as caught:
        asyncio.run(
            collect_assets(
                [message],
                tmp_path,
                transport=httpx.MockTransport(respond),
            )
        )

    assert str(caught.value) == "attachment not found"
    assert "private-secret" not in str(caught.value)
    assert "private response body" not in str(caught.value)
    assert list(tmp_path.iterdir()) == []


@pytest.mark.parametrize(
    ("failure", "expected"),
    [
        ("redirect", "attachment redirect rejected"),
        ("http", "attachment server failure"),
        ("timeout", "attachment network timeout"),
        ("tls", "attachment transport or TLS failure"),
    ],
)
def test_download_failures_are_sanitized(failure, expected, tmp_path) -> None:
    signed_url = "https://assets.example.test/file?signature=private-secret"

    def respond(request: httpx.Request) -> httpx.Response:
        if failure == "redirect":
            return httpx.Response(
                302,
                headers={"location": "https://other.example.test/private"},
                request=request,
            )
        if failure == "http":
            return httpx.Response(
                503, content=b"private response body", request=request
            )
        if failure == "timeout":
            raise httpx.ReadTimeout(f"timed out for {signed_url}", request=request)
        raise httpx.ConnectError(f"TLS failed for {signed_url}", request=request)

    message = message_types_pb2.Message(
        id="Eroot00000000001",
        attachments=[
            message_types_pb2.MessageAttachment(
                filename="file.txt",
                content_type="text/plain",
                asset_url=message_types_pb2.MessageAssetUrl(url=signed_url),
            )
        ],
    )

    with pytest.raises(AssetCollectionError) as caught:
        asyncio.run(
            collect_assets(
                [message],
                tmp_path,
                transport=httpx.MockTransport(respond),
            )
        )

    assert str(caught.value) == expected
    assert "private-secret" not in str(caught.value)
    assert "private response body" not in str(caught.value)
    assert list(tmp_path.iterdir()) == []


@pytest.mark.parametrize(
    "url",
    [
        "http://assets.example.test/file",
        "https://user:private-secret@assets.example.test/file",
        "https://assets.example.test/file#fragment",
        "https://assets.example.test:bad/file",
        "https://assets.example.test/file\n?signature=fake",
    ],
)
def test_rejects_unusable_asset_urls_without_network_access(url, tmp_path) -> None:
    requests: list[httpx.Request] = []

    def respond(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(200, content=b"unexpected", request=request)

    message = message_types_pb2.Message(
        id="Eroot00000000001",
        attachments=[
            message_types_pb2.MessageAttachment(
                filename="file.txt",
                content_type="text/plain",
                asset_url=message_types_pb2.MessageAssetUrl(url=url),
            )
        ],
    )

    with pytest.raises(AssetCollectionError) as caught:
        asyncio.run(
            collect_assets(
                [message],
                tmp_path,
                transport=httpx.MockTransport(respond),
            )
        )

    assert str(caught.value) == "attachment has no usable asset URL"
    assert "private-secret" not in str(caught.value)
    assert requests == []
    assert list(tmp_path.iterdir()) == []


def test_waiting_for_asset_io_does_not_block_other_event_loop_work(tmp_path) -> None:
    async def exercise() -> None:
        first_chunk_sent = asyncio.Event()
        continue_stream = asyncio.Event()
        probe_completed = asyncio.Event()

        class PausedAsset(httpx.AsyncByteStream):
            async def __aiter__(self):
                yield b"first "
                first_chunk_sent.set()
                await continue_stream.wait()
                yield b"chunk"

            async def aclose(self) -> None:
                return None

        class PausedTransport(httpx.AsyncBaseTransport):
            async def handle_async_request(
                self, request: httpx.Request
            ) -> httpx.Response:
                return httpx.Response(
                    200,
                    stream=PausedAsset(),
                    request=request,
                )

        message = message_types_pb2.Message(
            id="Eroot00000000001",
            attachments=[
                message_types_pb2.MessageAttachment(
                    filename="file.txt",
                    content_type="text/plain",
                    asset_url=message_types_pb2.MessageAssetUrl(
                        url="https://assets.example.test/file?signature=fake"
                    ),
                )
            ],
        )
        collection = asyncio.create_task(
            collect_assets([message], tmp_path, transport=PausedTransport())
        )
        await first_chunk_sent.wait()

        async def event_loop_probe() -> None:
            probe_completed.set()

        asyncio.create_task(event_loop_probe())
        async with asyncio.timeout(1):
            await probe_completed.wait()
        assert not collection.done()
        continue_stream.set()
        await collection

    asyncio.run(exercise())
    assert (tmp_path / "file.txt").read_bytes() == b"first chunk"

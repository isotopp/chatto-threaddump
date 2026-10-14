from __future__ import annotations

from pathlib import Path
from typing import Protocol

import httpx
from chattolib.exceptions import ChattoError
from google.protobuf.message import DecodeError

from chatto_threaddump.archive import ThreadArchive


class ThreadPublicationError(Exception):
    """A sanitized failure while uploading or publishing a thread archive."""


_OPERATION_ERRORS = (
    AssertionError,
    ChattoError,
    DecodeError,
    httpx.HTTPError,
    OSError,
    RuntimeError,
    ValueError,
)


class _PublisherClient(Protocol):
    async def upload_attachment(
        self,
        room_id: str,
        file_path: str | Path,
        *,
        content_type: str = "",
        filename: str | None = None,
    ) -> object: ...

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
    ) -> object: ...


async def publish_thread_archive(
    client: _PublisherClient,
    room_id: str,
    archive: ThreadArchive,
) -> None:
    """Upload and publish an archive as a top-level post in its source room.

    Each Chatto operation is attempted once. Errors are replaced with safe
    messages so transport details or response bodies do not escape to callers.
    """
    try:
        asset = await client.upload_attachment(
            room_id,
            archive.zip_path,
            content_type="application/zip",
            filename=archive.filename,
        )
    except _OPERATION_ERRORS:
        raise ThreadPublicationError("Could not upload the thread archive.") from None

    asset_id = getattr(asset, "id", None)
    if not isinstance(asset_id, str) or not asset_id:
        raise ThreadPublicationError("Chatto returned no archive attachment.")

    headline = f'Dump of Thread "{archive.starter_first_line}"'
    try:
        await client.post_message(
            room_id,
            headline,
            attachment_asset_ids=[asset_id],
        )
    except _OPERATION_ERRORS:
        raise ThreadPublicationError("Could not publish the thread archive.") from None

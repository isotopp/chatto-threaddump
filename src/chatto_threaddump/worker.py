from __future__ import annotations

import asyncio
import logging
from collections.abc import Mapping
from pathlib import Path
from typing import Any

import httpx

from chatto_threaddump.archive import create_thread_archive
from chatto_threaddump.chatto_replies import RPC_TIMEOUT_SECONDS, reply_to_event
from chatto_threaddump.contracts import DumpRequest
from chatto_threaddump.publisher import publish_thread_archive

_LOGGER = logging.getLogger(__name__)
_FAILURE_REPLY = "The thread dump could not be completed."


class _ArchiveReader:
    """Keep archive protobuf reads on the SDK's raw service interfaces."""

    def __init__(self, client: Any) -> None:
        self._client = client

    async def get_thread_events(
        self, request: Any, *, headers: Mapping[str, str] | None = None
    ) -> Any:
        async with asyncio.timeout(RPC_TIMEOUT_SECONDS):
            return await self._client.services.threads.get_thread_events(
                request, headers=dict(headers) if headers is not None else None
            )

    async def batch_get_users(
        self, request: Any, *, headers: Mapping[str, str] | None = None
    ) -> Any:
        async with asyncio.timeout(RPC_TIMEOUT_SECONDS):
            return await self._client.services.user.batch_get_users(
                request, headers=dict(headers) if headers is not None else None
            )


async def process_dump_request(
    client: Any,
    request: DumpRequest,
    *,
    api_key: str,
    temp_root: Path,
    asset_transport: httpx.AsyncBaseTransport | None = None,
) -> None:
    """Build and publish one request, reporting failure without leaking details.

    The archive context owns its temporary files through the upload and post.
    Every ordinary failure is isolated to this request, so a sequential inbox
    consumer can continue with its next item.
    """
    try:
        async with create_thread_archive(
            _ArchiveReader(client),
            request.room_id,
            request.thread_root_event_id,
            api_key=api_key,
            temp_root=temp_root,
            asset_transport=asset_transport,
        ) as archive:
            await publish_thread_archive(client, request.room_id, archive)
    except Exception:  # noqa: BLE001 - operation errors may contain private data.
        try:
            await reply_to_event(
                client,
                room_id=request.room_id,
                event_id=request.command_event_id,
                body=_FAILURE_REPLY,
            )
        except Exception:  # noqa: BLE001 - never log an external exception.
            _LOGGER.warning("Could not report failed thread dump request.")

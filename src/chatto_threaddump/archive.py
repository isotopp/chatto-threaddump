from __future__ import annotations

import asyncio
import os
import tempfile
import unicodedata
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol
from zipfile import ZIP_DEFLATED, ZipFile

import httpx

from chatto_threaddump.assets import collect_assets
from chatto_threaddump.thread_reader import (
    ThreadEventsClient,
    ThreadPage,
    read_thread,
)
from chatto_threaddump.transcript import UserServiceClient, write_transcript

_ZIP_CHUNK_SIZE = 64 * 1024


class ArchiveError(Exception):
    """A sanitized error while preparing a thread archive."""


class _ArchiveClient(ThreadEventsClient, UserServiceClient, Protocol):
    pass


@dataclass(frozen=True)
class ThreadArchive:
    """A complete ZIP and the starter line used for its publication headline."""

    zip_path: Path
    filename: str
    starter_first_line: str


@asynccontextmanager
async def create_thread_archive(
    client: _ArchiveClient,
    room_id: str,
    root_event_id: str,
    *,
    api_key: str,
    temp_root: Path,
    asset_transport: httpx.AsyncBaseTransport | None = None,
) -> AsyncIterator[ThreadArchive]:
    """Build and yield a complete isolated archive, then remove its parent.

    The yielded ZIP remains available for the duration of the context. Each
    invocation owns a unique parent under ``temp_root`` so its cleanup cannot
    remove another request's files.
    """
    parent = _create_unique_parent(temp_root)
    try:
        thread = await read_thread(client, room_id, root_event_id, api_key=api_key)
        bundle_dir = parent / f"thread_{thread.starter.created_at.seconds}"
        try:
            bundle_dir.mkdir()
        except OSError:
            raise ArchiveError("cannot create thread bundle") from None

        attachment_lines = await collect_assets(
            thread.messages, bundle_dir, transport=asset_transport
        )
        try:
            await write_transcript(
                thread.messages,
                thread.included_users,
                client,
                api_key=api_key,
                output_dir=bundle_dir,
                attachment_lines=attachment_lines,
            )
        except OSError:
            raise ArchiveError("cannot write thread transcript") from None

        filename = f"{bundle_dir.name}.zip"
        zip_path = parent / filename
        await _write_zip(bundle_dir, zip_path)
        yield ThreadArchive(
            zip_path=zip_path,
            filename=filename,
            starter_first_line=_starter_first_line(thread),
        )
    finally:
        await _remove_owned_parent(parent)


def _create_unique_parent(temp_root: Path) -> Path:
    try:
        temp_root.mkdir(parents=True, exist_ok=True)
        if temp_root.is_symlink() or not temp_root.is_dir():
            raise ArchiveError("temporary archive root is unavailable")
        return Path(tempfile.mkdtemp(prefix="chatto-threaddump-", dir=temp_root))
    except ArchiveError:
        raise
    except OSError:
        raise ArchiveError("temporary archive root is unavailable") from None


def _starter_first_line(thread: ThreadPage) -> str:
    starter = thread.starter
    if starter.HasField("body"):
        body = starter.body
    elif starter.HasField("deleted_at"):
        body = "_[message deleted]_"
    else:
        body = "_[message body unavailable]_"
    normalized = body.replace("\r\n", "\n").replace("\r", "\n")
    cleaned = "".join(
        character
        for character in normalized
        if character in "\n\t" or not unicodedata.category(character).startswith("C")
    )
    return cleaned.split("\n", 1)[0]


async def _write_zip(bundle_dir: Path, zip_path: Path) -> None:
    try:
        with (
            ZipFile(zip_path, mode="w", compression=ZIP_DEFLATED) as archive,
            os.scandir(bundle_dir) as entries,
        ):
            for entry in entries:
                source = Path(entry.path)
                if entry.is_symlink() or not entry.is_file(follow_symlinks=False):
                    raise ArchiveError("thread bundle contains an unusable file")
                archive_name = f"{bundle_dir.name}/{source.name}"
                with (
                    source.open("rb") as input_file,  # noqa: ASYNC230 - chunked reads yield below.
                    archive.open(archive_name, mode="w") as output_file,
                ):
                    while chunk := input_file.read(_ZIP_CHUNK_SIZE):
                        output_file.write(chunk)
                        await asyncio.sleep(0)
                await asyncio.sleep(0)
    except asyncio.CancelledError:
        raise
    except ArchiveError:
        raise
    except OSError:
        raise ArchiveError("cannot package thread archive") from None
    except Exception:  # noqa: BLE001 - ZIP errors may include local paths.
        raise ArchiveError("cannot package thread archive") from None


async def _remove_owned_parent(parent: Path) -> None:
    """Remove only this context's tree, yielding between filesystem entries."""

    try:
        await _remove_tree(parent)
    except asyncio.CancelledError:
        raise
    except OSError:
        raise ArchiveError("cannot remove temporary thread archive") from None


async def _remove_tree(directory: Path) -> None:
    try:
        if directory.is_symlink():
            directory.unlink(missing_ok=True)
            return
        with os.scandir(directory) as entries:
            for entry in entries:
                path = Path(entry.path)
                if entry.is_dir(follow_symlinks=False):
                    await _remove_tree(path)
                else:
                    path.unlink(missing_ok=True)
                    await asyncio.sleep(0)
        directory.rmdir()
    except FileNotFoundError:
        return

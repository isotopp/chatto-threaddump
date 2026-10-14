from __future__ import annotations

import asyncio
import re
import unicodedata
from collections.abc import Mapping, Sequence
from pathlib import Path
from urllib.parse import quote, urlsplit

import httpx

ASSET_TIMEOUT_SECONDS = 5.0
_CHUNK_SIZE = 64 * 1024


class AssetCollectionError(Exception):
    """A sanitized failure while collecting a thread's required assets."""


async def collect_assets(
    messages: Sequence[object],
    bundle_dir: Path,
    *,
    transport: httpx.AsyncBaseTransport | None = None,
) -> Mapping[str, Sequence[str]]:
    """Download all message attachments and return ordered Markdown links.

    Asset URLs are signed bearer capabilities, so they are requested without
    Chatto credentials and are never included in an error message.
    """
    try:
        bundle_dir.mkdir(parents=True, exist_ok=True)
        if bundle_dir.is_symlink() or not bundle_dir.is_dir():
            raise AssetCollectionError("attachment bundle directory is unavailable")
        used_names = {"_index.md", *(entry.name for entry in bundle_dir.iterdir())}
    except AssetCollectionError:
        raise
    except OSError:
        raise AssetCollectionError(
            "attachment bundle directory is unavailable"
        ) from None

    links: dict[str, list[str]] = {}
    created_files: list[Path] = []
    try:
        async with httpx.AsyncClient(
            follow_redirects=False,
            timeout=ASSET_TIMEOUT_SECONDS,
            transport=transport,
        ) as client:
            for message in messages:
                message_id, attachments = _message_attachments(message)
                for attachment in attachments:
                    filename, mime_type, description, url = _attachment_fields(
                        attachment
                    )
                    filename = _unique_name(_safe_basename(filename), used_names)
                    target = quote(filename, safe="")
                    label = description or filename
                    if mime_type.lower().startswith("image/"):
                        link = f"![{_escape_metadata(label)}]({target})"
                    else:
                        link = f"[{_escape_metadata(filename)}]({target})"
                        if description:
                            link += f" — {_escape_metadata(description)}"

                    destination = bundle_dir / filename
                    await _download(client, url, destination, created_files)
                    links.setdefault(message_id, []).append(link)
    except asyncio.CancelledError:
        _remove_created_files(created_files)
        raise
    except AssetCollectionError:
        _remove_created_files(created_files)
        raise
    except OSError:
        _remove_created_files(created_files)
        raise AssetCollectionError("cannot write attachment file") from None
    except Exception:  # noqa: BLE001 - transport errors can include signed URLs.
        _remove_created_files(created_files)
        raise AssetCollectionError("attachment collection failed") from None

    return links


def _message_attachments(message: object) -> tuple[str, Sequence[object]]:
    message_id = getattr(message, "id", None)
    attachments = getattr(message, "attachments", None)
    if (
        not isinstance(message_id, str)
        or not message_id
        or not isinstance(attachments, Sequence)
        or isinstance(attachments, (str, bytes, bytearray))
    ):
        raise AssetCollectionError("malformed Chatto attachment message")
    return message_id, attachments


def _attachment_fields(attachment: object) -> tuple[str, str, str, str]:
    filename = getattr(attachment, "filename", None)
    if not isinstance(filename, str):
        filename = "attachment"

    mime_type = getattr(attachment, "content_type", None)
    if not isinstance(mime_type, str):
        raise AssetCollectionError("malformed attachment content type")
    mime_type = " ".join(_clean_text(mime_type).split()) or "application/octet-stream"

    description = getattr(attachment, "description", "")
    if description is None:
        description = ""
    if not isinstance(description, str):
        raise AssetCollectionError("malformed attachment description")
    description = " ".join(_clean_text(description).split())

    asset_url = getattr(attachment, "asset_url", None)
    url = getattr(asset_url, "url", None)
    return filename, mime_type, description, _validated_asset_url(url)


def _validated_asset_url(value: object) -> str:
    if not isinstance(value, str) or not value:
        raise AssetCollectionError("attachment has no usable asset URL")
    try:
        parsed = urlsplit(value)
        _ = parsed.port
    except ValueError:
        raise AssetCollectionError("attachment has no usable asset URL") from None
    if (
        parsed.scheme.lower() != "https"
        or not parsed.netloc
        or parsed.hostname is None
        or parsed.username is not None
        or parsed.password is not None
        or parsed.fragment
        or any(
            character.isspace() or unicodedata.category(character) == "Cc"
            for character in value
        )
    ):
        raise AssetCollectionError("attachment has no usable asset URL")
    return value


async def _download(
    client: httpx.AsyncClient,
    url: str,
    destination: Path,
    created_files: list[Path],
) -> None:
    try:
        async with client.stream("GET", url) as response:
            status = response.status_code
            if 300 <= status < 400:
                raise AssetCollectionError("attachment redirect rejected")
            if not 200 <= status < 300:
                if status == 404:
                    raise AssetCollectionError("attachment not found")
                if status >= 500:
                    raise AssetCollectionError("attachment server failure")
                raise AssetCollectionError("attachment download failed")

            try:
                with destination.open("xb") as output:
                    created_files.append(destination)
                    async for chunk in response.aiter_bytes(chunk_size=_CHUNK_SIZE):
                        output.write(chunk)
                        await asyncio.sleep(0)
            except OSError:
                raise AssetCollectionError("cannot write attachment file") from None
    except asyncio.CancelledError:
        raise
    except AssetCollectionError:
        raise
    except httpx.TimeoutException:
        raise AssetCollectionError("attachment network timeout") from None
    except httpx.TransportError:
        raise AssetCollectionError("attachment transport or TLS failure") from None
    except httpx.HTTPError:
        raise AssetCollectionError("attachment download failed") from None
    except OSError:
        raise AssetCollectionError("attachment transport or TLS failure") from None


def _safe_basename(value: str) -> str:
    cleaned = " ".join(_clean_text(value).split()).replace("\\", "/")
    basename = cleaned.rsplit("/", 1)[-1]
    return basename if basename not in ("", ".", "..") else "attachment"


def _unique_name(value: str, used_names: set[str]) -> str:
    if value not in used_names:
        used_names.add(value)
        return value
    path = Path(value)
    suffix = path.suffix
    stem = value[: -len(suffix)] if suffix else value
    index = 1
    while True:
        candidate = f"{stem}.{index}{suffix}"
        if candidate not in used_names and candidate != "_index.md":
            used_names.add(candidate)
            return candidate
        index += 1


def _clean_text(value: str) -> str:
    value = value.replace("\r\n", "\n").replace("\r", "\n")
    return "".join(
        character
        for character in value
        if character in "\n\t" or not unicodedata.category(character).startswith("C")
    )


def _escape_metadata(value: str) -> str:
    value = value.replace("\\", "\\\\")
    return re.sub(r"([`*_\[\]()#+])", r"\\\1", value)


def _remove_created_files(files: Sequence[Path]) -> None:
    for path in reversed(files):
        try:
            path.unlink(missing_ok=True)
        except OSError:
            pass

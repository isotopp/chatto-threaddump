from __future__ import annotations

import ipaddress
import os
import re
from dataclasses import dataclass, field
from pathlib import Path
from urllib.parse import urlsplit

from dotenv import load_dotenv

_DNS_LABEL = re.compile(r"[A-Za-z0-9](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?\Z")


class ConfigurationError(Exception):
    """Raised when required bot configuration is invalid or missing."""


@dataclass(frozen=True)
class Settings:
    server_url: str
    api_key: str = field(repr=False)
    role_name: str
    queue_length: int
    temp_dir: Path


def load_settings() -> Settings:
    """Load and validate the bot's working-directory configuration."""
    load_dotenv(Path.cwd() / ".env", override=False)

    server_url = _required("CHATTO_THREADDUMP_SERVER_URL")
    api_key = _required("CHATTO_THREADDUMP_API_KEY")
    role_name = _required("CHATTO_THREADDUMP_ROLE")
    queue_value = _required("CHATTO_THREADDUMP_QUEUE_LENGTH")

    _validate_server_url(server_url)
    try:
        queue_length = int(queue_value)
    except ValueError as exc:
        raise ConfigurationError(
            "CHATTO_THREADDUMP_QUEUE_LENGTH must be a positive integer"
        ) from exc
    if queue_length <= 0:
        raise ConfigurationError(
            "CHATTO_THREADDUMP_QUEUE_LENGTH must be a positive integer"
        )

    temp_dir = Path.cwd() / "tmpdir"
    try:
        temp_dir.mkdir(mode=0o700, exist_ok=True)
    except OSError as exc:
        raise ConfigurationError("cannot create temporary work directory") from exc

    return Settings(
        server_url=server_url.rstrip("/"),
        api_key=api_key,
        role_name=role_name,
        queue_length=queue_length,
        temp_dir=temp_dir,
    )


def _required(name: str) -> str:
    value = os.environ.get(name, "").strip()
    if not value:
        raise ConfigurationError(f"missing required configuration: {name}")
    return value


def _validate_server_url(value: str) -> None:
    try:
        parsed = urlsplit(value)
        port = parsed.port
    except ValueError as exc:
        raise ConfigurationError("invalid Chatto server origin") from exc

    if (
        parsed.scheme.lower() != "https"
        or not parsed.netloc
        or parsed.hostname is None
        or parsed.username is not None
        or parsed.password is not None
        or parsed.path not in ("", "/")
        or parsed.query
        or parsed.fragment
        or "?" in value
        or "#" in value
        or any(character.isspace() or ord(character) < 0x20 for character in value)
    ):
        raise ConfigurationError("invalid Chatto server origin")

    if port is not None and not 1 <= port <= 65535:
        raise ConfigurationError("invalid Chatto server origin")
    hostname = parsed.hostname
    try:
        hostname.encode("ascii")
    except UnicodeEncodeError as exc:
        raise ConfigurationError("invalid Chatto server origin") from exc
    try:
        ipaddress.ip_address(hostname)
    except ValueError:
        labels = hostname.rstrip(".").split(".")
        if not labels or any(not _DNS_LABEL.fullmatch(label) for label in labels):
            raise ConfigurationError("invalid Chatto server origin")

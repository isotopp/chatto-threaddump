from pathlib import Path

import pytest

from chatto_threaddump.config import ConfigurationError, load_settings
from chatto_threaddump.contracts import DumpRequest


def _set_valid_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("CHATTO_THREADDUMP_SERVER_URL", "https://chatto.example")
    monkeypatch.setenv("CHATTO_THREADDUMP_API_KEY", "fake-key")
    monkeypatch.setenv("CHATTO_THREADDUMP_ROLE", "thread-exporter")
    monkeypatch.setenv("CHATTO_THREADDUMP_QUEUE_LENGTH", "3")


def test_loads_bot_settings_from_dotenv(tmp_path: Path, monkeypatch) -> None:
    (tmp_path / ".env").write_text(
        "CHATTO_THREADDUMP_SERVER_URL=https://chatto.example\n"
        "CHATTO_THREADDUMP_API_KEY=fake-key\n"
        "CHATTO_THREADDUMP_ROLE=thread-exporter\n"
        "CHATTO_THREADDUMP_QUEUE_LENGTH=3\n",
        encoding="utf-8",
    )
    monkeypatch.chdir(tmp_path)
    for name in (
        "CHATTO_THREADDUMP_SERVER_URL",
        "CHATTO_THREADDUMP_API_KEY",
        "CHATTO_THREADDUMP_ROLE",
        "CHATTO_THREADDUMP_QUEUE_LENGTH",
    ):
        monkeypatch.delenv(name, raising=False)

    settings = load_settings()

    assert settings.server_url == "https://chatto.example"
    assert settings.api_key == "fake-key"
    assert settings.role_name == "thread-exporter"
    assert settings.queue_length == 3
    assert settings.temp_dir == tmp_path / "tmpdir"
    assert settings.temp_dir.is_dir()


def test_process_environment_overrides_dotenv(tmp_path: Path, monkeypatch) -> None:
    (tmp_path / ".env").write_text(
        "CHATTO_THREADDUMP_SERVER_URL=https://from-file.example\n"
        "CHATTO_THREADDUMP_API_KEY=file-key\n"
        "CHATTO_THREADDUMP_ROLE=file-role\n"
        "CHATTO_THREADDUMP_QUEUE_LENGTH=4\n",
        encoding="utf-8",
    )
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("CHATTO_THREADDUMP_SERVER_URL", "https://from-env.example")

    settings = load_settings()

    assert settings.server_url == "https://from-env.example"


def test_missing_role_fails_without_exposing_credential(
    tmp_path: Path, monkeypatch
) -> None:
    monkeypatch.chdir(tmp_path)
    _set_valid_environment(monkeypatch)
    monkeypatch.delenv("CHATTO_THREADDUMP_ROLE")

    with pytest.raises(ConfigurationError) as error:
        load_settings()

    assert "CHATTO_THREADDUMP_ROLE" in str(error.value)
    assert "fake-key" not in str(error.value)


def test_dump_request_carries_the_command_and_thread_identity() -> None:
    request = DumpRequest(
        room_id="R12345678901234",
        command_event_id="E12345678901234",
        thread_root_event_id="Eabcdefghijklmn",
    )

    assert request.room_id == "R12345678901234"
    assert request.command_event_id == "E12345678901234"
    assert request.thread_root_event_id == "Eabcdefghijklmn"


def test_server_origin_rejects_invalid_dns_hostname(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.chdir(tmp_path)
    _set_valid_environment(monkeypatch)
    monkeypatch.setenv("CHATTO_THREADDUMP_SERVER_URL", "https://chatto.example,evil")

    with pytest.raises(ConfigurationError, match="invalid Chatto server origin"):
        load_settings()

import json

import httpx

from chatto_threaddump import main


def test_console_entry_point_exists() -> None:
    assert callable(main)


def test_exports_an_explicit_thread_page(monkeypatch, tmp_path, capsys) -> None:
    requests: list[httpx.Request] = []
    client_options: dict[str, object] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(
            200,
            json={
                "events": [
                    {
                        "id": "Eroot000000000",
                        "messagePosted": {
                            "message": {
                                "id": "Eroot000000000",
                                "roomId": "Rroom000000000",
                                "actorId": "Uroot",
                                "createdAt": "2026-09-19T10:00:00Z",
                                "body": "Root body",
                            }
                        },
                    },
                    {
                        "id": "Ereply00000000",
                        "messagePosted": {
                            "message": {
                                "id": "Ereply00000000",
                                "roomId": "Rroom000000000",
                                "actorId": "Ureply",
                                "createdAt": "2026-09-19T10:01:00Z",
                                "body": "Reply body",
                            }
                        },
                    },
                ],
                "includes": {
                    "users": {
                        "Uroot": {"id": "Uroot", "displayName": "Root author"},
                        "Ureply": {"id": "Ureply", "login": "reply"},
                    }
                },
            },
            request=request,
        )

    monkeypatch.setenv("CHATTO_THREADDUMP_SERVER_URL", "https://api.example.test")
    monkeypatch.setenv("CHATTO_THREADDUMP_API_KEY", "test-key")
    real_client = httpx.Client

    def client(**kwargs):
        client_options.update(kwargs)
        return real_client(transport=httpx.MockTransport(handler), **kwargs)

    monkeypatch.setattr(
        httpx,
        "Client",
        client,
    )

    output = tmp_path / "nested" / "bundle"
    assert (
        main(
            [
                "https://frontend.example.test/chat/api.example.test/Rroom000000000/Eroot000000000/m/Ereply00000000",
                str(output),
            ]
        )
        == 0
    )

    assert (output / "_index.md").read_text(encoding="utf-8") == (
        "# Chatto thread\n\n"
        "Started: 2026-09-19T10:00:00Z\n\n"
        "## Root author\n\n"
        "Root body\n\n"
        "## reply\n\n"
        "Reply body\n"
    )
    assert capsys.readouterr().out == ""
    assert len(requests) == 1
    assert str(requests[0].url) == (
        "https://api.example.test/api/connect/"
        "chatto.api.v1.ThreadService/GetThreadEvents"
    )
    assert requests[0].headers["Authorization"] == "Bearer test-key"
    assert json.loads(requests[0].content) == {
        "roomId": "Rroom000000000",
        "threadRootEventId": "Eroot000000000",
        "limit": 500,
    }
    assert client_options["timeout"] == 5.0


def test_process_environment_overrides_dotenv_and_timeout_is_configurable(
    monkeypatch, tmp_path
) -> None:
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(
            200,
            json={
                "events": [
                    {
                        "messagePosted": {
                            "message": {
                                "actorId": "U",
                                "createdAt": "2026-09-19T10:00:00Z",
                                "body": "body",
                            }
                        }
                    }
                ],
                "includes": {"users": {"U": {"displayName": "Author"}}},
            },
            request=request,
        )

    monkeypatch.chdir(tmp_path)
    (tmp_path / ".env").write_text(
        "CHATTO_THREADDUMP_SERVER_URL=https://wrong.example\n"
        "CHATTO_THREADDUMP_API_KEY=dotenv-key\n",
        encoding="utf-8",
    )
    monkeypatch.setenv("CHATTO_THREADDUMP_SERVER_URL", "https://api.example.test")
    monkeypatch.setenv("CHATTO_THREADDUMP_API_KEY", "process-key")
    real_client = httpx.Client
    monkeypatch.setattr(
        httpx,
        "Client",
        lambda **kwargs: real_client(transport=httpx.MockTransport(handler), **kwargs),
    )

    assert (
        main(
            [
                "--force",
                "-t",
                "1.25",
                "https://frontend.example.test/chat/api.example.test/Rroom000000000/Eroot000000000/m/Ereply00000000",
                str(tmp_path / "bundle"),
            ]
        )
        == 0
    )
    assert requests[0].headers["Authorization"] == "Bearer process-key"


def test_dotenv_supplies_missing_process_configuration(monkeypatch, tmp_path) -> None:
    monkeypatch.chdir(tmp_path)
    (tmp_path / ".env").write_text(
        "CHATTO_THREADDUMP_SERVER_URL=https://api.example.test\n"
        "CHATTO_THREADDUMP_API_KEY=dotenv-key\n",
        encoding="utf-8",
    )
    monkeypatch.delenv("CHATTO_THREADDUMP_SERVER_URL", raising=False)
    monkeypatch.delenv("CHATTO_THREADDUMP_API_KEY", raising=False)

    real_client = httpx.Client

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "events": [
                    {
                        "messagePosted": {
                            "message": {
                                "actorId": "U",
                                "createdAt": "2026-09-19T10:00:00Z",
                                "body": "body",
                            }
                        }
                    }
                ],
                "includes": {"users": {"U": {"displayName": "Author"}}},
            },
            request=request,
        )

    monkeypatch.setattr(
        httpx,
        "Client",
        lambda **kwargs: real_client(transport=httpx.MockTransport(handler), **kwargs),
    )
    assert (
        main(
            [
                "https://frontend.example.test/chat/api.example.test/Rroom000000000/Eroot000000000/m/Ereply00000000",
                str(tmp_path / "bundle"),
            ]
        )
        == 0
    )


def test_missing_configuration_is_reported_before_url_parsing(
    monkeypatch, tmp_path, capsys
) -> None:
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("HOME", str(tmp_path / "empty-home"))
    monkeypatch.delenv("CHATTO_THREADDUMP_SERVER_URL", raising=False)
    monkeypatch.delenv("CHATTO_THREADDUMP_API_KEY", raising=False)

    assert main(["not a Chatto URL", str(tmp_path / "bundle")]) == 1
    captured = capsys.readouterr()
    assert "missing Chatto configuration" in captured.err
    assert "invalid Chatto thread URL" not in captured.err
    assert captured.out == ""

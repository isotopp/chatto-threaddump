import json

import httpx
import pytest

from chatto_threaddump import main


def test_console_entry_point_exists() -> None:
    assert callable(main)


def test_help_describes_arguments_without_configuration(capsys) -> None:
    with pytest.raises(SystemExit) as raised:
        main(["--help"])
    assert raised.value.code == 0
    help_text = capsys.readouterr().out
    assert "chatto_url" in help_text
    assert "output_directory" in help_text
    assert "--force" in help_text and "-f" in help_text
    assert "--timeout" in help_text and "-t" in help_text


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
                        "id": "E12345678901234",
                        "roomId": "R12345678901234",
                        "actorId": "Uroot",
                        "createdAt": "2026-09-19T10:00:00Z",
                        "messagePosted": {
                            "message": {
                                "id": "E12345678901234",
                                "roomId": "R12345678901234",
                                "actorId": "Uroot",
                                "createdAt": "2026-09-19T10:00:00Z",
                                "body": "Root body",
                            }
                        },
                    },
                    {
                        "id": "E22345678901234",
                        "roomId": "R12345678901234",
                        "actorId": "Ureply",
                        "createdAt": "2026-09-19T10:01:00Z",
                        "messagePosted": {
                            "message": {
                                "id": "E22345678901234",
                                "roomId": "R12345678901234",
                                "actorId": "Ureply",
                                "threadRootEventId": "E12345678901234",
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
                "page": {"hasOlder": False, "startCursor": ""},
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
                "https://frontend.example.test/chat/api.example.test/R12345678901234/E12345678901234/m/E22345678901234",
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
        "## @reply\n\n"
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
        "roomId": "R12345678901234",
        "threadRootEventId": "E12345678901234",
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
                        "id": "E12345678901234",
                        "roomId": "R12345678901234",
                        "actorId": "U",
                        "createdAt": "2026-09-19T10:00:00Z",
                        "messagePosted": {
                            "message": {
                                "id": "E12345678901234",
                                "roomId": "R12345678901234",
                                "actorId": "U",
                                "createdAt": "2026-09-19T10:00:00Z",
                                "body": "body",
                            }
                        },
                    }
                ],
                "includes": {"users": {"U": {"id": "U", "displayName": "Author"}}},
                "page": {"hasOlder": False, "startCursor": ""},
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
                "https://frontend.example.test/chat/api.example.test/R12345678901234/E12345678901234/m/E22345678901234",
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
                        "id": "E12345678901234",
                        "roomId": "R12345678901234",
                        "actorId": "U",
                        "createdAt": "2026-09-19T10:00:00Z",
                        "messagePosted": {
                            "message": {
                                "id": "E12345678901234",
                                "roomId": "R12345678901234",
                                "actorId": "U",
                                "createdAt": "2026-09-19T10:00:00Z",
                                "body": "body",
                            }
                        },
                    }
                ],
                "includes": {"users": {"U": {"id": "U", "displayName": "Author"}}},
                "page": {"hasOlder": False, "startCursor": ""},
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
                "https://frontend.example.test/chat/api.example.test/R12345678901234/E12345678901234/m/E22345678901234",
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


@pytest.mark.parametrize(
    "server_url, chatto_url",
    [
        (
            "http://api.example.test",
            "https://frontend.example.test/chat/api.example.test/R12345678901234/E12345678901234/m/E22345678901234",
        ),
        (
            "https://api.example.test/private",
            "https://frontend.example.test/chat/api.example.test/R12345678901234/E12345678901234/m/E22345678901234",
        ),
        (
            "https://api.example.test",
            "https://frontend.example.test/chat/other.example.test/R12345678901234/E12345678901234/m/E22345678901234",
        ),
        (
            "https://api.example.test",
            "https://frontend.example.test/chat/api.example.test/R1234567890123%2F4/E12345678901234/m/E22345678901234",
        ),
        (
            "https://api.example.test",
            "https://user:secret@frontend.example.test/chat/api.example.test/R12345678901234/E12345678901234/m/E22345678901234",
        ),
        (
            "https://api.example.test",
            "https://frontend.example.test/chat/api.example.test/R12345678901234/E12345678901234/m/E22345678901234?x=1",
        ),
    ],
)
def test_rejects_untrusted_origins_and_malformed_links(
    monkeypatch, tmp_path, capsys, server_url, chatto_url
) -> None:
    calls = 0
    monkeypatch.setenv("CHATTO_THREADDUMP_SERVER_URL", server_url)
    monkeypatch.setenv("CHATTO_THREADDUMP_API_KEY", "test-key")

    def client(**kwargs):
        nonlocal calls
        calls += 1
        raise AssertionError("invalid input must not create an HTTP client")

    monkeypatch.setattr(httpx, "Client", client)
    assert main([chatto_url, str(tmp_path / "bundle")]) == 1
    assert calls == 0
    assert "test-key" not in capsys.readouterr().err


def test_resolves_an_unthreaded_room_message(monkeypatch, tmp_path) -> None:
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(
            200,
            json={
                "message": {
                    "id": "E22345678901234",
                    "roomId": "R12345678901234",
                    "actorId": "U",
                    "createdAt": "2026-09-19T10:00:00Z",
                    "body": "Standalone body",
                },
                "includes": {"users": {"U": {"id": "U", "displayName": "Author"}}},
            },
            request=request,
        )

    monkeypatch.setenv("CHATTO_THREADDUMP_SERVER_URL", "https://api.example.test")
    monkeypatch.setenv("CHATTO_THREADDUMP_API_KEY", "test-key")
    real_client = httpx.Client
    monkeypatch.setattr(
        httpx,
        "Client",
        lambda **kwargs: real_client(transport=httpx.MockTransport(handler), **kwargs),
    )

    output = tmp_path / "bundle"
    assert (
        main(
            [
                "https://frontend.example.test/chat/api.example.test/R12345678901234/m/E22345678901234",
                str(output),
            ]
        )
        == 0
    )
    assert len(requests) == 1
    assert requests[0].url.path.endswith("MessageService/GetMessage")
    assert json.loads(requests[0].content) == {
        "roomId": "R12345678901234",
        "eventId": "E22345678901234",
    }
    assert "Standalone body" in (output / "_index.md").read_text(encoding="utf-8")


def test_resolves_a_room_reply_through_its_thread(monkeypatch, tmp_path) -> None:
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        if request.url.path.endswith("MessageService/GetMessage"):
            payload = {
                "message": {
                    "id": "E22345678901234",
                    "roomId": "R12345678901234",
                    "actorId": "Ureply",
                    "threadRootEventId": "E12345678901234",
                    "createdAt": "2026-09-19T10:01:00Z",
                    "body": "Reply body",
                    "thread": {},
                }
            }
        else:
            payload = {
                "events": [
                    {
                        "id": "E12345678901234",
                        "roomId": "R12345678901234",
                        "actorId": "Uroot",
                        "createdAt": "2026-09-19T10:00:00Z",
                        "messagePosted": {
                            "message": {
                                "id": "E12345678901234",
                                "roomId": "R12345678901234",
                                "actorId": "Uroot",
                                "createdAt": "2026-09-19T10:00:00Z",
                                "body": "Root body",
                            }
                        },
                    },
                    {
                        "id": "E22345678901234",
                        "roomId": "R12345678901234",
                        "actorId": "Ureply",
                        "createdAt": "2026-09-19T10:01:00Z",
                        "messagePosted": {
                            "message": {
                                "id": "E22345678901234",
                                "roomId": "R12345678901234",
                                "actorId": "Ureply",
                                "threadRootEventId": "E12345678901234",
                                "createdAt": "2026-09-19T10:01:00Z",
                                "body": "Reply body",
                            }
                        },
                    },
                ],
                "includes": {
                    "users": {
                        "Uroot": {"id": "Uroot", "displayName": "Root"},
                        "Ureply": {"id": "Ureply", "displayName": "Reply"},
                    }
                },
                "page": {"hasOlder": False, "startCursor": ""},
            }
        return httpx.Response(200, json=payload, request=request)

    monkeypatch.setenv("CHATTO_THREADDUMP_SERVER_URL", "https://api.example.test")
    monkeypatch.setenv("CHATTO_THREADDUMP_API_KEY", "test-key")
    real_client = httpx.Client
    monkeypatch.setattr(
        httpx,
        "Client",
        lambda **kwargs: real_client(transport=httpx.MockTransport(handler), **kwargs),
    )

    output = tmp_path / "bundle"
    assert (
        main(
            [
                "https://api.example.test/chat/-/R12345678901234/E22345678901234",
                str(output),
            ]
        )
        == 0
    )
    assert len(requests) == 2
    assert json.loads(requests[1].content) == {
        "roomId": "R12345678901234",
        "threadRootEventId": "E12345678901234",
        "limit": 500,
    }
    content = (output / "_index.md").read_text(encoding="utf-8")
    assert content.index("Root body") < content.index("Reply body")


def test_rejects_a_room_lookup_that_returns_another_message(
    monkeypatch, tmp_path
) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "message": {
                    "id": "E12345678901234",
                    "roomId": "R12345678901234",
                }
            },
            request=request,
        )

    monkeypatch.setenv("CHATTO_THREADDUMP_SERVER_URL", "https://api.example.test")
    monkeypatch.setenv("CHATTO_THREADDUMP_API_KEY", "test-key")
    real_client = httpx.Client
    monkeypatch.setattr(
        httpx,
        "Client",
        lambda **kwargs: real_client(transport=httpx.MockTransport(handler), **kwargs),
    )

    output = tmp_path / "bundle"
    assert (
        main(
            [
                "https://frontend.example.test/chat/api.example.test/R12345678901234/m/E22345678901234",
                str(output),
            ]
        )
        == 1
    )
    assert not output.exists()


def test_rejects_inconsistent_message_event_without_publishing(
    monkeypatch, tmp_path
) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "events": [
                    {
                        "id": "E12345678901234",
                        "roomId": "R12345678901234",
                        "actorId": "Uroot",
                        "createdAt": "2026-09-19T10:00:00Z",
                        "messagePosted": {
                            "message": {
                                "id": "E12345678901234",
                                "roomId": "R12345678901234",
                                "actorId": "Ureply",
                                "createdAt": "2026-09-19T10:00:00Z",
                                "body": "body",
                            }
                        },
                    }
                ],
                "page": {"hasOlder": False, "startCursor": ""},
            },
            request=request,
        )

    monkeypatch.setenv("CHATTO_THREADDUMP_SERVER_URL", "https://api.example.test")
    monkeypatch.setenv("CHATTO_THREADDUMP_API_KEY", "test-key")
    real_client = httpx.Client
    monkeypatch.setattr(
        httpx,
        "Client",
        lambda **kwargs: real_client(transport=httpx.MockTransport(handler), **kwargs),
    )
    output = tmp_path / "bundle"
    assert (
        main(
            [
                "https://frontend.example.test/chat/api.example.test/R12345678901234/E12345678901234/m/E22345678901234",
                str(output),
            ]
        )
        == 1
    )
    assert not output.exists()


def test_ignores_a_well_formed_non_message_event(monkeypatch, tmp_path) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "events": [
                    {
                        "id": "E32345678901234",
                        "roomId": "R12345678901234",
                        "actorId": "Uroot",
                        "createdAt": "2026-09-19T09:59:00Z",
                        "reactionAdded": {},
                    },
                    {
                        "id": "E12345678901234",
                        "roomId": "R12345678901234",
                        "actorId": "Uroot",
                        "createdAt": "2026-09-19T10:00:00Z",
                        "messagePosted": {
                            "message": {
                                "id": "E12345678901234",
                                "roomId": "R12345678901234",
                                "actorId": "Uroot",
                                "createdAt": "2026-09-19T10:00:00Z",
                                "body": "body",
                            }
                        },
                    },
                ],
                "includes": {
                    "users": {"Uroot": {"id": "Uroot", "displayName": "Root"}}
                },
                "page": {"hasOlder": False, "startCursor": ""},
            },
            request=request,
        )

    monkeypatch.setenv("CHATTO_THREADDUMP_SERVER_URL", "https://api.example.test")
    monkeypatch.setenv("CHATTO_THREADDUMP_API_KEY", "test-key")
    real_client = httpx.Client
    monkeypatch.setattr(
        httpx,
        "Client",
        lambda **kwargs: real_client(transport=httpx.MockTransport(handler), **kwargs),
    )
    output = tmp_path / "bundle"
    assert (
        main(
            [
                "https://frontend.example.test/chat/api.example.test/R12345678901234/E12345678901234/m/E22345678901234",
                str(output),
            ]
        )
        == 0
    )
    assert (output / "_index.md").exists()


def test_prepends_older_pages_and_merges_page_users(monkeypatch, tmp_path) -> None:
    requests: list[httpx.Request] = []

    def message_event(
        event_id: str, actor_id: str, timestamp: str, body: str, root: bool = False
    ):
        message = {
            "id": event_id,
            "roomId": "R12345678901234",
            "actorId": actor_id,
            "createdAt": timestamp,
            "body": body,
        }
        if not root:
            message["threadRootEventId"] = "E12345678901234"
        return {
            "id": event_id,
            "roomId": "R12345678901234",
            "actorId": actor_id,
            "createdAt": timestamp,
            "messagePosted": {"message": message},
        }

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        if len(requests) == 1:
            payload = {
                "events": [
                    message_event(
                        "E12345678901234",
                        "Uroot",
                        "2026-09-19T10:00:00Z",
                        "Root",
                        root=True,
                    ),
                    message_event(
                        "E22345678901234",
                        "Unew",
                        "2026-09-19T10:02:00Z",
                        "Newest",
                    ),
                ],
                "includes": {
                    "users": {
                        "Uroot": {"id": "Uroot", "displayName": "Root"},
                        "Unew": {"id": "Unew", "displayName": "Newest author"},
                    }
                },
                "page": {"hasOlder": True, "startCursor": "cursor-older"},
            }
        else:
            payload = {
                "events": [
                    message_event(
                        "E32345678901234",
                        "Uold",
                        "2026-09-19T10:01:00Z",
                        "Older",
                    )
                ],
                "includes": {
                    "users": {"Uold": {"id": "Uold", "displayName": "Older author"}}
                },
                "page": {"hasOlder": False, "startCursor": ""},
            }
        return httpx.Response(200, json=payload, request=request)

    monkeypatch.setenv("CHATTO_THREADDUMP_SERVER_URL", "https://api.example.test")
    monkeypatch.setenv("CHATTO_THREADDUMP_API_KEY", "test-key")
    real_client = httpx.Client
    monkeypatch.setattr(
        httpx,
        "Client",
        lambda **kwargs: real_client(transport=httpx.MockTransport(handler), **kwargs),
    )

    output = tmp_path / "bundle"
    assert (
        main(
            [
                "https://frontend.example.test/chat/api.example.test/R12345678901234/E12345678901234/m/E22345678901234",
                str(output),
            ]
        )
        == 0
    )
    assert len(requests) == 2
    assert json.loads(requests[1].content) == {
        "roomId": "R12345678901234",
        "threadRootEventId": "E12345678901234",
        "limit": 500,
        "before": "cursor-older",
    }
    content = (output / "_index.md").read_text(encoding="utf-8")
    assert content.index("Root") < content.index("Older") < content.index("Newest")
    assert "Older author" in content and "Newest author" in content


def test_rejects_missing_or_repeated_pagination_cursors(monkeypatch, tmp_path) -> None:
    responses = [
        {
            "events": [
                {
                    "id": "E12345678901234",
                    "roomId": "R12345678901234",
                    "actorId": "Uroot",
                    "createdAt": "2026-09-19T10:00:00Z",
                    "messagePosted": {
                        "message": {
                            "id": "E12345678901234",
                            "roomId": "R12345678901234",
                            "actorId": "Uroot",
                            "createdAt": "2026-09-19T10:00:00Z",
                            "body": "root",
                        }
                    },
                }
            ],
            "page": {"hasOlder": True, "startCursor": "same"},
        },
        {
            "events": [
                {
                    "id": "E22345678901234",
                    "roomId": "R12345678901234",
                    "actorId": "Ureply",
                    "createdAt": "2026-09-19T10:01:00Z",
                    "messagePosted": {
                        "message": {
                            "id": "E22345678901234",
                            "roomId": "R12345678901234",
                            "actorId": "Ureply",
                            "threadRootEventId": "E12345678901234",
                            "createdAt": "2026-09-19T10:01:00Z",
                            "body": "reply",
                        }
                    },
                }
            ],
            "page": {"hasOlder": True, "startCursor": "same"},
        },
    ]
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        payload = responses[min(len(requests) - 1, 1)]
        return httpx.Response(200, json=payload, request=request)

    monkeypatch.setenv("CHATTO_THREADDUMP_SERVER_URL", "https://api.example.test")
    monkeypatch.setenv("CHATTO_THREADDUMP_API_KEY", "test-key")
    real_client = httpx.Client
    monkeypatch.setattr(
        httpx,
        "Client",
        lambda **kwargs: real_client(transport=httpx.MockTransport(handler), **kwargs),
    )
    output = tmp_path / "bundle"
    assert (
        main(
            [
                "https://frontend.example.test/chat/api.example.test/R12345678901234/E12345678901234/m/E22345678901234",
                str(output),
            ]
        )
        == 1
    )
    assert not output.exists()


def test_rejects_a_missing_pagination_cursor(monkeypatch, tmp_path) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "events": [
                    {
                        "id": "E12345678901234",
                        "roomId": "R12345678901234",
                        "actorId": "Uroot",
                        "createdAt": "2026-09-19T10:00:00Z",
                        "messagePosted": {
                            "message": {
                                "id": "E12345678901234",
                                "roomId": "R12345678901234",
                                "actorId": "Uroot",
                                "createdAt": "2026-09-19T10:00:00Z",
                                "body": "root",
                            }
                        },
                    }
                ],
                "page": {"hasOlder": True},
            },
            request=request,
        )

    monkeypatch.setenv("CHATTO_THREADDUMP_SERVER_URL", "https://api.example.test")
    monkeypatch.setenv("CHATTO_THREADDUMP_API_KEY", "test-key")
    real_client = httpx.Client
    monkeypatch.setattr(
        httpx,
        "Client",
        lambda **kwargs: real_client(transport=httpx.MockTransport(handler), **kwargs),
    )
    output = tmp_path / "bundle"
    assert (
        main(
            [
                "https://frontend.example.test/chat/api.example.test/R12345678901234/E12345678901234/m/E22345678901234",
                str(output),
            ]
        )
        == 1
    )
    assert not output.exists()


def test_rejects_duplicate_events_across_pages(monkeypatch, tmp_path) -> None:
    def event(event_id: str, timestamp: str, root: bool = False) -> dict[str, object]:
        message = {
            "id": event_id,
            "roomId": "R12345678901234",
            "actorId": "U",
            "createdAt": timestamp,
            "body": event_id,
        }
        if not root:
            message["threadRootEventId"] = "E12345678901234"
        return {
            "id": event_id,
            "roomId": "R12345678901234",
            "actorId": "U",
            "createdAt": timestamp,
            "messagePosted": {"message": message},
        }

    calls = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        if calls == 1:
            payload = {
                "events": [
                    event("E12345678901234", "2026-09-19T10:00:00Z", root=True),
                    event("E22345678901234", "2026-09-19T10:01:00Z"),
                ],
                "page": {"hasOlder": True, "startCursor": "older"},
            }
        else:
            payload = {
                "events": [event("E22345678901234", "2026-09-19T10:01:00Z")],
                "page": {"hasOlder": False, "startCursor": ""},
            }
        return httpx.Response(200, json=payload, request=request)

    monkeypatch.setenv("CHATTO_THREADDUMP_SERVER_URL", "https://api.example.test")
    monkeypatch.setenv("CHATTO_THREADDUMP_API_KEY", "test-key")
    real_client = httpx.Client
    monkeypatch.setattr(
        httpx,
        "Client",
        lambda **kwargs: real_client(transport=httpx.MockTransport(handler), **kwargs),
    )
    output = tmp_path / "bundle"
    assert (
        main(
            [
                "https://frontend.example.test/chat/api.example.test/R12345678901234/E12345678901234/m/E22345678901234",
                str(output),
            ]
        )
        == 1
    )
    assert not output.exists()


def test_rejects_page_order_inconsistency(monkeypatch, tmp_path) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "events": [
                    {
                        "id": "E12345678901234",
                        "roomId": "R12345678901234",
                        "actorId": "U",
                        "createdAt": "2026-09-19T10:00:00Z",
                        "messagePosted": {
                            "message": {
                                "id": "E12345678901234",
                                "roomId": "R12345678901234",
                                "actorId": "U",
                                "createdAt": "2026-09-19T10:00:00Z",
                                "body": "root",
                            }
                        },
                    },
                    {
                        "id": "E22345678901234",
                        "roomId": "R12345678901234",
                        "actorId": "U",
                        "createdAt": "2026-09-19T09:00:00Z",
                        "messagePosted": {
                            "message": {
                                "id": "E22345678901234",
                                "roomId": "R12345678901234",
                                "actorId": "U",
                                "threadRootEventId": "E12345678901234",
                                "createdAt": "2026-09-19T09:00:00Z",
                                "body": "reply",
                            }
                        },
                    },
                ],
                "page": {"hasOlder": False, "startCursor": ""},
            },
            request=request,
        )

    monkeypatch.setenv("CHATTO_THREADDUMP_SERVER_URL", "https://api.example.test")
    monkeypatch.setenv("CHATTO_THREADDUMP_API_KEY", "test-key")
    real_client = httpx.Client
    monkeypatch.setattr(
        httpx,
        "Client",
        lambda **kwargs: real_client(transport=httpx.MockTransport(handler), **kwargs),
    )
    output = tmp_path / "bundle"
    assert (
        main(
            [
                "https://frontend.example.test/chat/api.example.test/R12345678901234/E12345678901234/m/E22345678901234",
                str(output),
            ]
        )
        == 1
    )
    assert not output.exists()


def test_hydrates_missing_authors_in_first_seen_batches_of_100(
    monkeypatch, tmp_path
) -> None:
    batch_requests: list[list[str]] = []
    root_id = "E12345678901234"

    def event(index: int) -> dict[str, object]:
        event_id = root_id if index == 0 else f"E{index + 1:014d}"
        actor_id = f"U{index}"
        timestamp = f"2026-09-19T10:{index // 60:02d}:{index % 60:02d}Z"
        message = {
            "id": event_id,
            "roomId": "R12345678901234",
            "actorId": actor_id,
            "createdAt": timestamp,
            "body": f"body {index}",
        }
        if index:
            message["threadRootEventId"] = root_id
        return {
            "id": event_id,
            "roomId": "R12345678901234",
            "actorId": actor_id,
            "createdAt": timestamp,
            "messagePosted": {"message": message},
        }

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("ThreadService/GetThreadEvents"):
            payload = {
                "events": [event(index) for index in range(101)],
                "page": {"hasOlder": False, "startCursor": ""},
            }
        else:
            user_ids = json.loads(request.content)["userIds"]
            batch_requests.append(user_ids)
            payload = {
                "users": [
                    {
                        "user": {
                            "id": user_id,
                            "displayName": f"Author {user_id[1:]}",
                        }
                    }
                    for user_id in user_ids
                ]
            }
        return httpx.Response(200, json=payload, request=request)

    monkeypatch.setenv("CHATTO_THREADDUMP_SERVER_URL", "https://api.example.test")
    monkeypatch.setenv("CHATTO_THREADDUMP_API_KEY", "test-key")
    real_client = httpx.Client
    monkeypatch.setattr(
        httpx,
        "Client",
        lambda **kwargs: real_client(transport=httpx.MockTransport(handler), **kwargs),
    )
    output = tmp_path / "bundle"
    assert (
        main(
            [
                "https://frontend.example.test/chat/api.example.test/R12345678901234/E12345678901234/m/E22345678901234",
                str(output),
            ]
        )
        == 0
    )
    assert [len(batch) for batch in batch_requests] == [100, 1]
    assert batch_requests[0] == [f"U{index}" for index in range(100)]
    assert batch_requests[1] == ["U100"]
    assert "Author 100" in (output / "_index.md").read_text(encoding="utf-8")


def test_rejects_an_unrequested_batch_user_without_publishing(
    monkeypatch, tmp_path
) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("ThreadService/GetThreadEvents"):
            payload = {
                "events": [
                    {
                        "id": "E12345678901234",
                        "roomId": "R12345678901234",
                        "actorId": "U0",
                        "createdAt": "2026-09-19T10:00:00Z",
                        "messagePosted": {
                            "message": {
                                "id": "E12345678901234",
                                "roomId": "R12345678901234",
                                "actorId": "U0",
                                "createdAt": "2026-09-19T10:00:00Z",
                                "body": "body",
                            }
                        },
                    }
                ],
                "page": {"hasOlder": False, "startCursor": ""},
            }
        else:
            payload = {"users": [{"user": {"id": "U-other", "displayName": "Other"}}]}
        return httpx.Response(200, json=payload, request=request)

    monkeypatch.setenv("CHATTO_THREADDUMP_SERVER_URL", "https://api.example.test")
    monkeypatch.setenv("CHATTO_THREADDUMP_API_KEY", "test-key")
    real_client = httpx.Client
    monkeypatch.setattr(
        httpx,
        "Client",
        lambda **kwargs: real_client(transport=httpx.MockTransport(handler), **kwargs),
    )
    output = tmp_path / "bundle"
    assert (
        main(
            [
                "https://frontend.example.test/chat/api.example.test/R12345678901234/E12345678901234/m/E22345678901234",
                str(output),
            ]
        )
        == 1
    )
    assert not output.exists()


def test_omitted_batch_users_use_unknown_author(monkeypatch, tmp_path) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("ThreadService/GetThreadEvents"):
            payload = {
                "events": [
                    {
                        "id": "E12345678901234",
                        "roomId": "R12345678901234",
                        "actorId": "U0",
                        "createdAt": "2026-09-19T10:00:00Z",
                        "messagePosted": {
                            "message": {
                                "id": "E12345678901234",
                                "roomId": "R12345678901234",
                                "actorId": "U0",
                                "createdAt": "2026-09-19T10:00:00Z",
                                "body": "body",
                            }
                        },
                    }
                ],
                "page": {"hasOlder": False, "startCursor": ""},
            }
        else:
            payload = {"users": []}
        return httpx.Response(200, json=payload, request=request)

    monkeypatch.setenv("CHATTO_THREADDUMP_SERVER_URL", "https://api.example.test")
    monkeypatch.setenv("CHATTO_THREADDUMP_API_KEY", "test-key")
    real_client = httpx.Client
    monkeypatch.setattr(
        httpx,
        "Client",
        lambda **kwargs: real_client(transport=httpx.MockTransport(handler), **kwargs),
    )
    output = tmp_path / "bundle"
    assert (
        main(
            [
                "https://frontend.example.test/chat/api.example.test/R12345678901234/E12345678901234/m/E22345678901234",
                str(output),
            ]
        )
        == 0
    )
    content = (output / "_index.md").read_text(encoding="utf-8")
    assert "## Unknown author" in content
    assert "U0" not in content


def test_normalizes_untrusted_text_without_escaping_message_markdown(
    monkeypatch, tmp_path
) -> None:
    def message_event(
        event_id: str,
        actor_id: str,
        timestamp: str,
        message: dict[str, object],
    ) -> dict[str, object]:
        return {
            "id": event_id,
            "roomId": "R12345678901234",
            "actorId": actor_id,
            "createdAt": timestamp,
            "messagePosted": {"message": message},
        }

    def message(
        event_id: str,
        actor_id: str,
        timestamp: str,
        **extra: object,
    ) -> dict[str, object]:
        value: dict[str, object] = {
            "id": event_id,
            "roomId": "R12345678901234",
            "actorId": actor_id,
            "createdAt": timestamp,
            **extra,
        }
        if event_id != "E12345678901234":
            value["threadRootEventId"] = "E12345678901234"
        return value

    def handler(request: httpx.Request) -> httpx.Response:
        events = [
            message_event(
                "E12345678901234",
                "U1",
                "2026-09-19T10:00:00Z",
                message(
                    "E12345678901234",
                    "U1",
                    "2026-09-19T10:00:00Z",
                    body="line\r\n\t*markdown*\x1b",
                ),
            ),
            message_event(
                "E22345678901234",
                "U2",
                "2026-09-19T10:01:00Z",
                message(
                    "E22345678901234",
                    "U2",
                    "2026-09-19T10:01:00Z",
                    body="",
                ),
            ),
            message_event(
                "E32345678901234",
                "U3",
                "2026-09-19T10:02:00Z",
                message(
                    "E32345678901234",
                    "U3",
                    "2026-09-19T10:02:00Z",
                    deletedAt="2026-09-19T10:03:00Z",
                ),
            ),
            message_event(
                "E42345678901234",
                "U4",
                "2026-09-19T10:03:00Z",
                message("E42345678901234", "U4", "2026-09-19T10:03:00Z"),
            ),
        ]
        return httpx.Response(
            200,
            json={
                "events": events,
                "includes": {
                    "users": {
                        "U1": {"id": "U1", "displayName": "  Alice\n#  "},
                        "U2": {"id": "U2", "login": "  bob\t"},
                        "U3": {"id": "U3", "displayName": "Deleted"},
                        "U4": {"id": "U4", "displayName": "Unavailable"},
                    }
                },
                "page": {"hasOlder": False, "startCursor": ""},
            },
            request=request,
        )

    monkeypatch.setenv("CHATTO_THREADDUMP_SERVER_URL", "https://api.example.test")
    monkeypatch.setenv("CHATTO_THREADDUMP_API_KEY", "test-key")
    real_client = httpx.Client
    monkeypatch.setattr(
        httpx,
        "Client",
        lambda **kwargs: real_client(transport=httpx.MockTransport(handler), **kwargs),
    )
    output = tmp_path / "bundle"
    assert (
        main(
            [
                "https://frontend.example.test/chat/api.example.test/R12345678901234/E12345678901234/m/E22345678901234",
                str(output),
            ]
        )
        == 0
    )
    content = (output / "_index.md").read_text(encoding="utf-8")
    assert "Started: 2026-09-19T10:00:00Z" in content
    assert "## Alice \\#" in content
    assert "line\n\t*markdown*" in content
    assert "## @bob" in content
    assert "_[message deleted]_" in content
    assert "_[message body unavailable]_" in content
    assert "\x1b" not in content
    assert "U1" not in content


def test_downloads_and_renders_ordered_attachments_without_api_auth(
    monkeypatch, tmp_path
) -> None:
    asset_requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("ThreadService/GetThreadEvents"):
            payload = {
                "events": [
                    {
                        "id": "E12345678901234",
                        "roomId": "R12345678901234",
                        "actorId": "U",
                        "createdAt": "2026-09-19T10:00:00Z",
                        "messagePosted": {
                            "message": {
                                "id": "E12345678901234",
                                "roomId": "R12345678901234",
                                "actorId": "U",
                                "createdAt": "2026-09-19T10:00:00Z",
                                "attachments": [
                                    {
                                        "filename": "photo.png",
                                        "mimeType": "image/png",
                                        "description": "A photo",
                                        "assetUrl": {
                                            "url": "https://assets.example/photo.png?sig=fake"
                                        },
                                    },
                                    {
                                        "filename": "doc.pdf",
                                        "mimeType": "application/pdf",
                                        "description": "Read me",
                                        "assetUrl": {
                                            "url": "https://assets.example/doc.pdf?sig=fake"
                                        },
                                    },
                                ],
                            }
                        },
                    }
                ],
                "includes": {"users": {"U": {"id": "U", "displayName": "Author"}}},
                "page": {"hasOlder": False, "startCursor": ""},
            }
            return httpx.Response(200, json=payload, request=request)
        asset_requests.append(request)
        data = b"PNG" if request.url.path.endswith("photo.png") else b"PDF"
        return httpx.Response(200, content=data, request=request)

    monkeypatch.setenv("CHATTO_THREADDUMP_SERVER_URL", "https://api.example.test")
    monkeypatch.setenv("CHATTO_THREADDUMP_API_KEY", "test-key")
    real_client = httpx.Client
    monkeypatch.setattr(
        httpx,
        "Client",
        lambda **kwargs: real_client(transport=httpx.MockTransport(handler), **kwargs),
    )
    output = tmp_path / "bundle"
    assert (
        main(
            [
                "https://frontend.example.test/chat/api.example.test/R12345678901234/E12345678901234/m/E22345678901234",
                str(output),
            ]
        )
        == 0
    )
    assert (output / "photo.png").read_bytes() == b"PNG"
    assert (output / "doc.pdf").read_bytes() == b"PDF"
    content = (output / "_index.md").read_text(encoding="utf-8")
    assert "![A photo](photo.png)" in content
    assert "[doc.pdf](doc.pdf) — Read me" in content
    assert content.index("photo.png") < content.index("doc.pdf")
    assert len(asset_requests) == 2
    assert all("Authorization" not in request.headers for request in asset_requests)


def test_attachment_names_are_safe_unique_and_percent_encoded(
    monkeypatch, tmp_path
) -> None:
    asset_number = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal asset_number
        if request.url.path.endswith("ThreadService/GetThreadEvents"):
            attachments = [
                {
                    "filename": "../a #.txt",
                    "mimeType": "text/plain",
                    "assetUrl": {"url": "https://assets.example/1"},
                },
                {
                    "filename": "a #.txt",
                    "mimeType": "text/plain",
                    "assetUrl": {"url": "https://assets.example/2"},
                },
                {
                    "filename": "_index.md",
                    "mimeType": "text/plain",
                    "assetUrl": {"url": "https://assets.example/3"},
                },
                {
                    "filename": "",
                    "mimeType": "image/png\r\n",
                    "assetUrl": {"url": "https://assets.example/4"},
                },
                {
                    "filename": "dir\\attachment",
                    "mimeType": "text/plain",
                    "assetUrl": {"url": "https://assets.example/5"},
                },
            ]
            payload = {
                "events": [
                    {
                        "id": "E12345678901234",
                        "roomId": "R12345678901234",
                        "actorId": "U",
                        "createdAt": "2026-09-19T10:00:00Z",
                        "messagePosted": {
                            "message": {
                                "id": "E12345678901234",
                                "roomId": "R12345678901234",
                                "actorId": "U",
                                "createdAt": "2026-09-19T10:00:00Z",
                                "attachments": attachments,
                            }
                        },
                    }
                ],
                "includes": {"users": {"U": {"id": "U", "displayName": "Author"}}},
                "page": {"hasOlder": False, "startCursor": ""},
            }
            return httpx.Response(200, json=payload, request=request)
        asset_number += 1
        return httpx.Response(
            200, content=f"asset-{asset_number}".encode(), request=request
        )

    monkeypatch.setenv("CHATTO_THREADDUMP_SERVER_URL", "https://api.example.test")
    monkeypatch.setenv("CHATTO_THREADDUMP_API_KEY", "test-key")
    real_client = httpx.Client
    monkeypatch.setattr(
        httpx,
        "Client",
        lambda **kwargs: real_client(transport=httpx.MockTransport(handler), **kwargs),
    )
    output = tmp_path / "bundle"
    assert (
        main(
            [
                "https://frontend.example.test/chat/api.example.test/R12345678901234/E12345678901234/m/E22345678901234",
                str(output),
            ]
        )
        == 0
    )
    assert (output / "a #.txt").exists()
    assert (output / "a #.1.txt").exists()
    assert (output / "_index.1.md").exists()
    assert (output / "attachment").exists()
    assert (output / "attachment.1").exists()
    content = (output / "_index.md").read_text(encoding="utf-8")
    assert "[a \\#.txt](a%20%23.txt)" in content
    assert "[a \\#.1.txt](a%20%23.1.txt)" in content
    assert "[_index.1.md](_index.1.md)" not in content
    assert "_index.1.md" in content
    assert "../" not in content


def test_existing_output_blocks_network_without_force(monkeypatch, tmp_path) -> None:
    output = tmp_path / "bundle"
    output.mkdir()
    (output / "sentinel.txt").write_text("keep", encoding="utf-8")
    calls = 0

    def client(**kwargs):
        nonlocal calls
        calls += 1
        raise AssertionError("existing output must be checked before networking")

    monkeypatch.setenv("CHATTO_THREADDUMP_SERVER_URL", "https://api.example.test")
    monkeypatch.setenv("CHATTO_THREADDUMP_API_KEY", "test-key")
    monkeypatch.setattr(httpx, "Client", client)
    assert (
        main(
            [
                "https://frontend.example.test/chat/api.example.test/R12345678901234/E12345678901234/m/E22345678901234",
                str(output),
            ]
        )
        == 1
    )
    assert calls == 0
    assert (output / "sentinel.txt").read_text(encoding="utf-8") == "keep"


def test_force_replaces_existing_output_after_success(monkeypatch, tmp_path) -> None:
    output = tmp_path / "bundle"
    output.mkdir()
    (output / "sentinel.txt").write_text("remove", encoding="utf-8")

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "events": [
                    {
                        "id": "E12345678901234",
                        "roomId": "R12345678901234",
                        "actorId": "U",
                        "createdAt": "2026-09-19T10:00:00Z",
                        "messagePosted": {
                            "message": {
                                "id": "E12345678901234",
                                "roomId": "R12345678901234",
                                "actorId": "U",
                                "createdAt": "2026-09-19T10:00:00Z",
                                "body": "new",
                            }
                        },
                    }
                ],
                "includes": {"users": {"U": {"id": "U", "displayName": "Author"}}},
                "page": {"hasOlder": False, "startCursor": ""},
            },
            request=request,
        )

    monkeypatch.setenv("CHATTO_THREADDUMP_SERVER_URL", "https://api.example.test")
    monkeypatch.setenv("CHATTO_THREADDUMP_API_KEY", "test-key")
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
                "https://frontend.example.test/chat/api.example.test/R12345678901234/E12345678901234/m/E22345678901234",
                str(output),
            ]
        )
        == 0
    )
    assert not (output / "sentinel.txt").exists()
    assert "new" in (output / "_index.md").read_text(encoding="utf-8")


def test_failed_force_export_preserves_existing_output(monkeypatch, tmp_path) -> None:
    output = tmp_path / "bundle"
    output.mkdir()
    (output / "sentinel.txt").write_text("keep", encoding="utf-8")

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(500, request=request)

    monkeypatch.setenv("CHATTO_THREADDUMP_SERVER_URL", "https://api.example.test")
    monkeypatch.setenv("CHATTO_THREADDUMP_API_KEY", "test-key")
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
                "https://frontend.example.test/chat/api.example.test/R12345678901234/E12345678901234/m/E22345678901234",
                str(output),
            ]
        )
        == 1
    )
    assert (output / "sentinel.txt").read_text(encoding="utf-8") == "keep"


@pytest.mark.parametrize(
    "asset_url",
    [
        None,
        {"url": "http://assets.example/file"},
        {"url": "https://user:secret@assets.example/file"},
    ],
)
def test_rejects_unusable_attachment_urls_without_publishing(
    monkeypatch, tmp_path, asset_url
) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        payload = {
            "events": [
                {
                    "id": "E12345678901234",
                    "roomId": "R12345678901234",
                    "actorId": "U",
                    "createdAt": "2026-09-19T10:00:00Z",
                    "messagePosted": {
                        "message": {
                            "id": "E12345678901234",
                            "roomId": "R12345678901234",
                            "actorId": "U",
                            "createdAt": "2026-09-19T10:00:00Z",
                            "attachments": [
                                {
                                    "filename": "file.txt",
                                    "mimeType": "text/plain",
                                    "assetUrl": asset_url,
                                }
                            ],
                        }
                    },
                }
            ],
            "includes": {"users": {"U": {"id": "U", "displayName": "Author"}}},
            "page": {"hasOlder": False, "startCursor": ""},
        }
        return httpx.Response(200, json=payload, request=request)

    monkeypatch.setenv("CHATTO_THREADDUMP_SERVER_URL", "https://api.example.test")
    monkeypatch.setenv("CHATTO_THREADDUMP_API_KEY", "test-key")
    real_client = httpx.Client
    monkeypatch.setattr(
        httpx,
        "Client",
        lambda **kwargs: real_client(transport=httpx.MockTransport(handler), **kwargs),
    )
    output = tmp_path / "bundle"
    assert (
        main(
            [
                "https://frontend.example.test/chat/api.example.test/R12345678901234/E12345678901234/m/E22345678901234",
                str(output),
            ]
        )
        == 1
    )
    assert not output.exists()


def test_redirected_or_late_failed_asset_preserves_existing_output(
    monkeypatch, tmp_path
) -> None:
    output = tmp_path / "bundle"
    output.mkdir()
    (output / "sentinel.txt").write_text("keep", encoding="utf-8")
    asset_requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("ThreadService/GetThreadEvents"):
            payload = {
                "events": [
                    {
                        "id": "E12345678901234",
                        "roomId": "R12345678901234",
                        "actorId": "U",
                        "createdAt": "2026-09-19T10:00:00Z",
                        "messagePosted": {
                            "message": {
                                "id": "E12345678901234",
                                "roomId": "R12345678901234",
                                "actorId": "U",
                                "createdAt": "2026-09-19T10:00:00Z",
                                "attachments": [
                                    {
                                        "filename": "first.txt",
                                        "mimeType": "text/plain",
                                        "assetUrl": {
                                            "url": "https://assets.example/first"
                                        },
                                    },
                                    {
                                        "filename": "second.txt",
                                        "mimeType": "text/plain",
                                        "assetUrl": {
                                            "url": "https://assets.example/second"
                                        },
                                    },
                                ],
                            }
                        },
                    }
                ],
                "includes": {"users": {"U": {"id": "U", "displayName": "Author"}}},
                "page": {"hasOlder": False, "startCursor": ""},
            }
            return httpx.Response(200, json=payload, request=request)
        asset_requests.append(request)
        if request.url.path.endswith("second"):
            return httpx.Response(
                302,
                headers={"location": "https://other.example/redirected?sig=fake"},
                request=request,
            )
        return httpx.Response(200, content=b"first", request=request)

    monkeypatch.setenv("CHATTO_THREADDUMP_SERVER_URL", "https://api.example.test")
    monkeypatch.setenv("CHATTO_THREADDUMP_API_KEY", "test-key")
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
                "https://frontend.example.test/chat/api.example.test/R12345678901234/E12345678901234/m/E22345678901234",
                str(output),
            ]
        )
        == 1
    )
    assert len(asset_requests) == 2
    assert (output / "sentinel.txt").read_text(encoding="utf-8") == "keep"
    assert not (output / "first.txt").exists()


@pytest.mark.parametrize(
    "status, diagnostic",
    [
        (401, "authentication failure"),
        (403, "permission denied"),
        (404, "resource not found"),
        (500, "server failure"),
    ],
)
def test_classifies_http_failures_without_private_details(
    monkeypatch, tmp_path, capsys, status, diagnostic
) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(status, content=b"private response body", request=request)

    monkeypatch.setenv("CHATTO_THREADDUMP_SERVER_URL", "https://api.example.test")
    monkeypatch.setenv("CHATTO_THREADDUMP_API_KEY", "test-key")
    real_client = httpx.Client
    monkeypatch.setattr(
        httpx,
        "Client",
        lambda **kwargs: real_client(transport=httpx.MockTransport(handler), **kwargs),
    )
    assert (
        main(
            [
                "https://frontend.example.test/chat/api.example.test/R12345678901234/E12345678901234/m/E22345678901234",
                str(tmp_path / "bundle"),
            ]
        )
        == 1
    )
    captured = capsys.readouterr()
    assert diagnostic in captured.err
    assert "private response body" not in captured.err
    assert "test-key" not in captured.err
    assert captured.out == ""


def test_classifies_connectrpc_and_json_failures(monkeypatch, tmp_path, capsys) -> None:
    responses = [
        httpx.Response(
            400,
            json={"code": "permission_denied", "message": "private details"},
        ),
        httpx.Response(200, content=b"not-json"),
    ]

    def handler(request: httpx.Request) -> httpx.Response:
        response = responses.pop(0)
        response.request = request
        return response

    monkeypatch.setenv("CHATTO_THREADDUMP_SERVER_URL", "https://api.example.test")
    monkeypatch.setenv("CHATTO_THREADDUMP_API_KEY", "test-key")
    real_client = httpx.Client
    monkeypatch.setattr(
        httpx,
        "Client",
        lambda **kwargs: real_client(transport=httpx.MockTransport(handler), **kwargs),
    )
    args = [
        "https://frontend.example.test/chat/api.example.test/R12345678901234/E12345678901234/m/E22345678901234",
        str(tmp_path / "bundle"),
    ]
    assert main(args) == 1
    captured = capsys.readouterr()
    assert "ConnectRPC failure" in captured.err
    assert "private details" not in captured.err
    assert main(args) == 1
    captured = capsys.readouterr()
    assert "malformed JSON response" in captured.err


def test_classifies_network_timeouts(monkeypatch, tmp_path, capsys) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ReadTimeout("private timeout details", request=request)

    monkeypatch.setenv("CHATTO_THREADDUMP_SERVER_URL", "https://api.example.test")
    monkeypatch.setenv("CHATTO_THREADDUMP_API_KEY", "test-key")
    real_client = httpx.Client
    monkeypatch.setattr(
        httpx,
        "Client",
        lambda **kwargs: real_client(transport=httpx.MockTransport(handler), **kwargs),
    )
    assert (
        main(
            [
                "https://frontend.example.test/chat/api.example.test/R12345678901234/E12345678901234/m/E22345678901234",
                str(tmp_path / "bundle"),
            ]
        )
        == 1
    )
    captured = capsys.readouterr()
    assert "network timeout" in captured.err
    assert "private timeout details" not in captured.err


def test_accepts_live_chatto_page_envelope(monkeypatch, tmp_path) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "page": {
                    "events": [
                        {
                            "id": "E12345678901234",
                            "actorId": "U",
                            "createdAt": "2026-09-19T10:00:00Z",
                            "messagePosted": {
                                "message": {
                                    "id": "E12345678901234",
                                    "roomId": "R12345678901234",
                                    "actorId": "U",
                                    "createdAt": "2026-09-19T10:00:00Z",
                                    "body": "body",
                                }
                            },
                        }
                    ],
                    "includes": {"users": {"U": {"id": "U", "displayName": "Author"}}},
                    "startCursor": "opaque-start",
                    "endCursor": "opaque-end",
                }
            },
            request=request,
        )

    monkeypatch.setenv("CHATTO_THREADDUMP_SERVER_URL", "https://api.example.test")
    monkeypatch.setenv("CHATTO_THREADDUMP_API_KEY", "test-key")
    real_client = httpx.Client
    monkeypatch.setattr(
        httpx,
        "Client",
        lambda **kwargs: real_client(transport=httpx.MockTransport(handler), **kwargs),
    )
    output = tmp_path / "bundle"
    assert (
        main(
            [
                "https://frontend.example.test/chat/api.example.test/R12345678901234/E12345678901234/m/E22345678901234",
                str(output),
            ]
        )
        == 0
    )

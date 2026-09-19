import json

import httpx

from chatto_threaddump import main


def test_console_entry_point_exists() -> None:
    assert callable(main)


def test_exports_an_explicit_thread_page(monkeypatch, tmp_path, capsys) -> None:
    requests: list[httpx.Request] = []

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
    monkeypatch.setattr(
        httpx,
        "Client",
        lambda **kwargs: real_client(transport=httpx.MockTransport(handler), **kwargs),
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

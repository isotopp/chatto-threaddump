# Chatto thread dump

`chatto-threaddump` is a read-only command-line program that exports one
complete Chatto thread as a deterministic directory bundle: `_index.md` plus
local copies of its attachments. It targets Chatto `v0.5.0-beta.1`'s unary
ConnectRPC JSON API.

## Installation

Python 3.14 and [uv](https://docs.astral.sh/uv/) are required.

Install the command from a checkout:

```console
uv tool install .
```

For development:

```console
uv sync
uv run chatto-threaddump --help
```

## Configuration and bot permissions

Create a dedicated bot in **Server Admin -> Bots**. Join it only to rooms that
may be exported and grant room-scoped `message.read`. Do not grant posting,
editing, deletion, following, or administrative permissions. The bot owner
must also have effective read permission.

Set the exact trusted HTTPS origin that issued the bot key and the key itself:

```console
export CHATTO_THREADDUMP_SERVER_URL=https://chatto.example.com
export CHATTO_THREADDUMP_API_KEY=replace-with-the-bot-api-key
```

Configuration may instead be stored in `.env` in the working directory. If
that file is absent, `~/.chatto-threaddump.env` is used. Process environment
values always take precedence, and neither file is required when both values
are already exported.

```dotenv
CHATTO_THREADDUMP_SERVER_URL=https://chatto.example.com
CHATTO_THREADDUMP_API_KEY=replace-with-the-bot-api-key
```

Keep both files out of version control. Never put the API key in command
arguments, logs, screenshots, or test fixtures. Rotate it by creating a
replacement key, confirming a read, and revoking the old key.

## Usage

Pass exactly one absolute Chatto URL and one output directory:

```console
chatto-threaddump \
  https://chat.chatto.run/chat/chatto.koehntopp.de/RXOVJqrHUmYtnTO/m/ExMuReQy3a3Bk0O \
  exports/thread
```

The direct-server browser form is also supported:

```console
chatto-threaddump \
  https://chatto.koehntopp.de/chat/-/RXOVJqrHUmYtnTO/ExMuReQy3a3Bk0O \
  exports/thread
```

Use `--timeout SECONDS` (or `-t`) for a positive finite network timeout; the
default is five seconds. Use `--force` (or `-f`) to replace an existing output
directory, but only after a complete export is ready.

## Output and failures

The bundle contains `_index.md` with the root and replies in chronological
order, resolved author labels, normalized text, and local attachment links.
Images use Markdown image links; other files use Markdown file links. Message
bodies are not fetched for previews or links.

Without `--force`, an existing output directory is left unchanged. With
`--force`, a successful export replaces it as a whole; an API, pagination, or
attachment failure restores the previous directory. A failed export never
publishes a partial transcript or an attachment reference to a missing file.

Success produces no stdout. Failures produce a concise stderr diagnostic and a
nonzero exit status. Diagnostics never include the API key, signed attachment
URLs, or private response bodies. Requests use HTTPS, do not follow redirects,
and honor the standard proxy and custom-CA environment supported by `httpx`.

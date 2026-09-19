# Chatto thread dump

`chatto-threaddump` is a read-only command-line program that exports one
complete Chatto thread as deterministic Markdown. It writes the transcript to
standard output, so it can be redirected to a file or piped to another Unix
command.

The CLI is currently under development against Chatto `v0.5.0-beta.1`.

## Installation

Python 3.14 and [uv](https://docs.astral.sh/uv/) are required.

Install the command from a local checkout:

```console
uv tool install .
```

For development, install all runtime and development dependencies instead:

```console
uv sync
```

## Configuration

Create a dedicated Chatto bot in **Server Admin -> Bots**, join it only to
rooms that may be exported, and grant it room-scoped `message.read`. The bot's
owner must also have effective read permission. Do not grant posting or
administrative permissions.

Configure the exact trusted HTTPS origin that issued the bot key and the key
itself:

```console
export CHATTO_THREADDUMP_SERVER_URL=https://chatto.example.com
export CHATTO_THREADDUMP_API_KEY=replace-with-the-bot-api-key
```

The same variables may be stored in a `.env` file in the working directory:

```dotenv
CHATTO_THREADDUMP_SERVER_URL=https://chatto.example.com
CHATTO_THREADDUMP_API_KEY=replace-with-the-bot-api-key
```

Keep `.env` out of version control. Never place the key in command arguments,
logs, screenshots, or test fixtures. To rotate it, create and configure a new
key, verify an export, and revoke the old key.

## Usage

Pass exactly one absolute Chatto message or thread URL:

```console
chatto-threaddump \
  https://chat.example.com/chat/chatto.example.com/RXOVJqrHUmYtnTO/m/ExMuReQy3a3Bk0O \
  > thread.md
```

From a development checkout, use `uv run chatto-threaddump` instead.

The command writes only a complete Markdown transcript to stdout and sends
diagnostics to stderr. It exits with status 0 on success and nonzero for
invalid input, missing credentials, access failures, malformed responses, or
incomplete pagination. It never downloads attachments or follows links from
message bodies.

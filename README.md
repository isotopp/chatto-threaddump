# Chatto thread dump bot

`chatto-threaddump` is a long-running Chatto bot for exporting complete threads.
It listens for direct mentions of the bot. A request is a direct mention whose
message contains the standalone word `dump` (case-insensitive), for example
`@thread-dumper dump this thread`. The request must be made inside a thread.

For a valid request, the bot checks the sender's assigned role, replies to the
request message to acknowledge queue admission, reads the complete thread, and
creates a ZIP containing `_index.md` and downloaded message attachments. It
uploads that ZIP and publishes a top-level message in the same channel:

```text
Dump of Thread "{first line of the thread starter message}"
```

The post has the ZIP attached. This publication is intentional: the published
archive is available to people who can read the channel. The original thread
remains in place. The user who requested the dump does not need to be a room
member; Chatto still requires the bot itself to be a member of a room to read
its thread, upload the archive, and publish the result.

## Install and run

Python 3.14 and [uv](https://docs.astral.sh/uv/) are required. To install the
command from a checkout:

```console
uv tool install .
```

Create a runtime directory, copy [`.env.example`](.env.example) to `.env`, and
edit its values. Run the command with that directory as its working directory:

```console
cd /path/to/chatto-threaddump-runtime
chatto-threaddump
```

The process listens until it receives SIGTERM or Ctrl-C. `--help` prints local
usage and the required setting names without reading configuration or contacting
Chatto. The development checkout can run the same executable with:

```console
uv sync
uv run chatto-threaddump --help
uv run chatto-threaddump
```

The bot reads `.env` from the current working directory with `python-dotenv`.
Values already present in the process environment take precedence. It does not
load a home-directory fallback. Keep the real `.env` private and never commit a
real API key.

## Configuration

`.env.example` contains safe placeholders for the required settings:

| Setting | Meaning |
| --- | --- |
| `CHATTO_THREADDUMP_SERVER_URL` | The trusted HTTPS Chatto origin that issued the bot API key. |
| `CHATTO_THREADDUMP_API_KEY` | The bot API key. It is sent only to that configured Chatto origin. |
| `CHATTO_THREADDUMP_ROLE` | Exact Chatto role **name** required of a requester. Matching is case-sensitive; this is not a role ID. |
| `CHATTO_THREADDUMP_QUEUE_LENGTH` | Positive number of dump requests that may wait in memory. One request is processed at a time. |

The role gate applies to the human who sent the mention. Chatto API grants are
separate: passing the role check does not grant the bot access to a room, and
bot API permissions do not make a requester eligible.

## Chatto access and required grants

The permission requirements below were checked against the local Chatto source
tag `v0.5.0-beta.10` (commit
`ef35cd000259c243f3c377227bd6007d5b750647`), rather than inferred from the
older command-line exporter:

- **Read the thread:** the bot must be a member of the room and have `message.read`
  or `message.read-interactions` with an active interaction relationship to
  that thread. A direct mention can establish that relationship. The broad
  `message.read` grant includes the interaction-read permission.
- **Upload the ZIP:** the bot must be a member of the room and have
  `message.attach`. Chatto's upload authorization checks this grant; an upload
  does not itself require thread-post permission.
- **Publish the top-level result:** the bot must be a member of the room and
  have `message.post` for that room. The bot posts the result as a new channel
  message, with no thread root or reply attribution.

These requirements are defined by beta.10's `proto/chatto/api/v1/threads.proto`
(`GetThreadEvents`), `proto/chatto/api/v1/messages.proto` (`CreateMessage`), and
`cli/internal/core/asset_uploads.go`/`can.go` (`message.attach`). The permission
catalog and bot-specific role behavior are in `cli/internal/core/permission.go`
and `docs/fdr/FDR-038-bot-accounts.md`; the direct-mention interaction
relationship is specified in `docs/fdr/FDR-039-message-access-and-interactions.md`.
The tagged upload authorization uses `CanAttachFiles` to check `message.attach`;
the tagged `TestAssetUploadServiceDoesNotRequireThreadPostPermission` test also
confirms that uploading does not require thread-post permission. The bot makes no
room-join, membership, or permission changes. Add it to rooms and configure its
grants separately.

The bot uses authenticated `UserService.GetUser` directory data to check the
requester's role names. It compares the stable role name exactly, including
case; Chatto includes the virtual `everyone` role for human users, so configuring
`everyone` allows every human requester through this role gate. This check is
separate from bot API authorization and does not give the bot role-management
permissions.

## Requests and results

- A direct mention without the standalone word `dump` receives help. For a
  `dump` command, the requester must have the exact configured role; only then
  does the bot check that the command is inside a thread.
- A role failure, top-level command, full queue, or failed dump receives a
  specific reply to the original request message. An accepted request receives
  an acknowledgement; the top-level ZIP post is the success notification.
- Requests are independent. The in-memory queue is bounded by
  `CHATTO_THREADDUMP_QUEUE_LENGTH`; one request is active while up to that many
  further requests wait. A full queue is reported to the requester.
- Each accepted job reads the whole thread and downloads every included image
  or file attachment. `_index.md` contains the chronological transcript and
  links to the local asset copies in the ZIP.
- The bot does not retry failed API calls or resume a request after restart.
  Realtime reception is live-only; events sent while the bot is stopped are
  lost. A failed or incomplete read/download/archive is never uploaded. If a
  Chatto call has an uncertain result, the bot still does not retry it.

## Temporary files and limits

The bot creates `./tmpdir` below its working directory. Each request gets a
unique temporary parent containing `thread_<starter Unix timestamp>/`, the
transcript, downloaded assets, and the ZIP. The local request directory is
removed after a handled success or failure and during cooperative shutdown.
An abrupt process kill can leave temporary files behind; startup does not replay
jobs or recover prior temporary work. The uploaded archive remains attached to
the published Chatto message according to the server's asset lifecycle.

The bot has no additional page-count or bundle-size cap. It relies on the
existing Chatto API and server limits, including attachment upload limits and
room permissions. The queue is the bot's only configured workload bound.

## Development checks

Install dependencies and run the full local suite:

```console
uv sync
uv run ruff check --fix
uv run ruff format
uv run ty check
uv run pytest
```

Tests use generated beta.10 protobuf messages, fake transports, fake credentials,
and temporary directories. They must not contact a live Chatto server.

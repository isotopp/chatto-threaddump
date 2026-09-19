# Chatto thread dump

## Epic goal

Provide a small, read-only command-line program that accepts a Chatto message or
thread URL and an output directory, then writes the complete thread as an
LLM-friendly Markdown source document with local copies of its attachments. The
program is intended for operator-controlled exports from rooms that a dedicated
bot has explicitly been allowed to read.

The initial repository is an otherwise empty Python 3.14 `uv` project with the
console entry point `chatto-threaddump`. Implement it as a stateless one-shot
CLI using Chatto's unary ConnectRPC JSON interface over HTTPS.

## US-1: Export one Chatto thread as Markdown

As a Chatto operator, I want to pass a Chatto message or thread URL and output
directory to `chatto-threaddump`, so that I get a self-contained page bundle
that an LLM can use as source material for a blog article.

Example usage:
```
$ uv run chatto-threaddump 'https://chatto.koehntopp.de/chat/-/RXOVJqrHUmYtnTO/m/ExMuReQy3a3Bk0O' out
```

### Public interface

```console
CHATTO_THREADDUMP_SERVER_URL=https://chatto.koehntopp.de \
CHATTO_THREADDUMP_API_KEY=... \
chatto-threaddump [--force] [--timeout SECONDS] CHATTO_URL OUTPUT_DIRECTORY
```

- The command accepts exactly two positional arguments: one absolute Chatto URL
  and one output-directory path.
- Create the output directory and missing parents when necessary. If the output
  directory already exists, fail without changing it unless `--force` or `-f`
  is present. With `--force`, replace the existing directory and all its
  contents only after the complete replacement bundle is ready. An existing
  non-directory path is always an error.
- Apply a five-second network timeout by default. `--timeout SECONDS` or
  `-t SECONDS` accepts a positive finite number and applies it to API and
  attachment requests.
- Write the transcript to `OUTPUT_DIRECTORY/_index.md` and downloaded
  attachments to separate files in the same directory.
- Do not write normal output to stdout. Diagnostics are written to stderr.
- Success returns exit status 0. Invalid input, authentication or authorization
  failure, an inaccessible message or attachment, malformed API data, or an
  incomplete page traversal returns nonzero and does not publish a knowingly
  incomplete `_index.md`.
- The credential is read from `CHATTO_THREADDUMP_API_KEY`. It must not be
  accepted as a command-line argument, included in output, or included in error
  messages.
- The trusted Chatto API origin is read from `CHATTO_THREADDUMP_SERVER_URL`.
  The credential may be sent only to this configured origin.

### Configuration resolution

- Load a `.env` file from the working directory without overriding variables
  already present in the process environment. If no working-directory `.env`
  exists, try `~/.chatto-threaddump.env` with the same precedence rule. The
  absence of both files is valid when the process environment supplies the
  required variables.
- Require both configuration variables to be present and non-empty before
  parsing the input URL or making a request.
- Require `CHATTO_THREADDUMP_SERVER_URL` to be an absolute HTTPS origin: no
  user information, query, fragment, or path other than `/`. Compare origins
  by normalized scheme, DNS hostname, and effective port, so an omitted HTTPS
  port and an explicit port `443` are equivalent.
- Do not follow HTTP redirects. An authenticated request must never be
  redirected to another origin.
- Honor standard proxy and custom-CA environment variables through `httpx`'s
  default `trust_env=True` behavior.

### Supported Chatto links

Support the canonical Chatto frontend forms:

```text
/chat/<server-segment>/<room-id>/m/<message-id>
/chat/<server-segment>/<room-id>/<thread-root-event-id>/m/<message-id>
/chat/-/<room-id>/<message-id>
```

The third form is the direct-server room-message URL produced by the Chatto
browser UI. The input must use HTTPS and match one of these paths exactly.
Reject user information, query strings, fragments, percent-encoded path
separators, trailing path components, and empty path segments.

For example:

```text
https://chat.chatto.run/chat/chatto.koehntopp.de/RXOVJqrHUmYtnTO/m/ExMuReQy3a3Bk0O
```

This example identifies:

```text
API origin: https://chatto.koehntopp.de
room ID:    RXOVJqrHUmYtnTO
message ID: ExMuReQy3a3Bk0O
```

The same URL may also be given as

```text
https://chatto.koehntopp.de/chat/-/RXOVJqrHUmYtnTO/ExMuReQy3a3Bk0O
```

or

```
https://chatto.koehntopp.de/chat/-/RXOVJqrHUmYtnTO/m/ExMuReQy3a3Bk0O
```

This example identifies the same items:

```text
API origin: https://chatto.koehntopp.de
room ID:    RXOVJqrHUmYtnTO
message ID: ExMuReQy3a3Bk0O
```

Validate the URL structure, identifiers, and server identity before making any
request. A non-`-` server segment must be an ASCII DNS hostname without a port;
require that hostname to match `CHATTO_THREADDUMP_SERVER_URL` and require the
configured origin to use the default HTTPS port 443. A `-` segment denotes the
URL's complete origin, including an explicit non-default port when present,
which must match the configured origin. Use the configured origin for all API
requests; never derive the destination of a bearer-authenticated request solely
from untrusted input. Current Chatto identifiers use these forms:

```text
channel room: R followed by 14 ASCII letters or digits
DM room:      14 lowercase hexadecimal characters
event:        E followed by 14 ASCII letters or digits
```

For an explicit thread-message URL, use the thread-root segment directly. For
a room-message URL, call `MessageService.GetMessage` with the room and message
IDs. If the returned message has a non-empty `threadRootEventId`, use it;
otherwise the linked message ID is the thread root. When that root message has
no `thread` summary, it is not an established thread: render the already loaded
message as a valid one-message export without calling `GetThreadEvents`. A
present `thread` summary denotes an established thread, including one with no
replies, and must be traversed normally.

### Chatto API workflow

Use these JSON ConnectRPC calls beneath the resolved API origin:

```text
POST /api/connect/chatto.api.v1.MessageService/GetMessage
POST /api/connect/chatto.api.v1.ThreadService/GetThreadEvents
POST /api/connect/chatto.api.v1.UserService/BatchGetUsers
```

Every request uses:

```text
Authorization: Bearer <bot API key>
Content-Type: application/json
Connect-Protocol-Version: 1
```

The message lookup body is:

```json
{"roomId": "<room-id>", "eventId": "<message-id>"}
```

The initial thread request is:

```json
{
  "roomId": "<room-id>",
  "threadRootEventId": "<thread-root-event-id>",
  "limit": 500
}
```

The initial thread page contains the root followed by the latest replies. When
`page.hasOlder` is true, request another page with the same room, root, and
limit plus `before: page.startCursor`. Cursor pages contain replies only. Keep
requesting older pages until `hasOlder` is false, prepend each older reply page
to the replies already collected, and emit the root exactly once. Treat cursors
as opaque, viewer-bound values; never parse, manufacture, persist, or reuse
them for another room, thread, or credential.

Each page's `events` are in chronological display order. Only
`messagePosted.message` entries belong in the transcript. Merge the
`page.includes.users` maps from all pages. Collect actor IDs that still lack a
user record and request them from `UserService.BatchGetUsers` in first-seen
batches of at most 100:

```json
{"userIds": ["<actor-id>"]}
```

Merge returned `users[].user` records, then render each author with
`displayName`, falling back to `@login`, then `Unknown author`. User IDs remain
available internally for joins and validation but must not be rendered.

### Response validation and traversal invariants

Treat a response as malformed and fail the complete export when any required
object or field has the wrong JSON type or when any of these invariants fails:

- `GetMessage.message.id` and `roomId` match the requested identifiers.
- An initial thread page contains the requested root exactly once and before
  its replies; an older cursor page does not contain the root.
- For each message event, the nested message ID, room ID, actor ID, and creation
  timestamp agree with the enclosing timeline event, and each reply names the
  requested thread root.
- Event IDs are unique across the traversal. Reject duplicate events rather
  than silently de-duplicating them.
- Events within each page remain in chronological display order, and each
  older page belongs before the replies already collected.
- A user included under a map key has the same stable user ID as that key.
- A batch user result contains only requested, unique user IDs. An omitted user
  is unresolved and uses the non-identifying author fallback.
- `hasOlder: true` is accompanied by a non-empty, previously unseen
  `startCursor`. Reject a repeated or unusable cursor.

Ignore well-formed non-message timeline events. They do not appear in the
transcript and do not relax any pagination or cursor checks.

The API does not promise an atomic snapshot across pages. The transcript is a
complete traversal as observed by that invocation; a later run is how an
operator captures activity posted after the initial latest-page read.

### Markdown transcript

`_index.md` is source material for an LLM that will write a blog article, not a
forensic event log. The output must be deterministic for the same API responses
and use this structure:

```markdown
# Chatto thread

Started: <root creation timestamp in ISO 8601 UTC>

## <resolved author name or @handle>

<message body>

<local attachment links, when present>
```

Repeat the author section for the root first and then every reply oldest first.
Show the root creation timestamp once as the thread start; do not show
per-message timestamps. Do not render room IDs, event IDs, thread-root IDs,
user IDs, attachment IDs, or other internal identifiers.

The API distinguishes an absent body from a present empty string. Preserve a
present body exactly after text normalization, including an empty body. Render
`_[message deleted]_` for a body absent with `deletedAt` and
`_[message body unavailable]_` for a body absent without it. An attachment-only
message remains visible under its author heading.

Do not fetch links or previews found inside message bodies.

### Attachment download and naming

- Download every message attachment that has an `assetUrl.url` into the output
  directory. Signed asset URLs are time-limited secrets: never write them to
  Markdown, logs, diagnostics, or persisted metadata.
- Fail the complete export when an attachment has no usable signed asset URL,
  including while media processing is incomplete or the source asset is
  unavailable. Do not publish a partial bundle or an unavailable-attachment
  placeholder.
- Never attach `CHATTO_THREADDUMP_API_KEY` to an asset request. Require an HTTPS
  asset URL and do not follow redirects.
- Preserve attachment order. Render `image/*` attachments as
  `![<description-or-filename>](<local-filename>)`; render every other MIME type
  as `[<filename>](<local-filename>)`, followed by its description when one is
  present.
- Derive each local name from the attachment filename's basename. Reject path
  traversal, remove control characters, and use `attachment` when no usable
  basename remains. Reserve `_index.md` for the transcript.
- Deduplicate names in encounter order by inserting `.1`, `.2`, and so on
  before the final suffix: `filename.txt`, `filename.1.txt`,
  `filename.2.txt`. Apply the same rule when a sanitized filename would be
  `_index.md`.
- Escape the link label and percent-encode the relative Markdown link target as
  needed. Never place an absolute local path in `_index.md`.

### Text normalization

- Convert CRLF and bare CR line endings to LF.
- Remove Unicode control characters from untrusted text except LF and tab.
- Collapse whitespace in author labels to single spaces and trim it before
  applying the `displayName`, `@login`, `Unknown author` fallback.
- Normalize attachment filenames and MIME types to one line. Preserve line
  breaks in message bodies and attachment descriptions.
- Escape untrusted metadata according to the selected Markdown layout, but do
  not escape the message body itself: the Chatto body is already Markdown.

### Authorization and operator setup

Use a dedicated bot identity such as `thread_dump_bot`:

1. In **Server Admin -> Bots**, create the bot and copy its API key once.
2. Join the bot only to rooms that may be exported.
3. Grant room-scoped `message.read` for those rooms. Do not grant posting or
   administrative permissions.
4. Ensure the bot's human owner also has effective read permission; the owner
   is the bot's permission ceiling.
5. Supply the key through `CHATTO_THREADDUMP_API_KEY` from local secret storage
   or the process environment. Never commit it to Git or place it in shell
   history, command arguments, logs, screenshots, or test fixtures.
6. Set `CHATTO_THREADDUMP_SERVER_URL` to the exact trusted HTTPS origin that
   issued the bot key.
7. Rotate by creating a replacement key, updating the secret, confirming a
   read, and revoking the old key. Revoke the key to stop exports immediately.

Room membership alone does not expose messages. Broad thread export requires
room-scoped `message.read`; interaction-scoped access is not sufficient for an
arbitrary operator-selected thread.

### Failure and safety behavior

- Use the configured network timeout and fail closed on TLS, transport,
  non-2xx, ConnectRPC, JSON, or response-shape errors.
- Do not retry automatically and do not follow redirects.
- Distinguish invalid URLs, missing credentials, authentication failure,
  permission denial, missing messages, and server failures without exposing
  the credential or private response bodies.
- Stage the complete bundle in a temporary sibling of the output directory so
  publication uses same-filesystem renames. Publish only after the complete
  traversal and every required attachment download succeeds. Without
  `--force`, never alter an existing output directory. With `--force`, move the
  existing directory aside, move the staged bundle into place, restore the old
  directory if publication fails, and remove the old directory only after the
  replacement succeeds.
- Do not impose application-level response, page-count, attachment-size, or
  total-bundle limits; rely on the explicitly trusted Chatto server's resource
  limits. Still reject an unusable or repeated cursor.
- Treat the input URL, message bodies, author data, filenames, descriptions,
  and API errors as untrusted input. A link naming a different server must fail
  before the credential is attached to any request.
- The CLI performs reads only. It must not post, edit, delete, mark as read,
  follow a thread, change membership, or change Chatto permissions.

### Acceptance criteria

1. Given the example root-message URL and authorized fake API responses, the
   command resolves `chatto.koehntopp.de`, the room ID, and the root ID, then
   writes `_index.md` with the root and all replies in chronological order.
2. Given an explicit thread-message URL, the command uses the encoded thread
   root and still includes the entire thread rather than only the linked reply.
3. Given more than 500 replies, the command follows every `before` cursor,
   orders older reply pages correctly, and writes no duplicate root or reply.
4. Given authors supplied only in different page-local `includes.users` maps,
   all available display names or `@login` handles appear in the completed
   transcript, and no user ID appears as an author fallback.
5. Given a deleted body or an attachment-only message, the event remains
   visible in the transcript under its resolved author.
6. Given an invalid URL, identifier, or server origin that differs from
   `CHATTO_THREADDUMP_SERVER_URL`, no network request is made.
7. Given any failed page or attachment download after earlier successful work,
   an existing output directory remains unchanged, no new output directory is
   published, and the command exits nonzero.
8. Fake HTTP tests observe the bearer header but never print or persist the
   test credential. Automated tests never call a live Chatto server.
9. The repository's formatter, type checker, and test suite pass under the
   Python version and `uv` workflow declared by the project.
10. Given a root message without a `thread` summary, the command emits the
    loaded root as a one-message `_index.md` without requesting a thread page.
11. Given inconsistent room, event, actor, timestamp, thread-root, user-map, or
    cursor data, the command fails without publishing a transcript.
12. Process-environment configuration takes precedence over `.env`, and no
    authenticated request follows a redirect.
13. `_index.md` contains the thread start time once, resolved author labels and
    message bodies, but no per-message timestamps or internal IDs.
14. Given image and non-image attachments, the command downloads them without
    the API authorization header and renders local image and file links.
15. Given duplicate, unsafe, empty, or `_index.md` attachment filenames, the
    command creates safe deterministic names without overwriting one attachment
    with another or allowing a path to escape the output directory.
16. Given the direct-server `/-/` browser URL without an `/m/` segment, the
    command accepts it as a room-message URL and resolves the same room and
    message as the corresponding canonical frontend URL.
17. Given authors missing from page-local includes, the command resolves them
    through bounded `BatchGetUsers` requests and still never renders a user ID.
18. Given an existing output directory without `--force`, the command exits
    nonzero without changing it. Given `--force`, a successful export replaces
    the entire directory, while a failed export preserves the old directory.
19. Network calls use a five-second timeout by default and a positive finite
    value supplied through `--timeout` or `-t` when present.
20. Given an attachment without a usable signed asset URL, the command fails
    without publishing or replacing an output bundle.
21. API and attachment clients honor the standard proxy and custom-CA
    environment trusted by `httpx`.
22. A named server segment accepts only the default HTTPS port; the `-` form
    also accepts an explicit non-default port when the input and configured
    origins match exactly.

## Documentation and source pointers

Chatto is pre-1.0, so the implementation should be checked against the target
server release without creating a speculative compatibility framework.
`v0.5.0-beta.1` contains the API described above.

- [ConnectRPC API overview](https://dev-docs.chatto.run/reference/connectrpc-api/)
- [Integrating with the Chatto API](https://dev-docs.chatto.run/guides/integrations/chatto-api/)
- [MessageService.GetMessage](https://dev-docs.chatto.run/reference/connectrpc-api/messages/#chatto-api-v1-MessageService-GetMessage)
- [ThreadService.GetThreadEvents](https://dev-docs.chatto.run/reference/connectrpc-api/threads/#chatto-api-v1-ThreadService-GetThreadEvents)
- [Timeline, message, user, and attachment types](https://dev-docs.chatto.run/reference/connectrpc-api/types/)
- [Bot accounts and least-privilege grants](https://dev-docs.chatto.run/guides/integrations/bot-accounts/)
- [Permissions and owner ceilings](https://dev-docs.chatto.run/guides/operations/permissions/)
- [API compatibility guidance](https://dev-docs.chatto.run/guides/integrations/api-compatibility/)
- [`v0.5.0-beta.1` thread timeline protobuf](https://github.com/chattocorp/chatto/blob/v0.5.0-beta.1/proto/chatto/api/v1/room_timeline.proto)
- [`v0.5.0-beta.1` message and attachment types](https://github.com/chattocorp/chatto/blob/v0.5.0-beta.1/proto/chatto/api/v1/message_types.proto)
- [`v0.5.0-beta.1` message protobuf](https://github.com/chattocorp/chatto/blob/v0.5.0-beta.1/proto/chatto/api/v1/messages.proto)
- [`v0.5.0-beta.1` thread service protobuf](https://github.com/chattocorp/chatto/blob/v0.5.0-beta.1/proto/chatto/api/v1/threads.proto)
- [`v0.5.0-beta.1` user service protobuf](https://github.com/chattocorp/chatto/blob/v0.5.0-beta.1/proto/chatto/api/v1/user_service.proto)

## Repository and workflow handover

The repository contains the Python 3.14 `uv` scaffold, runtime dependencies
`httpx` and `python-dotenv`, development dependencies `ruff`, `ty`, and
`pytest`, repository-local workflow skills, and a placeholder console entry
point. Commits `65b3fde` (`base scaffolding`) and `1fadd56` (`Vendor repository
development skills`) established that foundation; `10e360d` (`Raw user
story.`) added this epic. No product behavior has been implemented.

Prefer the standard library and these existing dependencies unless an observed
requirement justifies another dependency.

Continue with the specialization workflow:

1. Commit the reviewed `user-stories.md` without implementation changes.
2. Derive `tickets.md` in observable, TDD-sized implementation order.
3. Obtain approval of the ticket and behavior plan, then commit `tickets.md`.
4. Implement tickets in order with one behavior-focused failing test at a
   time, the minimum code to pass it, project checks, and one commit per
   completed ticket.
5. If implementation exposes new scope, update and re-approve `tickets.md`
   before continuing.

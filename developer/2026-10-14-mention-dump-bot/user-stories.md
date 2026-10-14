# Mention-driven Chatto thread dump bot

## Epic goal

Replace the existing one-shot CLI with a permanently running Chatto bot. A user
mentions the bot inside a thread and includes the whole word `dump`. If the user
has the configured role, the bot exports the complete thread and its attachments,
packages the result as a ZIP, and posts that ZIP at channel top level.

The existing implementation is reference material for API knowledge and export
behavior only. There is no backward compatibility requirement and no requirement
to retain its code, CLI arguments, or architecture. Implement the new application
in Python 3.14 using the repository's `uv` workflow.

## Scope and settled decisions

- Configuration is loaded from a dotenv file using `python-dotenv`.
- `CHATTO_THREADDUMP_ROLE` holds one stable Chatto role name. A requesting user
  must have that role; there is no administrator exemption or role hierarchy.
- An actual direct mention of the bot is required. The message must also contain
  the standalone word `dump`, case-insensitively. Display-name text alone is not
  a mention. `DuMp this` qualifies; `dumpingprices` does not.
- A mention without the command receives help. A request by a user without the
  role receives a specific role error. An authorized command at channel top
  level receives a specific thread-only error.
- Replies target the message mentioning the bot, using the reply API. Chatto
  determines their placement; whether they appear inside the thread is immaterial.
- Each request is independent. There is no deduplication, per-thread exclusion,
  cooldown, durable job history, or recovery after downtime or process restart.
- The bot runs in one Python thread, processing exports sequentially through a
  bounded Python queue. It reports queue saturation rather than accepting work
  it cannot enqueue.
- The complete thread is exported as observed during traversal, including the
  command if present in the returned thread. There is no command-time cutoff.
- The ZIP contains `_index.md` and every downloaded attachment, with the same
  observable export behavior as the current program.
- Publishing the ZIP to the channel is intentional, irrespective of differences
  between thread and channel membership. Do not add a membership comparison.
- Requests, downloads, uploads, and posts are not retried automatically.
- Deployment and Ansible integration are outside this epic. The planned hosting
  environment is background context only, not an implementation obligation.

The old read-only, stateless CLI restrictions are superseded for this epic.
Posting acknowledgments, errors, help, and ZIP attachments is explicitly in
scope. Editing or deleting messages, marking messages read, following threads,
changing membership, and changing permissions remain outside scope.

## US-1: Run a configured, permanently listening bot

As a Chatto operator, I want to run the installed bot as a permanent service,
so that eligible users can request exports without invoking a command-line tool.

### Public interface

- The installed `chatto-threaddump` executable starts the bot. The old positional
  URL/output-directory interface and CLI compatibility are not retained.
- Use `python-dotenv` to load the deployment's dotenv configuration. Keep
  already-present process-environment values authoritative.
- Retain `CHATTO_THREADDUMP_SERVER_URL` for the trusted HTTPS API origin and
  `CHATTO_THREADDUMP_API_KEY` for the dedicated bot credential.
- Add `CHATTO_THREADDUMP_ROLE` for the required stable role name and
  `CHATTO_THREADDUMP_QUEUE_LENGTH` for the bounded queue capacity.
- Require nonempty origin, credential, and role configuration and a positive
  integer queue capacity. Invalid configuration fails startup with a sanitized
  diagnostic before authenticated requests are made.
- Temporary work can live in `tmpdir/` beneath the installation directory.

### Acceptance criteria

1. Valid configuration starts a bot that listens for new realtime messages.
2. Missing or invalid required configuration prevents startup and never prints
   the credential or private server response data.
3. The bot uses the configured trusted HTTPS origin for authenticated API calls;
   bearer credentials are never sent to attachment URLs or another origin.
4. Starting after downtime does not backfill old requests. Restarting does not
   recover queued or interrupted exports or persist/reuse realtime cursors.
5. Automated tests use fake credentials, API responses, and realtime events;
   they never contact a live Chatto server.

## US-2: Recognize direct mentions and explain how to request an export

As a user mentioning the bot, I want a clear explanation when my message lacks
the export command, so that I can make a valid request.

### Observable behavior

- Use authoritative direct-mention data to identify the bot as a recipient.
  Do not infer mentions by finding its display name or login in message text.
- Evaluate the plain text of the message containing that mention for the whole
  word `dump`, ignoring case.
- A direct mention without that word receives a reply such as:
  `To dump a thread you need to be in a thread, and your message must include the word dump.`
- Messages without a direct mention are ignored. Ignore the bot's own messages
  and channel echoes so replies and exported content do not trigger a loop.
- One posted message is one request, even if it contains multiple textual
  mentions of the bot. Separate posted messages remain independent requests.

### Acceptance criteria

1. A direct mention with `dump this thread`, `dump it`, or `DuMp this` is
   recognized as a command candidate.
2. A direct mention containing only `dumpingprices` receives help, not an export.
3. Plain display-name text, an unrelated user's mention, or `dump` without a
   direct bot mention does not trigger a response or export.
4. Help uses the reply API with the mentioning message as its target.
5. The bot's own posts and channel echoes do not create requests.

## US-3: Authorize requests and require thread context

As a Chatto operator, I want only users with the configured role to trigger
exports, and only from inside threads, so that the bot follows the requested
authorization and interaction rules.

### Observable behavior

- Resolve the command author's current roles from the user directory. Check
  exact membership of `CHATTO_THREADDUMP_ROLE`, the stable role name.
- For command candidates, check the role before checking thread placement.
- A user lacking the role receives:
  `You do not have the required role {rolename}.`
- An authorized command with no thread context receives:
  `This only works inside a thread, not at the top level.`
- A top-level message does not qualify merely because it is a thread starter
  with existing replies. The command itself must be posted inside the thread.
- Authorization lookup failures fail closed. Report a sanitized failure using
  the reply API when possible; do not proceed with an export.
- Help for a mention without `dump` follows US-2; it does not enqueue an export.

### Acceptance criteria

1. A user with the configured role can submit a command inside a thread.
2. A user without the role receives the role error and no export is queued.
3. A user lacking the role who commands at top level receives the role error.
4. An authorized top-level command receives the thread-only error and no export
   is queued, even when that message starts an established thread.
5. An unsuccessful role lookup cannot grant permission.
6. All error replies target the command message through the reply API. Their
   eventual placement is left to Chatto.
7. Authorization does not compare thread membership with channel membership.

## US-4: Queue independent requests and process them sequentially

As a requesting user, I want an affirmative response when my request is accepted
or a clear queue-full error, so that I know whether the bot will handle it.

### Observable behavior

- Use a bounded Python queue with capacity `CHATTO_THREADDUMP_QUEUE_LENGTH`.
  Rely on the queue's own occupancy/full semantics; no additional product-level
  interpretation of capacity is required.
- Process accepted exports one after another in a single Python thread. The
  service must still receive mentions and issue queue-full responses while an
  export is active; blocking export work must not silently stop ingestion.
- An accepted request receives an affirmative reply to the command message.
- A request that cannot be enqueued receives a clear reply such as:
  `The thread dump queue is full. Please try again later.`
- Separate commands for the same thread are all eligible. Do not suppress a
  request based on earlier or active exports of that thread.
- Queued requests and processing state are invocation-local and disposable.

### Acceptance criteria

1. Accepted requests are processed in queue order with at most one export active.
2. Filling the queue rejects the next otherwise valid request with a queue-full
   reply and without starting its export.
3. After capacity becomes available, another valid request can be accepted.
4. Two separate commands for the same thread are processed independently.
5. Accepted requests receive affirmative replies targeting their command messages.
6. Restarting loses pending requests without replay or recovery.

## US-5: Export the full thread and all assets into an isolated bundle

As a requesting user, I want the same complete Markdown and attachment bundle
as the existing exporter produced, so that the ZIP is self-contained source
material rather than a generated summary.

### Observable behavior inherited from the existing exporter

Reimplement these behaviors without requiring retention of the existing code:

- Resolve the thread root from the command's thread context. Include the starter
  once, then every reply oldest first, following all older-page cursors.
- Preserve current response validation: consistent room/message/actor/time/root
  identities, chronological page ordering, unique events, and valid, unrepeated
  opaque cursors. Ignore well-formed non-message timeline events.
- Traverse the full thread as observed by that invocation, not an atomic snapshot
  or a command-time cutoff. The command and other bot replies may be included
  when returned by the API.
- Merge included author records and hydrate missing authors. Render display name,
  then `@login`, then `Unknown author`; never render internal IDs as fallback.
- Write `_index.md` with `# Chatto thread`, the starter's ISO 8601 UTC timestamp
  once, and an author section with each message body and local attachment links.
  Do not add per-message timestamps or internal identifiers.
- Preserve empty bodies, deleted/unavailable-body placeholders, attachment-only
  messages, normalized line endings, and removal of unsafe control characters.
- Download all attached images and files. Never fetch arbitrary links or previews
  in message text. Use local image/file links in the Markdown.
- Use safe basenames and deterministic suffixes for duplicate attachment names,
  reserve `_index.md`, and prevent paths from escaping the bundle.
- Require usable HTTPS signed attachment URLs, reject redirects, and never send
  the API credential to asset requests or persist signed URLs in the transcript.
- Fail the whole export on a failed page, malformed response, unavailable asset,
  or failed download. Do not create a knowingly incomplete archive.
- Keep the existing five-second network timeout behavior and no automatic retries.
  Preserve current reliance on the trusted server's resource limits: do not add
  application-level response, page-count, attachment-size, or bundle-size caps.
  The existing page size is 500 and author hydration batches are at most 100.

### Temporary layout

For each request create a unique temporary parent beneath the installation's
temporary location, then create a bundle named `thread_<time_t>` inside it.
Use the starter message's Unix creation timestamp for `<time_t>`. Timestamp
collisions between requests are harmless because their parents are unique.

```text
tmpdir/<unique-request-directory>/
  thread_<starter-unix-timestamp>/
    _index.md
    <downloaded assets>
  thread_<starter-unix-timestamp>.zip
```

### Acceptance criteria

1. A multi-page thread produces one starter and all replies in chronological
   order, with resolved author labels and all local attachment references.
2. The triggering command is not deliberately removed from the transcript.
3. Duplicate or unsafe filenames cannot overwrite `_index.md`, another asset,
   or a path outside the bundle.
4. Any failed page or attachment prevents ZIP publication; no partial archive
   is uploaded or posted.
5. Concurrently existing temporary parents for separate requests cannot collide,
   even when exporting the same thread with the same starter timestamp.
6. The new output has the existing exporter's observable Markdown and asset
   behavior without retaining the old CLI interface or implementation.

## US-6: Publish the ZIP at channel top level

As a requesting user, I want the exported bundle attached to a channel post,
so that everyone in the channel can download it.

### Public output

Create a ZIP containing the `thread_<time_t>/` directory and everything in it.
Upload it to the source channel with content type `application/zip`, then create
a top-level message with the uploaded ZIP attached and this text:

```text
Dump of Thread "{first line of the thread starter message}"
```

Use the starter's first line after the exporter's text normalization. Do not
generate an AI summary, add the earlier proposed thread link, substitute a
200-character excerpt, or include the entire multiline starter. The publication
has no reply target or source-thread placement; Chatto may make it a new thread
root according to channel policy.

### Acceptance criteria

1. The ZIP contains the named bundle directory, `_index.md`, and all downloaded
   assets with working relative references after extraction.
2. The post appears at source-channel top level and has the ZIP attached.
3. A multiline starter contributes only its first line to the quoted headline.
4. The ZIP is published even when thread and channel membership differ, provided
   the requester has the configured role and the bot's API permissions allow it.
5. Each separate successful request can create its own top-level ZIP post.
6. A failed upload or post is not retried, including an uncertain posting outcome.

## US-7: Report failures and remove temporary data

As a Chatto operator, I want isolated failures and prompt temporary-file cleanup,
so that failed requests do not accumulate exported private data or stop unrelated
queued requests.

### Observable behavior

- On export, ZIP creation, upload, or post failure, attempt a clear sanitized
  error reply to the command message. Do not include credentials, signed URLs,
  private response bodies, or arbitrary server exception text.
- Do not retry the failed operation or a failed error reply. If replying is
  impossible, retain only a sanitized operational diagnostic.
- After success or handled failure, delete the unique temporary parent and all
  its contents, including the Markdown, assets, staging files, and ZIP.
- Keep temporary files through the upload/post attempt, then clean up. There is
  no retained local archive; the original Chatto thread remains the source.
- A failed request does not prevent later queued requests from being processed.
- Cleanup is required on handled exit paths. Forced process termination can
  prevent cleanup; it does not introduce job recovery or request retries.

### Acceptance criteria

1. Successful publication leaves no request directory or ZIP in `tmpdir/`.
2. Export, packaging, upload, and posting failures clean up their request's files
   and attempt an error reply targeting that request's command message.
3. Error replies and logs expose neither credentials nor private response data.
4. A failed request is attempted once and a later queued request can succeed.
5. Cleanup is scoped to the request's unique parent and cannot delete another
   request's data or files outside the temporary location.

## US-8: Deployment — out of scope

Removed from this epic at the user's explicit request. Do not derive tickets,
acceptance obligations, or dependencies from deployment. No changes to
`kris-kvm`, Ansible, systemd units, or server installation are authorized by this
implementation plan. The eventual hosting environment is background only.

## API evidence and hosting background

The current repository pins `chattolib==0.5.0b10`. Target the documented Chatto
`v0.5.0-beta.10` API, checking the installed descriptors and immutable server
tag rather than inventing compatibility with other releases.

- Local server source: `/Users/kris/Source/chatto`; immutable reference
  `v0.5.0-beta.10`. Relevant protobufs include `roles.proto`, `user_service.proto`,
  `messages.proto`, and `message_types.proto` under `proto/chatto/api/v1/`.
- Stable `Role.name` is the assignment/permission identity. `UserService.GetUser`
  returns a directory member with assigned roles, avoiding a room-membership test.
- Realtime `MessagePostedEvent` exposes typed direct mentions and whole-message
  plain text. Check the direct target against the bot user ID; do not rely on
  the library's best-effort `BotMessageEvent.is_mention` substring detector.
- Thread reading, author hydration, and signed-asset download behavior can be
  studied in `src/chatto_threaddump/__init__.py` as it exists before replacement.
- The library supports ZIP attachment upload and message creation with attachment
  asset IDs. Realtime support requires its realtime dependency extra.
- `in_reply_to` identifies the reply target. The server may infer thread placement
  from that target; this is accepted behavior for help and request replies.
- Avoid helper defaults that automatically join rooms. The bot must not change
  room membership as a side effect of posting.
- Deployment references in `kris-kvm`: `roles/chatto/tasks/rss_bridge.yml`,
  `roles/chatto/templates/chatto-rss-bridge.service.j2`, and role defaults/main
  tasks. Inspect only secret handling, not existing credential contents.

## Planning notes and remaining clarification

- Q17 (command locality) did not receive an explicit answer. This document uses
  the whole mentioning message's plain text because the API supplies per-message
  mention targets rather than a command span for each textual mention. Confirm
  that `@threaddump hello, @someone dump this` is intended to qualify when reviewing
  the behavior plan; do not silently implement a mention-adjacent parser.
- Deployment configuration, release selection, and service naming are outside
  this epic. Local dotenv loading and queue-capacity validation remain in scope.
- Single-threaded reception plus sequential exports requires a scheduling approach
  that keeps event ingestion responsive. Select that approach in the ticket plan;
  do not introduce parallel export workers to solve it.
- The resource-limit behavior above explicitly follows the user's instruction to
  preserve the current exporter. Its trusted-server reliance supersedes the old
  generic guardrail requiring new application-level response/pagination caps.

## Workflow handover

This artifact covers the user-story step only. No implementation, deployment
changes, ticket plan, or automatic commit is part of this request.

Before creating `tickets.md`, commit the epic's reviewed user stories and any
relevant reviews. Derive tickets in dependency order with observable behaviors
and public interfaces. Obtain approval of that ticket/behavior plan, commit it,
then implement tickets using the repository's TDD and per-ticket commit workflow.
Update current project documentation and guardrails during implementation to
describe the replacement bot while preserving historical epic artifacts.

Required project checks for changes remain:

```console
uv run ruff check --fix
uv run ruff format
uv run ty check
uv run pytest
```

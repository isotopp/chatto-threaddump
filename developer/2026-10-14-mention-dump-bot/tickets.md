# Mention dump bot implementation tickets

## Plan status and scope

Draft for approval. Source: `user-stories.md`, committed at `0a56e1b` after the
initial story commit `461f0be`. 

Replace the current CLI; use it only as evidence of export behavior and API
knowledge. No backward compatibility or code retention is required. The ticket
numbers give a valid sequential implementation order. Parallel work is allowed
only after a ticket's hard prerequisites have been committed and integrated.

Before implementation, approve this behavior and ticket plan, then commit this
file. Each ticket is a separate commit using the `git-commit` skill. Within a
ticket, use one behavior-focused red-green cycle at a time, not a batch of failing
tests followed by a batch of implementation.

## Proposed interfaces and scheduling contract

These are the small cross-lane interfaces to settle in Ticket 1. Names are
suggestions; their observable contracts are the parallel-work boundary.

| Interface | Observable contract |
| --- | --- |
| Configuration loader | Returns validated origin, credential, stable role name, positive queue capacity, and local temporary root; errors are sanitized. |
| Mention input | Bot identity, actor, room, command event ID, optional thread root, authoritative direct mentions, and whole-message plain text; no persisted event history. |
| Dump request | Immutable room, command event ID, and thread root identifying one accepted request; separate commands remain separate requests. |
| Thread reader | Async load of a validated full chronological thread, starter metadata, and included user records from room/root IDs. |
| Transcript writer | Writes the documented Markdown from validated messages, resolved authors, and a per-message local attachment-link mapping. |
| Asset collector | Async downloads into the bundle directory; returns the attachment-link mapping; fails if any required asset is unavailable. |
| Archive context | Async context yielding a complete ZIP path, filename, and normalized starter first line; cleans its unique temporary parent on exit. |
| Reply operation | Async response attributed to the command via `in_reply_to`; server decides placement; no automatic room join. |
| Publisher | Async upload and top-level post of the ZIP; no source-thread placement or reply attribution; no retries. |
| Request inbox | Nonblocking admission to a bounded queue, plus one sequential consumer; no deduplication or durable storage. |

Use one `asyncio` event loop and `asyncio.Queue(maxsize=capacity)` in one Python
thread. Async tasks may receive events while one export is active; tasks do not
mean parallel exports. All network paths must await asynchronous I/O. Long local
file/ZIP work must cooperate with the event loop rather than monopolize it for an
entire archive. Do not use worker threads or `to_thread` to make the old synchronous
exporter fit. Exact chunking is an implementation choice, not a new resource cap.

Ticket 1 establishes only the shared contracts actually needed by the lanes and
a working configuration path. Do not add empty implementations for later features
or build a generic bot framework.

### Behavior choices included in this approval

- Evaluate `dump` anywhere in the mentioning message's plain text, as noted in
  the stories' unanswered Q17. A message mentioning both this bot and another
  user still qualifies when its text contains the standalone word `dump`.
- Load `.env` from the working directory with `python-dotenv`, without replacing
  process-environment values. Use `./tmpdir` for local request data. Server-side
  placement and secret installation are outside scope.
- Check command authorization during admission; do not recheck it when dequeued.
- A mention without `dump` receives help; role-first precedence applies to command
  candidates. No command is admitted without the exact configured role.
- Acknowledge only an accepted request. If that reply fails, log a sanitized
  diagnostic and keep the accepted job; do not retry or silently discard it.
- Use the normalized starter body first line for the headline. For an absent
  body, use the exporter's deleted/unavailable-body placeholder; a present empty
  body contributes an empty quoted line. Add no summary, link, or truncation rule.
- The top-level ZIP post is the success notification; there is no additional
  completion reply to the command.
- A lost realtime connection may terminate the process with a sanitized error.
  Do not add in-process replay/reconnection recovery or persisted resume cursors.
  A later process starts live-only; no request operation is retried.

## Test and delivery surfaces

- Exercise exported feature interfaces and the eventual installed executable.
  Fake external Chatto transports, realtime sources, and asset endpoints, not
  internal helpers. Assert replies, observed requests, files, archive contents,
  queue acceptance, and process results rather than private implementation state.
- Use generated beta.10 protobuf messages where the wire boundary requires them.
  Verify mention targeting, role records, thread identities, and request fields
  against immutable beta.10 source or installed descriptors.
- Use temporary directories and fake credentials only. No live Chatto calls,
  plaintext real credentials, or private response bodies in test fixtures.
- Async tests can run through `asyncio.run`; do not add a test plugin solely to
  drive the event loop. Use controlled events/barriers and finite event streams
  rather than sleeps to demonstrate ordering and responsiveness.
- Extract ZIPs into a separate temporary directory and verify contents and local
  references. Use filesystem failure injection only at filesystem boundaries.
- Preserve observable behavior from existing tests where applicable, but replace
  tests tied to removed CLI arguments or private helpers rather than carrying
  legacy interfaces forward.
- The pinned `chattolib` realtime extra is justified by event reception. Prefer
  existing dependencies and the standard library for queues, ZIPs, and lifecycle.
- Before completing each ticket run `uv run ruff check --fix`, `uv run ruff format`,
  `uv run ty check`, and `uv run pytest`, then commit that ticket before a dependent
  ticket starts. Repeat integration checks after merging parallel branches.

## Dependencies and suggested ownership

Paths below are suggested ownership boundaries under `src/chatto_threaddump/`
and `tests/`. Ticket 1 freezes the actual names. Shared-file edits belong to the
listed integrator; communicate needed changes instead of editing another lane.

| Ticket | Deliverable | Hard prerequisites | Story coverage | Suggested owned files |
| --- | --- | --- | --- | --- |
| 1 | Configuration and shared contracts | None | US-1 | `config.py`, `contracts.py`, `tests/test_config.py`, dependency metadata/lock |
| 2 | Live mentions, roles, and reply API | 1 | US-1–3 | `chatto_events.py`, `chatto_replies.py`, `tests/test_chatto_events.py`, `tests/test_chatto_replies.py` |
| 3 | Validated single-page thread read | 1 | US-5 | `thread_reader.py`, `tests/test_thread_reader.py` |
| 4 | Complete and consistent pagination | 3 | US-5 | Continue Ticket 3 ownership |
| 5 | Author hydration and Markdown | 3 | US-5 | `transcript.py`, `tests/test_transcript.py` |
| 6 | Complete safe attachment download | 3 | US-5 | `assets.py`, `tests/test_assets.py` |
| 7 | Isolated complete ZIP context | 4, 5, 6 | US-5, US-6, US-7 | `archive.py`, `tests/test_archive.py` |
| 8 | Mention admission and user feedback | 2 | US-2, US-3 | `admission.py`, `tests/test_admission.py` |
| 9 | Bounded sequential request inbox | 1 | US-4 | `inbox.py`, `tests/test_inbox.py` |
| 10 | ZIP upload and channel publication | 2, 7 | US-6 | `publisher.py`, `tests/test_publisher.py` |
| 11 | One-request lifecycle and failure isolation | 7, 9, 10 | US-4, US-7 | `worker.py`, `tests/test_worker.py` |
| 12 | Replace CLI with responsive bot runtime | 2, 8, 9, 11 | US-1–7 | `runtime.py`, `__init__.py`, `tests/test_runtime.py`, entry point metadata, legacy test retirement |
| 13 | Operator contract and final integration verification | 12 | US-1–7 | `README.md`, `AGENTS.md`, fake dotenv example, `tests/test_bot_integration.py` |

### Parallel worktree plan

1. Complete and merge Ticket 1 first. It is the contract gate for all worktrees.
2. From that committed base, lanes **2 → 8**, **3 → 4**, and **9** can proceed
   independently. Their files and tests must remain separate.
3. Once Ticket 3 is merged, Tickets 4, 5, and 6 can run in parallel, while the
   admission and inbox lanes continue if unfinished.
4. Merge Tickets 4, 5, and 6 before Ticket 7. Ticket 10 follows Tickets 2 and 7;
   Ticket 11 follows Tickets 7, 9, and 10. Tickets 12 and 13 are integration gates.
5. A ticket's worktree branches from a commit containing every hard prerequisite,
   never from another lane's uncommitted checkout. Use `codex/` branch names.
6. A finished ticket is checked and committed in its own worktree, then integrated
   before dependents start. Re-run project checks on the merged result. Resolve
   contract changes at the integration point and update/reapprove this plan if
   they change observable scope.
7. Do not create worktrees or implement code during this planning step. An
   implementer may create worktrees after approval. Do not share `tmpdir`, dotenv
   secrets, test output directories, or an active bot process between worktrees.

Parallel implementation is a scheduling choice, not a change to the single-threaded
runtime contract. No lane needs work in the deployment repository.

## Ticket 1 — Validate dotenv configuration and establish lane contracts

**Depends on:** none. **Covers:** US-1 configuration and trusted origin.

### Deliverable and public behavior

Expose the configuration loader and the minimum request/thread/archive contracts
needed by parallel lanes. Valid `.env` or environment input yields a trusted HTTPS
origin, fake/testable credential, stable role name, positive queue capacity, and
working-directory `tmpdir`. Reject missing/empty values, invalid queue capacities,
and origins with user information, non-root paths, queries, fragments, or non-HTTPS
schemes before authenticated I/O. Keep credentials out of error text and printable
configuration representations. Add the pinned realtime dependency extra here;
own shared dependency/lock changes centrally.

**Test surface:** Public loader with isolated environment and temporary `.env`;
valid input, environment precedence, missing values, malformed origin, zero or
noninteger capacity, and credential-safe failures. Agree exported contracts before
other lanes start; no tests that merely mirror dataclass fields.

## Ticket 2 — Receive live direct mentions, look up roles, and send replies

**Depends on:** 1. **Covers:** US-1 realtime boundary and US-2/3 API operations.

### Deliverable and public behavior

Expose a live-only async event stream, requester-role lookup, and reply operation
against beta.10. Preserve authoritative bot mention targets, actor/room/event/root
IDs, and plain text. Ignore self posts and channel echoes; name text is not a
mention. Read stable assigned roles via the user directory, not room membership.
Reply using `in_reply_to=<command event ID>`; accept server-inferred placement.
Never auto-join a room. Use the configured authenticated origin, safe errors,
finite request timeouts, and no redirects or retries from the first working path.

**Test surface:** Fake realtime events and ConnectRPC transport; direct target vs
display-name text, unrelated mentions, self/echo events, actor identity, exact role
records, reply attribution, no membership mutations, and rejected transport errors.
Starting the event stream must not request historical events or reuse a cursor.

## Ticket 3 — Read and validate a single-page thread

**Depends on:** 1. **Covers:** US-5 thread identity and initial traversal.

### Deliverable and public behavior

The async thread-reader interface accepts room/root IDs and returns the starter,
ordered replies, and included authors from one complete page. Validate response
shapes, room/root/event/actor/time identity, root placement, and reply roots. Ignore
well-formed non-message events. A command must already identify an actual thread;
do not revive the old CLI's top-level single-message export behavior.

**Test surface:** Public reader with a fake API transport; ordinary page, malformed
response, missing/misplaced root, mismatched identities, and non-message events.
Assert chronological messages and sanitized failures, never private parsing helpers.

## Ticket 4 — Traverse every older page with consistency checks

**Depends on:** 3. **Covers:** US-5 complete thread traversal.

### Deliverable and public behavior

Follow opaque older cursors with the same room/root and page size 500. Merge
includes, emit the starter once, and order replies oldest first. Reject repeated
or unusable cursors, duplicate events, roots on older pages, and ordering violations
within/across pages. Fail on a late page error. Add no new response/page-count cap.

**Test surface:** Reader across generated multi-page fake responses, including more
than 500 replies, page-local authors, cursor failure, duplicate IDs, and a late
transport failure. Observe request cursors and complete ordered output.

## Ticket 5 — Hydrate authors and render the existing Markdown contract

**Depends on:** 3. **Covers:** US-5 author and transcript behavior.

### Deliverable and public behavior

Hydrate unresolved authors in first-seen batches of at most 100, rejecting duplicate
or unrequested returned users. Produce `_index.md` using the documented title,
single starter timestamp, author fallback, message bodies, and provided local
attachment links. Preserve empty/deleted/unavailable bodies and attachment-only
messages. Normalize controls/line endings and escape metadata while preserving
message Markdown; omit internal IDs and per-message timestamps.

**Test surface:** Public transcript path with fake user responses and actual file
output; batch boundaries, missing users, author fallbacks, body variants, Unicode
and control characters, and image/file links supplied by the agreed mapping.

## Ticket 6 — Download every asset with safe local names and references

**Depends on:** 3. **Covers:** US-5 asset completeness and credential separation.

### Deliverable and public behavior

The async asset collector writes each required attachment into the supplied bundle
directory and returns ordered local links. Enforce safe basenames, deterministic
suffixes, `_index.md` reservation, and correctly encoded relative links. Require
usable HTTPS signed URLs, reject redirects, and never attach the bot credential to
asset requests. Honor the existing five-second timeout and trusted HTTP environment.
Missing, malformed, or failed assets fail the collection; do not insert placeholders,
fetch body links/previews, or impose new asset/bundle-size caps.

**Test surface:** Public collector using fake asset transport and temporary files;
image/file order, descriptions, colliding/traversal/control filenames, signed URL
absence, redirects, timeout/TLS/HTTP failures, no bearer header, and no signed URL
leakage. Show that I/O yields control without starting a second export.

## Ticket 7 — Yield a complete isolated ZIP and clean its temporary parent

**Depends on:** 4, 5, 6. **Covers:** US-5 bundle, US-6 archive, US-7 cleanup.

### Deliverable and public behavior

The archive context reads the full thread and creates a unique parent with
`thread_<starter Unix timestamp>/`, `_index.md`, every asset, and a ZIP containing
that directory prefix. Yield the ZIP and normalized starter first line only after
all work succeeds. Keep files while the caller uses the context, then remove the
entire parent on success, failure, or cooperative cancellation. No archive is
available after a failed page or asset. ZIP work cooperates with the event loop.

**Test surface:** Context API, actual ZIP extraction, and temporary filesystem;
complete references, command retention, equal timestamps in distinct parents,
early and late failure, packaging error, cancellation, and cleanup of only the
owned parent. Use an event-loop heartbeat/barrier to detect an archive operation
that blocks ingestion for its entire workload.

## Ticket 8 — Admit eligible mentions or reply with specific guidance

**Depends on:** 2. **Covers:** US-2 and US-3, plus US-4 admission feedback.

### Deliverable and public behavior

Expose admission behavior with an injected queue-admission boundary. A direct
mention without whole-word case-insensitive `dump` receives help. For commands,
check exact configured role membership first; deny with the role name before
checking thread context. Authorized top-level commands receive the thread-only
error. Eligible commands enqueue independent requests; successful admission gets
an affirmative reply, and rejected capacity gets a queue-full reply. Lookup errors
fail closed. A failed affirmative reply does not discard an already accepted job.

**Test surface:** Public admission using fake API responses and bounded admission;
`dump`, `DuMp`, punctuation, `dumpingprices`, multiple mention targets, help,
unauthorized/top-level precedence, established starter vs actual thread reply,
authorization failure, queue-full behavior, and failed acknowledgment. All replies
target the mentioning message, with no membership comparison or admin exemption.

## Ticket 9 — Provide a bounded inbox with one sequential consumer

**Depends on:** 1. **Covers:** US-4 queue and invocation-local state.

### Deliverable and public behavior

Expose nonblocking queue admission and a sequential consumer using the configured
Python queue capacity. Accept independent requests for the same thread, maintain
FIFO order, and report full without waiting for space. An active request does not
prevent other async tasks from attempting admission. State exists only in memory.

**Test surface:** Public inbox with a controlled async execution boundary; hold
one request active, fill the queue, observe rejection and later acceptance, release
the barrier, and verify FIFO with no overlapping executions. A fresh inbox has no
prior jobs. No assertions about queue internals or sleeps to infer scheduling.

## Ticket 10 — Upload and publish the ZIP at channel top level

**Depends on:** 2, 7. **Covers:** US-6 publication.

### Deliverable and public behavior

The publisher uploads the complete ZIP to the source room as `application/zip`
and posts `Dump of Thread "{first line of the thread starter message}"` with the
returned asset attached. Set neither reply attribution nor source-thread placement
on that post. Do not join rooms, compare memberships, add a summary/link/excerpt,
or retry uploads/posts, including an uncertain result.

**Test surface:** Public publisher using a real small generated archive and fake
upload/message transport; verify uploaded ZIP bytes, MIME/filename, asset binding,
first-line/empty/deleted starter cases, no reply/root fields, no membership writes,
upload failure prevents posting, and ambiguous post failure causes one attempt.

## Ticket 11 — Isolate each request's lifecycle and failures

**Depends on:** 7, 9, 10. **Covers:** US-4 continuation and US-7 failure reporting.

### Deliverable and public behavior

One worker operation opens the archive context, publishes while its ZIP exists,
and releases all temporary data afterward. On a failed read/download/ZIP/upload/
post, attempt one sanitized error reply to that request's command. If the error
reply also fails, log only a safe diagnostic. Continue with later queued requests.
No job retries, persistent status, extra success reply, or server-side deletion.

**Test surface:** Worker through fake external APIs and actual files, driven by the
inbox; first job fails and second succeeds, archive exists during upload, all
handled paths clean up, error reply targets command, reply failure is not retried,
and credentials/signed URLs/private bodies cannot escape through errors or logs.

## Ticket 12 — Replace the CLI with the complete responsive bot runtime

**Depends on:** 2, 8, 9, 11. **Covers:** integrated US-1 through US-7.

### Deliverable and public behavior

Wire the installed executable to configuration, live-only reception, admission,
and one worker in a single event-loop thread. Retire the old positional CLI and
its incompatible implementation/tests. Reception remains responsive during active
export I/O and local archive work. Stop cooperatively on shutdown, cancel active
work with cleanup, and discard pending jobs. Connection/process failure gives a
sanitized nonzero result, without cursor persistence, replay, or automatic retry.

**Test surface:** Executable/runtime with finite fake realtime streams and fake
HTTP; valid mention to top-level downloadable archive, help/role/thread replies,
ingestion and queue-full reply while an export is held active, two same-thread
requests, process restart with no recovery, shutdown cleanup, and exactly one
Python thread. Assert outputs and requests, not task implementation details.

## Ticket 13 — Verify the public operating contract and document the replacement

**Depends on:** 12. **Covers:** final US-1 through US-7 acceptance.

### Deliverable and public behavior

Update README, package description/help, and current development guardrails for
the bot contract. Document local installation/invocation, fake/example dotenv,
role-name semantics, queue settings, temporary files, actual required API grants,
intentional channel publication, no retries/recovery, and existing server-resource
limit reliance. Verify grants against beta.10 rather than guessing. Preserve old
epic artifacts. Do not write service units, deployment scripts, Ansible tasks, or
server provisioning instructions as deliverables.

**Test surface:** End-to-end fake API/realtime integration suite and installed
executable smoke test; choose any acceptance gaps revealed by integration, rather
than duplicating every earlier test. Check safe transport destinations, timeout/
redirect behavior, sanitized failures, extracted Markdown/assets, and absence of
partial publication. Run all required checks and confirm documentation matches
the implemented public behavior before the final ticket commit.

## Approval and implementation handover

Review the proposed interface/scheduling contract and explicit behavior choices
alongside the tickets. Approval must cover the whole-message `dump` assumption
from Q17. Commit the approved ticket plan before any code changes. Changes to
observable scope discovered during implementation return here for amendment,
approval, and a new plan commit before work resumes.

The story commits are complete; this ticket plan remains uncommitted until that
approval. No deployment work is implied or authorized by approving this plan.

# Chatto thread dump implementation tickets

These tickets implement the accepted behavior in `user-stories.md`. Complete
them in order. Each ticket is one commit and may contain several red-green
cycles, but each cycle adds one observable behavior at a time.

## Test and delivery approach

- Exercise the public console entry point with argument lists, environment
  variables, captured stdout/stderr, and temporary directories.
- Fake HTTP only at the external transport boundary. Do not mock internal
  functions or call a live Chatto server.
- Assert generated files and observed HTTP requests, not internal object or
  module structure.
- Keep stdout empty during normal operation. Test credentials and signed asset
  URLs must never appear in output, diagnostics, or fixtures committed to Git.
- After every red-green cycle, keep the focused test green. Before completing a
  ticket, run `uv run ruff check --fix`, `uv run ruff format`,
  `uv run ty check`, and `uv run pytest`.

## Package plan

No ticket currently requires a new package. Ticket 1 uses the existing
`httpx` dependency for unary ConnectRPC JSON requests; a ConnectRPC library
would add no required behavior. Ticket 2 uses the existing `python-dotenv`
dependency and the standard-library CLI parser. Attachment requests in Ticket
11 also use `httpx`; filesystem publication, text normalization, filename
handling, and URL parsing use the standard library.

If implementation reveals a required external package, update that ticket to
name and justify the package and add it to `pyproject.toml` as part of the same
approved ticket. Do not add speculative dependencies.

## Dependency map

The implementation order is the dependency order:

```text
1 tracer export
└─ 2 CLI and configuration
   └─ 3 URL trust boundary
      └─ 4 room-message lookup
         └─ 5 single-response validation
            └─ 6 pagination
               └─ 7 traversal validation
                  └─ 8 atomic publication
                     └─ 9 author hydration
                        └─ 10 text rendering
                           └─ 11 attachment download
                              └─ 12 attachment naming
                                 └─ 13 atomic attachment failure
                                    └─ 14 failure contract
                                       └─ 15 documentation
```

Dependencies are deliberately linear so every ticket starts from a green,
committed public path and no ticket requires code planned for a later commit.

## Ticket 1 — Export one explicit, single-page thread

**Depends on:** none.

**Package impact:** none; use the existing `httpx` dependency directly rather
than adding a ConnectRPC client library.

Deliver the first complete path through the public CLI.

### Public behavior

- Given valid process-environment configuration, an explicit thread-message
  URL, a nonexistent output directory, and one successful fake thread page,
  exit zero and create the directory with `_index.md`.
- Render the fixed title, root start time, authors present in page includes,
  root body, and replies in chronological order.
- Omit per-message timestamps and all internal IDs from Markdown.
- Send the documented bearer and ConnectRPC headers only to the configured API
  origin, do not follow redirects, and make no live request.
- Keep stdout empty.

### Deferred

Room-message lookup, pagination, missing author hydration, content edge cases,
attachments, safe staged publication, `--force`, and detailed failure
classification remain for later tickets.

### Acceptance coverage

Acceptance criteria 2, 8, 9, and 13 for the single-page, included-author,
ordinary-body, no-attachment case.

## Ticket 2 — Parse CLI options and resolve configuration

**Depends on:** Ticket 1.

**Package impact:** none; use the standard library and the existing
`python-dotenv` dependency.

Establish the command contract independently of URL and network behavior.

### Public behavior

- Require exactly `CHATTO_URL OUTPUT_DIRECTORY`.
- Accept `--force/-f` and a positive finite `--timeout/-t`; use five seconds by
  default and apply the selected value to API requests.
- Resolve configuration in order: process environment, working-directory
  `.env`, then `~/.chatto-threaddump.env`, without overriding a value already
  resolved at a higher precedence.
- Reject missing or empty required configuration before parsing the input URL
  or making a network request.
- Keep credentials out of diagnostics.

### Acceptance coverage

Acceptance criteria 12 and 19 for CLI parsing, configuration precedence, and
API request timeouts.

## Ticket 3 — Validate server origins and Chatto URLs

**Depends on:** Ticket 2.

Close the credential trust boundary before adding more request paths.

### Public behavior

- Require the configured server to be an absolute HTTPS origin with no user
  information, query, fragment, or non-root path.
- Accept all three documented URL shapes and valid channel, DM, and event IDs.
- Enforce named-server hostname/default-port matching and complete-origin
  matching for `/-/`, including explicit non-default ports.
- Reject malformed paths, identifiers, user information, queries, fragments,
  encoded separators, and mismatched origins before any network request.
- Always use the configured origin as the authenticated request destination.

### Acceptance coverage

Acceptance criteria 6, 16, and 22.

## Ticket 4 — Resolve room-message links

**Depends on:** Ticket 3.

Support links that do not already encode the thread root.

### Public behavior

- For either room-message URL form, request the linked message with its room
  and event IDs.
- If the linked message is a reply, traverse the thread named by its
  `threadRootEventId`.
- If the linked message is an established root, traverse that thread.
- If it is a root without a `thread` summary, publish the loaded message as a
  one-message bundle without requesting thread events.
- Reject a lookup whose returned message ID or room ID does not match the
  request, without publishing output.

### Acceptance coverage

Acceptance criteria 1, 10, 11, and 16.

## Ticket 5 — Validate individual API responses

**Depends on:** Ticket 4.

Reject malformed or internally inconsistent data before adding pagination.

### Public behavior

- Reject required response objects or fields with the wrong JSON type.
- Require the initial thread page to contain the requested root exactly once
  and before its replies.
- For every message event, require the nested message ID, room ID, actor ID,
  and creation timestamp to agree with the enclosing event, and every reply to
  name the requested thread root.
- Require every included user map key to equal that user's stable ID.
- Ignore only well-formed non-message events.
- Fail without publishing output on any violation.

### Acceptance coverage

Acceptance criterion 11 for response shape, root placement, nested event
identity, thread identity, and user-map identity on one page.

## Ticket 6 — Traverse older thread pages

**Depends on:** Ticket 5.

Export complete large threads through opaque cursor pagination.

### Public behavior

- Follow `before: startCursor` while `hasOlder` is true, retaining the same
  room, root, and page limit on every request.
- Prepend each older reply page so a generated thread with more than 500
  replies is rendered in chronological order with the root exactly once.
- Merge page-local user includes across the traversal.
- Require older pages not to contain the root.
- Reject `hasOlder` without a usable new cursor and reject repeated cursors.
- Do not impose an application-level response-size, page-count, or transcript
  limit beyond the server API and cursor validity.

### Acceptance coverage

Acceptance criteria 3, 4, and 11 for pagination, older-page root placement,
and cursor progress.

## Ticket 7 — Validate consistency across a traversal

**Depends on:** Ticket 6.

Reject data that is locally valid but inconsistent with earlier pages.

### Public behavior

- Require event IDs to be unique across all pages.
- Require events within each page to remain in chronological display order.
- Require every older page to precede replies already collected.
- Fail without publishing when a later page request or validation fails.

### Acceptance coverage

Acceptance criteria 3, 7, and 11 for cross-page uniqueness, ordering, and
late-page failure.

## Ticket 8 — Publish and replace bundles safely

**Depends on:** Ticket 7.

Make directory publication atomic and recoverable before adding downloads.

### Public behavior

- Prepare the complete bundle in a temporary sibling of the output directory,
  then create a missing destination and any missing parents by rename only
  after preparation succeeds.
- Reject an existing non-directory path.
- Reject an existing directory without `--force` before making a network
  request or changing it.
- With `--force`, replace the entire existing directory only after preparation
  succeeds.
- Restore the old directory if publication fails and remove the backup only
  after replacement succeeds.

### Acceptance coverage

Acceptance criteria 7 and 18 for transcript-only bundles.

## Ticket 9 — Resolve authors missing from page includes

**Depends on:** Ticket 8.

Hydrate authors through the bounded public API workflow.

### Public behavior

- Request unresolved actor IDs through first-seen `BatchGetUsers` batches of
  at most 100 IDs.
- Merge valid returned users with page-local includes.
- Reject batch results containing unrequested or duplicate user IDs without
  publishing output.
- Allow omitted requested users to remain unresolved.
- Prefer normalized `displayName`, then normalized `@login`, then
  `Unknown author`; never render a user ID.

### Acceptance coverage

Acceptance criteria 4, 11, and 17.

## Ticket 10 — Normalize and render untrusted text

**Depends on:** Ticket 9.

Complete message rendering without leaking controls or Markdown structure
through metadata.

### Public behavior

- Preserve message Markdown, tabs, line breaks, and ordinary Unicode while
  normalizing line endings and removing other control characters.
- Render present empty bodies as empty, absent deleted bodies as
  `_[message deleted]_`, and other absent bodies as
  `_[message body unavailable]_`.
- Collapse author-label whitespace and apply the documented author fallback.
- Escape untrusted Markdown metadata while leaving message-body Markdown
  intact.
- Keep stdout empty and omit internal identities from all rendered variants.

### Acceptance coverage

Acceptance criteria 5 and 13, excluding attachment-only messages.

## Ticket 11 — Download and render attachments

**Depends on:** Ticket 10.

**Package impact:** none; reuse the existing `httpx` dependency.

Add the basic self-contained attachment path on top of atomic publication.

### Public behavior

- Download every attachment from its HTTPS signed asset URL using the selected
  timeout and the environment trusted by `httpx`.
- Do not follow redirects or send the Chatto API authorization header on asset
  requests.
- Render `image/*` files as local Markdown images and other MIME types as local
  file links, preserving attachment order and descriptions.
- Keep attachment-only messages visible under their authors.
- Do not impose an application-level attachment or bundle-size limit.

### Acceptance coverage

Acceptance criteria 5, 14, 19, and 21 for valid, uniquely named attachments.

## Ticket 12 — Make attachment names safe and deterministic

**Depends on:** Ticket 11.

Prevent path escape and collisions in the published bundle.

### Public behavior

- Derive safe basenames from untrusted filenames, removing controls and path
  structure and using `attachment` when no usable name remains.
- Reserve `_index.md` for the transcript.
- Deduplicate names in encounter order by inserting `.1`, `.2`, and later
  numbers before the final suffix.
- Normalize filenames and MIME types to one line, escape link labels, and
  percent-encode relative link targets as needed.
- Never render an absolute local path.

### Acceptance coverage

Acceptance criterion 15.

## Ticket 13 — Fail attachment exports atomically

**Depends on:** Ticket 12.

Close the attachment failure paths without exposing signed URLs.

### Public behavior

- Fail when an attachment URL is absent, unusable, not HTTPS, redirected, or
  fails to download.
- Never include a signed asset URL in Markdown or diagnostics.
- On any attachment failure, preserve an existing destination and publish no
  new destination or partial bundle.
- Never leave a published transcript that references a missing file.

### Acceptance coverage

Acceptance criteria 7 and 20.

## Ticket 14 — Complete network and failure handling

**Depends on:** Ticket 13.

Give operators useful failures while preserving the credential boundary.

### Public behavior

- Honor the standard proxy and custom-CA environment trusted by `httpx` for
  API and attachment requests.
- Do not retry automatically or follow redirects.
- Return nonzero with a concise stderr diagnostic that distinguishes invalid
  input, missing configuration, authentication failure, permission denial,
  missing resources, transport/TLS/timeout failure, server failure,
  ConnectRPC failure, malformed JSON, and malformed API data.
- Never include the API key, signed asset URLs, or private response bodies in
  diagnostics; keep stdout empty on success and failure.

### Acceptance coverage

Acceptance criteria 6, 7, 8, 9, 12, 19, and 21 for completed failure and
transport behavior.

## Ticket 15 — Document the operator workflow

**Depends on:** Ticket 14.

Make the command's built-in and repository documentation match its behavior.

### Public behavior

- Make `--help` describe both positional arguments, `--force/-f`, and
  `--timeout/-t` without requiring configuration or network access.
- Update `README.md` with installation, `.env` fallback, least-privilege bot
  setup, current invocation examples, output bundle contents, replacement
  behavior, and failure expectations.

### Acceptance coverage

The user-facing installation, configuration, and operation contract, with
acceptance criteria 18 and 19 reflected in the documentation.

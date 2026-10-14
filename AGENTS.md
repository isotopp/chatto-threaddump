# Development guide

This repository contains a Python 3.14, `uv`-managed long-running Chatto thread
dump bot. Project code and documentation are in English. The old stateless CLI
exporter has been replaced; do not preserve its command-line or read-only
contracts.

## Setup and checks

Install the project and its development dependencies with `uv sync`.

Run all checks before completing a change:

```console
uv run ruff check --fix
uv run ruff format
uv run ty check
uv run pytest
```

Application code belongs in `src/chatto_threaddump/`; tests belong in `tests/`.
Keep runtime dependencies in `[project].dependencies` and development tools in
`[dependency-groups].dev`.

## Product contract

- The installed `chatto-threaddump` command is a permanently running bot. It
  reads `.env` from its current working directory and accepts only the
  configuration described in `README.md` and `.env.example`.
- Only a direct mention of this bot containing the standalone word `dump`
  requests a dump. Check the requester's exact assigned role name before the
  thread-context check. Do not add membership exemptions, admin bypasses, or
  role-ID reinterpretation.
- A valid request is queued in a bounded in-memory FIFO and handled by one
  sequential consumer. Keep reception responsive on the same asyncio event
  loop; do not add worker threads, parallel exports, request deduplication, or
  persistent queues.
- A complete thread archive is assembled before publication. Reply to the
  original request for admission and errors; on success upload the ZIP and post
  it at the channel top level. This posting and upload are intentional API
  writes. Do not post to the source thread or change the user-facing headline.
- Do not edit, delete, react to, follow, mark read, join rooms, change room
  membership, or change roles/permissions. The bot must be configured as a room
  member where Chatto requires membership for reading, uploading, and posting.
- Requests are live-only and invocation-local. Do not add retries, automatic
  reconnect/replay, saved cursors, job recovery, or server-side deletion. On
  handled success/failure and cooperative cancellation, remove only the
  request's unique temporary parent under `./tmpdir`.

## API and data safety

- Send the Chatto API key only to the validated configured HTTPS origin. Reject
  invalid origins before authenticated I/O, use finite timeouts, and refuse
  redirects. Signed attachment URLs are independent HTTPS capabilities; never
  send the Chatto key to an asset endpoint.
- Never print, log, persist, or include credentials, signed URLs, or private
  response bodies in diagnostics. Replace transport errors with sanitized
  operation messages. Tests use fake transports, fake credentials, and fake
  response data only.
- Treat URLs, cursors, API messages, user records, filenames, descriptions, and
  signed URLs as untrusted. Keep the API-version validation and file naming
  safeguards. Do not output an archive if any thread page, required asset,
  transcript write, or ZIP operation failed.
- Keep the exported Chatto API contract scoped to
  `chattolib[realtime]==0.5.0b10` / Chatto `v0.5.0-beta.10`. Automated tests
  must not call a live Chatto server.
- Do not introduce independent bundle/page caps without an approved change to
  the epic. The bot currently relies on the existing Chatto API/server resource
  limits and the configured queue bound.

## Epic workflow

The specialization workflow creates epics in `./developer`, dated and with a
slug. It uses committed `user-stories.md` and `tickets.md` artifacts to capture
behavior before implementation. Implement each approved ticket in order, use
behavior-focused tests, run the required checks, and commit each ticket with the
`git-commit` skill. Deployment, Ansible, service-manager, and provisioning work
is outside this repository epic unless separately requested.

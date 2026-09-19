# Development guide

This repository contains a Python 3.14, `uv`-managed command-line program that
exports a Chatto thread as Markdown. Project code and documentation are in
English.

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

## Development workflow

`developer/2026-09-19-thread-dump/user-stories.md` is the behavior source of
truth. Follow its repository handover: approve and commit the user stories,
derive and approve `tickets.md`, then implement tickets in order. Use one
behavior-focused failing test at a time, write the minimum code that passes it,
run all checks, and commit each completed ticket separately. Re-approve ticket
scope before implementing newly discovered behavior.

Prefer the standard library and existing dependencies. Add a dependency only
when an observed requirement justifies it. Keep the CLI stateless, one-shot,
and narrowly scoped to the documented Chatto API version.

## Agent guardrails

- Never send the API key anywhere except the configured trusted HTTPS origin.
  Validate the input URL and origin before creating an authenticated request.
- Never print, log, persist, or include credentials or private response bodies
  in errors. Tests must use fake HTTP responses and fake credentials only.
- Keep the program read-only. Do not post, edit, delete, mark as read, follow
  threads, change membership, or change permissions.
- Buffer the complete transcript before writing stdout. On any failed page,
  exit nonzero without knowingly emitting a partial transcript.
- Treat URLs, cursors, API data, message text, author data, filenames, and
  descriptions as untrusted. Bound response sizes and pagination, reject bad or
  repeated cursors, and remove terminal control characters from rendered text.
- Treat cursors as opaque and invocation-local. Do not parse, manufacture,
  persist, or reuse them across rooms, threads, or credentials.
- Do not call a live Chatto server from automated tests.
- Do not weaken validation, TLS, timeouts, least-privilege behavior, or error
  handling to simplify an implementation.

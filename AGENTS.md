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

The development workflow uses skills, which are provided in `.agents/skills`.

The specialization workflow creates epics in `./developer`, dated and with a slug.
An example of such an epic is `developer/2026-09-19-thread-dump/user-stories.md`.

The specialization workflow skill explains the process:
- creation of a `user-stories.md` to define the expected behavior in a structured way.
- creation of a `tickets.md` from that file, in actionable tickets of an implementable size, in dependency order.

Both steps profit immensely from a high tier LLM; here ChatGPT-5.6 Sol/medium was used.

The tickets can then be implemented, in order, with a commit before moving on to the next ticket.
This does no longer require a high tier model, here ChatGPT-5.6 Luna/xhigh was used.

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

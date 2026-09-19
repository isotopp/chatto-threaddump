---
name: git-commit
description: Create meaningfull git-commit descriptions when asked to commit a change.
---

The project uses commit message that are structured as described below.
Use this to create a commit message when asked to commit a change.

# Useful `git commit` messages

A commit message consists of a title, a newline and an itemized list of changes.

The title is a single line, at most 78 characters long.
If the commit implements a ticket in an epic, the format is "epic slug: ticketnumber - ticket title".
For example "authentication-improvements: 1 - basic config".

After a newline, the list of changes implemented by the ticket is listed as an itemized list.
For example:
- implement a username and password prompt.
- create helper functions to read username and password.
- validate the login with username and password.

The list of changes should come from the source, and be structurally driven by the ticket.

Items should be enriched by filename and line references.
- create helper function fondle() (src/module/file.py:17-33)
- create helper module fondle (src/module/fondle.py)

# Hint for Newlines

For multi-line commit messages, use separate `-m` arguments for separate paragraphs,
and put actual line breaks inside a quoted `-m` argument for multiple lines within the same paragraph.
Do not use literal `\n` sequences.

```
git commit \
  -m "Vendor repository development skills" \
  -m "- copy git-commit, TDD, and specialization workflow skill files (.agents/skills)
- record source revisions and checksums for each skill (.agents/skills/*/UPSTREAM.md)
- ignore Python, uv, test, coverage, and PyCharm artifacts (.gitignore)"
```

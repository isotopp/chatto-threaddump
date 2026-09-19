---
name: specialization-workflow
description: Plan and implement a repository epic through committed user stories, reviews, tickets, and TDD-sized code tickets. Use when the user asks to follow the specialization workflow, start or plan an epic, turn an epic into tickets, or implement an epic under this workflow. Do not use for an explicitly scoped ad-hoc experiment, debugging task, or fix.
---

# Specialization workflow

Use `developer/<YYYY-MM-DD>-<epic-slug>/` for one epic. Do not modify
artifacts belonging to another epic or user story.

An explicit scoped request for debugging, an ad-hoc fix, or an experiment
bypasses this workflow. Otherwise, do not write implementation code outside
the steps below.

## 1. User story

Create the epic directory and write structured user stories in
`user-stories.md`. Put relevant reviews there too, such as
`security-review.md` or `refactoring-review.md`.

## 2. Tickets

Commit the epic's relevant user-story and review artifacts before creating
`tickets.md`. Develop actionable tickets in implementation order from those artifacts.

Use TDD thinking here: state observable behavior and the public interface each
ticket must cover, not implementation steps. Obtain user approval of the
ticket and behavior plan, then commit `tickets.md` before implementation.

## 3. Implementation

Implement tickets in order. For each ticket, use the TDD skill:

1. Write one behavior-focused failing test.
2. Write the minimum code to make it pass.
3. Repeat one behavior at a time; refactor only while green.
4. Run the required project checks.
5. Commit the completed ticket with the `git-commit` skill before starting the
   next ticket.

If implementation reveals new scope, return to `tickets.md`, obtain approval,
commit the updated ticket plan, then resume.

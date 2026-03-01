# CLAUDE.md — AI Assistant Guide for Felix

This file provides guidance for AI coding assistants (Claude Code and others) working in this repository. Update this file as the project evolves.

---

## Repository Overview

| Field | Value |
|---|---|
| **Project name** | Felix |
| **Repository** | bryn-rm/Felix |
| **Current state** | Early-stage / skeleton |
| **Primary branch** | `master` |
| **Last updated** | 2026-03-01 |

The project currently contains only an initial commit. No language, framework, or build tooling has been configured yet. All sections below should be updated once the technology stack is chosen.

---

## Branch Conventions

- `master` — stable production branch; never push broken code here
- `claude/<session-id>` — auto-created branches used by Claude Code sessions (e.g. `claude/claude-md-mm7s5si9rac8buul-V2LTs`)
- Feature branches should follow the pattern: `<type>/<short-description>` (e.g. `feat/user-auth`, `fix/login-bug`)

When working as an AI assistant:
1. Always develop on the designated session branch (never `master` unless explicitly told).
2. Commit with clear, descriptive messages explaining the *why*, not just the *what*.
3. Push with `git push -u origin <branch-name>`.

---

## Project Structure

The repository is currently empty beyond the README. When source code is added, document the structure here. A suggested layout for a typical project:

```
Felix/
├── CLAUDE.md          # This file — AI assistant guide
├── README.md          # Human-facing project overview
├── src/               # Application source code
├── tests/             # Test files mirroring src/ structure
├── docs/              # Extended documentation
└── scripts/           # Build, deploy, and utility scripts
```

Update this section to reflect the real layout once files are added.

---

## Technology Stack

> **TODO:** Fill in once the stack is decided.

| Concern | Tool |
|---|---|
| Language | TBD |
| Framework | TBD |
| Package manager | TBD |
| Test runner | TBD |
| Linter / formatter | TBD |
| CI/CD | TBD |

---

## Development Workflow

### Getting started

```bash
# Clone the repo
git clone http://local_proxy@127.0.0.1:16578/git/bryn-rm/Felix
cd Felix

# (install dependencies once the stack is configured)
```

### Common commands

> **TODO:** Add real commands once build tooling is in place.

```bash
# Run tests
# <test command here>

# Lint / format
# <lint command here>

# Build
# <build command here>

# Start development server
# <dev server command here>
```

---

## Coding Conventions

These apply regardless of the final technology stack:

- **Simplicity first** — prefer the minimum complexity that solves the problem; avoid premature abstractions.
- **No speculative code** — only implement what is currently required; do not add "future-proofing" unless asked.
- **Small, focused commits** — each commit should represent a single logical change.
- **Tests alongside code** — new features should include tests; bug fixes should include a regression test.
- **No secrets in source** — never commit API keys, passwords, or tokens; use environment variables loaded from a `.env` file (excluded via `.gitignore`).

---

## Testing

> **TODO:** Document the test framework and how to run tests once configured.

General principles to follow until the framework is chosen:
- Tests live next to or near the code they test.
- All tests must pass before merging to `master`.
- Aim for coverage of edge cases and error paths, not just the happy path.

---

## AI Assistant Instructions

When working in this repo as an AI assistant:

1. **Read before editing** — always read a file before modifying it; never guess at content.
2. **Minimal changes** — make only the changes needed to satisfy the request; do not refactor unrelated code.
3. **No unasked improvements** — do not add comments, docstrings, type annotations, or error handling that wasn't asked for.
4. **Security** — do not introduce command injection, XSS, SQL injection, or other OWASP Top 10 vulnerabilities.
5. **Confirm before destructive actions** — deleting files/branches, force-pushing, or modifying CI/CD pipelines requires explicit user confirmation.
6. **Update this file** — if you add a significant feature, change the stack, or establish a new convention, update the relevant section of CLAUDE.md.

---

## Git Remote

```
origin → http://local_proxy@127.0.0.1:16578/git/bryn-rm/Felix
```

Push to a branch with:
```bash
git push -u origin <branch-name>
```

If a push fails due to a network error, retry up to 4 times with exponential backoff (2 s, 4 s, 8 s, 16 s).

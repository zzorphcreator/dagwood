# Contributing to Dagwood

Thanks for taking a look. Dagwood is a learning project: it is meant to stay small and readable,
so the most useful contributions are bug fixes, clearer code, better tests and better docs.

## Before you start

- For anything larger than a small fix, open an issue first so we can agree on the approach.
- Features that add a lot of code or a new dependency need a good reason. "Readable in an
  afternoon" matters more here than feature count.

## Setup

Follow the [Quick start](README.md#quick-start). You don't need any API key to run the tests.

## Checks

Run these before opening a pull request. CI runs the same ones.

```bash
python -m pytest -q tests        # backend; no network, no keys

cd ui
npm run lint
npm run build                    # type-check and build
```

## Guidelines

- **Add a test with every behaviour change.** Backend tests use pydantic-ai's `TestModel` and
  `FunctionModel`, so they never call a real model. Keep it that way: a test must not need a key
  or spend money.
- **Keep changes focused.** One fix or feature per pull request, without unrelated refactors.
- **Comments explain why**, not what. Many comments in this repo record a failure that led to the
  code; that is the style to follow.
- **Update the docs you touch.** New settings go in `.env.example`; new limits or caveats go in
  the README's Limitations section.
- **Never commit run data or secrets.** `.env`, `runs.db` and `artifacts/` are git-ignored
  because they hold keys, prompts and agent outputs. Check screenshots for the same.

## Reporting bugs

Include what you submitted (task, cost mode, "Deliver as"), what you expected, what happened, and
the failing agent's error from the node panel if there is one. Remove anything private first.

Security problems: see [SECURITY.md](SECURITY.md) instead of opening a public issue.

## License

By contributing you agree that your contributions are licensed under the [MIT License](LICENSE).

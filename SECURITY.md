# Security

## What Dagwood is designed for

Dagwood is a learning project that runs **on your own machine, for one user**. It has no
authentication, and it stores prompts and agent outputs in plain text (`runs.db`, `artifacts/`).
Do not expose it to a network or run it as a shared service. See
[Limitations](README.md#limitations-read-before-you-rely-on-it).

Within that design, these are the protections it has, and a break in any of them is a
vulnerability worth reporting:

- The API only answers requests whose `Host` header is localhost (`ORCHESTRATOR_ALLOWED_HOSTS`),
  so web pages cannot reach it through DNS rebinding.
- Generated files are served only by names recorded on a run, never by a client-supplied path.
  Generated HTML slide decks are served in a sandbox with no access to the API.
- PDF rendering does not fetch any URL.
- The built-in `calc` tool evaluates a whitelisted arithmetic subset with bounded results.
- Tools that Composio marks as destructive are blocked unless `COMPOSIO_ALLOW_DESTRUCTIVE=true`.

Known and out of scope: agents read untrusted text (web pages, tool results) and can be steered
by it (prompt injection). The safeguards are the per-agent tool and token caps, plan review, and
the destructive-tool block. Give agents only the toolkits a task needs.

## Reporting a vulnerability

Please do not open a public issue. Use GitHub's private reporting instead: on the repository's
**Security** tab, choose **Report a vulnerability**.

Include the affected file or endpoint, steps to reproduce, and what an attacker gains. This is a
spare-time project, so there is no guaranteed response time, but reports are read and fixed in
order of severity.

Only the latest commit on `main` is supported.

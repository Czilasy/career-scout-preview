# Security Policy

## Supported Versions

Only the latest release on `master` receives security fixes.

## Reporting a Vulnerability

Do **not** open a public issue for security problems. Send a private report to `czyooutzilas-sketch@users.noreply.github.com` with as much detail as possible:

- affected version / commit
- steps to reproduce
- impact and severity estimate

You will receive a reply as soon as the report can be triaged. Fixes are coordinated before public disclosure.

## Local Data Boundaries

- AI API keys are stored in the operating system credential store, never in SQLite, logs, exports, or API responses.
- Résumés and job data stay on the local machine unless the user explicitly configures an external AI service.
- State and browser profiles live under `~/.career-scout/` and are intentionally excluded from Git.
- Automated browser profiles are isolated and never share cookies with the user's main Chrome.

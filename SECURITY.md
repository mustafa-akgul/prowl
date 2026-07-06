# Security Policy

## Supported versions

| Version | Supported |
|---------|-----------|
| 3.x | ✅ |
| < 3.0 | ❌ |

## Reporting a vulnerability

Please **do not** open a public issue for security problems. Instead, use
GitHub's [private vulnerability reporting](https://docs.github.com/en/code-security/security-advisories/guidance-on-reporting-and-writing-information-about-vulnerabilities/privately-reporting-a-security-vulnerability)
on this repository. You can expect an initial response within a few days.

## Handling secrets

- All credentials (LLM API keys, GitHub tokens, Jira tokens, SMTP passwords,
  webhook secrets) are supplied via environment variables or `.env` —
  never commit them. `.env` and `.data/` are gitignored.
- Webhook payloads are verified with HMAC-SHA256 (`GITHUB_WEBHOOK_SECRET`);
  run the dashboard behind TLS in production (the bundled nginx config is a
  starting point).
- The Settings API masks stored secrets before returning them to the browser.

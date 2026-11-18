# Security Policy

## Reporting a vulnerability

Please **do not** open a public issue for security problems.
Use GitHub's private security advisory form (Security tab → "Report a vulnerability").
You'll get a reply within 5 working days.

## Credentials

TokenWarden only needs **read-only** credentials (provider admin usage keys,
Azure *Cost Management Reader*). Never commit `.env` or `collectors/teams.yaml`;
both are git-ignored. The scanner's demo mode generates fake keys at runtime.

# Contributing

Thanks for helping out!

1. Fork the repo and create a branch: `git checkout -b feat/my-change`
2. Run the demo locally: `docker compose up -d`, then open http://localhost:3000
3. Test collectors without keys: `docker compose run --rm collector --mock`
4. Keep Python lint-clean with `ruff check`
5. Use [Conventional Commits](https://www.conventionalcommits.org) (`feat:`, `fix:`, `docs:`)
6. Open a pull request describing what changed and how you tested it

Never include real API keys, customer data or `.env` files in a PR.

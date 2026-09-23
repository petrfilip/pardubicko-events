# Repository Guidelines

## Source of truth (ADR 0008) — read first

Since 2026-09-23 the production SQLite database behind https://pardubicko.tix.cz
is the single source of truth, and agents read and write it only through the
token-authenticated `/api/v1` with the client `tools/client/pardubicko_client.py`.
Event data in `data/`, `research/`, `stats/` and `config/` is a frozen snapshot:
do not edit it and do not read current state from it; `pipeline.py export`,
`publish-candidate --apply` and `resolve-candidate` refuse to write.

- Agent roles, client setup, `run_id` and reporting: `docs/agents/README.md`
  (in Czech, like the rest of `docs/`).
- Weekly collection: the `collect-events-week` skill.
- Pipeline of registered sources: `tools/pipeline/run.py`, an API client with a
  local cache only (`tools/pipeline/README.md`).
- The daily pipeline and curation run in the NanoClaw group `pardubicko`.

## Project Structure & Module Organization

The root `index.html`, CSS, and `js/` modules form the static reference site; `web/src/`, `web/templates/`, and `web/public/` contain the PHP/SQLite application. Published events, sources and taxonomies live in the server database; `data/weeks/` and `config/` hold the frozen 2026-09-23 snapshot that tests and the local import use. Python ingestion, validation, pipeline, and operations code is under `tools/`. Keep architecture notes in `docs/` and fixtures beside their owning tool, such as `tools/pipeline/fixtures/`.

## Build, Test, and Development Commands

- `docker compose up web` serves the static site at `http://localhost:8080`.
- `python3 tools/pipeline/pipeline.py import` builds a local SQLite database from the frozen repository snapshot, for development and tests only.
- `docker compose up app` serves the PHP application at `http://localhost:8081` over that local database.
- `bin/deploy` tests, stages and deploys the PHP app and API to pardubicko.tix.cz (`docs/production-runbook.md`).
- `docker compose run --rm validate` checks JSON schemas and cross-file data rules.
- `docker compose run --rm --build tests` runs Python, Node, PHP, HTTP smoke, validation, and lossless round-trip checks.
- `python3 tools/run_tests.py --list` lists deterministic test scripts.
- `docker compose run --rm linkcheck` performs the separate, network-dependent source URL check.

## Coding Style & Naming Conventions

Match nearby code: four-space indentation in Python and PHP, two spaces in JavaScript, and no tabs. Use `snake_case` for Python functions and files, `camelCase` for JavaScript and PHP methods, and `PascalCase` for PHP classes. PHP uses `declare(strict_types=1)`. Preserve repository JSON formatting and use kebab-case stable IDs; week files must follow `YYYY-Www.json`. No repository-wide formatter is configured, so keep diffs minimal.

## Testing Guidelines

Tests are standalone deterministic scripts discovered as `test_*.py` and `test_*.mjs`; PHP and HTTP integration tests live in `web/tests/`. Add regression coverage beside the changed subsystem and avoid live network access in deterministic tests. For validation rules, include a fixture that proves malformed input fails. Run the full Docker test command before submitting data, pipeline, or web changes; no numeric coverage threshold is enforced.

## Commit & Pull Request Guidelines

Recent commits use short imperative subjects, usually `type(scope): summary` (for example, `feat(pipeline): add ...`) or focused prefixes such as `docs:`, `ops:`, and `ci:`. Keep commits narrowly scoped. Pull requests should explain intent, list verification commands, link relevant issues or ADRs, and include screenshots for visible frontend changes. Call out schema, configuration, migration, or operational impacts explicitly.

## Security & Configuration

Copy settings from `.env.example`; never commit `.env`, inbox tokens, TLS keys, databases, or `var/` runtime snapshots. Git-tracked JSON is a frozen snapshot from 2026-09-23; the production database is the source of truth (ADR 0008).

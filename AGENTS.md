# Repository Guidelines

## Project Structure & Module Organization

The root `index.html`, CSS, and `js/` modules form the static reference site; `web/src/`, `web/templates/`, and `web/public/` contain the PHP/SQLite application. Published events live in `data/weeks/YYYY-Www.json`; curated registries and taxonomies belong in `config/`. Python ingestion, validation, pipeline, and operations code is under `tools/`. Keep architecture notes in `docs/` and fixtures beside their owning tool, such as `tools/pipeline/fixtures/`.

## Build, Test, and Development Commands

- `docker compose up web` serves the static site at `http://localhost:8080`.
- `python3 tools/pipeline/pipeline.py import` rebuilds the derived SQLite database from repository data.
- `docker compose up app` serves the PHP application at `http://localhost:8081` after import.
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

Copy settings from `.env.example`; never commit `.env`, inbox tokens, TLS keys, databases, or `var/` runtime snapshots. Git-tracked JSON remains the source of truth; treat SQLite as rebuildable derived state.

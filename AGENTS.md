# Repository Guidelines

## Project Structure & Module Organization
`camsleuth.py` is the main CLI and contains the discovery, indexing, export, and local map-serving logic. Source catalogs and seed data live in `sources/`, including `open_trailcam_dbs.json`, `personal_trailcam_sources.json`, `official_govt_cam_sources.json`, `partner_nonprofit_cam_sources.json`, `social_trailcam_sources.json`, and `social_manual_seeds.csv`. The static map UI lives in `web/` with `index.html`, `app.js`, and `style.css`. Generated artifacts should stay out of source files and typically land in `trailcam_cache/`, `trailcam_api_maps/`, `trailcam_coverage/`, `trailcam_personal_cache/`, `trailcam_government_cam_cache/`, `trailcam_partner_nonprofit_cache/`, or `trailcam_social_cache/`.

## Build, Test, and Development Commands
Run the CLI directly with Python 3:

```bash
python3 camsleuth.py --check
python3 camsleuth.py --validate-government-cam-config
python3 camsleuth.py --validate-partner-nonprofit-config
python3 camsleuth.py --build-location-index
python3 camsleuth.py --serve-map --map-port 8765
python3 camsleuth.py --personal-sources sources/personal_trailcam_sources.json --discover
```

Use `--check` to validate configured open datasets, `--validate-government-cam-config` for agency camera sources, `--validate-partner-nonprofit-config` for partner camera sources, `--build-location-index` to generate coverage outputs, and `--serve-map` to preview the frontend at `http://127.0.0.1:8765/`. Use the personal, government-cam, partner/nonprofit, or social discovery commands from `README.md` when changing source-specific logic.

## Coding Style & Naming Conventions
Follow the existing style in each file: Python uses 4-space indentation, snake_case names, and small helper functions; frontend files use 2-space indentation, `const`/`let`, and camelCase function names. Prefer standard-library Python unless a dependency is clearly necessary. Keep config keys descriptive and stable, matching existing patterns like `source_id`, `display_name`, and `license_status`.

## Testing Guidelines
There is no formal automated test suite yet. Before opening a PR, run the CLI paths touched by your change and verify outputs are written to the expected generated directories. For frontend changes, rebuild coverage data if needed, run `python3 camsleuth.py --serve-map`, and manually confirm the map, layer toggles, and planned-camera interactions in the browser.

## Commit & Pull Request Guidelines
Recent commits use short, imperative subjects such as `added web page` and `Add personal and social discovery workflows`. Keep commit messages concise, action-oriented, and scoped to one change. PRs should include a clear summary, note any config or data-file changes, link related issues, and add screenshots when `web/` behavior changes.

## Security & Configuration Tips
Do not commit `trailcam_creds.local.json`, cached downloads, or generated coverage exports unless intentionally updating fixtures. Treat personal and social sources as metadata-only discovery inputs; preserve the repository’s rule against implying reuse rights for private or platform-hosted media.

# CamSleuth

This package contains:

- `camsleuth.py` - CLI for checking, mapping, and searching configured open/public trail-camera databases.
- `sources/open_trailcam_dbs.json` - hand-editable database catalog.
- `sources/official_govt_cam_sources.json` - official government/public-agency wildlife camera catalog.
- `sources/partner_nonprofit_cam_sources.json` - partner, nonprofit, university, refuge, and conservancy camera catalog.
- `trailcam_creds.local.json` - generated local credential template. Do not commit this file.

## Basic usage

```bash
python3 camsleuth.py --init-creds
python3 camsleuth.py --check
python3 camsleuth.py --scan-dbs
python3 camsleuth.py --map
python3 camsleuth.py --list-api lila_catalog
python3 camsleuth.py --db caltech_camera_traps --metadata-extract 5a21788e-23d2-11e8-a6a3-ec086b02610b.jpg
python3 camsleuth.py --db lila_catalog --find deer Pennsylvania --limit 10
```

## Personal / Small-Collection Trail-Cam Sources

```bash
python3 camsleuth.py --personal-sources sources/personal_trailcam_sources.json --validate-personal-config
python3 camsleuth.py --personal-sources sources/personal_trailcam_sources.json --check-personal
python3 camsleuth.py --personal-sources sources/personal_trailcam_sources.json --discover
python3 camsleuth.py --personal-source all --find bobcat --limit 10
python3 camsleuth.py --personal-source all --find "trail camera" coyote --export-results coyote_personal_results.csv
python3 camsleuth.py --personal-sources sources/personal_trailcam_sources.json --export-leads trailcam_personal_leads.csv
```

These sources are public web sources, not open-source datasets. Use them for discovery, metadata indexing, and outreach. Do not download or reuse full media unless the license permits it or the owner grants permission.

## Official Government Wildlife Cams

Official agency camera pages are tracked separately from personal sources and open datasets:

```bash
python3 camsleuth.py --government-cam-sources sources/official_govt_cam_sources.json --validate-government-cam-config
python3 camsleuth.py --government-cam-sources sources/official_govt_cam_sources.json --check-government-cams
python3 camsleuth.py --government-cam-sources sources/official_govt_cam_sources.json --discover-government-cams
python3 camsleuth.py --government-cam-source all --find elk --limit 10
python3 camsleuth.py --government-cam-sources sources/official_govt_cam_sources.json --export-government-cam-leads trailcam_government_cam_leads.csv
```

These sources are public viewing or public-submission surfaces, not open media datasets. Keep media downloads disabled unless agency terms or explicit permission allow reuse.

## Partner / Nonprofit Wildlife Cams

Partner and nonprofit camera pages are tracked separately from official agency and personal sources:

```bash
python3 camsleuth.py --partner-nonprofit-sources sources/partner_nonprofit_cam_sources.json --validate-partner-nonprofit-config
python3 camsleuth.py --partner-nonprofit-sources sources/partner_nonprofit_cam_sources.json --check-partner-nonprofit
python3 camsleuth.py --partner-nonprofit-sources sources/partner_nonprofit_cam_sources.json --discover-partner-nonprofit
python3 camsleuth.py --partner-nonprofit-source all --find osprey --limit 10
python3 camsleuth.py --partner-nonprofit-sources sources/partner_nonprofit_cam_sources.json --export-partner-nonprofit-leads trailcam_partner_nonprofit_leads.csv
```

These include nonprofit, university, refuge friends group, conservancy, and partner-hosted wildlife cameras. Treat them as discovery and outreach leads unless the partner explicitly grants reuse rights.

## Social Trail-Cam Discovery

Social platforms are used for metadata-only discovery and outreach. They are not treated as open datasets. The tool does not download videos/images by default and does not scrape private or logged-in content.

```bash
python3 camsleuth.py --social-sources sources/social_trailcam_sources.json --validate-social-config
python3 camsleuth.py --social-sources sources/social_trailcam_sources.json --check-social
python3 camsleuth.py --social-sources sources/social_trailcam_sources.json --discover-social
python3 camsleuth.py --social-source all --find bobcat --limit 10
python3 camsleuth.py --social-source all --find pennsylvania deer --export-social-results pa_deer_social_results.csv
python3 camsleuth.py --social-sources sources/social_trailcam_sources.json --export-social-leads trailcam_social_leads.csv
```

Public social posts are not automatically reusable training data. Use exported leads for outreach. Ingest original media only when the creator grants permission or the post/license explicitly allows reuse.

## Location Coverage

CamSleuth can build a location index from open datasets, personal sources, official government cams, partner/nonprofit cams, social discovery metadata, and manual leads. It tracks coordinate precision explicitly so county/state/social signals are not confused with confirmed camera deployments.

```bash
python3 camsleuth.py --build-location-index
python3 camsleuth.py --export-geojson trailcam_coverage/trailcam_locations.geojson
python3 camsleuth.py --serve-map --map-port 8765
```

Then open [http://127.0.0.1:8765/](http://127.0.0.1:8765/).

```bash
python3 camsleuth.py --coverage-place "Oley, PA" --radius-miles 25 --coverage-place-report trailcam_coverage/reports/oley_pa_25mi.md
python3 camsleuth.py --export-h3-coverage trailcam_coverage/trailcam_h3_coverage.geojson --h3-resolution 7
python3 camsleuth.py --export-admin-rollups
python3 camsleuth.py --coverage-report trailcam_coverage/coverage_report.md
python3 camsleuth.py --export-leads trailcam_coverage/oley_trailcam_leads.csv
```

Coverage maps show confirmed deployments, broad location signals, and leads. County/state/social points are not exact trail-camera locations. Exact private locations are never inferred.

## Searching LILA COCO datasets

LILA member datasets usually require downloading large metadata archives first:

```bash
python3 camsleuth.py --db caltech_camera_traps --download-metadata --find coyote --limit 10
python3 camsleuth.py --db nacti --download-metadata --find deer --limit 10
```

Without `--download-metadata`, structured LILA dataset search only works if the metadata archive is already cached under `trailcam_cache/`.

## API maps

Generate one API/static-surface map per configured database:

```bash
python3 camsleuth.py --map
```

Maps are written to `trailcam_api_maps/*.api_map.json` by default.

For full COCO key/count inspection, add:

```bash
python3 camsleuth.py --db caltech_camera_traps --map --download-metadata
```

## Credential model

Most configured sources are public static datasets or public download pages. The generated credentials file still exists so private/tokenized endpoints can be added later without changing the script.

To add a tokenized source, edit `sources/open_trailcam_dbs.json`:

```json
"auth": {"type": "bearer_token", "required": true}
```

Then regenerate creds:

```bash
python3 camsleuth.py --init-creds
```

Set the generated environment variable before use.

## Design constraint

This tool does not pretend that every source has a live public search API. It treats each source honestly:

- LILA datasets: static public metadata + images, mostly COCO Camera Traps JSON.
- LILA catalog: public CSV catalog.
- Wildlife Insights / eMammal: public web/download surfaces, not a declared universal public REST search API in this config.

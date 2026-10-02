# aisc-gr-statistics

## Installation

Clone the repository, then install the project and its dependencies:

```bash
uv sync
```

## Usage

Run via the CLI entrypoint:

```bash
uv run aisc-gr-statistics
```

With no arguments, the command checks the local iMIS, representative, Senate,
Census, district, and aggregate snapshots without contacting any services. It
shows whether each is usable and displays its recorded retrieval date (or the
iMIS CSV modification date). In a terminal, unavailable data is red, data no
more than 14 days old is green, and older usable data is white. It then offers
a numbered menu whose update actions identify the local snapshots they refresh.
When output is piped or redirected, it prints the same status without colors,
usage help, and exits without prompting; use one of the explicit commands below
in scripts.

The original underscore command, `uv run aisc_gr_statistics`, remains
supported for compatibility.

Create a statewide Illinois certification and membership PDF from a local iMIS
CSV export:

```bash
uv run aisc-gr-statistics report \
  --imis-csv data/raw/imis/imis-tonnage-for-gr-statistics.csv \
  --imis-export-date 2026-09-18 \
  --external-output data/processed/illinois-certification-membership-external.pdf \
  --internal-output data/processed/illinois-certification-membership-internal.pdf \
  --conflicts-csv data/processed/field-conflicts.csv \
  --candidate-matches-csv data/processed/candidate-matches.csv \
  --reconciliation-csv data/processed/reconciliation.csv \
  --reconciliation-log data/processed/reconciliation.log \
  --unknown-imis-codes-csv data/processed/unknown-imis-codes.csv \
  --tonnage-review-csv data/processed/tonnage-review.csv
```

Each run creates two PDFs from the same prepared company data. The external
PDF is safe to share: it includes company name, city and state, membership,
and active certification statements (including Certified Erector). It never
includes street addresses, postal codes, company-level employee counts,
tonnage, or source/provenance details. When at least two listed companies have
valid employee counts, it shows one Illinois-wide employee total; otherwise it
does not show a total. The internal PDF is for staff review and retains the
detailed addresses, individual employee counts, tonnage, provenance, and
Illinois U.S. Senate contacts.

### Report exclusions

`config/report_exclusions.toml` is version-controlled business configuration
for companies that must be omitted from report outputs. Add or remove quoted
phrases in `excluded_name_substrings`; matching is case-insensitive and works
as a substring. When either iMIS or Salesforce name matches a phrase, that
record and any exact shared-iMIS-ID counterpart are omitted from the PDFs,
conflicts, candidate matches, and reconciliation files. The original source
data and source-wide iMIS code and tonnage review scans are unchanged.

## Congressional district enrichment

### External district and Senate PDFs

Create the saved district and aggregate snapshots first, then create a
one- or two-page external PDF without making any network calls:

```bash
uv run aisc-gr-statistics district-report \
  --district 7 --senate \
  --districts-csv data/processed/company-districts.csv \
  --aggregates-csv data/processed/district-aggregates.csv \
  --as-of 2026-09-30 \
  --output-dir data/processed
```

Use `--district` more than once for individual PDFs, or use `--all-districts`
for one House PDF containing only districts with companies. Selecting an empty
district is informational and creates no PDF; if every district is empty, the
combined command also succeeds without creating a PDF. Output filenames are stable
(`illinois-congressional-district-07-external.pdf`,
`illinois-congressional-districts-external.pdf`, and
`illinois-senate-delegation-external.pdf`). The report only contains a member
identity, company name/city/county, one approved relationship summary,
company counts, thresholded known jobs totals and employee-data coverage, and
source dates. Company summaries use the full printable width and wrap at
measured word boundaries. A map is drawn only when it has its own lower-page
region; House maps are omitted when necessary to keep each district on one
sheet, while Senate reports may use one text-only continuation page. It never
receives addresses, individual employee counts, tonnage, IDs, Census
coordinates, classifications, raw certification data, or reconciliation data.
District PDFs label their two totals `District known jobs` and `Illinois known
jobs`; statewide PDFs label them `Illinois known jobs` and `National known jobs`.
Each total is displayed only when at least two companies contribute employee
data; otherwise that total says `N/A (employee data not available)`.

House and Senate-delegation PDFs use the same public-display contract. Map
coordinates are private drawing input and are never public company fields.

Company entries use a minimum 8-point font and wrap long names at measured
word boundaries. Each complete entry stays in one column. Reports use the
existing full first-page layout with a 75%-opaque lower-page map behind the
text and, when needed, one compact text-only continuation page with the
company list and source footer.

Each CSV has a checksum sidecar written beside it. The report rejects a missing
or changed sidecar, missing aggregate row, incompatible Census map, an
unbreakable word wider than a column, or a complete list that cannot fit in at
most two pages at 8 points; in those cases it writes no new PDF and never
truncates company details. A Senate
report uses the confirmed Illinois snapshot population. Companies left in
`address-district-review.csv` are excluded from every aggregate and PDF list.

The checked-in Census KML/map metadata and Census place/county reference
snapshots are used offline, so PDF creation stays
reproducible when a network connection is unavailable. Maintainers can refresh
the official Census 2025 Illinois 119th-Congress KML archive, then review and
commit both files:

```bash
uv run aisc-gr-statistics refresh-district-boundaries
```

The refresh command downloads the official [Census Cartographic Boundary
File](https://www.census.gov/geographies/mapping-files/2025/geo/carto-boundary-file.html),
extracts its Illinois KML, verifies all 17 district GEOIDs and its geometry,
and records the source URL, retrieval time, Congress, and KML checksum. A bad
download, ZIP, XML document, or district set leaves the existing KML and
metadata unchanged. Both files are staged before replacement; if either
replacement fails, the command restores the prior KML and metadata as a pair.
`--source-url` and `--congressional-session` are optional overrides for a
reviewed future Census release.

District-map company markers deliberately use city-level precision: a saved
city name is normalized and matched to the checked-in Census place snapshot.
If it does not match, its saved county FIPS selects the Census county reference
point instead. The address-geocoder latitude and longitude in the saved CSV
never reach the external PDF. Refresh and review the accompanying Census
Gazetteer snapshots separately when needed:

```bash
uv run aisc-gr-statistics refresh-map-references
```

The command fetches the official Census place and county Gazetteer files,
validates Illinois places and all 102 counties, then records checksums and
provenance. House maps frame the selected district and nearby company points;
Senate maps retain an Illinois-wide view. Markers that collide in final PDF
positions become a single deterministic, labeled count marker. There are no
web tiles, runtime downloads, or interactive map behavior during PDF creation.

District lookup is intentionally separate, so PDF creation remains offline. It
uses the public [U.S. Census Geocoding Services API](https://geocoding.geo.census.gov/geocoder/Geocoding_Services_API.html)
with Census's current address benchmark and congressional-geography vintage:

```bash
uv run aisc-gr-statistics enrich-districts \
  --imis-csv data/raw/imis/imis-tonnage-for-gr-statistics.csv \
  --districts-csv data/processed/company-districts.csv \
  --review-csv data/processed/address-district-review.csv \
  --address-conversions-csv data/processed/address-conversions.csv \
  --as-of 2026-09-30
```

The required conversion table records each report company's selected source
address alongside the normalized street, city, state, and ZIP fields sent to
Census, plus a status and reason when the address cannot be used. It is an
auditable local output only: it never changes the original iMIS or Salesforce
data.

The command handles the report population: Illinois iMIS companies and
report-eligible Salesforce-only certified companies when credentials are
available. It prefers a complete Salesforce Billing Address, then a complete
iMIS address. “Complete” means street number, city, state, and ZIP code.
When iMIS has no separate postal-code value, a ZIP or ZIP+4 at the end of its
address field is used for the Census lookup. It may be followed only by a final
U.S. country label such as `UNITED STATES`, `US`, `U.S.`, or `U.S.A.`.
For district enrichment only, repeated non-blank iMIS IDs use the row with the
latest parseable `Submission Date`; tied, missing, or invalid dates keep the
first CSV row. Blank iMIS IDs are kept as separate rows.

Only a single Census candidate with both county and congressional-district
geography is accepted. `company-districts.csv` records the standardized address,
FIPS/GEOID values, coordinates, benchmark, vintage, and lookup date. Review
`address-district-review.csv` for incomplete addresses, no/multiple candidates,
missing geography, malformed responses, or service errors. The command writes
both files after a Census outage but exits nonzero, so partial data is not
mistaken for a finished run. Refresh districts by running the command again;
each run records the current Census values it used.

`config/reviewed-geography-fallback.csv` can provide a reviewed assignment only
after Census cannot assign a company. Its company name, classification, iMIS
ID, and Salesforce account ID must exactly match; at least one ID, complete
FIPS/GEOID values, reviewer/source note, and ISO reviewed date are required.
This Illinois snapshot accepts only textual state `IL` paired with state FIPS
`17`.
Optional map references may only be checked-in Census `place` keys or `county`
FIPS keys—coordinates are never accepted. Use `--fallback-csv` to provide a
different reviewed file. Nonmatching rows remain unresolved, and snapshot
metadata records Census-confirmed, fallback-confirmed, unresolved, and
included-company counts.

`--as-of YYYY-MM-DD` is required and is the certification-effective report
date. Enrichment saves exactly one safe public relationship phrase per company:
a validated `AISC Certified Fabricator`, `AISC Certified Erector`, or `AISC
Certified Fabricator/Erector` phrase; a translated iMIS membership/category
label; or `AISC relationship unavailable`. Raw Salesforce/iMIS codes,
certification names, IDs, and statuses never enter the external PDF. Regenerate
the ignored operational district and aggregate snapshots after source changes,
then use that exact date with `district-report`.

`Current_Current` is Census's moving current vintage, so its response can use a
session-qualified layer such as `120th Congressional Districts`, matching the
[current 120th Congress geography](https://tigerweb.geo.census.gov/tigerwebmain/TIGERweb_main.html).
When Census returns more than one numbered congressional layer, enrichment uses
the highest session number. The generic `Congressional Districts` layer is used
only when no numbered layer is present; a malformed newest layer is sent to
review rather than replaced with older geography.

Create national, Illinois-state, and congressional-district aggregate data for later reports
from a saved district snapshot:

```bash
uv run aisc-gr-statistics aggregate-districts \
  --imis-csv data/raw/imis/imis-tonnage-for-gr-statistics.csv \
  --districts-csv data/processed/company-districts.csv \
  --review-csv data/processed/address-district-review.csv \
  --aggregates-csv data/processed/district-aggregates.csv
```

This step performs no Census lookup. It requires the confirmed and unresolved
CSVs to partition the current eligible population exactly once; unresolved
companies are excluded from every displayed total. Each aggregate records
`included_company_count`, Census-confirmed and fallback-confirmed counts,
`unresolved_company_count`, and employee-data coverage counts. `known_jobs` is
an internal partial sum of valid whole-number employee counts; public reports
show `N/A` when fewer than two contributors have employee data. Aggregate
metadata records checksums for both inputs, and a PDF rejects an aggregate made
from a different district snapshot.

To resolve a review record, verify its geography, then manually copy its exact
identity plus required FIPS/GEOID, reviewer source note, reviewed date, and
safe place/county map reference into the version-controlled
`config/reviewed-geography-fallback.csv`. Rerun enrichment and aggregation;
the review CSV itself remains the unresolved queue.

The internal Senate-contact section reads a committed local Senate.gov XML
snapshot, so report creation does not fetch data from the network and remains
reproducible. The snapshot lives at `data/reference/senate/senators.xml`; its
source URL, UTC retrieval time, and SHA-256 integrity checksum are in
`data/reference/senate/senators.json`.

The internal PDF ends with a **Report provenance** block. It records the iMIS export
filename and required `--imis-export-date`, the selected tonnage calendar year,
the Senate snapshot's elected-official data retrieval date, and the Salesforce
retrieval date. Dates use `YYYY-MM-DD`. If Salesforce credentials are missing
or its query fails, the PDF says `Not retrieved` without including credentials
or error details.

Maintainers refresh that reference data intentionally, then review and commit
the changed XML and JSON together:

```bash
uv run aisc-gr-statistics refresh-senators
```

The official source is the [U.S. Senate contact-information XML](https://www.senate.gov/general/contact_information/senators_cfm.xml).

### U.S. House contact reference data

Both PDF versions include all 17 current Illinois U.S. House districts. Report
creation remains offline: it reads the committed snapshot in
`data/reference/house/members.xml`, `data/reference/house/contacts.json`,
`data/reference/house/metadata.json`, and the optional
`data/reference/house/photos/` assets plus their `manifest.json`. The metadata records UTC retrieval time,
the Clerk publication date when supplied, both government source URLs, and
SHA-256 checksums for the saved data.

The [House Clerk current-member XML](https://clerk.house.gov/xml/lists/MemberData.xml)
is the authority for who holds each seat (or whether it is vacant). The
[official House directory](https://www.house.gov/representatives) may only
supplement those current Clerk seats with official member-site and contact-page
links. It never fills a vacancy or substitutes a historical representative.
Any non-government source needs a documented justification before it can be
used.

Maintainers intentionally refresh the snapshot, review the XML, contacts JSON,
metadata, photo manifest, and any downloaded photo assets together, and commit them together:

```bash
uv run aisc-gr-statistics refresh-representatives
```

When the official House directory supplies an image URL, the refresh command
downloads it once, validates it as a JPEG or PNG, records its source URL,
filename, media type, and SHA-256 checksum in the manifest, and stores it in
the version-controlled `photos/` directory. A failed or invalid image aborts
the refresh without replacing the prior snapshot. Rendering only embeds these
validated local files and never makes a network request. This separate refresh
step makes an earlier report reproducible without network access; the final PDF
Sources section identifies the House sources and snapshot date.

The source export and generated PDF/CSV files should stay in the ignored `data/raw/` and
`data/processed/` folders. The report reads Salesforce only when both
`SF_CLIENT_ID` and `SF_CLIENT_SECRET` are set. iMIS and Salesforce records are
joined only by an exact shared iMIS ID; names never create a join. IDs are
trimmed text keys, not numbers, so `00123` and `123` remain different IDs. If a
nonblank ID appears more than once in either source, none of the records with
that ID are joined; they remain separate for review. The conflicts CSV lists
both differing values on valid ID joins and duplicate-ID findings. The
candidate-matches CSV lists name/city/state lookalikes with different or missing
IDs. The reconciliation CSV is the complete, spreadsheet-filterable source
review file; its companion log summarizes counts and records needing attention.
Review both reconciliation files in `data/processed/` before using the PDF.
Also review `unknown-imis-codes.csv`: it lists blank and unconfirmed iMIS Type
and Category codes from every export row, including rows outside Illinois.
Unknown or blank codes have no PDF label. Confirm each code’s meaning, then add
it to the central mapping in `src/aisc_gr_statistics/imis_fields.py`.
The internal PDF is a bordered two-column card directory titled **AISC
Certification and Membership Summary: Illinois**. It displays a
company name, address, `N Employees`, translated membership type/category,
`YEAR Structural Steel Tonnage: VALUE Tons`, and concise `AISC Certified …`
active-certification sentences. Detailed Salesforce certification names are
grouped, deduplicated, and ordered for display using the maintained rules in
`src/aisc_gr_statistics/certification_groups.py`; unfamiliar active names stay
visible in their own `AISC Certified …` sentence so they can be reviewed and
mapped later. Salesforce owns the displayed company name (with iMIS used when
that name is blank), employee count, and certification data; iMIS owns the
membership type/category and annual structural-steel tonnage. Employee counts
are whole numbers with thousands separators.

Public company cards are ordered alphabetically without considering case or
punctuation. Addresses omit `United States` regardless of capitalization and
place the city/locality on its own line. For an exact shared-iMIS-ID match, the
PDF displays the Salesforce address when available, otherwise the iMIS address,
without a source label. Address differences remain in the conflicts CSV with
both source values for reconciliation. Tonnage is rounded to a whole number in
the PDF.

Optional company-card values follow one exact rule: blank, invalid, unrecognized, or
unavailable values omit both their label and value. The PDF never uses blanks
or placeholders for Client Type, certification status, congressional district,
or representatives. Senator contacts come only from the validated local
snapshot. Name validation and source-data issues remain in the separate
reconciliation outputs. A certification is displayed only when the
Account status is exactly `Certified` and its child certification is `Active`
and effective on the report date. Client Type, including `Erector`, never
creates a certification label. Only Salesforce-only Illinois Accounts whose
Account status is exactly `Certified` are included as certification-only cards.
Eligible Salesforce-only Accounts with complete addresses that match after
case, punctuation, and whitespace normalization are combined into one card:
their names are slash-separated, active certification categories are retained
for display grouping, and valid employee counts are summed. The first formatted address
is displayed. iMIS-only and ID-matched records are never address-merged. These
cards omit iMIS membership and tonnage lines. A matched certified Account without an active child remains in
the PDF and is reported in reconciliation as needing review.

Tonnage submissions may be monthly or irregular. Each report selects the most
recently completed calendar year (a 2026 run selects 2025) and totals unique
`(iMIS ID, Tonnage Year, Submission Date)` entries. Submission Date accepts ISO
timestamps, `MM/DD/YYYY`, 24-hour `MM/DD/YYYY HH:MM:SS`, and iMIS 12-hour
`MM/DD/YYYY HH:MM:SS AM/PM` timestamps. Valid timestamps are normalized in
memory, so equivalent 24-hour and AM/PM forms are duplicate keys. Exact
duplicate keys with the same tonnage are counted once. Missing timestamps,
conflicting same-key tonnage, and other invalid selected-year rows are excluded
and written to the required `--tonnage-review-csv`; review rows retain the
original exported timestamp, and the newest valid submission supplies company
display fields. The PDF labels the selected tonnage year.

Run with development environment settings:

```bash
uv run aisc-gr-statistics
```

Or run as a Python module:

```bash
uv run python -m aisc_gr_statistics
```

## Environment Variables

`.env.example` is the committed template. Copy it to `.env` for local development:

```bash
cp .env.example .env
```

- `LOG_LEVEL` defaults to `INFO`; `.env` sets it to `DEBUG` for more detailed console output.
- `LOG_FILE` defaults to `app.log` and controls where file logs are written.

The CLI automatically loads `.env` from the directory where you run it. Values
already set in your shell take precedence over `.env` values.

## Testing

Run the test suite:

```bash
uv run pytest
```

Run tests with coverage:

```bash
uv run pytest --cov
```

GitHub Actions independently runs the full test suite (`uv run pytest`) and
lint suite (`uv run ruff check .`) for every pull request and every push to
`main`, so both checks run before changes are merged.

## Documentation

The [verified Salesforce mapping](docs/salesforce.md) documents the Account,
iMIS ID, Client Type, and child-certification relationships. Its live
validation uses read-only Salesforce metadata and SOQL requests only.

Preview documentation locally:

```bash
uv run python scripts/serve_docs.py
```

Build static documentation:

```bash
uv run mkdocs build
```

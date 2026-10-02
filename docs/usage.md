# Usage

## Installation

Clone the repository and install dependencies:

```bash
uv sync
```

## Running

Via the CLI entrypoint:

```bash
uv run aisc_gr_statistics                          # loads .env when present
```

Or as a Python module:

```bash
uv run python -m aisc_gr_statistics
```

## Illinois PDF report

Create a printable, letter-size statewide report by selecting a local iMIS
CSV explicitly:

```bash
uv run aisc_gr_statistics report \
  --imis-csv data/raw/imis/imis-tonnage-for-gr-statistics.csv \
  --imis-export-date 2026-09-18 \
  --output data/processed/illinois-certification-membership.pdf \
  --conflicts-csv data/processed/field-conflicts.csv \
  --candidate-matches-csv data/processed/candidate-matches.csv \
  --reconciliation-csv data/processed/reconciliation.csv \
  --reconciliation-log data/processed/reconciliation.log \
  --unknown-imis-codes-csv data/processed/unknown-imis-codes.csv \
  --tonnage-review-csv data/processed/tonnage-review.csv
```

Keep real exports under ignored `data/raw/imis/` and generated PDFs under
ignored `data/processed/`; neither should be committed. The command accepts
common forms of the company-name, state, city, shared-iMIS-ID, membership-type,
tonnage, and congressional-district headers. Company name, state, city, and a
recognizable shared iMIS ID column are required (individual ID and city values
may be blank).

### Report exclusions

Edit the checked-in `config/report_exclusions.toml` list to add or remove
quoted company-name phrases. Each phrase matches case-insensitively anywhere
in an iMIS or Salesforce company name. A match omits the record and any exact
shared-iMIS-ID counterpart from the PDFs and combined-account outputs
(conflicts, candidate matches, and reconciliation files). This is display
policy only: it does not change the raw source exports or the source-wide
unknown-code and tonnage-review scans.

## Congressional district enrichment

### Census boundary reference data

External district PDFs use the committed Illinois Census KML and metadata, so
rendering reports is offline and reproducible. To deliberately refresh the
official 2025 Illinois 119th-Congress boundary snapshot, run:

```bash
uv run aisc_gr_statistics refresh-district-boundaries
```

The command downloads Census's [Cartographic Boundary
File](https://www.census.gov/geographies/mapping-files/2025/geo/carto-boundary-file.html),
extracts and validates the KML ZIP, and records its source URL, UTC retrieval
time, Congress, and SHA-256 checksum. It validates all 17 Illinois districts
and their polygon geometry before atomically replacing each reference file;
download, ZIP, XML, checksum, or validation failures preserve the prior
snapshot. Both files are staged first, and a failure while replacing either
one rolls the pair back to its prior contents. Review and commit the KML and
JSON together. Maintainers may supply `--source-url` and
`--congressional-session` only when reviewing a future official Census
release.

### Census map-marker reference data

External company markers use only reviewed Census geography points. The report
normalizes each saved city and matches it to the committed Illinois place
snapshot; if there is no match, it uses the committed county point selected by
the saved county FIPS. It never draws an address or the address-geocoder
latitude/longitude stored in `company-districts.csv`.

Refresh these version-controlled Census Gazetteer snapshots separately from
normal offline PDF generation:

```bash
uv run aisc_gr_statistics refresh-map-references
```

The refresh validates Illinois place data, all 102 Illinois counties, metadata,
and checksums before replacing the snapshots. Review and commit the two data
files and metadata together. House maps frame the selected district plus marker
locations and show nearby boundaries lightly; Senate maps stay Illinois-wide.
Markers are clustered after projection into their final PDF positions, so a
nearby/overlapping group has one deterministic dot labeled with its company
count. PDF rendering uses no tiles, web-map library, runtime download, or
interactive behavior.

## External congressional reports

After `enrich-districts` and `aggregate-districts`, create offline external
one- or two-page PDFs from their saved CSV snapshots:

```bash
uv run aisc_gr_statistics district-report \
  --district 7 --district 8 --senate \
  --districts-csv data/processed/company-districts.csv \
  --aggregates-csv data/processed/district-aggregates.csv \
  --as-of 2026-09-30
```

`--all-districts` creates the House batch. The command validates CSV checksum
sidecars, House and Senate snapshots, and the committed Census boundary KML
before writing a PDF. Company-list text is never smaller than 8 points. Long
names wrap at measured word boundaries, and each company stays together in a
single column. The first page layers its text over a 75%-opaque map that fills
the printable lower page. The usual report is one page; a full list may use
one compact text-only continuation page with its own heading and source footer.

The command stops before producing a new PDF if a word is too wide for a
column or if the complete list cannot fit safely within two pages. It does not
shrink below 8 points, truncate details, or add a third page. The Senate
version also stops until every Illinois company has confirmed geography.
External reports intentionally disclose only company name, city, county, an
one approved relationship summary, safe aggregate known jobs/coverage,
public official contact information, locally stored official House photos, map,
and source dates. They never disclose
addresses, individual employee counts, tonnage, IDs, Census coordinates,
classifications, raw Salesforce/iMIS codes, certification names/statuses, or
reconciliation data. Known jobs is shown only when at
least two companies contribute employee data; otherwise it reads `N/A
(employee data not available)`.

House and Senate-delegation reports share this one public-display contract.
Map coordinates remain private map-drawing data, separate from public company
text.

Keep Census lookups separate from the offline PDF command:

```bash
uv run aisc_gr_statistics enrich-districts \
  --imis-csv data/raw/imis/imis-tonnage-for-gr-statistics.csv \
  --districts-csv data/processed/company-districts.csv \
  --review-csv data/processed/address-district-review.csv \
  --address-conversions-csv data/processed/address-conversions.csv \
  --as-of 2026-09-30
```

The required address-conversions CSV includes one row for every company in the
report population. It preserves the selected iMIS or Salesforce address and
shows the derived, normalized fields submitted to Census, including a status
and reason for incomplete addresses. It is generated under `data/processed/`
and does not modify either source system's data.

### Reviewed fallback geography

`config/reviewed-geography-fallback.csv` is checked-in configuration for
explicit assignments that have been reviewed after Census cannot assign a
company. Its `company_name`, `company_classification`, `imis_id`, and
`salesforce_account_id` are an exact composite match (with at least one ID).
Each row also needs consistent state/county/district FIPS and GEOID values, a
reviewer/source note, and an ISO reviewed date. This Illinois snapshot accepts
only textual state `IL` paired with state FIPS `17`. Optional map references
are only checked-in Census `place` keys or three-digit `county` keys;
coordinates are rejected. Missing matches stay unresolved in the review CSV.
The snapshot metadata records Census-confirmed, fallback-confirmed,
unresolved, and included-company counts; aggregates and PDFs use confirmed
rows only.

This command uses the public [U.S. Census Geocoding Services API](https://geocoding.geo.census.gov/geocoder/Geocoding_Services_API.html), requesting
`Public_AR_Current` and `Current_Current`. It includes the report population:
iMIS companies plus report-eligible Salesforce-only certified companies when
Salesforce credentials are available. A complete Salesforce Billing Address is
preferred; otherwise a complete iMIS address is used. Completeness requires a
street number, city, state, and ZIP code.
When iMIS has no separate postal-code value, a ZIP or ZIP+4 at the end of its
address field is used for the Census lookup. It may be followed only by a final
U.S. country label such as `UNITED STATES`, `US`, `U.S.`, or `U.S.A.`.
For district enrichment only, repeated non-blank iMIS IDs use the row with the
latest parseable `Submission Date`; tied, missing, or invalid dates keep the
first CSV row. Blank iMIS IDs remain separate rows.

`company-districts.csv` contains only safe assignments: exactly one Census
candidate with both County and Congressional District geography. It includes the
source and standardized addresses, county and state FIPS values, district GEOID,
coordinates, Census benchmark/vintage, and lookup date. Review every row in
`address-district-review.csv` before using the result; it explains incomplete
addresses, no or multiple candidates, missing geography, malformed responses,
and service errors. A Census service error writes the available successful and
review rows, then returns a nonzero exit status. Run enrichment again whenever
districts should be refreshed; Census current values are recorded for audit.

`--as-of YYYY-MM-DD` is required. It is saved in district snapshot metadata
and makes child-certification validation reproducible. Enrichment stores only
one safe relationship phrase for the public PDF: a validated mapped AISC
Certified phrase, a translated iMIS membership/category label, or `AISC
relationship unavailable`. It never exposes raw source codes, child
certification names, identifiers, or status values. Regenerate ignored
operational snapshots after source changes, and pass the exact saved date to
`district-report`.

The Census API's `Current_Current` vintage moves forward over time and can
return session-qualified keys such as `120th Congressional Districts`, which
corresponds to the [current 120th Congress geography](https://tigerweb.geo.census.gov/tigerwebmain/TIGERweb_main.html).
If several numbered congressional layers are present, the command selects the
highest session number. It uses the generic `Congressional Districts` key only
when there is no numbered layer, and sends a malformed newest layer to review
instead of falling back to older geography.

## District and national aggregates

Build the CSV used by later district reports without performing another Census
lookup. When Salesforce credentials are configured, the command reads the
current Salesforce population and employee counts:

```bash
uv run aisc_gr_statistics aggregate-districts \
  --imis-csv data/raw/imis/imis-tonnage-for-gr-statistics.csv \
  --districts-csv data/processed/company-districts.csv \
  --aggregates-csv data/processed/district-aggregates.csv
```

The output contains a `national` row for every included company and `district`
rows only for companies with a safe assignment in `company-districts.csv`.
Companies without an assignment therefore remain national-only, and district
totals may not equal the national total. `known_jobs` sums only valid
whole-number employee counts. A downstream report must call this **Known jobs**
and show the employee-data coverage columns, so readers do not mistake it for
a total across every company. Generated aggregate CSVs remain ignored
operational data in `data/processed/`.

`--imis-export-date` is required and must be the date iMIS created the CSV,
written as `YYYY-MM-DD`. The final **Report provenance** block records that
filename/date, the selected tonnage calendar year, the Senate snapshot's
elected-official data retrieval date, and the Salesforce data retrieval date.
All provenance dates use UTC. When Salesforce was not queried because
credentials are absent, or its query failed, its field says `Not retrieved`;
the PDF never includes credentials or raw error messages.
Tonnage submissions may be monthly or irregular. The report selects the most
recently completed calendar year (so a 2026 run selects 2025), then totals each
unique `(iMIS ID, Tonnage Year, Submission Date)` entry's `Bridge Tonnage`,
`Building Tonnage`, and `S C Tonnage`. The PDF labels the selected year. Exact
duplicate keys with identical tonnage count once; missing timestamps,
conflicting same-key tonnage, and other invalid selected-year rows are excluded
and written to the required `--tonnage-review-csv`. Company display fields come
from the latest valid selected-year submission.

### Senate contact reference data

The report places Illinois's two current U.S. Senate contacts before company
cards. It reads the committed files `data/reference/senate/senators.xml` and
`data/reference/senate/senators.json` only; it does not make a network request
while generating a PDF. The JSON records the official source, UTC retrieval
time, and SHA-256 checksum of the XML, which lets the report detect a missing,
changed, or malformed snapshot instead of silently using unverified data.

To intentionally download a new snapshot from the official [U.S. Senate XML
contact list](https://www.senate.gov/general/contact_information/senators_cfm.xml), run:

```bash
uv run aisc_gr_statistics refresh-senators
```

The command validates that every state has exactly two complete records before
replacing either local file. Review and commit the XML and JSON together after
a refresh. This makes a normal report run reproducible and usable offline.

When both `SF_CLIENT_ID` and `SF_CLIENT_SECRET` are configured, the command
reads Salesforce Account records with their child certifications. It joins only
on a populated, exact-text matching `IMISID__c` value—similar company names do
not join. IDs are whitespace-trimmed text keys and are never converted to
numbers, so `00123` and `123` do not match. If a nonblank ID is duplicated in
either source, all records using that ID remain source-only rather than being
automatically joined. This prevents a duplicate export value from silently
enriching the wrong company.
The modernized sample-inspired PDF is titled **AISC Certification and
Membership Summary: Illinois** and uses bordered two-column company cards. It
can display the company name, address, `N Employees`, translated membership
type/category, `YEAR Structural Steel Tonnage: VALUE Tons`, and concise,
wrapping `AISC Certified …` sentences for active certifications. Display rules
in `src/aisc_gr_statistics/certification_groups.py` group and deduplicate the
detailed Salesforce names, then order Fabricator and Erector groups. An active
Salesforce name without a rule remains visible in its own final `AISC Certified
…` sentence so it can be reviewed and added to that module. Salesforce owns the
displayed name for an ID match (falling back to iMIS only when that name is
blank), employee count, and certification data. iMIS owns membership
type/category and annual tonnage. Employee counts must be whole numbers and
are displayed with thousands separators.

Public company cards are sorted alphabetically while ignoring case and
punctuation. Addresses omit `United States` in any capitalization and put the
city/locality on a separate line. For an exact shared-iMIS-ID match, the PDF
displays the Salesforce address when available, otherwise the iMIS address,
without a source label. Every address difference still appears in
`--conflicts-csv` with both iMIS and Salesforce values. The PDF rounds tonnage
to a whole number.

The missing-value rule is exact: blank, invalid, unrecognized, or unavailable
optional company-card values omit both their label and value. The PDF does not display
Client Type, certification status, congressional district, or representative
fields. Senator contacts are instead a dedicated validated reference-data
section. Name validation and all source-data/reconciliation
findings remain separate from the public PDF.

A child certification is displayed only when the Account status is exactly
`Certified`, the child status is `Active`, and the report date is inclusively
between its start and end dates. Client Type is descriptive only: an `Erector`
Client Type never implies an `AISC Certified Erector` label. If more than one
Salesforce Account shares a normalized name, the iMIS company is not enriched.

The report adds only Salesforce-only Accounts whose `BillingState` is `IL` or
`Illinois` and whose `Cert_Certification_Status__c` is exactly `Certified`.
Eligible Salesforce-only Accounts with complete addresses that match after
normalizing capitalization, punctuation, and excess whitespace combine into
one card. The card uses slash-separated names, active certification categories
that are later grouped for display, summed valid whole-number employee counts,
and the first formatted
address. iMIS-only and ID-matched records are never address-merged. These
certification-only cards omit iMIS membership and tonnage lines. A matched iMIS company remains in the PDF when its Salesforce
Account is `Certified` but has no active child certifications; its missing
certification line is omitted and the `certified account without active
certifications` reconciliation issue remains.

`--conflicts-csv` always writes columns for shared iMIS ID, classification,
field, and the two source values. In addition to value conflicts from valid ID
joins, it records duplicate-ID findings with `duplicate iMIS ID` in the field
column, the affected source classification, and a count such as `2 iMIS
records` or `2 Salesforce records`. `--candidate-matches-csv` records
review-only lookalikes when normalized name, city, and state agree but IDs
differ or are missing; it never changes report matching. Records with equal
duplicate IDs are not candidate matches because their duplicate finding is
already recorded in the conflicts CSV.

`--reconciliation-csv` writes one row in `data/processed/` for every combined
company record. It includes matched, iMIS-only, and Salesforce-only records,
source IDs and names, the Salesforce Account ID, and any review issues. It is
the complete, spreadsheet-filterable reconciliation artifact. Its companion
`--reconciliation-log` writes readable counts for matches, source-only records,
duplicate IDs, missing IDs, ID-matched name differences, and certified Accounts
without active child certifications, followed by the details of every
questionable record. Review both reconciliation files before using the PDF.

`--unknown-imis-codes-csv` is a required review file for the source export. It
scans every row before Illinois filtering and aggregates blank or unconfirmed
Membership Type and Category values into `iMIS field`, `iMIS code`, `status`,
and `occurrences` columns. Known codes match after trimming and ignoring case.
Blank and unknown codes have no PDF label, so the report does not display raw,
unconfirmed values. Review this CSV, confirm each code’s meaning, and then add
the confirmed mapping in `src/aisc_gr_statistics/imis_fields.py`.

## Environment Variables

| Variable    | Default    | Description                          |
|-------------|------------|--------------------------------------|
| `LOG_LEVEL` | `INFO`     | Console log level (DEBUG, INFO, …)   |
| `LOG_FILE`  | `app.log`  | Path to the log file                 |

Copy `.env.example` to `.env` for development defaults. The CLI loads it
automatically; shell environment values take precedence.

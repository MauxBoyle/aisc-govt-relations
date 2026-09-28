 ## Recommended development issues

  ### P0 — Reliable statewide company data

  A. Identify and document the required Salesforce fields and relationships - Done

     Determine the exact Salesforce API fields for:
      - Imis_Id
      - Client Type
      - employee count
      - company address
      - Active Certifications
      - certification name/category
      - certification status and effective dates

     Validate the results against A. Lucas & Sons and A&H Steel, LLC.

     Done when Salesforce returns enough information to explain every Salesforce-derived value shown for those two examples.

  B. Pull Salesforce Accounts and Active Certifications into the report - Done

     Extend the existing read-only Salesforce connection to retrieve Accounts plus their attached Active Certifications.

     Important behaviors:
      - Include currently active certifications only.
      - Support multiple certifications per company.
      - Include Salesforce-only companies such as A&H Steel.
      - Keep certification names separate instead of combining them prematurely.

     Example acceptance results:
      - A. Lucas shows Building Fabricator and Highway Component Manufacturer.
      - A&H Steel appears even though it has no iMIS record.

  C. Create one combined company data model - Done

     The current report starts with iMIS companies and merely enriches them from Salesforce. That means Salesforce-only companies cannot appear.

     Build a combined list containing:
      - companies found in both systems;
      - iMIS-only companies;
      - Salesforce-only companies;
      - the source of every displayed value.

     This becomes the dependable input for PDF generation.

  D. Match Salesforce and iMIS companies by their shared iMIS identifier - Done

     Replace normalized company-name matching as the primary rule with:

     Salesforce Account.Imis_Id == iMIS ImisId

     Include safeguards for:
      - duplicate identifiers;
      - blank identifiers;
      - identifiers stored as numbers in one source and text in another;
      - iMIS-only and Salesforce-only companies;
      - conflicting company names on otherwise matching records.

     Name matching should only produce a review suggestion—not silently combine records.

  E. Create a reconciliation report for unmatched and questionable companies - Done

     Generate a CSV or log summary showing:
      - matched companies;
      - Salesforce-only companies;
      - iMIS-only companies;
      - duplicate ImisId values;
      - missing identifiers;
      - name differences for ID-matched companies.

     This will make data problems visible before they become incorrect PDF content.

  F. Define Membership Type and Category lookup dictionaries - Done

     Store the meanings of the iMIS codes in a central Python dictionary rather than scattering labels through the report code.

     For example, once confirmed:

     MEMBERSHIP_TYPES = {
         "ACT": "Full Member",
     }

     MEMBER_CATEGORIES = {
         "FAB": "Fabricator",
     }

     The report can then combine them into “Full Member Fabricator.” Tests should cover known codes and clearly flag unknown ones.

  G. Aggregate tonnage for the most recent completed calendar year - Done

     The current code adds Bridge, Building, and S C Tonnage within each input row, but it does not group historical records by company and year.

     Add logic to:
      - identify the year or reporting-period field;
      - choose the most recent full calendar year automatically;
      - for a report run during 2026, select 2025;
      - collect only that year’s entries;
      - sum all periods and all applicable tonnage categories into one annual company total;
      - avoid counting duplicate records twice;
      - display the chosen year explicitly.

     Before implementation, verify whether there are always four quarterly entries and what field distinguishes the reporting year and period.

  H. Apply confirmed business rules to report labels

     Establish which system controls each field. My suggested initial rules are:

      Report value              Recommended source
     ━━━━━━━━━━━━━━━━━━━━━━━━  ━━━━━━━━━━━━━━━━━━━━
      Membership status/type    iMIS
     ────────────────────────  ────────────────────
      Member category           iMIS
     ────────────────────────  ────────────────────
      Annual tonnage            iMIS
     ────────────────────────  ────────────────────
      Client Type               Salesforce
     ────────────────────────  ────────────────────
      Employee count            Salesforce
     ────────────────────────  ────────────────────
      Active Certifications     Salesforce
     ────────────────────────  ────────────────────
      Cross-system identity     Shared iMIS ID

     Also clarify whether Salesforce Client Type “Erector” is a company classification, a certification, or both. It should not automatically be
     displayed as “AISC Certified Erector” unless an active certification supports that wording.

  ### P1 — Finish and validate the statewide report

  I. Update the PDF to match the approved sample layout and terminology

     Render the combined data with:
      - company name and address;
      - employee count when available;
      - translated membership type and category;
      - one annual tonnage value with its year;
      - all active certifications;
      - clear handling of missing values.

     Long company names, addresses, and multiple certifications should wrap without overlapping other content.

  J. Add current U.S. senators and contact information

  For a statewide report, retrieve the two current senators by state. Senate.gov provides state pages, contact information, and an XML option, which
  is preferable to scraping visual HTML. Senate contact information

  Store the retrieval date and cache a snapshot so report generation remains reproducible if the site is temporarily unavailable.

  K. Add automated data-quality and PDF regression tests

  Test the important examples directly:

  - A. Lucas matches through its ID.
  - A. Lucas has two active certifications.
  - A&H Steel appears as Salesforce-only.
  - four quarterly tonnage records become one annual total.
  - older years do not appear.
  - duplicate IDs cause a visible validation error.
  - unknown membership/category codes are flagged.
  - PDF text and page layout remain usable.

  L. Show report provenance and “as of” dates

  Add a small footer or metadata section stating:

  - Salesforce retrieval date;
  - iMIS export filename/date;
  - tonnage calendar year;
  - elected-official data retrieval date.

  This prevents an older PDF from being mistaken for current information.

### P2 — External report data foundation

The report completed in P1 becomes the **Internal Government Relations Report**. It may contain detailed company-level information useful to AISC staff.

Beginning with P2, create a separate **External Government Relations Report** intended for use with members of Congress and other outside audiences. Both reports should use the same cleaned underlying company data, but the external report must apply stricter disclosure rules.

#### M. Define and enforce external-report disclosure rules

Create an explicit set of rules governing which information can appear in externally distributed reports.

Current requirements from stakeholder review:

- Never display tonnage information in the external report.
- Do not display an individual company's employee count.
- Employee/job counts may be displayed only as aggregate figures.
- Company names may be displayed.
- Company locations may be displayed at the city/county level.
- Include both AISC members and certified participants, including erectors.
- Detailed internal information can remain available in the Internal Government Relations Report.

Implement these as report-generation rules rather than relying on someone to manually remove sensitive fields before distribution.

Add automated tests that fail if prohibited company-level information appears in an external report.

#### N. Convert company addresses to congressional districts

Use company address information to assign each included company to its current congressional district.

Save:

- standardized address;
- state;
- congressional district;
- county;
- match status or confidence;
- congressional/geographic vintage;
- lookup date.

Unmatched or ambiguous addresses should go into a review file rather than being guessed.

Preserve enough geographic information internally to support mapping and future updates, even though the external report will generally display only city and county.

#### O. Calculate congressional-district and national aggregates

For each congressional district, calculate:

- number of included companies;
- total known employee/job count;
- number of companies contributing employee data;
- number of companies for which employee data is missing.

Also calculate the equivalent national totals needed to provide context in district reports.

Define how missing employee counts affect the displayed "jobs" figure so that the report does not imply that an incomplete employee total represents every company in the district.

Validate district totals against the underlying company records.

#### P. Retrieve current House-member information

For each congressional district, retrieve the current member of Congress and the information needed for the report.

Include where available:

- name;
- party;
- state and district;
- photograph;
- appropriate contact information.

Store the retrieval date and source so that reports can show when elected-official information was last updated.

Be careful to distinguish current representation from historical member records.

### P3 — Printable External Congressional District Report

#### Q. Build the single-page external district report

Create a printable one-page PDF designed for Government Relations staff to take into meetings with members of Congress.

The initial layout should include:

- member of Congress name, party, district, and photograph;
- number of AISC member/certified-participant companies in the district;
- aggregate industry jobs/employees in the district;
- national jobs/employees figure for context;
- company names;
- company city and county;
- congressional-district map;
- appropriate data "as of" dates.

The external report must not contain:

- tonnage;
- individual-company employee counts;
- other company-level information classified as internal-only.

Long company lists and districts with unusual amounts of information should remain printable and readable without exposing prohibited information.

#### R. Add district map features

Create a map suitable for the single-page district report.

The map should show, as practical:

- congressional-district boundary;
- company locations;
- enough surrounding geography to make the district recognizable.

Explore whether company locations are best represented individually or through counts/clustering when several companies are geographically close.

The map should support the printable report first. Interactive behavior is a later enhancement and should not block delivery of the meeting-ready PDF.

#### S. Add external-report validation and regression tests

Test both numerical accuracy and disclosure safety.

Tests should confirm that:

- every displayed company belongs to the selected district;
- district company counts match the underlying data;
- aggregate employee/job totals are calculated correctly;
- missing employee data is handled according to the agreed rule;
- the correct current member of Congress appears;
- tonnage never appears;
- individual-company employee counts never appear;
- internal-only fields cannot accidentally enter the external template;
- long company lists and names remain readable;
- the output remains a usable printable single-page report where practical.

#### T. Review sample external report with Government Relations

Generate one or more sample district reports for stakeholder review.

Confirm:

- terminology;
- usefulness of company count and jobs figures;
- treatment of companies with missing employee data;
- amount of company location information;
- map usefulness;
- congressional-member information;
- national comparison/context;
- readability in a printed meeting copy.

Capture requested changes before expanding the workflow.

### P4 — Statewide External Report

#### U. Adapt the external-report rules to a statewide view

Once the congressional-district report is approved, create a statewide external version using the same disclosure rules and underlying data.

Potential statewide information includes:

- total number of included companies;
- aggregate employee/jobs count;
- both U.S. senators;
- company names and city/county;
- congressional-district breakdown;
- statewide/district map.

Reuse the district-report components wherever practical rather than creating a separate reporting system.

### P5 — Interactive Government Relations Tool

#### V. Add a report-selection interface

Allow Government Relations staff to select:

- state;
- congressional district;
- statewide or district report;
- output location.

Start with the simplest interface that makes report generation reliable. A more polished web interface can follow after the external reports themselves are stable.

#### W. Add interactive congressional map/navigation

Explore an interactive interface where Government Relations staff can:

- select or click a member of Congress;
- select a state or congressional district;
- zoom to the relevant geography;
- see company counts and aggregate jobs;
- see company names/locations;
- generate the corresponding printable meeting sheet.

The interactive display must use the same external disclosure rules as the printable report.

#### X. Add preview and validation before external PDF creation

Before producing the final report, show useful validation information such as:

- selected district;
- number of companies;
- number with employee data;
- number missing employee data;
- address/district lookup failures;
- elected-official data retrieval date.

Allow data problems to be reviewed before the externally distributable PDF is produced.

### P6 — Reproducibility and Supporting Outputs

#### Y. Preserve reproducible data snapshots and caching

Preserve dated, private snapshots of the data needed to reproduce a report, including:

- Salesforce results;
- iMIS imports;
- district/geographic lookups;
- elected-official information.

Keep internal snapshots private even when they contain information that cannot appear in the External Government Relations Report.

#### Z. Add optional CSV/Excel review output

Produce a tabular internal companion file when useful for staff review of:

- district assignments;
- company counts;
- employee aggregates;
- missing employee data;
- geographic lookup failures;
- other exceptions.

This output is an internal quality-control tool and is not subject to the same presentation requirements as the external PDF.
  ## Recommended development issues

  ### P0 — Reliable statewide company data

  3. Identify and document the required Salesforce fields and relationships

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

  5. Pull Salesforce Accounts and Active Certifications into the report

     Extend the existing read-only Salesforce connection to retrieve Accounts plus their attached Active Certifications.

     Important behaviors:
      - Include currently active certifications only.
      - Support multiple certifications per company.
      - Include Salesforce-only companies such as A&H Steel.
      - Keep certification names separate instead of combining them prematurely.

     Example acceptance results:
      - A. Lucas shows Building Fabricator and Highway Component Manufacturer.
      - A&H Steel appears even though it has no iMIS record.

  7. Create one combined company data model

     The current report starts with iMIS companies and merely enriches them from Salesforce. That means Salesforce-only companies cannot appear.

     Build a combined list containing:
      - companies found in both systems;
      - iMIS-only companies;
      - Salesforce-only companies;
      - the source of every displayed value.

     This becomes the dependable input for PDF generation.

  9. Match Salesforce and iMIS companies by their shared iMIS identifier

     Replace normalized company-name matching as the primary rule with:

     Salesforce Account.Imis_Id == iMIS ImisId

     Include safeguards for:
      - duplicate identifiers;
      - blank identifiers;
      - identifiers stored as numbers in one source and text in another;
      - iMIS-only and Salesforce-only companies;
      - conflicting company names on otherwise matching records.

     Name matching should only produce a review suggestion—not silently combine records.

  11. Create a reconciliation report for unmatched and questionable companies

     Generate a CSV or log summary showing:
      - matched companies;
      - Salesforce-only companies;
      - iMIS-only companies;
      - duplicate ImisId values;
      - missing identifiers;
      - name differences for ID-matched companies.

     This will make data problems visible before they become incorrect PDF content.

  13. Define Membership Type and Category lookup dictionaries

     Store the meanings of the iMIS codes in a central Python dictionary rather than scattering labels through the report code.

     For example, once confirmed:

     MEMBERSHIP_TYPES = {
         "ACT": "Full Member",
     }

     MEMBER_CATEGORIES = {
         "FAB": "Fabricator",
     }

     The report can then combine them into “Full Member Fabricator.” Tests should cover known codes and clearly flag unknown ones.

  15. Aggregate tonnage for the most recent completed calendar year

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

  17. Apply confirmed business rules to report labels

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

  19. Update the PDF to match the approved sample layout and terminology

     Render the combined data with:
      - company name and address;
      - employee count when available;
      - translated membership type and category;
      - one annual tonnage value with its year;
      - all active certifications;
      - clear handling of missing values.

     Long company names, addresses, and multiple certifications should wrap without overlapping other content.

  21. Add current U.S. senators and contact information

  For a statewide report, retrieve the two current senators by state. Senate.gov provides state pages, contact information, and an XML option, which
  is preferable to scraping visual HTML. Senate contact information

  Store the retrieval date and cache a snapshot so report generation remains reproducible if the site is temporarily unavailable.

  23. Add automated data-quality and PDF regression tests

  Test the important examples directly:

  - A. Lucas matches through its ID.
  - A. Lucas has two active certifications.
  - A&H Steel appears as Salesforce-only.
  - four quarterly tonnage records become one annual total.
  - older years do not appear.
  - duplicate IDs cause a visible validation error.
  - unknown membership/category codes are flagged.
  - PDF text and page layout remain usable.

  25. Show report provenance and “as of” dates

  Add a small footer or metadata section stating:

  - Salesforce retrieval date;
  - iMIS export filename/date;
  - tonnage calendar year;
  - elected-official data retrieval date.

  This prevents an older PDF from being mistaken for current information.

  ### P2 — Congressional-district reports

  27. Convert company addresses to congressional districts

  Use the Census Geocoder’s geography response to map a complete address to the current congressional district. It supports both individual and
  batch address processing. Census Geocoding Services API

  Save:

  - standardized address;
  - matched district;
  - match status or confidence;
  - congressional/geographic vintage;
  - lookup date.

  Unmatched or ambiguous addresses should go into a review file rather than being guessed.

  29. Retrieve the current House member for each district

  After obtaining state and district, look up the current representative and contact details. The Congress.gov API offers machine-readable member
  data and requires an API key. Official Congress.gov API repository

  Be careful to select the member’s current term, because historical member records can contain earlier districts.

  31. Generate one congressional-district report at a time

  Reuse the cleaned company model and PDF components from the statewide report. Each district report should show:

  - current representative;
  - representative contact details;
  - companies located in that district;
  - active certifications;
  - membership classification;
  - selected-year tonnage;
  - senators, if desired.

  ### P3 — User-facing workflow and enhancements

  33. Add a report-selection interface

  Let the user choose:

  - statewide or congressional report;
  - state;
  - congressional district when applicable;
  - output location;
  - optionally, tonnage year.

  Start with an improved command-line interface. A small web interface can follow after the underlying data is reliable.

  35. Add preview and validation before PDF creation

  Show the number of:

  - matched companies;
  - Salesforce-only companies;
  - iMIS-only companies;
  - address lookup failures;
  - unknown codes;
  - missing certification details.

  Let the user correct the source data before producing the final PDF.

  37. Add reproducible data snapshots and caching

  Preserve dated, private snapshots of Salesforce results, iMIS imports, elected officials, and district lookups. This makes troubleshooting
  possible without querying every external service again.

  39. Add optional CSV/Excel output

  Producing a tabular companion file would help staff review totals, filters, matches, and exceptions before distributing the polished PDF.

  

# Project Plan

## Purpose

This project will provide the Government Relations team, currently represented by
Hope Hrabowy, with printable data pages that summarize Certification and
Membership information by:

- U.S. state
- Congressional district

Each page will also include contact information, a photograph of the current
elected representative, and other useful outside information.

## Current Goal: Monthly Report

The first version will create a multi-page report that can be sent to Hope each
month.

The report should make it easy to review the Certification and Membership
information for each state and congressional district. It should be designed
for printing or sharing as a PDF.

### Planned Data

| Data area | Initial source | Example information |
|---|---|---|
| Certification | Salesforce | Certified companies and employee counts |
| Membership | iMIS export | Member companies, company type, and annual structural steel tonnage |
| Elected representatives | External reference data | Name, contact information, photograph, and congressional district |
| Other outside information | To be identified | Information relevant to Government Relations |

## Future Goal: Self-Service Pages

Eventually, Hope should be able to create the pages she needs on demand without
having to use Python directly.

Possible ways to support this goal include:

- A simple web interface where Hope chooses a state or congressional district.
- A form or dashboard that creates a printable report or PDF.
- A scheduled monthly report, with an option to generate an updated report at
  any time.

The best approach can be chosen after the monthly-report workflow is working
and the team has learned which information is most useful.

## Proposed Phases

### Phase 1: Define the report

- [ ] Decide the exact layout and fields for each printable page.
- [ ] Identify the states and congressional districts that should be included.
- [ ] Confirm what “other relevant outside information” should appear.
- [ ] Choose the report format, such as PDF, Excel, or a printable web page.
- [ ] Review a sample page with Hope and gather feedback.

### Phase 2: Prepare the data

- [x] Bring over the initial Salesforce Account-field catalog, certification
  status values, and read-only connection foundation.
- [ ] Confirm that the initial Salesforce fields meet the report's needs.
- [ ] Define a repeatable Salesforce export process.
- [ ] Identify the iMIS fields needed for Membership data.
- [ ] Define a repeatable iMIS export process.
- [ ] Create rules for matching companies between Salesforce and iMIS when
  needed.
- [ ] Decide how representative contact information and photographs will be
  sourced and kept current.

### Phase 3: Build the monthly report

- [ ] Import and clean the Salesforce Certification data.
- [ ] Import and clean the iMIS Membership export.
- [ ] Combine the data by state and congressional district.
- [ ] Create a printable multi-page report.
- [ ] Add automated checks for missing, duplicate, or unexpected data.
- [ ] Test the report with a small sample before generating the full report.
- [ ] Establish a monthly process for creating and sending the report to Hope.

### Phase 4: Improve automation

- [ ] Replace the iMIS export with a direct iMIS API connection, if available.
- [ ] Consider a direct Salesforce connection or API workflow.
- [ ] Automate collection of representative information where appropriate.
- [ ] Document the update process and data-source assumptions.

### Phase 5: Create a self-service experience

- [ ] Choose an interface that Hope can use without Python.
- [ ] Build a prototype for selecting a state or congressional district.
- [ ] Generate printable pages or PDFs from the selected information.
- [ ] Test the workflow with Hope and incorporate feedback.
- [ ] Document how to use and maintain the tool.

## Decisions to Make

- [ ] What report format is easiest for Hope to use and print?
- [ ] What is the source of truth for a company's congressional district?
- [ ] How should companies with locations in multiple districts be handled?
- [ ] Which elected-representative details are essential?
- [ ] What additional outside information would help Government Relations?
- [ ] Who will review the monthly data before it is sent?

## Project Notes

- The project currently contains a basic Python application scaffold only; data
  ingestion and report generation have not yet been implemented.
- The first practical milestone is a reliable monthly report, not a
  self-service application.
- Data quality and a clear matching process between sources will be important
  before automation is expanded.

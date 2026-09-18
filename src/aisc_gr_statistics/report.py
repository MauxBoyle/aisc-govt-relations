"""Prepare and render the statewide Illinois membership report.

The functions in this module deliberately turn source data into plain report
models before ReportLab is involved.  A future HTML renderer can reuse that
same preparation step.
"""

import csv
import re
from collections import Counter
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from datetime import UTC, date, datetime
from decimal import ROUND_HALF_UP, Decimal, InvalidOperation
from enum import StrEnum
from pathlib import Path

from reportlab.lib import colors
from reportlab.lib.enums import TA_CENTER
from reportlab.lib.pagesizes import letter
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import inch
from reportlab.platypus import Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle

from .certification_groups import format_certification_paragraphs
from .imis_fields import (
    UndefinedImisCodeFinding,
    membership_label,
    scan_undefined_imis_codes,
)
from .salesforce_fields import (
    CertificationAccountField,
    CertificationField,
    CertificationRelationship,
    CertificationStatus,
    is_active_certification,
)
from .senate import SenatorContact


class ReportDataError(ValueError):
    """Raise when an iMIS export cannot provide the required report data."""


@dataclass(frozen=True)
class Company:
    """A cleaned membership company from an iMIS CSV export."""

    name: str
    state: str
    imis_id: str = ""
    city: str = ""
    address: str = ""
    membership_type: str = ""
    tonnage: str = ""
    district: str = ""


@dataclass(frozen=True)
class TonnageReviewFinding:
    """A selected-year iMIS row excluded from annual tonnage totals."""

    imis_id: str
    tonnage_year: str
    submission_date: str
    reason: str
    bridge_tonnage: str
    building_tonnage: str
    sc_tonnage: str


@dataclass(frozen=True)
class ReportCompany:
    """A company with display-ready values for the public PDF.

    Optional values are empty rather than placeholders.  The renderer omits
    their complete line, so incomplete source data never creates invented
    public-facing content.
    """

    name: str
    address: str = ""
    employee_count: str = ""
    membership_type: str = ""
    tonnage: str = ""
    certification_categories: tuple[str, ...] = ()


class CompanyClassification(StrEnum):
    """How a company is represented in the two source systems."""

    BOTH = "both"
    IMIS_ONLY = "imis-only"
    SALESFORCE_ONLY = "salesforce-only"


@dataclass(frozen=True)
class CombinedCompany:
    """One in-scope company, preserving both source records after an ID join."""

    classification: CompanyClassification
    imis: Company | None
    salesforce: Mapping[str, object] | None
    duplicate_id_count: int = 0

    @property
    def shared_imis_id(self) -> str:
        if self.imis:
            return _normalize_imis_identifier(self.imis.imis_id)
        return _normalize_imis_identifier(
            self.salesforce.get(CertificationAccountField.IMIS_ID)
            if self.salesforce
            else None
        )


@dataclass(frozen=True)
class Conflict:
    """A differing comparable value on an authoritatively joined company."""

    shared_imis_id: str
    company_classification: CompanyClassification
    field: str
    imis_value: str
    salesforce_value: str


@dataclass(frozen=True)
class CandidateMatch:
    """A name/location lookalike that must be reviewed, never auto-joined."""

    imis_id: str
    salesforce_account_id: str
    imis_name: str
    salesforce_name: str
    imis_city: str
    salesforce_city: str
    imis_state: str
    salesforce_state: str


@dataclass(frozen=True)
class ReconciliationRow:
    """One spreadsheet-ready row for reviewing the two source systems.

    This model is deliberately separate from ``ReportCompany``: it describes
    source-data quality and never affects the ID-only join or PDF content.
    """

    classification: str
    imis_id: str
    salesforce_account_id: str
    imis_name: str
    salesforce_name: str
    issues: tuple[str, ...]


HEADER_ALIASES = {
    "imis_id": ("imis id", "imisid", "iMISID", "shared imis id"),
    "name": ("company name", "company_name", "company", "full name"),
    "state": ("state", "billing state", "state province"),
    "city": ("city", "billing city", "city name"),
    "address": ("address", "street address", "billing street", "full address"),
    "membership_type": (
        "membership type",
        "company type",
        "company_type",
        "member type",
    ),
    "category": ("category", "member category", "membership category"),
    "tonnage": (
        "annual structural steel tonnage",
        "annual_structural_steel_tonnage",
        "tonnage",
    ),
    "district": (
        "congressional district",
        "congressional_district",
        "district",
        "us congress",
    ),
    "bridge_tonnage": ("bridge tonnage",),
    "building_tonnage": ("building tonnage",),
    "sc_tonnage": ("s c tonnage",),
    "tonnage_year": ("tonnage year",),
    "submission_date": ("submission date",),
}


def normalize_company_name(name: str) -> str:
    """Normalize punctuation and whitespace for a conservative exact-name match."""
    return " ".join(re.sub(r"[^a-z0-9]+", " ", name.lower()).split())


def read_imis_companies(
    path: Path | str, report_date: date | None = None
) -> list[Company]:
    """Read an iMIS CSV with shared ID/city columns and return Illinois rows."""
    companies, _, _ = read_imis_companies_with_tonnage_review(path, report_date)
    return companies


def read_imis_companies_with_tonnage_review(
    path: Path | str, report_date: date | None = None
) -> tuple[list[Company], list[TonnageReviewFinding], int | None]:
    """Read Illinois iMIS data, aggregating the prior calendar year when dated.

    ``report_date`` is injectable so scheduled runs and tests choose their year
    deterministically. Without it, this retains the legacy one-row-per-company
    reader behavior for callers that do not have a dated tonnage export.
    """
    source_path = Path(path)
    try:
        file_handle = source_path.open(newline="", encoding="utf-8-sig")
    except OSError as error:
        raise ReportDataError(f"Could not read iMIS CSV: {source_path}") from error

    with file_handle:
        reader = csv.DictReader(file_handle)
        fields = _recognized_fields(reader.fieldnames)
        missing = [
            field for field in ("imis_id", "name", "city", "state") if field not in fields
        ]
        if missing:
            raise ReportDataError(
                "The iMIS CSV is missing required column(s): "
                + ", ".join({"imis_id": "shared iMIS ID", "name": "company name"}.get(field, field) for field in missing)
                + ". Re-export iMIS including these columns."
            )

        rows = list(reader)
        if report_date is not None:
            annual_fields = ("tonnage_year", "submission_date")
            missing_annual = [field for field in annual_fields if field not in fields]
            if missing_annual:
                names = {"tonnage_year": "Tonnage Year", "submission_date": "Submission Date"}
                raise ReportDataError(
                    "The dated iMIS CSV is missing required column(s): "
                    + ", ".join(names[field] for field in missing_annual)
                    + ". Re-export iMIS including these columns."
                )
            selected_year = report_date.year - 1
            return _aggregate_annual_imis_companies(rows, fields, selected_year)

        companies = []
        for row_number, row in enumerate(rows, start=2):
            state = _cell(row, fields["state"])
            if not state:
                raise ReportDataError(f"Row {row_number} is missing a state.")
            if state.casefold() not in {"il", "illinois"}:
                continue
            name = _cell(row, fields["name"])
            if not name:
                raise ReportDataError(
                    f"Row {row_number} is missing a company name. This report needs "
                    "company-name data for Illinois rows. Re-export iMIS with the "
                    "organization/company name field included."
                )
            companies.append(
                Company(
                    name=name,
                    state=state,
                    imis_id=_normalize_imis_identifier(_cell(row, fields["imis_id"])),
                    city=_cell(row, fields["city"]),
                    address=_optional_cell(row, fields, "address"),
                    membership_type=membership_label(
                        _optional_cell(row, fields, "membership_type"),
                        _optional_cell(row, fields, "category"),
                    ),
                    tonnage=_report_tonnage(row, fields, row_number),
                    district=_optional_cell(row, fields, "district"),
                )
            )
    return (
        sorted(companies, key=lambda company: (company.name.casefold(), company.name)),
        [],
        None,
    )


def _aggregate_annual_imis_companies(
    rows: Iterable[Mapping[str | None, str | None]],
    fields: Mapping[str, str],
    selected_year: int,
) -> tuple[list[Company], list[TonnageReviewFinding], int]:
    """Sum unique Illinois submissions for one completed calendar year."""
    selected = []
    findings = []
    for row_number, row in enumerate(rows, start=2):
        state = _cell(row, fields["state"])
        if not state:
            raise ReportDataError(f"Row {row_number} is missing a state.")
        if state.casefold() not in {"il", "illinois"}:
            continue
        if _cell(row, fields["tonnage_year"]) != str(selected_year):
            continue
        imis_id = _normalize_imis_identifier(_cell(row, fields["imis_id"]))
        submission_date = _cell(row, fields["submission_date"])
        if not submission_date:
            findings.append(_tonnage_finding(row, fields, imis_id, "missing submission date"))
            continue
        try:
            timestamp = _parse_submission_date(submission_date)
            tonnage = _tonnage_decimal(row, fields, row_number)
        except ReportDataError as error:
            findings.append(_tonnage_finding(row, fields, imis_id, str(error)))
            continue
        selected.append((imis_id, submission_date, timestamp, tonnage, row, row_number))

    grouped: dict[tuple[str, int, datetime], list[tuple[str, str, datetime, Decimal, Mapping[str | None, str | None], int]]] = {}
    for item in selected:
        grouped.setdefault((item[0], selected_year, item[2]), []).append(item)

    accepted = []
    for key, submissions in grouped.items():
        values = {submission[3] for submission in submissions}
        if len(values) > 1:
            for submission in submissions:
                findings.append(_tonnage_finding(submission[4], fields, key[0], "conflicting tonnage for submission key"))
            continue
        accepted.append(submissions[0])
        for submission in submissions[1:]:
            findings.append(_tonnage_finding(submission[4], fields, key[0], "exact duplicate submission key"))

    companies = []
    by_imis_id: dict[str, list[tuple[str, str, datetime, Decimal, Mapping[str | None, str | None], int]]] = {}
    for submission in accepted:
        by_imis_id.setdefault(submission[0], []).append(submission)
    for imis_id, submissions in by_imis_id.items():
        latest = max(submissions, key=lambda submission: submission[2])
        row, row_number = latest[4], latest[5]
        name = _cell(row, fields["name"])
        if not name:
            raise ReportDataError(
                f"Row {row_number} is missing a company name. This report needs "
                "company-name data for Illinois rows. Re-export iMIS with the "
                "organization/company name field included."
            )
        companies.append(
            Company(
                name=name,
                state=_cell(row, fields["state"]),
                imis_id=imis_id,
                city=_cell(row, fields["city"]),
                address=_optional_cell(row, fields, "address"),
                membership_type=membership_label(_optional_cell(row, fields, "membership_type"), _optional_cell(row, fields, "category")),
                tonnage=_format_tonnage(sum((submission[3] for submission in submissions), Decimal())),
                district=_optional_cell(row, fields, "district"),
            )
        )
    return sorted(companies, key=lambda company: (company.name.casefold(), company.name)), findings, selected_year


def _parse_submission_date(value: str) -> datetime:
    """Parse common iMIS timestamp formats into a comparison-safe timestamp."""
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        for format_string in (
            "%m/%d/%Y",
            "%m/%d/%Y %H:%M:%S",
            "%m/%d/%Y %I:%M:%S %p",
        ):
            try:
                parsed = datetime.strptime(value, format_string)
                break
            except ValueError:
                continue
        else:
            raise ReportDataError(f"invalid submission date: {value!r}") from None
    return parsed.replace(tzinfo=None)


def _tonnage_finding(
    row: Mapping[str | None, str | None], fields: Mapping[str, str], imis_id: str, reason: str
) -> TonnageReviewFinding:
    return TonnageReviewFinding(
        imis_id, _cell(row, fields["tonnage_year"]), _cell(row, fields["submission_date"]), reason,
        _optional_cell(row, fields, "bridge_tonnage"), _optional_cell(row, fields, "building_tonnage"), _optional_cell(row, fields, "sc_tonnage"),
    )


def find_undefined_imis_codes(path: Path | str) -> list[UndefinedImisCodeFinding]:
    """Scan every iMIS export row for blank or unconfirmed Type/Category codes.

    This deliberately does not use ``read_imis_companies``: the review artifact
    must include every source row, including non-Illinois rows.
    """
    source_path = Path(path)
    try:
        file_handle = source_path.open(newline="", encoding="utf-8-sig")
    except OSError as error:
        raise ReportDataError(f"Could not read iMIS CSV: {source_path}") from error

    with file_handle:
        reader = csv.DictReader(file_handle)
        fields = _recognized_fields(reader.fieldnames)
        source_fields = {
            "Type": fields.get("membership_type"),
            "Category": fields.get("category"),
        }
        return scan_undefined_imis_codes(
            (label, _cell(row, field))
            for row in reader
            for label, field in source_fields.items()
            if field is not None
        )


def combine_companies(
    companies: Iterable[Company],
    salesforce_accounts: Iterable[Mapping[str, object]] = (),
)-> list[CombinedCompany]:
    """Join only unique, populated, exact shared iMIS ID text values."""
    companies = list(companies)
    salesforce_accounts = [
        account for account in salesforce_accounts if _is_illinois(account)
    ]
    accounts_by_imis_id: dict[str, list[Mapping[str, object]]] = {}
    for account in salesforce_accounts:
        identifier = _normalize_imis_identifier(
            account.get(CertificationAccountField.IMIS_ID)
        )
        if identifier:
            accounts_by_imis_id.setdefault(identifier, []).append(account)

    imis_id_counts = _identifier_counts(company.imis_id for company in companies)
    salesforce_id_counts = _identifier_counts(
        account.get(CertificationAccountField.IMIS_ID)
        for account in salesforce_accounts
    )

    used_accounts: set[int] = set()
    combined = []
    for company in companies:
        identifier = _normalize_imis_identifier(company.imis_id)
        matches = accounts_by_imis_id.get(identifier, []) if identifier else []
        is_unique_match = (
            identifier
            and imis_id_counts[identifier] == 1
            and salesforce_id_counts[identifier] == 1
        )
        account = matches[0] if is_unique_match else None
        if account is not None:
            used_accounts.add(id(account))
            combined.append(CombinedCompany(CompanyClassification.BOTH, company, account))
        else:
            combined.append(
                CombinedCompany(
                    CompanyClassification.IMIS_ONLY,
                    company,
                    None,
                    imis_id_counts[identifier] if imis_id_counts[identifier] > 1 else 0,
                )
            )
    for account in salesforce_accounts:
        if id(account) in used_accounts:
            continue
        identifier = _normalize_imis_identifier(
            account.get(CertificationAccountField.IMIS_ID)
        )
        combined.append(
            CombinedCompany(
                CompanyClassification.SALESFORCE_ONLY,
                None,
                account,
                salesforce_id_counts[identifier]
                if salesforce_id_counts[identifier] > 1
                else 0,
            )
        )
    return combined


def combined_conflicts(companies: Iterable[CombinedCompany]) -> list[Conflict]:
    """Return differing comparable values from ID-joined records."""
    conflicts = []
    reported_duplicates: set[tuple[CompanyClassification, str]] = set()
    for company in companies:
        duplicate_key = (company.classification, company.shared_imis_id)
        if (
            company.duplicate_id_count > 1
            and duplicate_key not in reported_duplicates
        ):
            reported_duplicates.add(duplicate_key)
            source = (
                "iMIS"
                if company.classification is CompanyClassification.IMIS_ONLY
                else "Salesforce"
            )
            detail = f"{company.duplicate_id_count} {source} records"
            conflicts.append(
                Conflict(
                    company.shared_imis_id,
                    company.classification,
                    "duplicate iMIS ID",
                    detail if source == "iMIS" else "",
                    detail if source == "Salesforce" else "",
                )
            )
        if not company.imis or not company.salesforce:
            continue
        comparisons = {
            "name": (company.imis.name, _account_value(company.salesforce, CertificationAccountField.NAME)),
            "city": (company.imis.city, _account_value(company.salesforce, CertificationAccountField.BILLING_CITY)),
            "state": (company.imis.state, _account_value(company.salesforce, CertificationAccountField.BILLING_STATE)),
            "address": (_imis_address(company.imis), _salesforce_address(company.salesforce)),
        }
        for field, (imis_value, salesforce_value) in comparisons.items():
            if imis_value and salesforce_value and not _same_value(field, imis_value, salesforce_value):
                conflicts.append(Conflict(company.shared_imis_id, company.classification, field, imis_value, salesforce_value))
    return conflicts


def candidate_matches(companies: Iterable[CombinedCompany]) -> list[CandidateMatch]:
    """Find exact normalized name/city/state lookalikes among unjoined records."""
    rows = list(companies)
    candidates = []
    for imis_row in (row for row in rows if row.imis and not row.salesforce):
        for salesforce_row in (row for row in rows if row.salesforce and not row.imis):
            imis = imis_row.imis
            account = salesforce_row.salesforce
            assert imis is not None and account is not None
            imis_id = _normalize_imis_identifier(imis.imis_id)
            salesforce_id = _normalize_imis_identifier(
                account.get(CertificationAccountField.IMIS_ID)
            )
            if imis_id and salesforce_id and imis_id == salesforce_id:
                continue
            name, city, state = (_account_value(account, field) for field in (CertificationAccountField.NAME, CertificationAccountField.BILLING_CITY, CertificationAccountField.BILLING_STATE))
            if all((imis.name, imis.city, imis.state, name, city, state)) and normalize_company_name(imis.name) == normalize_company_name(name) and normalize_company_name(imis.city) == normalize_company_name(city) and _same_value("state", imis.state, state):
                candidates.append(CandidateMatch(imis_id, _account_value(account, CertificationAccountField.ID), imis.name, name, imis.city, city, imis.state, state))
    return candidates


def build_reconciliation_rows(
    companies: Iterable[CombinedCompany],
    as_of: date | str | None = None,
) -> list[ReconciliationRow]:
    """Build one complete review row per combined source record.

    A matched pair becomes one row. Source-only records, including every
    record affected by a duplicate identifier, remain individual rows so a
    reviewer can filter and investigate them in a spreadsheet.
    """
    companies = list(companies)
    duplicate_ids = {
        company.shared_imis_id
        for company in companies
        if company.duplicate_id_count > 1 and company.shared_imis_id
    }
    rows = []
    for company in companies:
        imis = company.imis
        account = company.salesforce
        imis_id = _normalize_imis_identifier(imis.imis_id if imis else None)
        salesforce_id = _normalize_imis_identifier(
            account.get(CertificationAccountField.IMIS_ID) if account else None
        )
        shared_id = imis_id or salesforce_id
        issues = []
        if not shared_id:
            issues.append("missing iMIS ID")
        if shared_id in duplicate_ids:
            issues.append("duplicate iMIS ID")
        if (
            imis
            and account
            and imis.name
            and (salesforce_name := _account_value(account, CertificationAccountField.NAME))
            and not _same_value("name", imis.name, salesforce_name)
        ):
            issues.append("name difference")
        if account and _is_certified_account(account) and not _active_certification_names(account, as_of):
            issues.append("certified account without active certifications")
        if company.classification is CompanyClassification.BOTH:
            classification = "matched"
        else:
            classification = company.classification.value
        rows.append(
            ReconciliationRow(
                classification=classification,
                imis_id=shared_id,
                salesforce_account_id=_account_value(account, CertificationAccountField.ID),
                imis_name=imis.name if imis else "",
                salesforce_name=_account_value(account, CertificationAccountField.NAME),
                issues=tuple(issues),
            )
        )
    return rows


def build_report_companies(
    companies: Iterable[Company] | Iterable[CombinedCompany],
    salesforce_accounts: Iterable[Mapping[str, object]] = (),
    as_of: date | str | None = None,
) -> list[ReportCompany]:
    """Turn the ID-based combined model into PDF rows with owned fields.

    iMIS owns membership and tonnage. Salesforce owns the Account name,
    employee count, and certification data when available. Optional public
    fields are represented by empty values; source-data gaps remain visible in
    the separate reconciliation outputs instead of in the PDF.
    """
    rows = list(companies)
    combined = (
        rows
        if rows and all(isinstance(row, CombinedCompany) for row in rows)
        else combine_companies(rows, salesforce_accounts)  # type: ignore[arg-type]
    )
    report_companies = []
    salesforce_only_accounts = []
    for company in combined:
        imis, account = company.imis, company.salesforce
        if company.classification is CompanyClassification.SALESFORCE_ONLY:
            # Certification-only listings are public only for Accounts whose
            # Account-level status is exactly Certified.  The reconciliation
            # model intentionally continues to retain every source record.
            if account and _is_certified_account(account):
                salesforce_only_accounts.append(account)
            continue
        categories = _active_certification_names(account, as_of) if _is_certified_account(account) else ()
        # A valid ID join gives Salesforce ownership of the displayed Account
        # name; iMIS remains the fallback for a blank Salesforce name.
        name = _account_value(account, CertificationAccountField.NAME) or (imis.name if imis else "")
        imis_address = _imis_address(imis) if imis else ""
        salesforce_address = _salesforce_address(account) if account else ""
        # Salesforce provides the public address for an ID match. The
        # conflicts CSV separately preserves both values when they differ.
        address = salesforce_address or imis_address
        report_companies.append(
            ReportCompany(
                name,
                address,
                _format_employee_count(
                    account.get(CertificationAccountField.EMPLOYEE_COUNT)
                    if account
                    else None
                ),
                imis.membership_type if imis else "",
                imis.tonnage if imis else "",
                categories,
            )
        )
    report_companies.extend(
        _merged_salesforce_only_report_companies(salesforce_only_accounts, as_of)
    )
    return sorted(
        report_companies,
        key=lambda company: (normalize_company_name(company.name), company.name.casefold()),
    )


def render_illinois_report(
    companies: Iterable[ReportCompany],
    output: Path | str,
    tonnage_year: int | None = None,
    senators: Iterable[SenatorContact] = (),
    senate_source_url: str = "",
    senate_retrieved_at: datetime | None = None,
    *,
    imis_export_filename: str = "",
    imis_export_date: date | None = None,
    salesforce_retrieved_at: datetime | None = None,
) -> None:
    """Create a printable, letter-size statewide Illinois PDF report."""
    output_path = Path(output)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    document = SimpleDocTemplate(
        str(output_path),
        pagesize=letter,
        leftMargin=0.55 * inch,
        rightMargin=0.55 * inch,
        topMargin=0.55 * inch,
        bottomMargin=0.55 * inch,
        title="AISC Certification and Membership Summary: Illinois",
    )
    styles = getSampleStyleSheet()
    title = ParagraphStyle(
        "ReportTitle",
        parent=styles["Title"],
        alignment=TA_CENTER,
        fontSize=16,
        leading=21,
    )
    body = ParagraphStyle(
        "ReportBody", parent=styles["BodyText"], fontSize=9, leading=12, spaceAfter=2
    )
    company_heading = ParagraphStyle(
        "CompanyHeading",
        parent=body,
        fontName="Helvetica-Bold",
        leading=12,
        spaceAfter=4,
    )
    senator_heading = ParagraphStyle(
        "SenatorHeading", parent=body, fontName="Helvetica-Bold", leading=12, spaceAfter=2
    )

    story = [
        Paragraph("AISC Certification and Membership Summary: Illinois", title),
        Spacer(1, 0.18 * inch),
    ]
    senator_contacts = tuple(senators)
    if senator_contacts:
        story.append(Paragraph("<b>Illinois U.S. Senate Contacts</b>", body))
        if senate_retrieved_at:
            retrieval_date = senate_retrieved_at.astimezone(UTC).date().isoformat()
            story.append(Paragraph(f"Retrieved: {retrieval_date}", body))
        for senator in senator_contacts:
            senator_lines = [
                Paragraph(_escape(senator.name), senator_heading),
                Paragraph(_escape(senator.address).replace("\n", "<br/>"), body),
                Paragraph(f"Phone: {_escape(senator.phone)}", body),
                Paragraph(f"Contact form: {_escape(senator.contact_form_url)}", body),
            ]
            story.append(Table([[senator_lines]], colWidths=[6.9 * inch]))
            story[-1].setStyle(
                TableStyle(
                    [
                        ("BOX", (0, 0), (-1, -1), 0.35, colors.HexColor("#777777")),
                        ("LEFTPADDING", (0, 0), (-1, -1), 7),
                        ("RIGHTPADDING", (0, 0), (-1, -1), 7),
                        ("TOPPADDING", (0, 0), (-1, -1), 6),
                        ("BOTTOMPADDING", (0, 0), (-1, -1), 6),
                    ]
                )
            )
            story.append(Spacer(1, 0.06 * inch))
        if senate_source_url:
            story.append(Paragraph(f"Source: {_escape(senate_source_url)}", body))
        story.append(Spacer(1, 0.12 * inch))
    story.extend(
        [
            Paragraph("<b>Certified and Member Companies</b>", body),
            Spacer(1, 0.08 * inch),
        ]
    )
    for company in companies:
        company_cell = [
            Paragraph(_escape(company.name), company_heading),
        ]
        if company.address:
            # Paragraph treats HTML-like markup specially. Escape source text
            # first, then intentionally turn our display line breaks into the
            # safe markup ReportLab expects.
            company_cell.append(Paragraph(_escape(company.address).replace("\n", "<br/>"), body))
        details_cell = []
        if company.employee_count:
            details_cell.append(Paragraph(f"{_escape(company.employee_count)} Employees", body))
        if company.membership_type:
            details_cell.append(Paragraph(_escape(company.membership_type), body))
        if company.tonnage and tonnage_year is not None:
            details_cell.append(
                Paragraph(
                    f"{tonnage_year} Structural Steel Tonnage: "
                    f"{_escape(_rounded_tonnage_for_display(company.tonnage))} Tons",
                    body,
                )
            )
        certification_paragraphs = format_certification_paragraphs(
            company.certification_categories
        )
        if certification_paragraphs:
            # Keep the concise Fabricator/Erector sentences together. An
            # unknown Salesforce value follows in its own paragraph so it is
            # plainly visible for a future mapping update.
            for sentence in certification_paragraphs:
                details_cell.append(Paragraph(_escape(sentence), body))
        table = Table(
            [[company_cell, details_cell]], colWidths=[3.35 * inch, 3.55 * inch], splitByRow=1
        )
        table.setStyle(
            TableStyle(
                [
                    ("VALIGN", (0, 0), (-1, -1), "TOP"),
                    ("BOX", (0, 0), (-1, -1), 0.35, colors.HexColor("#777777")),
                    ("INNERGRID", (0, 0), (-1, -1), 0.35, colors.HexColor("#777777")),
                    ("LEFTPADDING", (0, 0), (-1, -1), 7),
                    ("RIGHTPADDING", (0, 0), (-1, -1), 7),
                    ("TOPPADDING", (0, 0), (-1, -1), 6),
                    ("BOTTOMPADDING", (0, 0), (-1, -1), 6),
                ]
            )
        )
        story.extend([table, Spacer(1, 0.08 * inch)])
    story.extend(_provenance_section(
        body,
        imis_export_filename=imis_export_filename,
        imis_export_date=imis_export_date,
        tonnage_year=tonnage_year,
        senate_retrieved_at=senate_retrieved_at,
        salesforce_retrieved_at=salesforce_retrieved_at,
    ))
    document.build(story)


def _provenance_section(
    body: ParagraphStyle,
    *,
    imis_export_filename: str,
    imis_export_date: date | None,
    tonnage_year: int | None,
    senate_retrieved_at: datetime | None,
    salesforce_retrieved_at: datetime | None,
) -> list[object]:
    """Build the final PDF block that identifies the report's source data."""
    imis_details = _escape(imis_export_filename) if imis_export_filename else "Not provided"
    if imis_export_date:
        imis_details += f" (exported {imis_export_date.isoformat()})"
    elif not imis_export_filename:
        imis_details = "Not provided"
    else:
        imis_details += " (export date not provided)"
    tonnage_details = str(tonnage_year) if tonnage_year is not None else "Not available"
    senate_details = _provenance_date(senate_retrieved_at)
    salesforce_details = _provenance_date(salesforce_retrieved_at)
    return [
        Spacer(1, 0.12 * inch),
        Paragraph("<b>Report provenance</b>", body),
        Paragraph(f"iMIS export: {imis_details}", body),
        Paragraph(f"Tonnage calendar year: {tonnage_details}", body),
        Paragraph(f"Elected-official data retrieved: {senate_details}", body),
        Paragraph(f"Salesforce data retrieved: {salesforce_details}", body),
    ]


def _provenance_date(retrieved_at: datetime | None) -> str:
    """Display a source retrieval's UTC calendar date without exposing errors."""
    return retrieved_at.astimezone(UTC).date().isoformat() if retrieved_at else "Not retrieved"


def _recognized_fields(headers: list[str] | None) -> dict[str, str]:
    if not headers:
        return {}
    normalized = {_normalize_header(header): header for header in headers if header}
    fields = {}
    for field, aliases in HEADER_ALIASES.items():
        for alias in aliases:
            matched = normalized.get(_normalize_header(alias))
            if matched:
                fields[field] = matched
                break
    return fields


def _normalize_header(header: str) -> str:
    return re.sub(r"[^a-z0-9]+", " ", header.casefold()).strip()


def _cell(row: Mapping[str | None, str | None], field: str) -> str:
    return (row.get(field) or "").strip()


def _optional_cell(row, fields: Mapping[str, str], name: str) -> str:
    return _cell(row, fields[name]) if name in fields else ""


def _report_tonnage(
    row: Mapping[str | None, str | None], fields: Mapping[str, str], row_number: int
) -> str:
    """Return the direct tonnage or the total of the three current iMIS fields."""
    source_fields = ("bridge_tonnage", "building_tonnage", "sc_tonnage")
    if not any(field in fields for field in source_fields):
        return _optional_cell(row, fields, "tonnage")

    return _format_tonnage(_tonnage_decimal(row, fields, row_number))


def _tonnage_decimal(
    row: Mapping[str | None, str | None], fields: Mapping[str, str], row_number: int
) -> Decimal:
    """Return the numeric total of the three current iMIS tonnage columns."""
    source_fields = ("bridge_tonnage", "building_tonnage", "sc_tonnage")
    if not any(field in fields for field in source_fields):
        value = _optional_cell(row, fields, "tonnage")
        if not value:
            return Decimal()
        try:
            return Decimal(value.replace(",", ""))
        except InvalidOperation as error:
            raise ReportDataError(
                f"Row {row_number} has an invalid {fields['tonnage']} value: {value!r}."
            ) from error

    total = Decimal()
    for field in source_fields:
        if field not in fields:
            continue
        value = _cell(row, fields[field])
        if not value:
            continue
        try:
            total += Decimal(value.replace(",", ""))
        except InvalidOperation as error:
            raise ReportDataError(
                f"Row {row_number} has an invalid {fields[field]} value: {value!r}."
            ) from error
    return total


def _format_tonnage(value: Decimal) -> str:
    """Format a tonnage total with readable thousands separators."""
    if value == value.to_integral():
        return f"{value:,.0f}"
    return f"{value:,.2f}".rstrip("0").rstrip(".")


def _rounded_tonnage_for_display(value: str) -> str:
    """Round a numeric tonnage string to a whole number for the PDF."""
    try:
        tonnage = Decimal(value.replace(",", ""))
    except InvalidOperation:
        return value
    return f"{tonnage.quantize(Decimal('1'), rounding=ROUND_HALF_UP):,}"


def _active_certification_names(
    account: Mapping[str, object] | None, as_of: date | str | None
) -> tuple[str, ...]:
    """Return valid active child certification names in Salesforce query order."""
    if account is None:
        return ()
    relationship = account.get(CertificationRelationship.ACCOUNT_CHILD)
    if not isinstance(relationship, Mapping):
        return ()
    records = relationship.get("records")
    if not isinstance(records, list):
        return ()
    names = []
    for certification in records:
        if not isinstance(certification, Mapping):
            continue
        name = certification.get(CertificationField.NAME)
        if (
            isinstance(name, str)
            and name.strip()
            and is_active_certification(
                certification.get(CertificationField.STATUS),
                certification.get(CertificationField.START_DATE),
                certification.get(CertificationField.END_DATE),
                as_of,
            )
        ):
            names.append(name.strip())
    return tuple(names)


def _is_certified_account(account: Mapping[str, object] | None) -> bool:
    """Return whether an Account is eligible to display child certifications."""
    return bool(account) and _account_value(
        account, CertificationAccountField.CERTIFICATION_STATUS
    ) == CertificationStatus.CERTIFIED


def _format_employee_count(value: object) -> str:
    """Format Salesforce's whole-person employee count, or return an empty value."""
    count = _employee_count_decimal(value)
    return f"{count:,.0f}" if count is not None else ""


def _employee_count_decimal(value: object) -> Decimal | None:
    """Return a valid whole-person employee count for calculations."""
    if isinstance(value, bool):
        return None
    try:
        if isinstance(value, str):
            count = Decimal(value.replace(",", ""))
        elif isinstance(value, int):
            count = Decimal(value)
        elif isinstance(value, float):
            count = Decimal(str(value))
        else:
            return None
    except (InvalidOperation, ValueError):
        return None
    if not count.is_finite() or count != count.to_integral_value() or count < 0:
        return None
    return count


def _merged_salesforce_only_report_companies(
    accounts: Iterable[Mapping[str, object]], as_of: date | str | None
) -> list[ReportCompany]:
    """Build public-only Salesforce rows, combining equal complete addresses."""
    groups: dict[str, list[tuple[Mapping[str, object], str]]] = {}
    rows = []
    for account in accounts:
        address = _salesforce_address(account)
        address_key = _salesforce_full_address_key(account)
        # A blank address is not a complete address, so it must not merge
        # otherwise unrelated Accounts into one public listing.
        if not address_key:
            rows.append(_salesforce_only_report_company(account, address, as_of))
            continue
        groups.setdefault(address_key, []).append((account, address))

    for grouped_accounts in groups.values():
        first_account, address = grouped_accounts[0]
        if len(grouped_accounts) == 1:
            rows.append(_salesforce_only_report_company(first_account, address, as_of))
            continue
        names = " / ".join(
            _account_value(account, CertificationAccountField.NAME)
            for account, _ in grouped_accounts
            if _account_value(account, CertificationAccountField.NAME)
        )
        categories = tuple(
            dict.fromkeys(
                category
                for account, _ in grouped_accounts
                for category in _active_certification_names(account, as_of)
            )
        )
        employee_counts = [
            count
            for account, _ in grouped_accounts
            if (count := _employee_count_decimal(
                account.get(CertificationAccountField.EMPLOYEE_COUNT)
            )) is not None
        ]
        rows.append(
            ReportCompany(
                name=names,
                address=address,
                employee_count=(
                    f"{sum(employee_counts):,.0f}" if employee_counts else ""
                ),
                certification_categories=categories,
            )
        )
    return rows


def _salesforce_only_report_company(
    account: Mapping[str, object], address: str, as_of: date | str | None
) -> ReportCompany:
    """Return one eligible Salesforce-only public row without iMIS fields."""
    return ReportCompany(
        name=_account_value(account, CertificationAccountField.NAME),
        address=address,
        employee_count=_format_employee_count(
            account.get(CertificationAccountField.EMPLOYEE_COUNT)
        ),
        certification_categories=_active_certification_names(account, as_of),
    )


def _imis_address(company: Company) -> str:
    """Format iMIS address fields using the same display layout as Salesforce."""
    return _format_address(company.address, company.city, company.state)


def _salesforce_address(account: Mapping[str, object] | None) -> str:
    """Format the available Salesforce billing address without blank segments."""
    if account is None:
        return ""
    street = _string_value(account.get(CertificationAccountField.BILLING_STREET))
    city = _string_value(account.get(CertificationAccountField.BILLING_CITY))
    state = _string_value(account.get(CertificationAccountField.BILLING_STATE))
    postal_code = _string_value(account.get(CertificationAccountField.BILLING_POSTAL_CODE))
    country = _string_value(account.get(CertificationAccountField.BILLING_COUNTRY))
    return _format_address(street, city, state, postal_code, country)


def _format_address(
    street: str, city: str, state: str, postal_code: str = "", country: str = ""
) -> str:
    """Create a compact address with its city/locality on a separate line."""
    street = _without_united_states(street)
    country = _without_united_states(country)
    if not street and not city:
        return ""
    locality = " ".join(part for part in (state, postal_code) if part)
    city_line = ", ".join(part for part in (city, locality) if part)
    if country:
        city_line = ", ".join(part for part in (city_line, country) if part)
    return "\n".join(part for part in (street, city_line) if part)


def _without_united_states(value: str) -> str:
    """Remove United States from a source address, ignoring capitalization."""
    value = re.sub(r"\bUnited\s+States\b", "", value, flags=re.IGNORECASE)
    value = re.sub(r"\s*,\s*,+", ", ", value)
    return value.strip(" ,\n\t")


def _normalize_full_address(address: str) -> str:
    """Normalize a complete formatted address for Salesforce-only grouping."""
    return normalize_company_name(address)


def _salesforce_full_address_key(account: Mapping[str, object]) -> str:
    """Return a grouping key only when street, city, and state are all present."""
    components = (
        _string_value(account.get(CertificationAccountField.BILLING_STREET)),
        _string_value(account.get(CertificationAccountField.BILLING_CITY)),
        _string_value(account.get(CertificationAccountField.BILLING_STATE)),
    )
    if not all(components):
        return ""
    return _normalize_full_address(_salesforce_address(account))


def _string_value(value: object) -> str:
    return value.strip() if isinstance(value, str) else ""


def _normalize_imis_identifier(value: object) -> str:
    """Return a trimmed text iMIS ID without converting numeric-looking values."""
    return value.strip() if isinstance(value, str) else ""


def _identifier_counts(identifiers: Iterable[object]) -> Counter[str]:
    """Count only populated normalized identifiers for duplicate detection."""
    return Counter(
        identifier
        for value in identifiers
        if (identifier := _normalize_imis_identifier(value))
    )


def _account_value(
    account: Mapping[str, object] | None, field: CertificationAccountField
) -> str:
    return _string_value(account.get(field) if account else None)


def _is_illinois(account: Mapping[str, object]) -> bool:
    return _account_value(account, CertificationAccountField.BILLING_STATE).casefold() in {
        "il",
        "illinois",
    }


def _same_value(field: str, left: str, right: str) -> bool:
    """Compare display fields while treating Illinois's common spellings alike."""
    if field == "state":
        normalized = {"il": "illinois", "illinois": "illinois"}
        return normalized.get(left.casefold(), left.casefold()) == normalized.get(
            right.casefold(), right.casefold()
        )
    return normalize_company_name(left) == normalize_company_name(right)


def write_conflicts_csv(conflicts: Iterable[Conflict], output: Path | str) -> None:
    """Write the required conflict-review CSV, including its header when empty."""
    _write_csv(
        output,
        ("shared iMIS ID", "company classification", "field", "iMIS value", "Salesforce value"),
        (
            (item.shared_imis_id, item.company_classification, item.field, item.imis_value, item.salesforce_value)
            for item in conflicts
        ),
    )


def write_undefined_imis_codes_csv(
    findings: Iterable[UndefinedImisCodeFinding], output: Path | str
) -> None:
    """Write the required undefined-iMIS-code review CSV, including its header."""
    _write_csv(
        output,
        ("iMIS field", "iMIS code", "status", "occurrences"),
        (
            (finding.field, finding.code, finding.status, finding.occurrences)
            for finding in findings
        ),
    )


def write_tonnage_review_csv(
    findings: Iterable[TonnageReviewFinding], output: Path | str
) -> None:
    """Write selected-year tonnage rows excluded from the annual total."""
    _write_csv(
        output,
        (
            "iMIS ID", "Tonnage Year", "Submission Date", "reason",
            "Bridge Tonnage", "Building Tonnage", "S C Tonnage",
        ),
        (
            (
                finding.imis_id, finding.tonnage_year, finding.submission_date,
                finding.reason, finding.bridge_tonnage, finding.building_tonnage,
                finding.sc_tonnage,
            )
            for finding in findings
        ),
    )


def write_candidate_matches_csv(
    matches: Iterable[CandidateMatch], output: Path | str
) -> None:
    """Write the required candidate-match CSV, including its header when empty."""
    _write_csv(
        output,
        ("iMIS ID", "Salesforce Account ID", "iMIS name", "Salesforce name", "iMIS city", "Salesforce city", "iMIS state", "Salesforce state"),
        (
            (item.imis_id, item.salesforce_account_id, item.imis_name, item.salesforce_name, item.imis_city, item.salesforce_city, item.imis_state, item.salesforce_state)
            for item in matches
        ),
    )


def write_reconciliation_csv(
    rows: Iterable[ReconciliationRow], output: Path | str
) -> None:
    """Write the complete spreadsheet-filterable reconciliation artifact."""
    _write_csv(
        output,
        (
            "classification",
            "shared iMIS ID",
            "Salesforce Account ID",
            "iMIS name",
            "Salesforce name",
            "issues",
        ),
        (
            (
                row.classification,
                row.imis_id,
                row.salesforce_account_id,
                row.imis_name,
                row.salesforce_name,
                "; ".join(row.issues),
            )
            for row in rows
        ),
    )


def write_reconciliation_log(
    rows: Iterable[ReconciliationRow], output: Path | str
) -> None:
    """Write a concise count summary and details for questionable records."""
    rows = list(rows)
    matched = sum(row.classification == "matched" for row in rows)
    imis_only = sum(row.classification == "imis-only" for row in rows)
    salesforce_only = sum(row.classification == "salesforce-only" for row in rows)
    duplicate_ids = {
        row.imis_id for row in rows if "duplicate iMIS ID" in row.issues and row.imis_id
    }
    missing_ids = [row for row in rows if "missing iMIS ID" in row.issues]
    name_differences = [row for row in rows if "name difference" in row.issues]
    missing_active_certifications = [
        row for row in rows
        if "certified account without active certifications" in row.issues
    ]
    lines = [
        "Reconciliation summary",
        "======================",
        f"Matched records: {matched}",
        f"iMIS-only records: {imis_only}",
        f"Salesforce-only records: {salesforce_only}",
        f"Distinct duplicate iMIS IDs: {len(duplicate_ids)}",
        f"Records missing iMIS IDs: {len(missing_ids)}",
        f"ID-matched name differences: {len(name_differences)}",
        f"Certified accounts without active certifications: {len(missing_active_certifications)}",
        "",
        "Questionable records:",
    ]
    categories = (
        ("Missing iMIS IDs", "missing iMIS ID"),
        ("Duplicate iMIS IDs", "duplicate iMIS ID"),
        ("ID-matched name differences", "name difference"),
        ("Certified accounts without active certifications", "certified account without active certifications"),
    )
    for heading, issue in categories:
        lines.append(heading + ":")
        category_rows = [row for row in rows if issue in row.issues]
        if not category_rows:
            lines.append("- None")
            continue
        for row in category_rows:
            names = " | ".join(
                name for name in (row.imis_name, row.salesforce_name) if name
            )
            identifiers = ", ".join(
                value
                for value in (
                    f"iMIS ID={row.imis_id}" if row.imis_id else "",
                    f"Salesforce Account ID={row.salesforce_account_id}"
                    if row.salesforce_account_id
                    else "",
                )
                if value
            )
            detail = "; ".join(part for part in (names, identifiers) if part)
            lines.append(f"- {detail}")

    output_path = Path(output)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def _write_csv(output: Path | str, headers: tuple[str, ...], rows: Iterable[tuple[object, ...]]) -> None:
    output_path = Path(output)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    with output_path.open("w", newline="", encoding="utf-8") as file_handle:
        writer = csv.writer(file_handle)
        writer.writerow(headers)
        writer.writerows(rows)


def _escape(value: str) -> str:
    return value.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")

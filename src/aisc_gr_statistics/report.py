"""Prepare and render the statewide Illinois membership report.

The functions in this module deliberately turn source data into plain report
models before ReportLab is involved.  A future HTML renderer can reuse that
same preparation step.
"""

import csv
import re
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from datetime import date
from decimal import Decimal, InvalidOperation
from enum import StrEnum
from pathlib import Path

from reportlab.lib import colors
from reportlab.lib.enums import TA_CENTER
from reportlab.lib.pagesizes import letter
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import inch
from reportlab.platypus import Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle

from .imis_fields import membership_label
from .salesforce_fields import (
    CertificationAccountField,
    CertificationField,
    CertificationRelationship,
    is_active_certification,
)

PLACEHOLDER = "[PLACEHOLDER: unavailable]"
CERTIFICATION_CATEGORY_PLACEHOLDER = (
    "[PLACEHOLDER: certification category field needed]"
)
SENATORS_PLACEHOLDER = "[PLACEHOLDER: U.S. Senators needed]"
REPRESENTATIVES_PLACEHOLDER = "[PLACEHOLDER: U.S. Representatives needed]"


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
class ReportCompany:
    """A company with display-ready certification and membership values."""

    name: str
    address: str
    membership_type: str
    tonnage: str
    district: str
    certification_status: str
    certification_categories: tuple[str, ...]


class CompanyClassification(StrEnum):
    """How a company is represented in the two source systems."""

    BOTH = "both"
    IMIS_ONLY = "imis-only"
    SALESFORCE_ONLY = "salesforce-only"


@dataclass(frozen=True)
class SourcedValue:
    """A value retained with the system that supplied it."""

    imis: str = ""
    salesforce: str = ""

    def display(self, placeholder: str = PLACEHOLDER) -> str:
        """Return one agreed value or clearly label differing source values."""
        values = [("iMIS", self.imis), ("Salesforce", self.salesforce)]
        present = [(source, value) for source, value in values if value]
        if not present:
            return placeholder
        if len(present) == 1 or present[0][1] == present[1][1]:
            return present[0][1]
        return " | ".join(f"{source}: {value}" for source, value in present)


@dataclass(frozen=True)
class CombinedCompany:
    """One in-scope company, preserving both source records after an ID join."""

    classification: CompanyClassification
    imis: Company | None
    salesforce: Mapping[str, object] | None

    @property
    def shared_imis_id(self) -> str:
        if self.imis and self.imis.imis_id:
            return self.imis.imis_id
        return _string_value(
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
}


def normalize_company_name(name: str) -> str:
    """Normalize punctuation and whitespace for a conservative exact-name match."""
    return " ".join(re.sub(r"[^a-z0-9]+", " ", name.lower()).split())


def read_imis_companies(path: Path | str) -> list[Company]:
    """Read an iMIS CSV with shared ID/city columns and return Illinois rows."""
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

        companies = []
        for row_number, row in enumerate(reader, start=2):
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
                    imis_id=_cell(row, fields["imis_id"]),
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
    return sorted(
        companies, key=lambda company: (company.name.casefold(), company.name)
    )


def combine_companies(
    companies: Iterable[Company],
    salesforce_accounts: Iterable[Mapping[str, object]] = (),
)-> list[CombinedCompany]:
    """Join Illinois iMIS and Salesforce records only on populated shared IDs."""
    companies = list(companies)
    salesforce_accounts = [
        account for account in salesforce_accounts if _is_illinois(account)
    ]
    accounts_by_imis_id: dict[str, list[Mapping[str, object]]] = {}
    for account in salesforce_accounts:
        identifier = _string_value(account.get(CertificationAccountField.IMIS_ID))
        if identifier:
            accounts_by_imis_id.setdefault(identifier, []).append(account)

    used_accounts: set[int] = set()
    combined = []
    for company in companies:
        matches = accounts_by_imis_id.get(company.imis_id, []) if company.imis_id else []
        account = next((item for item in matches if id(item) not in used_accounts), None)
        if account is not None:
            used_accounts.add(id(account))
            combined.append(CombinedCompany(CompanyClassification.BOTH, company, account))
        else:
            combined.append(CombinedCompany(CompanyClassification.IMIS_ONLY, company, None))
    for account in salesforce_accounts:
        if id(account) in used_accounts:
            continue
        combined.append(CombinedCompany(CompanyClassification.SALESFORCE_ONLY, None, account))
    return combined


def combined_conflicts(companies: Iterable[CombinedCompany]) -> list[Conflict]:
    """Return differing comparable values from ID-joined records."""
    conflicts = []
    for company in companies:
        if not company.imis or not company.salesforce:
            continue
        comparisons = {
            "name": (company.imis.name, _account_value(company.salesforce, CertificationAccountField.NAME)),
            "city": (company.imis.city, _account_value(company.salesforce, CertificationAccountField.BILLING_CITY)),
            "state": (company.imis.state, _account_value(company.salesforce, CertificationAccountField.BILLING_STATE)),
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
            name, city, state = (_account_value(account, field) for field in (CertificationAccountField.NAME, CertificationAccountField.BILLING_CITY, CertificationAccountField.BILLING_STATE))
            if all((imis.name, imis.city, imis.state, name, city, state)) and normalize_company_name(imis.name) == normalize_company_name(name) and normalize_company_name(imis.city) == normalize_company_name(city) and _same_value("state", imis.state, state):
                candidates.append(CandidateMatch(imis.imis_id, _account_value(account, CertificationAccountField.ID), imis.name, name, imis.city, city, imis.state, state))
    return candidates


def build_report_companies(
    companies: Iterable[Company] | Iterable[CombinedCompany],
    salesforce_accounts: Iterable[Mapping[str, object]] = (),
    as_of: date | str | None = None,
) -> list[ReportCompany]:
    """Turn the ID-based combined model into source-labelled PDF rows."""
    rows = list(companies)
    combined = (
        rows
        if rows and all(isinstance(row, CombinedCompany) for row in rows)
        else combine_companies(rows, salesforce_accounts)  # type: ignore[arg-type]
    )
    report_companies = []
    for company in combined:
        imis, account = company.imis, company.salesforce
        name = SourcedValue(imis.name if imis else "", _account_value(account, CertificationAccountField.NAME)).display()
        address = SourcedValue(imis.address if imis else "", _salesforce_address(account) if account else "").display()
        status = _account_value(account, CertificationAccountField.CERTIFICATION_STATUS)
        categories = _active_certification_names(account, as_of) if account else ()
        report_companies.append(
            ReportCompany(
                name,
                address,
                _label_source("iMIS", imis.membership_type) if imis and imis.membership_type else PLACEHOLDER,
                _label_source("iMIS", imis.tonnage) if imis and imis.tonnage else PLACEHOLDER,
                _label_source("iMIS", imis.district) if imis and imis.district else PLACEHOLDER,
                _label_source("Salesforce", status) if status else PLACEHOLDER,
                tuple(_label_source("Salesforce", category) for category in categories)
                or (CERTIFICATION_CATEGORY_PLACEHOLDER,),
            )
        )
    return report_companies


def render_illinois_report(
    companies: Iterable[ReportCompany], output: Path | str
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
        title="Illinois Certification & Membership Report",
    )
    styles = getSampleStyleSheet()
    title = ParagraphStyle(
        "ReportTitle",
        parent=styles["Title"],
        alignment=TA_CENTER,
        fontSize=17,
        leading=21,
    )
    geography = ParagraphStyle(
        "Geography",
        parent=styles["Heading2"],
        alignment=TA_CENTER,
        fontSize=12,
        leading=15,
    )
    body = ParagraphStyle(
        "ReportBody", parent=styles["BodyText"], fontSize=8.7, leading=11
    )
    company_heading = ParagraphStyle(
        "CompanyHeading",
        parent=body,
        fontName="Helvetica-Bold",
        leading=12,
        spaceAfter=3,
    )

    story = [
        Paragraph("Illinois Certification &amp; Membership Report", title),
        Paragraph("Illinois", geography),
        Spacer(1, 0.12 * inch),
        Paragraph(f"<b>U.S. Senators:</b> {SENATORS_PLACEHOLDER}", body),
        Paragraph(f"<b>U.S. Representatives:</b> {REPRESENTATIVES_PLACEHOLDER}", body),
        Spacer(1, 0.12 * inch),
    ]
    for company in companies:
        company_cell = [
            Paragraph(f"<u>{_escape(company.name)}</u>", company_heading),
            Paragraph(_escape(company.address), body),
        ]
        details_cell = [
            Paragraph(
                f"<b>Membership type:</b> {_escape(company.membership_type)}", body
            ),
            Paragraph(
                f"<b>Structural steel tonnage:</b> {_escape(company.tonnage)}",
                body,
            ),
            Paragraph(
                f"<b>Congressional district:</b> {_escape(company.district)}", body
            ),
            Paragraph(
                f"<b>Certification status:</b> {_escape(company.certification_status)}",
                body,
            ),
            Paragraph(
                "<b>Certification category:</b><br/>"
                + "<br/>".join(_escape(category) for category in company.certification_categories),
                body,
            ),
        ]
        table = Table(
            [[company_cell, details_cell]], colWidths=[2.35 * inch, 4.55 * inch]
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
    document.build(story)


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
    return _format_tonnage(total)


def _format_tonnage(value: Decimal) -> str:
    """Format a tonnage total with readable thousands separators."""
    if value == value.to_integral():
        return f"{value:,.0f}"
    return f"{value:,.2f}".rstrip("0").rstrip(".")


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


def _salesforce_address(account: Mapping[str, object] | None) -> str:
    """Format the available Salesforce billing address without blank segments."""
    if account is None:
        return ""
    street = _string_value(account.get(CertificationAccountField.BILLING_STREET))
    city = _string_value(account.get(CertificationAccountField.BILLING_CITY))
    state = _string_value(account.get(CertificationAccountField.BILLING_STATE))
    postal_code = _string_value(account.get(CertificationAccountField.BILLING_POSTAL_CODE))
    country = _string_value(account.get(CertificationAccountField.BILLING_COUNTRY))
    locality = " ".join(part for part in (state, postal_code) if part)
    return ", ".join(part for part in (street, city, locality, country) if part)


def _string_value(value: object) -> str:
    return value.strip() if isinstance(value, str) else ""


def _label_source(source: str, value: str) -> str:
    return f"{source}: {value}"


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


def _write_csv(output: Path | str, headers: tuple[str, ...], rows: Iterable[tuple[object, ...]]) -> None:
    output_path = Path(output)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    with output_path.open("w", newline="", encoding="utf-8") as file_handle:
        writer = csv.writer(file_handle)
        writer.writerow(headers)
        writer.writerows(rows)


def _escape(value: str) -> str:
    return value.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")

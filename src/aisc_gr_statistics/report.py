"""Prepare and render the statewide Illinois membership report.

The functions in this module deliberately turn source data into plain report
models before ReportLab is involved.  A future HTML renderer can reuse that
same preparation step.
"""

import csv
import re
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from pathlib import Path

from reportlab.lib import colors
from reportlab.lib.enums import TA_CENTER
from reportlab.lib.pagesizes import letter
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import inch
from reportlab.platypus import Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle

from .imis_fields import membership_label
from .salesforce_fields import CertificationAccountField

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
    certification_category: str


HEADER_ALIASES = {
    "name": ("company name", "company_name", "company", "full name"),
    "state": ("state", "billing state", "state province"),
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
    """Read a CSV, validate required fields, and return Illinois companies by name."""
    source_path = Path(path)
    try:
        file_handle = source_path.open(newline="", encoding="utf-8-sig")
    except OSError as error:
        raise ReportDataError(f"Could not read iMIS CSV: {source_path}") from error

    with file_handle:
        reader = csv.DictReader(file_handle)
        fields = _recognized_fields(reader.fieldnames)
        missing = [field for field in ("name", "state") if field not in fields]
        if missing:
            raise ReportDataError(
                "The iMIS CSV is missing required column(s): "
                + ", ".join(
                    "company name" if field == "name" else field for field in missing
                )
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


def build_report_companies(
    companies: Iterable[Company],
    salesforce_accounts: Iterable[Mapping[str, object]] = (),
) -> list[ReportCompany]:
    """Combine companies with statuses from only unambiguous Salesforce matches."""
    matches: dict[str, list[Mapping[str, object]]] = {}
    for account in salesforce_accounts:
        name = account.get(CertificationAccountField.NAME)
        if isinstance(name, str) and name.strip():
            matches.setdefault(normalize_company_name(name), []).append(account)

    report_companies = []
    for company in companies:
        matched = matches.get(normalize_company_name(company.name), [])
        status = PLACEHOLDER
        if len(matched) == 1:
            value = matched[0].get(CertificationAccountField.CERTIFICATION_STATUS)
            if isinstance(value, str) and value.strip():
                status = value.strip()
        report_companies.append(
            ReportCompany(
                name=company.name,
                address=company.address or PLACEHOLDER,
                membership_type=company.membership_type or PLACEHOLDER,
                tonnage=company.tonnage or PLACEHOLDER,
                district=company.district or PLACEHOLDER,
                certification_status=status,
                certification_category=CERTIFICATION_CATEGORY_PLACEHOLDER,
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
                f"<b>Certification category:</b> {_escape(company.certification_category)}",
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


def _escape(value: str) -> str:
    return value.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")

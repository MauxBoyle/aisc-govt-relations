"""One-page, externally shareable Illinois congressional reports."""

import csv
from dataclasses import dataclass
from pathlib import Path

from reportlab.lib import colors
from reportlab.lib.pagesizes import letter
from reportlab.pdfbase.pdfmetrics import stringWidth
from reportlab.pdfgen.canvas import Canvas

from .census_boundaries import load_boundary_snapshot
from .districts import (
    DistrictAggregateRow,
    read_districts_csv,
    validate_snapshot_metadata,
)
from .house import members_for_state
from .senate import senators_for_state


class DistrictReportError(ValueError):
    """Required report inputs are inconsistent or cannot fit legibly."""


@dataclass(frozen=True)
class ExternalCompany:
    """The deliberately small data model permitted to reach this renderer."""
    name: str
    city: str
    county: str


def read_aggregates_csv(path: Path | str) -> list[DistrictAggregateRow]:
    with Path(path).open(newline="", encoding="utf-8") as handle:
        reader = csv.DictReader(handle)
        fields = tuple(DistrictAggregateRow.__dataclass_fields__)
        if reader.fieldnames is None or set(fields) - set(reader.fieldnames):
            raise DistrictReportError("aggregate snapshot is missing required columns")
        try:
            return [DistrictAggregateRow(**{key: _number(row.get(key, "")) if key.endswith(("count", "jobs", "data")) else (row.get(key) or "").strip() for key in fields}) for row in reader]
        except ValueError as error:
            raise DistrictReportError("aggregate snapshot contains invalid numbers") from error


def _number(value):
    return int(str(value).strip())


def district_filename(district: str) -> str:
    return f"illinois-congressional-district-{int(district):02d}-external.pdf"


def senate_filename() -> str:
    return "illinois-senate-delegation-external.pdf"


def render_house_report(district, districts_csv, aggregates_csv, output, house_members, *, boundary_paths=None, as_of=""):
    district = str(int(str(district)))
    member = next((m for m in members_for_state(tuple(house_members), "IL") if m.district == district), None)
    if member is None:
        raise DistrictReportError(f"Illinois district {district} is not in the House snapshot.")
    _validate_inputs(districts_csv, aggregates_csv)
    rows = read_districts_csv(districts_csv)
    selected = [row for row in rows if row.state == "IL" and row.congressional_district == district]
    if not selected:
        raise DistrictReportError(f"No saved companies exist for Illinois district {district}.")
    aggregates = read_aggregates_csv(aggregates_csv)
    geoids = {row.congressional_district_geoid for row in selected}
    if len(geoids) != 1:
        raise DistrictReportError("district snapshot has conflicting district GEOIDs.")
    district_aggregate = _aggregate(aggregates, "district", "IL", district, geoids.pop())
    national = _aggregate(aggregates, "national", "", "", "")
    _match_count(selected, district_aggregate)
    shapes, metadata = load_boundary_snapshot(*(boundary_paths or ())) if boundary_paths else load_boundary_snapshot()
    if district_aggregate.congressional_district_geoid not in shapes:
        raise DistrictReportError("selected district is absent from the Census boundary snapshot.")
    if str(metadata.get("congressional_session")) not in _sessions(rows):
        raise DistrictReportError("Census boundary session is incompatible with the district snapshot.")
    identity = "Vacant" if member.vacant else member.name
    return _render(output, f"Illinois Congressional District {district}", identity, selected, district_aggregate, national, shapes, district_aggregate.congressional_district_geoid, metadata, as_of)


def render_senate_report(districts_csv, aggregates_csv, output, senators, *, boundary_paths=None, as_of=""):
    _validate_inputs(districts_csv, aggregates_csv)
    rows = [row for row in read_districts_csv(districts_csv) if row.state == "IL"]
    aggregates = read_aggregates_csv(aggregates_csv)
    state = _aggregate(aggregates, "state", "IL", "", "")
    national = _aggregate(aggregates, "national", "", "", "")
    if len(rows) != state.included_company_count:
        raise DistrictReportError("Senate report requires complete confirmed Illinois geography; resolve address-review data first.")
    _match_count(rows, state)
    shapes, metadata = load_boundary_snapshot(*(boundary_paths or ())) if boundary_paths else load_boundary_snapshot()
    if str(metadata.get("congressional_session")) not in _sessions(rows):
        raise DistrictReportError("Census boundary session is incompatible with the district snapshot.")
    names = " / ".join(s.name for s in senators_for_state(tuple(senators), "IL"))
    return _render(output, "Illinois U.S. Senate Delegation", names, rows, state, national, shapes, "", metadata, as_of)


def _sessions(rows):
    # Census GEOIDs do not encode Congress; the enrichment's vintage is the
    # auditable compatibility marker. Current snapshots use 119th geography.
    return {"119"} if rows else set()


def _validate_inputs(districts_csv, aggregates_csv):
    try:
        validate_snapshot_metadata(districts_csv, "districts")
        validate_snapshot_metadata(aggregates_csv, "aggregates")
    except ValueError as error:
        raise DistrictReportError(str(error)) from error


def _aggregate(rows, scope, state, district, geoid):
    found = [r for r in rows if (r.scope, r.state, r.congressional_district, r.congressional_district_geoid) == (scope, state, district, geoid)]
    if len(found) != 1:
        raise DistrictReportError(f"required {scope} aggregate row is missing or duplicated")
    return found[0]


def _match_count(rows, aggregate):
    if len(rows) != aggregate.included_company_count:
        raise DistrictReportError("company list does not match its aggregate snapshot")


def _render(output, title, identity, rows, local, national, shapes, highlighted, metadata, as_of):
    companies = sorted((ExternalCompany(r.company_name, r.city, r.county) for r in rows), key=lambda c: c.name.casefold())
    _preflight(companies)
    output = Path(output)
    output.parent.mkdir(parents=True, exist_ok=True)
    temporary = output.with_suffix(output.suffix + ".tmp")
    canvas = Canvas(str(temporary), pagesize=letter)
    width, height = letter
    canvas.setTitle(title)
    canvas.setFont("Helvetica-Bold", 15)
    canvas.drawString(36, height - 42, title)
    canvas.setFont("Helvetica-Bold", 12)
    canvas.drawString(36, height - 62, identity)
    canvas.setFont("Helvetica", 8)
    canvas.drawRightString(width - 36, height - 42, "External information")
    _draw_photo_placeholder(canvas, width - 104, height - 105, "Official photo unavailable")
    canvas.setFont("Helvetica-Bold", 9)
    canvas.drawString(36, height - 88, f"Companies: {local.included_company_count}")
    canvas.setFont("Helvetica", 8)
    canvas.drawString(36, height - 102, _jobs("District/state", local))
    canvas.drawString(36, height - 114, _jobs("National", national))
    _draw_map(canvas, shapes, highlighted, 380, height - 230, 180, 105)
    canvas.setFont("Helvetica-Bold", 9)
    canvas.drawString(36, height - 140, "Companies")
    _draw_companies(canvas, companies, 36, height - 154)
    canvas.setFont("Helvetica", 7)
    source_date = str(metadata["retrieved_at"])[:10]
    canvas.drawString(36, 26, f"Census boundaries as of {source_date}; district data as of {as_of or 'saved snapshot'}.")
    canvas.save()
    temporary.replace(output)
    return output


def _jobs(label, row):
    return f"{label} Known jobs: {row.known_jobs:,} ({row.companies_with_employee_data} of {row.included_company_count} companies have employee data)"


def _draw_photo_placeholder(canvas, x, y, label):
    canvas.setStrokeColor(colors.grey)
    canvas.rect(x, y, 68, 54)
    canvas.setFont("Helvetica", 6)
    canvas.drawCentredString(x + 34, y + 25, label)


def _draw_map(canvas, shapes, highlighted, x, y, width, height):
    points = [point for shape in shapes.values() for point in shape]
    min_x, max_x = min(p[0] for p in points), max(p[0] for p in points)
    min_y, max_y = min(p[1] for p in points), max(p[1] for p in points)
    scale = min(width / (max_x - min_x), height / (max_y - min_y))
    for geoid, shape in shapes.items():
        path = canvas.beginPath()
        for index, (longitude, latitude) in enumerate(shape):
            px, py = x + (longitude - min_x) * scale, y + (latitude - min_y) * scale
            (path.moveTo if index == 0 else path.lineTo)(px, py)
        path.close()
        canvas.setFillColor(colors.HexColor("#c43d36") if geoid == highlighted else colors.white)
        canvas.setStrokeColor(colors.HexColor("#777777"))
        canvas.drawPath(path, fill=1, stroke=1)


def _preflight(companies):
    columns, rows_per_column = 3, 42
    if len(companies) > columns * rows_per_column:
        raise DistrictReportError("company list cannot fit on one readable page; split the report or reduce the list.")
    for company in companies:
        text = f"{company.name} — {company.city}, {company.county}"
        if stringWidth(text, "Helvetica", 7) > 170 or "\n" in text:
            raise DistrictReportError("company list contains text that cannot fit legibly on one page.")


def _draw_companies(canvas, companies, x, y):
    for index, company in enumerate(companies):
        column, row = divmod(index, 42)
        canvas.setFont("Helvetica", 7)
        canvas.drawString(x + column * 180, y - row * 10, f"{company.name} — {company.city}, {company.county}")

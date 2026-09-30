"""One- or two-page, externally shareable Illinois congressional reports."""

import csv
import math
from dataclasses import dataclass
from pathlib import Path

from reportlab.lib import colors
from reportlab.lib.pagesizes import letter
from reportlab.pdfbase.pdfmetrics import stringWidth
from reportlab.pdfgen.canvas import Canvas

from .census_boundaries import load_boundary_snapshot
from .census_map_references import MapPoint, load_map_references, point_for_company
from .districts import (
    DistrictAggregateRow,
    read_districts_csv,
    validate_snapshot_metadata,
)
from .house import members_for_state
from .senate import senators_for_state


class DistrictReportError(ValueError):
    """Required report inputs are inconsistent or cannot fit legibly."""


COMPANY_LIST_FONT = "Helvetica"
COMPANY_LIST_FONT_SIZE = 8
COMPANY_LIST_WIDTH = 170
COMPANY_LIST_LINE_HEIGHT = 10
COMPANY_LIST_ENTRY_GAP = 3
COMPANY_LIST_COLUMNS = 3
COMPANY_LIST_COLUMN_GAP = 10
COMPANY_LIST_FIRST_PAGE_TOP = letter[1] - 154
COMPANY_LIST_CONTINUATION_TOP = letter[1] - 68
COMPANY_LIST_FOOTER_TOP = 38
MAX_REPORT_PAGES = 2


@dataclass(frozen=True)
class ExternalCompany:
    """The deliberately small data model permitted to reach this renderer."""
    name: str
    city: str
    county: str
    point: MapPoint | None = None


@dataclass(frozen=True)
class CompanyLinePlacement:
    """One wrapped line and its final PDF position."""

    text: str
    page: int
    x: float
    y: float


@dataclass(frozen=True)
class CompanyPlacement:
    """All wrapped lines for one company, kept in one column and page."""

    company: ExternalCompany
    page: int
    column: int
    line_placements: tuple[CompanyLinePlacement, ...]
    footer_top: float = COMPANY_LIST_FOOTER_TOP

    @property
    def lines(self):
        return tuple(line.text for line in self.line_placements)

    @property
    def bottom(self):
        return self.line_placements[-1].y


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


def render_house_report(district, districts_csv, aggregates_csv, output, house_members, *, boundary_paths=None, map_reference_paths=None, as_of=""):
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
    references = load_map_references(*(map_reference_paths or ())) if map_reference_paths else load_map_references()
    return _render(output, f"Illinois Congressional District {district}", identity, _external_companies(selected, references), district_aggregate, national, shapes, district_aggregate.congressional_district_geoid, metadata, as_of)


def render_senate_report(districts_csv, aggregates_csv, output, senators, *, boundary_paths=None, map_reference_paths=None, as_of=""):
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
    references = load_map_references(*(map_reference_paths or ())) if map_reference_paths else load_map_references()
    return _render(output, "Illinois U.S. Senate Delegation", names, _external_companies(rows, references), state, national, shapes, "", metadata, as_of)


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


def _external_companies(rows, references):
    """Create the only company objects permitted into the external renderer."""
    return [
        ExternalCompany(row.company_name, row.city, row.county, point_for_company(row.city, row.county_fips, references))
        for row in rows
    ]


def _render(output, title, identity, rows, local, national, shapes, highlighted, metadata, as_of):
    companies = sorted((_company(r) for r in rows), key=lambda c: c.name.casefold())
    placements = _plan_company_layout(companies)
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
    _draw_map(canvas, shapes, highlighted, 380, height - 230, 180, 105, [c.point for c in companies if c.point])
    canvas.setFont("Helvetica-Bold", 9)
    canvas.drawString(36, height - 140, "Companies")
    source_date = str(metadata["retrieved_at"])[:10]
    footer = f"Census boundaries as of {source_date}; district data as of {as_of or 'saved snapshot'}."
    _draw_company_page(canvas, placements, 1, footer)
    if any(item.page == 2 for item in placements):
        canvas.showPage()
        # ReportLab resets font, colors, and other graphics state on showPage().
        canvas.setTitle(title)
        canvas.setFillColor(colors.black)
        canvas.setStrokeColor(colors.black)
        canvas.setFont("Helvetica-Bold", 11)
        canvas.drawString(36, height - 42, f"{title} — Companies continued")
        _draw_company_page(canvas, placements, 2, footer)
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


def _company(row):
    if isinstance(row, ExternalCompany):
        return row
    return ExternalCompany(row.company_name, row.city, row.county)


def _draw_map(canvas, shapes, highlighted, x, y, width, height, marker_points=()):
    """Draw a printable local House map or an Illinois-wide Senate map."""
    extent = _map_extent(shapes, highlighted, marker_points, width / height)
    def project(point):
        return _project_point(point, extent, x, y, width, height)
    for geoid, rings in shapes.items():
        # Senate intentionally retains the statewide view. House maps only draw
        # boundaries near the selected district/marker extent.
        if highlighted and not _intersects(_ring_bounds(rings), extent):
            continue
        selected = geoid == highlighted
        canvas.setFillColor(colors.HexColor("#c43d36") if selected else colors.white)
        canvas.setStrokeColor(colors.HexColor("#9b2d28") if selected else colors.HexColor("#aaaaaa"))
        if hasattr(canvas, "setLineWidth"):
            canvas.setLineWidth(1.2 if selected else 0.25)
        for ring in rings:
            path = canvas.beginPath()
            for index, (longitude, latitude) in enumerate(ring):
                px, py = project((longitude, latitude))
                (path.moveTo if index == 0 else path.lineTo)(px, py)
            path.close()
            canvas.drawPath(path, fill=1, stroke=1)
    _draw_markers(canvas, [project(point) for point in marker_points])


def _map_extent(shapes, highlighted, marker_points, aspect_ratio):
    """Return lon/lat bounds expanded to the final printable map aspect ratio."""
    if highlighted:
        source = [point for rings in [shapes[highlighted]] for ring in rings for point in ring]
        source.extend(marker_points)
    else:
        source = [point for rings in shapes.values() for ring in rings for point in ring]
    min_lon, max_lon = min(point[0] for point in source), max(point[0] for point in source)
    min_lat, max_lat = min(point[1] for point in source), max(point[1] for point in source)
    lon_pad, lat_pad = max((max_lon - min_lon) * 0.08, 0.04), max((max_lat - min_lat) * 0.08, 0.04)
    min_lon, max_lon, min_lat, max_lat = min_lon - lon_pad, max_lon + lon_pad, min_lat - lat_pad, max_lat + lat_pad
    mid_lat = (min_lat + max_lat) / 2
    projected_width, projected_height = (max_lon - min_lon) * math.cos(math.radians(mid_lat)), max_lat - min_lat
    if projected_width / projected_height < aspect_ratio:
        grow = (projected_height * aspect_ratio / math.cos(math.radians(mid_lat)) - (max_lon - min_lon)) / 2
        min_lon, max_lon = min_lon - grow, max_lon + grow
    else:
        grow = (projected_width / aspect_ratio - (max_lat - min_lat)) / 2
        min_lat, max_lat = min_lat - grow, max_lat + grow
    return min_lon, max_lon, min_lat, max_lat


def _project_point(point, extent, x, y, width, height):
    min_lon, max_lon, min_lat, max_lat = extent
    mid_lat = (min_lat + max_lat) / 2
    lon_scale = math.cos(math.radians(mid_lat))
    return (x + ((point[0] - min_lon) * lon_scale) / ((max_lon - min_lon) * lon_scale) * width, y + (point[1] - min_lat) / (max_lat - min_lat) * height)


def _ring_bounds(rings):
    points = [point for ring in rings for point in ring]
    return min(p[0] for p in points), max(p[0] for p in points), min(p[1] for p in points), max(p[1] for p in points)


def _intersects(bounds, extent):
    return not (bounds[1] < extent[0] or bounds[0] > extent[1] or bounds[3] < extent[2] or bounds[2] > extent[3])


def _cluster_markers(points, distance=7):
    """Cluster final PDF positions deterministically, not raw geographic points."""
    clusters = []
    for point in sorted(points):
        for cluster in clusters:
            if math.dist(point, cluster[0]) <= distance:
                cluster.append(point)
                break
        else:
            clusters.append([point])
    return clusters


def _draw_markers(canvas, points):
    if not hasattr(canvas, "circle"):
        return
    canvas.setFillColor(colors.HexColor("#174ea6"))
    canvas.setStrokeColor(colors.white)
    canvas.setLineWidth(0.5)
    for cluster in _cluster_markers(points):
        x, y = cluster[0]
        radius = 3.5 if len(cluster) == 1 else 5
        canvas.circle(x, y, radius, fill=1, stroke=1)
        if len(cluster) > 1:
            canvas.setFillColor(colors.white)
            canvas.setFont("Helvetica-Bold", 6)
            canvas.drawCentredString(x, y - 2, str(len(cluster)))
            canvas.setFillColor(colors.HexColor("#174ea6"))


def _company_text(company):
    return f"{company.name} — {company.city}, {company.county}"


def _wrap_company_text(text):
    """Wrap at word boundaries using ReportLab's actual font measurements."""
    words = text.split()
    if not words:
        return ("",)
    for word in words:
        if stringWidth(word, COMPANY_LIST_FONT, COMPANY_LIST_FONT_SIZE) > COMPANY_LIST_WIDTH:
            raise DistrictReportError(
                "company list contains an unbreakable word wider than a column"
            )
    lines = []
    current = words[0]
    for word in words[1:]:
        candidate = f"{current} {word}"
        if stringWidth(candidate, COMPANY_LIST_FONT, COMPANY_LIST_FONT_SIZE) <= COMPANY_LIST_WIDTH:
            current = candidate
        else:
            lines.append(current)
            current = word
    lines.append(current)
    return tuple(lines)


def _plan_company_layout(companies):
    """Measure and pack complete company entries across columns and pages."""
    placements = []
    page = 1
    column = 0
    y = COMPANY_LIST_FIRST_PAGE_TOP
    for company in companies:
        lines = _wrap_company_text(_company_text(company))
        entry_bottom = y - (len(lines) - 1) * COMPANY_LIST_LINE_HEIGHT
        if entry_bottom < COMPANY_LIST_FOOTER_TOP:
            column += 1
            if column == COMPANY_LIST_COLUMNS:
                page += 1
                column = 0
            if page > MAX_REPORT_PAGES:
                raise DistrictReportError(
                    "company list cannot fit within two pages at the 8-point minimum"
                )
            y = (
                COMPANY_LIST_FIRST_PAGE_TOP
                if page == 1
                else COMPANY_LIST_CONTINUATION_TOP
            )
            entry_bottom = y - (len(lines) - 1) * COMPANY_LIST_LINE_HEIGHT
            if entry_bottom < COMPANY_LIST_FOOTER_TOP:
                raise DistrictReportError(
                    "a complete company entry cannot fit within one report column"
                )
        x = 36 + column * (COMPANY_LIST_WIDTH + COMPANY_LIST_COLUMN_GAP)
        line_placements = tuple(
            CompanyLinePlacement(line, page, x, y - index * COMPANY_LIST_LINE_HEIGHT)
            for index, line in enumerate(lines)
        )
        placements.append(
            CompanyPlacement(company, page, column, line_placements)
        )
        y -= len(lines) * COMPANY_LIST_LINE_HEIGHT + COMPANY_LIST_ENTRY_GAP
    return tuple(placements)


def _preflight(companies):
    """Backward-compatible entry point for callers that only need validation."""
    return _plan_company_layout(companies)


def _draw_company_page(canvas, placements, page, footer):
    canvas.setFillColor(colors.black)
    canvas.setFont(COMPANY_LIST_FONT, COMPANY_LIST_FONT_SIZE)
    for placement in placements:
        if placement.page != page:
            continue
        for line in placement.line_placements:
            canvas.drawString(line.x, line.y, line.text)
    canvas.setFont("Helvetica", 7)
    canvas.drawString(36, 26, footer)


def _draw_companies(canvas, companies, x=36, y=COMPANY_LIST_FIRST_PAGE_TOP):
    """Draw a first-page list for compatibility with older focused tests."""
    del x, y
    _draw_company_page(canvas, _plan_company_layout(companies), 1, "")

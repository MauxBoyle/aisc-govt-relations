"""One- or two-page, externally shareable Illinois congressional reports."""

import csv
import math
from dataclasses import dataclass
from pathlib import Path

from reportlab.lib import colors
from reportlab.lib.pagesizes import letter
from reportlab.lib.utils import ImageReader
from reportlab.pdfbase.pdfmetrics import stringWidth
from reportlab.pdfgen.canvas import Canvas

from .census_boundaries import load_boundary_snapshot
from .census_map_references import (
    MapPoint,
    load_map_references,
    point_for_company,
    point_for_reference,
)
from .districts import (
    DistrictAggregateRow,
    read_districts_csv,
    validate_snapshot_metadata,
)
from .external_report_contract import (
    PublicCompany,
    PublicContact,
    PublicJobAggregate,
    format_company_text,
    format_known_jobs,
    format_public_contact,
    format_source_dates,
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

# The map uses the printable width and the lower two-thirds of a letter page.
# It is deliberately a background for first-page report text, not a separate
# panel competing with the company list.
MAP_X = 36
MAP_Y = 36
MAP_WIDTH = letter[0] - (2 * MAP_X)
MAP_HEIGHT = (letter[1] * 2 / 3) - MAP_Y
REPRESENTATIVE_PHOTO_WIDTH = 102
REPRESENTATIVE_PHOTO_HEIGHT = 81


ExternalCompany = PublicCompany


@dataclass(frozen=True)
class _MapCompany:
    """Private map input kept separate from public company display fields."""

    company: PublicCompany
    point: MapPoint | None


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
            return [
                DistrictAggregateRow(
                    **{
                        key: _number(row.get(key, ""))
                        if key.endswith(("count", "jobs", "data"))
                        else (row.get(key) or "").strip()
                        for key in fields
                    }
                )
                for row in reader
            ]
        except ValueError as error:
            raise DistrictReportError(
                "aggregate snapshot contains invalid numbers"
            ) from error


def _number(value):
    return int(str(value).strip())


def district_filename(district: str) -> str:
    return f"illinois-congressional-district-{int(district):02d}-external.pdf"


def senate_filename() -> str:
    return "illinois-senate-delegation-external.pdf"


def render_house_report(
    district,
    districts_csv,
    aggregates_csv,
    output,
    house_members,
    *,
    house_photos=(),
    boundary_paths=None,
    map_reference_paths=None,
    as_of="",
):
    district = str(int(str(district)))
    member = next(
        (
            m
            for m in members_for_state(tuple(house_members), "IL")
            if m.district == district
        ),
        None,
    )
    if member is None:
        raise DistrictReportError(
            f"Illinois district {district} is not in the House snapshot."
        )
    _validate_inputs(districts_csv, aggregates_csv)
    rows = read_districts_csv(districts_csv)
    selected = [
        row
        for row in rows
        if row.assignment_source in {"census-confirmed", "fallback-confirmed"}
        and row.state == "IL"
        and row.congressional_district == district
    ]
    if not selected:
        raise DistrictReportError(
            f"No saved companies exist for Illinois district {district}."
        )
    aggregates = read_aggregates_csv(aggregates_csv)
    geoids = {row.congressional_district_geoid for row in selected}
    if len(geoids) != 1:
        raise DistrictReportError("district snapshot has conflicting district GEOIDs.")
    district_aggregate = _aggregate(
        aggregates, "district", "IL", "17", district, geoids.pop()
    )
    state = _aggregate(aggregates, "state", "IL", "17", "", "")
    _match_count(selected, district_aggregate)
    shapes, metadata = (
        load_boundary_snapshot(*(boundary_paths or ()))
        if boundary_paths
        else load_boundary_snapshot()
    )
    if district_aggregate.congressional_district_geoid not in shapes:
        raise DistrictReportError(
            "selected district is absent from the Census boundary snapshot."
        )
    if str(metadata.get("congressional_session")) not in _sessions(rows):
        raise DistrictReportError(
            "Census boundary session is incompatible with the district snapshot."
        )
    contact = _house_contact(member)
    photo_path = next(
        (
            photo.path
            for photo in house_photos
            if (photo.state, photo.district) == (member.state, member.district)
        ),
        None,
    )
    references = (
        load_map_references(*(map_reference_paths or ()))
        if map_reference_paths
        else load_map_references()
    )
    companies, marker_points = _external_companies(selected, references)
    return _render(
        output,
        f"Illinois Congressional District {district}",
        contact,
        companies,
        district_aggregate,
        state,
        shapes,
        district_aggregate.congressional_district_geoid,
        metadata,
        as_of,
        "District",
        marker_points,
        photo_path=photo_path,
        comparison_label="Illinois",
    )


def render_senate_report(
    districts_csv,
    aggregates_csv,
    output,
    senators,
    *,
    boundary_paths=None,
    map_reference_paths=None,
    as_of="",
):
    _validate_inputs(districts_csv, aggregates_csv)
    rows = [
        row
        for row in read_districts_csv(districts_csv)
        if row.assignment_source in {"census-confirmed", "fallback-confirmed"}
        and row.state == "IL"
    ]
    aggregates = read_aggregates_csv(aggregates_csv)
    state = _aggregate(aggregates, "state", "IL", "17", "", "")
    national = _aggregate(aggregates, "national", "", "", "", "")
    _match_count(rows, state)
    shapes, metadata = (
        load_boundary_snapshot(*(boundary_paths or ()))
        if boundary_paths
        else load_boundary_snapshot()
    )
    if str(metadata.get("congressional_session")) not in _sessions(rows):
        raise DistrictReportError(
            "Census boundary session is incompatible with the district snapshot."
        )
    contacts = tuple(
        _senate_contact(s) for s in senators_for_state(tuple(senators), "IL")
    )
    references = (
        load_map_references(*(map_reference_paths or ()))
        if map_reference_paths
        else load_map_references()
    )
    companies, marker_points = _external_companies(rows, references)
    return _render(
        output,
        "Illinois U.S. Senate Delegation",
        contacts,
        companies,
        state,
        national,
        shapes,
        "",
        metadata,
        as_of,
        "Illinois",
        marker_points,
        comparison_label="National",
    )


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


def _aggregate(rows, scope, state, state_fips, district, geoid):
    found = [
        r
        for r in rows
        if (
            r.scope,
            r.state,
            r.state_fips,
            r.congressional_district,
            r.congressional_district_geoid,
        )
        == (scope, state, state_fips, district, geoid)
    ]
    if len(found) != 1:
        raise DistrictReportError(
            f"required {scope} aggregate row is missing or duplicated"
        )
    return found[0]


def _match_count(rows, aggregate):
    if len(rows) != aggregate.included_company_count:
        raise DistrictReportError("company list does not match its aggregate snapshot")


def _external_companies(rows, references):
    """Separate allow-listed display objects from private map-drawing points."""
    mapped = tuple(
        _MapCompany(
            PublicCompany(
                row.company_name,
                row.city,
                row.county,
                row.relationship_summary,
            ),
            point_for_reference(
                row.map_reference_kind, row.map_reference_key, references
            )
            if row.map_reference_kind
            else point_for_company(row.city, row.county_fips, references),
        )
        for row in rows
    )
    return (
        tuple(item.company for item in mapped),
        tuple(item.point for item in mapped if item.point),
    )


def _render(
    output,
    title,
    contacts,
    rows,
    local,
    comparison,
    shapes,
    highlighted,
    metadata,
    as_of,
    local_label="District",
    marker_points=(),
    photo_path=None,
    comparison_label="National",
):
    companies = sorted((_company(r) for r in rows), key=lambda c: c.name.casefold())
    contact_blocks = _contacts(contacts)
    company_top = _company_top(contact_blocks)
    placements = _plan_company_layout(companies, first_page_top=company_top)
    output = Path(output)
    output.parent.mkdir(parents=True, exist_ok=True)
    temporary = output.with_suffix(output.suffix + ".tmp")
    canvas = Canvas(str(temporary), pagesize=letter)
    width, height = letter
    canvas.setTitle(title)
    _draw_map(
        canvas,
        shapes,
        highlighted,
        MAP_X,
        MAP_Y,
        MAP_WIDTH,
        MAP_HEIGHT,
        marker_points,
    )
    canvas.setFont("Helvetica-Bold", 15)
    canvas.drawString(36, height - 42, title)
    contact_bottom = _draw_contact_blocks(
        canvas, contact_blocks, height - 58, photo_path
    )
    canvas.setFont("Helvetica-Bold", 9)
    canvas.drawString(
        36, contact_bottom - 14, f"Companies: {local.included_company_count}"
    )
    canvas.setFont("Helvetica", 8)
    canvas.drawString(36, contact_bottom - 28, _jobs(local_label, local))
    canvas.drawString(36, contact_bottom - 40, _jobs(comparison_label, comparison))
    footer = format_source_dates(metadata["retrieved_at"], as_of)
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
    return format_known_jobs(
        label,
        PublicJobAggregate(
            row.included_company_count,
            row.known_jobs,
            row.companies_with_employee_data,
        ),
    )


def _house_contact(member):
    if member.vacant:
        return PublicContact("Vacant")
    return PublicContact(
        member.name,
        f"{member.party} — District {member.district}",
        member.address,
        member.phone,
        member.website_url,
        member.contact_form_url,
    )


def _senate_contact(senator):
    return PublicContact(
        senator.name,
        "U.S. Senator",
        senator.address,
        senator.phone,
        "",
        senator.contact_form_url,
    )


def _contacts(value):
    if isinstance(value, PublicContact):
        return (value,)
    if isinstance(value, str):
        return (PublicContact(value),)
    return tuple(value)


def _company_top(contacts):
    """Reserve the card, three aggregate lines, and a clear gap above companies."""
    heights = []
    for contact in contacts:
        height = 11 + (10 if contact.affiliation else 0)
        for label, value in format_public_contact(contact):
            height += 10
            if label == "Address":
                height += max(0, len(value.splitlines()) - 1) * 9
        heights.append(height)
    return letter[1] - 58 - max(heights) - 54


def _draw_contact_blocks(canvas, contacts, top, photo_path):
    width, _ = letter
    columns = len(contacts)
    card_width = (width - 72 - (10 if columns == 2 else 0)) / columns
    bottoms = []
    for index, contact in enumerate(contacts):
        x = 36 + index * (card_width + 10)
        canvas.setFont("Helvetica-Bold", 10)
        canvas.drawString(x, top, contact.name)
        y = top - 11
        if contact.affiliation:
            canvas.setFont("Helvetica", 8)
            canvas.drawString(x, y, contact.affiliation)
            y -= 10
        canvas.setFont("Helvetica", 7.5)
        for label, value in format_public_contact(contact):
            if label == "Address":
                lines = value.splitlines()
                canvas.drawString(x, y, f"Address: {lines[0]}")
                for line in lines[1:]:
                    y -= 9
                    canvas.drawString(x + 28, y, line)
            else:
                text = f"{label}: {value}" if label else value
                canvas.drawString(x, y, text)
                if label in {"Website", "Contact form"}:
                    canvas.linkURL(
                        value,
                        (
                            x,
                            y - 2,
                            min(
                                x + stringWidth(text, "Helvetica", 7.5), x + card_width
                            ),
                            y + 8,
                        ),
                        relative=0,
                    )
            y -= 10
        bottoms.append(y)
    if photo_path and len(contacts) == 1:
        try:
            canvas.drawImage(
                ImageReader(str(photo_path)),
                width - 36 - REPRESENTATIVE_PHOTO_WIDTH,
                top - REPRESENTATIVE_PHOTO_HEIGHT,
                REPRESENTATIVE_PHOTO_WIDTH,
                REPRESENTATIVE_PHOTO_HEIGHT,
                preserveAspectRatio=True,
                anchor="c",
                mask="auto",
            )
        except (OSError, ValueError):
            # Snapshot validation catches corruption; rendering still never falls back to a network image.
            pass
    return min(bottoms)


def _company(row):
    if isinstance(row, PublicCompany):
        return row
    return PublicCompany(
        row.company_name, row.city, row.county, getattr(row, "relationship_summary", "")
    )


def _draw_map(canvas, shapes, highlighted, x, y, width, height, marker_points=()):
    """Draw a printable local House map or an Illinois-wide Senate map."""
    saved_state = hasattr(canvas, "saveState") and hasattr(canvas, "restoreState")
    if saved_state:
        canvas.saveState()
    try:
        # Alpha is part of the saved state, so it applies equally to polygon
        # fills/outlines, markers, and marker-count labels without affecting
        # report text drawn later.
        if hasattr(canvas, "setFillAlpha"):
            canvas.setFillAlpha(0.75)
        if hasattr(canvas, "setStrokeAlpha"):
            canvas.setStrokeAlpha(0.75)
        if hasattr(canvas, "clipPath"):
            clip = canvas.beginPath()
            clip.rect(x, y, width, height)
            canvas.clipPath(clip, stroke=0, fill=0)
        extent = _map_extent(shapes, highlighted, marker_points, width / height)

        def project(point):
            return _project_point(point, extent, x, y, width, height)

        for geoid, rings in shapes.items():
            # Senate intentionally retains the statewide view. House maps only draw
            # boundaries near the selected district/marker extent.
            if highlighted and not _intersects(_ring_bounds(rings), extent):
                continue
            selected = geoid == highlighted
            canvas.setFillColor(
                colors.HexColor("#c43d36") if selected else colors.white
            )
            canvas.setStrokeColor(
                colors.HexColor("#9b2d28") if selected else colors.HexColor("#aaaaaa")
            )
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
    finally:
        if saved_state:
            canvas.restoreState()


def _map_extent(shapes, highlighted, marker_points, aspect_ratio):
    """Return lon/lat bounds expanded to the final printable map aspect ratio."""
    if highlighted:
        source = [
            point for rings in [shapes[highlighted]] for ring in rings for point in ring
        ]
        source.extend(marker_points)
    else:
        source = [
            point for rings in shapes.values() for ring in rings for point in ring
        ]
    min_lon, max_lon = (
        min(point[0] for point in source),
        max(point[0] for point in source),
    )
    min_lat, max_lat = (
        min(point[1] for point in source),
        max(point[1] for point in source),
    )
    lon_pad, lat_pad = (
        max((max_lon - min_lon) * 0.08, 0.04),
        max((max_lat - min_lat) * 0.08, 0.04),
    )
    min_lon, max_lon, min_lat, max_lat = (
        min_lon - lon_pad,
        max_lon + lon_pad,
        min_lat - lat_pad,
        max_lat + lat_pad,
    )
    mid_lat = (min_lat + max_lat) / 2
    projected_width, projected_height = (
        (max_lon - min_lon) * math.cos(math.radians(mid_lat)),
        max_lat - min_lat,
    )
    if projected_width / projected_height < aspect_ratio:
        grow = (
            projected_height * aspect_ratio / math.cos(math.radians(mid_lat))
            - (max_lon - min_lon)
        ) / 2
        min_lon, max_lon = min_lon - grow, max_lon + grow
    else:
        grow = (projected_width / aspect_ratio - (max_lat - min_lat)) / 2
        min_lat, max_lat = min_lat - grow, max_lat + grow
    return min_lon, max_lon, min_lat, max_lat


def _project_point(point, extent, x, y, width, height):
    min_lon, max_lon, min_lat, max_lat = extent
    mid_lat = (min_lat + max_lat) / 2
    lon_scale = math.cos(math.radians(mid_lat))
    return (
        x
        + ((point[0] - min_lon) * lon_scale)
        / ((max_lon - min_lon) * lon_scale)
        * width,
        y + (point[1] - min_lat) / (max_lat - min_lat) * height,
    )


def _ring_bounds(rings):
    points = [point for ring in rings for point in ring]
    return (
        min(p[0] for p in points),
        max(p[0] for p in points),
        min(p[1] for p in points),
        max(p[1] for p in points),
    )


def _intersects(bounds, extent):
    return not (
        bounds[1] < extent[0]
        or bounds[0] > extent[1]
        or bounds[3] < extent[2]
        or bounds[2] > extent[3]
    )


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
    return format_company_text(company)


def _wrap_company_text(text):
    """Wrap at word boundaries using ReportLab's actual font measurements."""
    words = text.split()
    if not words:
        return ("",)
    for word in words:
        if (
            stringWidth(word, COMPANY_LIST_FONT, COMPANY_LIST_FONT_SIZE)
            > COMPANY_LIST_WIDTH
        ):
            raise DistrictReportError(
                "company list contains an unbreakable word wider than a column"
            )
    lines = []
    current = words[0]
    for word in words[1:]:
        candidate = f"{current} {word}"
        if (
            stringWidth(candidate, COMPANY_LIST_FONT, COMPANY_LIST_FONT_SIZE)
            <= COMPANY_LIST_WIDTH
        ):
            current = candidate
        else:
            lines.append(current)
            current = word
    lines.append(current)
    return tuple(lines)


def _plan_company_layout(companies, *, first_page_top=COMPANY_LIST_FIRST_PAGE_TOP):
    """Measure and pack complete company entries across columns and pages."""
    placements = []
    page = 1
    column = 0
    y = first_page_top
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
            y = first_page_top if page == 1 else COMPANY_LIST_CONTINUATION_TOP
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
        placements.append(CompanyPlacement(company, page, column, line_placements))
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

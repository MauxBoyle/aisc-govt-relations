"""Regression tests for externally shareable district PDFs."""

import csv
from pathlib import Path

import pytest
from pypdf import PdfReader
from reportlab.pdfbase.pdfmetrics import stringWidth

from aisc_gr_statistics import app as app_module
from aisc_gr_statistics import external_district_report as report_module
from aisc_gr_statistics.districts import (
    DistrictAggregateRow,
    DistrictRow,
    read_districts_csv,
    write_district_aggregates_csv,
    write_districts_csv,
)
from aisc_gr_statistics.external_district_report import (
    COMPANY_LIST_FONT_SIZE,
    COMPANY_LIST_WIDTH,
    MAP_HEIGHT,
    MAP_WIDTH,
    MAP_X,
    MAP_Y,
    MAX_REPORT_PAGES,
    DistrictReportError,
    ExternalCompany,
    _plan_company_layout,
    _draw_map,
    _render,
    render_house_report,
    render_senate_report,
)
from aisc_gr_statistics.house import HouseContact
from aisc_gr_statistics.house import load_snapshot as load_house_snapshot
from aisc_gr_statistics.senate import SenatorContact
from aisc_gr_statistics.senate import load_snapshot as load_senate_snapshot

FIXTURE = Path(__file__).parent / "fixtures/external-report-companies.csv"


def _snapshots(tmp_path, *, aggregate_count=None):
    districts = tmp_path / "districts.csv"
    aggregates = tmp_path / "aggregates.csv"
    with FIXTURE.open(newline="", encoding="utf-8") as handle:
        rows = [DistrictRow(**row) for row in csv.DictReader(handle)]
    write_districts_csv(rows, districts)
    count = len(rows) if aggregate_count is None else aggregate_count
    write_district_aggregates_csv(
        [
            DistrictAggregateRow("national", "", "", "", "", count, 37, 2, 1),
            DistrictAggregateRow("state", "IL", "17", "", "", count, 37, 2, 1),
            DistrictAggregateRow("district", "IL", "17", "7", "1707", 2, 25, 1, 1),
            DistrictAggregateRow("district", "IL", "17", "8", "1708", 1, 12, 1, 0),
        ],
        aggregates,
    )
    return districts, aggregates


def _house_member():
    return [
        HouseContact(
            "Representative Example" if district == 7 else f"Representative {district}",
            "D",
            "IL",
            str(district),
        )
        for district in range(1, 18)
    ]


def _senators():
    return [
        SenatorContact("Senator One", "IL", "", "", "https://example.test/one"),
        SenatorContact("Senator Two", "IL", "", "", "https://example.test/two"),
    ]


def _text(path):
    reader = PdfReader(path)
    return reader, "\n".join(page.extract_text() or "" for page in reader.pages)


def test_house_and_senate_use_the_correct_company_populations(tmp_path):
    districts, aggregates = _snapshots(tmp_path)
    house = render_house_report(
        7, districts, aggregates, tmp_path / "house.pdf", _house_member()
    )
    senate = render_senate_report(
        districts, aggregates, tmp_path / "senate.pdf", _senators()
    )

    house_reader, house_text = _text(house)
    senate_reader, senate_text = _text(senate)
    assert len(house_reader.pages) == len(senate_reader.pages) == 1
    assert "Prairie Structural Steel" in house_text
    assert "Lakefront Steel Works" in house_text
    assert "Fox River Fabricators" not in house_text
    assert all(name in senate_text for name in (
        "Prairie Structural Steel", "Lakefront Steel Works", "Fox River Fabricators"
    ))
    assert "District/state Known jobs: 25 (1 of 2 companies" in house_text
    assert "District/state Known jobs: 37 (2 of 3 companies" in senate_text


def test_render_draws_one_lower_page_map_before_company_text(monkeypatch, tmp_path):
    events = []

    def record_map(canvas, shapes, highlighted, x, y, width, height, marker_points):
        events.append(("map", x, y, width, height))

    def record_company_page(canvas, placements, page, footer):
        events.append(("companies", page))

    monkeypatch.setattr(report_module, "_draw_map", record_map)
    monkeypatch.setattr(report_module, "_draw_company_page", record_company_page)
    companies = []
    while not companies or max(item.page for item in _plan_company_layout(companies)) == 1:
        companies.append(ExternalCompany(f"Company {len(companies):03d}", "Chicago", "Cook County"))
    count = len(companies)
    aggregate = DistrictAggregateRow("state", "IL", "17", "", "", count, 0, 0, count)

    _render(
        tmp_path / "layered.pdf", "Title", "Identity", companies, aggregate, aggregate,
        {"1707": [[(-88.0, 41.0), (-87.0, 41.0), (-87.0, 42.0), (-88.0, 41.0)]]},
        "", {"retrieved_at": "2026-09-30T00:00:00Z"}, "",
    )

    assert events == [
        ("map", MAP_X, MAP_Y, MAP_WIDTH, MAP_HEIGHT),
        ("companies", 1),
        ("companies", 2),
    ]
    assert MAP_X == 36
    assert MAP_WIDTH == 612 - 72
    assert MAP_Y >= 36
    assert MAP_Y + MAP_HEIGHT <= 792 * 2 / 3


def test_draw_map_isolates_state_and_applies_alpha_to_shapes_and_markers():
    class RecordingCanvas:
        def __init__(self):
            self.calls = []

        def __getattr__(self, name):
            def record(*args, **kwargs):
                self.calls.append((name, args, kwargs))
                return self
            return record

    canvas = RecordingCanvas()
    _draw_map(
        canvas,
        {"1707": [[(-88.0, 41.0), (-87.0, 41.0), (-87.0, 42.0), (-88.0, 41.0)]]},
        "1707",
        MAP_X,
        MAP_Y,
        MAP_WIDTH,
        MAP_HEIGHT,
        [(-87.5, 41.5), (-87.5, 41.5)],
    )

    names = [name for name, _, _ in canvas.calls]
    assert names[0] == "saveState"
    assert names[-1] == "restoreState"
    assert ("setFillAlpha", (0.75,), {}) in canvas.calls
    assert ("setStrokeAlpha", (0.75,), {}) in canvas.calls
    assert "drawPath" in names
    assert "circle" in names
    assert "drawCentredString" in names


def test_external_allowlist_keeps_internal_snapshot_fields_out_of_pdf(tmp_path):
    districts, aggregates = _snapshots(tmp_path)
    output = render_senate_report(
        districts, aggregates, tmp_path / "senate.pdf", _senators()
    )
    _, text = _text(output)

    for sentinel in (
        "SECRET-IMIS", "SECRET-SF", "Hidden Street", "HIDDEN AVE",
        "41.SECRET", "87.PRIVATE", "INTERNAL-CLASS", "SECRET-BENCHMARK",
        "SECRET-VINTAGE", "SECRET-CONFIDENCE",
    ):
        assert sentinel not in text


def test_count_mismatch_fails_without_creating_final_pdf(tmp_path):
    districts, aggregates = _snapshots(tmp_path, aggregate_count=99)
    output = tmp_path / "senate.pdf"
    with pytest.raises(DistrictReportError, match="complete confirmed|does not match"):
        render_senate_report(districts, aggregates, output, _senators())
    assert not output.exists()


def test_layout_wraps_long_names_at_eight_points_and_preserves_entries():
    long_name = "Prairie Structural Steel Fabrication and Engineering Company"
    plans = _plan_company_layout(
        [ExternalCompany(long_name, "Arlington Heights", "Cook County")]
    )

    assert COMPANY_LIST_FONT_SIZE == 8
    assert MAX_REPORT_PAGES == 2
    assert len(plans) == 1
    assert len(plans[0].lines) > 1
    assert " ".join(plans[0].lines) == (
        f"{long_name} — Arlington Heights, Cook County"
    )
    assert all(
        stringWidth(line, "Helvetica", COMPANY_LIST_FONT_SIZE) <= COMPANY_LIST_WIDTH
        for line in plans[0].lines
    )
    assert plans[0].bottom >= plans[0].footer_top


def test_layout_uses_one_page_then_exactly_two_pages_without_splitting_entries():
    company = ExternalCompany("AISC Example Company", "Chicago", "Cook County")
    one_page = _plan_company_layout([company])
    assert {item.page for item in one_page} == {1}

    companies = []
    while True:
        companies.append(ExternalCompany(f"Company {len(companies):03d}", "Chicago", "Cook County"))
        plan = _plan_company_layout(companies)
        if max(item.page for item in plan) == 2:
            break
    assert {item.page for item in plan} == {1, 2}
    assert all(len({line.page for line in item.line_placements}) == 1 for item in plan)


def test_layout_rejects_over_capacity_and_unbreakable_tokens():
    with pytest.raises(DistrictReportError, match="cannot fit within two pages"):
        _plan_company_layout(
            [ExternalCompany(f"Company {index:04d}", "Chicago", "Cook County") for index in range(1000)]
        )
    with pytest.raises(DistrictReportError, match="unbreakable"):
        _plan_company_layout([ExternalCompany("X" * 100, "Chicago", "Cook County")])


def test_two_page_pdf_keeps_wrapped_names_extractable_and_above_footer(tmp_path):
    companies = [ExternalCompany(f"Company {index:03d} With A Longer Name", "Chicago", "Cook County") for index in range(100)]
    while True:
        try:
            plan = _plan_company_layout(companies)
        except DistrictReportError:
            companies.pop()
            continue
        if max(item.page for item in plan) == 2:
            break
        companies.append(ExternalCompany(f"Company {len(companies):03d} With A Longer Name", "Chicago", "Cook County"))
    assert min(item.bottom for item in plan) >= min(item.footer_top for item in plan)

    count = len(companies)
    aggregate = DistrictAggregateRow("state", "IL", "17", "", "", count, 0, 0, count)
    output = _render(
        tmp_path / "two-pages.pdf",
        "Illinois U.S. Senate Delegation",
        "Senator One / Senator Two",
        companies,
        aggregate,
        aggregate,
        {"1707": [[(-88.0, 41.0), (-87.0, 41.0), (-87.0, 42.0), (-88.0, 41.0)]]},
        "",
        {"retrieved_at": "2026-09-30T00:00:00Z"},
        "2026-09-30",
    )
    reader, text = _text(output)
    assert len(reader.pages) == 2
    assert "Companies continued" in text
    assert "Company 000 With A Longer Name" in " ".join(text.split())
    assert f"Company {count - 1:03d} With A Longer Name" in " ".join(text.split())
    streams = b"\n".join(page.get_contents().get_data() for page in reader.pages)
    assert b" 8 Tf" in streams


def test_render_over_capacity_fails_before_creating_output(tmp_path):
    companies = [
        ExternalCompany(f"Company {index:04d}", "Chicago", "Cook County")
        for index in range(1000)
    ]
    aggregate = DistrictAggregateRow("state", "IL", "17", "", "", len(companies), 0, 0, len(companies))
    output = tmp_path / "too-many.pdf"
    with pytest.raises(DistrictReportError, match="cannot fit within two pages"):
        _render(
            output, "Title", "Identity", companies, aggregate, aggregate,
            {"1707": [[(-88.0, 41.0), (-87.0, 41.0), (-87.0, 42.0), (-88.0, 41.0)]]},
            "", {"retrieved_at": "2026-09-30T00:00:00Z"}, "",
        )
    assert not output.exists()


def test_fixture_is_readable_through_public_snapshot_reader(tmp_path):
    districts, _ = _snapshots(tmp_path)
    assert len(read_districts_csv(districts)) == 3


def test_cli_uses_current_committed_house_and_senate_snapshots(monkeypatch, tmp_path):
    captured = {}

    def fake_house(district, districts_csv, aggregates_csv, output, members, **options):
        captured["house"] = tuple(member.name for member in members)
        return output

    def fake_senate(districts_csv, aggregates_csv, output, senators, **options):
        captured["senate"] = tuple(senator.name for senator in senators)
        return output

    monkeypatch.setattr(app_module, "render_house_report", fake_house)
    monkeypatch.setattr(app_module, "render_senate_report", fake_senate)
    app_module.main(
        ["district-report", "--district", "7", "--senate", "--output-dir", str(tmp_path)]
    )

    expected_house = tuple(member.name for member in load_house_snapshot().members)
    expected_senate = tuple(senator.name for senator in load_senate_snapshot().senators)
    assert captured == {"house": expected_house, "senate": expected_senate}

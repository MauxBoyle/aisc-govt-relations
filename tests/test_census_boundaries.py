"""Tests for the offline Illinois Census boundary snapshot."""

import hashlib
import json
from datetime import UTC, datetime
from io import BytesIO
from types import SimpleNamespace
from zipfile import ZipFile

import pytest
import requests
from reportlab.lib import colors

from aisc_gr_statistics import census_boundaries
from aisc_gr_statistics.census_boundaries import (
    ILLINOIS_GEOIDS,
    BoundarySnapshotError,
    load_boundary_snapshot,
    refresh_boundary_snapshot,
)
from aisc_gr_statistics.districts import DistrictAggregateRow
from aisc_gr_statistics.external_district_report import _draw_map, _render


def _kml(*, geoids=ILLINOIS_GEOIDS, multipart=False):
    placemarks = []
    for geoid in sorted(geoids):
        rings = """
            <Polygon><outerBoundaryIs><LinearRing><coordinates>
            -89,40 -88,40 -88,41 -89,40 -89,40
            </coordinates></LinearRing></outerBoundaryIs></Polygon>
        """
        if multipart and geoid == "1701":
            rings += """
                <Polygon><outerBoundaryIs><LinearRing><coordinates>
                -88,41 -87,41 -87,42 -88,41 -88,41
                </coordinates></LinearRing></outerBoundaryIs></Polygon>
            """
        placemarks.append(
            f'<Placemark><ExtendedData><SchemaData><SimpleData name="STATEFP">17'
            f'</SimpleData><SimpleData name="CDSESSN">119</SimpleData><SimpleData name="GEOID">{geoid}'
            f"</SimpleData></SchemaData></ExtendedData>{rings}</Placemark>"
        )
    return (
        '<?xml version="1.0"?><kml xmlns="http://www.opengis.net/kml/2.2">'
        f"<Document>{''.join(placemarks)}</Document></kml>"
    ).encode()


def _zip(kml_data):
    return _archive({"cb_2025_17_cd119_500k.kml": kml_data})


def _archive(members):
    data = BytesIO()
    with ZipFile(data, "w") as archive:
        for name, content in members.items():
            archive.writestr(name, content)
    return data.getvalue()


class _Response:
    def __init__(self, content):
        self.content = content

    def raise_for_status(self):
        return None


def test_refresh_extracts_valid_kml_zip_and_preserves_multipart_geometry(tmp_path):
    kml_path, metadata_path = tmp_path / "districts.kml", tmp_path / "districts.json"
    downloaded = _kml(multipart=True)

    shapes, metadata = refresh_boundary_snapshot(
        kml_path,
        metadata_path,
        get=lambda url, timeout: _Response(_zip(downloaded)),
        now=lambda: datetime(2026, 1, 2, tzinfo=UTC),
    )

    assert set(shapes) == ILLINOIS_GEOIDS
    assert len(shapes["1701"]) == 2
    assert kml_path.read_bytes() == downloaded
    assert metadata["source_url"] == census_boundaries.SOURCE_URL
    assert metadata["kml_sha256"] == hashlib.sha256(downloaded).hexdigest()
    assert metadata_path.read_text(encoding="utf-8") == json.dumps(metadata, indent=2) + "\n"


@pytest.mark.parametrize(
    "download",
    [
        pytest.param(
            lambda: (_ for _ in ()).throw(requests.ConnectionError("offline")),
            id="download-failure",
        ),
        pytest.param(lambda: b"not a ZIP", id="zip-failure"),
        pytest.param(lambda: _archive({"readme.txt": b"no KML here"}), id="no-kml-member"),
        pytest.param(
            lambda: _archive({"one.kml": _kml(), "two.kml": _kml()}),
            id="multiple-kml-members",
        ),
        pytest.param(lambda: _zip(b"not XML"), id="xml-failure"),
        pytest.param(lambda: _zip(_kml(geoids={"1701"})), id="district-validation-failure"),
    ],
)
def test_failed_refresh_leaves_existing_snapshot_files_unchanged(tmp_path, download):
    kml_path, metadata_path = tmp_path / "districts.kml", tmp_path / "districts.json"
    kml_path.write_bytes(b"old KML")
    metadata_path.write_text("old metadata", encoding="utf-8")

    def get(url, timeout):
        content = download()
        return _Response(content)

    with pytest.raises(BoundarySnapshotError):
        refresh_boundary_snapshot(kml_path, metadata_path, get=get)

    assert kml_path.read_bytes() == b"old KML"
    assert metadata_path.read_text(encoding="utf-8") == "old metadata"


def test_committed_snapshot_has_all_illinois_districts_and_real_geometry():
    shapes, metadata = load_boundary_snapshot()

    assert set(shapes) == ILLINOIS_GEOIDS
    assert all(len(ring) > 4 for rings in shapes.values() for ring in rings)
    assert metadata["congressional_session"] == "119"


def test_load_rejects_a_snapshot_whose_kml_checksum_changed(tmp_path):
    kml_path, metadata_path = tmp_path / "districts.kml", tmp_path / "districts.json"
    kml_path.write_bytes(_kml())
    metadata_path.write_text(
        json.dumps(
            {
                "source_url": "https://example.test/boundaries.zip",
                "congressional_session": "119",
                "retrieved_at": "2026-01-01T00:00:00Z",
                "kml_sha256": "not-the-kml-checksum",
            }
        ),
        encoding="utf-8",
    )

    with pytest.raises(BoundarySnapshotError, match="checksum"):
        load_boundary_snapshot(kml_path, metadata_path)


def test_refresh_command_uses_the_official_default_source(monkeypatch):
    from aisc_gr_statistics.app import main

    called = []
    monkeypatch.setattr(
        census_boundaries,
        "refresh_boundary_snapshot",
        lambda: called.append(True),
    )

    main(["refresh-district-boundaries"])

    assert called == [True]


def test_map_draws_every_multipart_ring_and_highlights_the_selected_district():
    class Path:
        def moveTo(self, *point):
            return None

        def lineTo(self, *point):
            return None

        def close(self):
            return None

    class Canvas:
        def __init__(self):
            self.drawn_paths = []
            self.fill_colors = []

        def beginPath(self):
            return Path()

        def setFillColor(self, color):
            self.fill_colors.append(color)

        def setStrokeColor(self, color):
            return None

        def drawPath(self, path, *, fill, stroke):
            self.drawn_paths.append((path, fill, stroke))

    canvas = Canvas()
    _draw_map(
        canvas,
        {
            "1701": [[(-89, 40), (-88, 40), (-88, 41)]],
            "1702": [
                [(-88, 40), (-87, 40), (-87, 41)],
                [(-87, 41), (-86, 41), (-86, 42)],
            ],
        },
        "1702",
        0,
        0,
        100,
        100,
    )

    assert len(canvas.drawn_paths) == 3
    assert canvas.fill_colors[1] == colors.HexColor("#c43d36")


def test_external_pdf_can_render_from_the_committed_boundary_snapshot(tmp_path):
    shapes, metadata = load_boundary_snapshot()
    district = DistrictAggregateRow("district", "IL", "17", "1", "1701", 1, 10, 1, 0)
    national = DistrictAggregateRow("national", "", "", "", "", 1, 10, 1, 0)
    output = _render(
        tmp_path / "district.pdf",
        "Illinois Congressional District 1",
        "Example Representative",
        [SimpleNamespace(company_name="Example Steel", city="Chicago", county="Cook")],
        district,
        national,
        shapes,
        "1701",
        metadata,
        "2026-01-01",
    )

    assert output.read_bytes().startswith(b"%PDF")

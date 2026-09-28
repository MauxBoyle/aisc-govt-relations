"""Tests for conservative congressional-district enrichment."""

import csv
from datetime import date

import pytest

from aisc_gr_statistics.districts import (
    CensusServiceError,
    DistrictRow,
    DistrictSnapshotError,
    aggregate_districts,
    enrich_companies,
    write_district_aggregates_csv,
    write_districts_csv,
    write_review_csv,
)
from aisc_gr_statistics.salesforce_fields import (
    CertificationAccountField,
    CertificationStatus,
)


def _imis_csv(tmp_path, address="100 iMIS Road", postal_code="60601"):
    path = tmp_path / "imis.csv"
    path.write_text(
        "company name,state,city,iMIS ID,address,postal code\n"
        f"Example Steel,IL,Chicago,IMIS-1,{address},{postal_code}\n",
        encoding="utf-8",
    )
    return path


def _match():
    return {
        "result": {
            "addressMatches": [
                {
                    "matchedAddress": "100 BILLING AVE, CHICAGO, IL, 60601",
                    "coordinates": {"x": -87.62, "y": 41.88},
                    "geographies": {
                        "Counties": [
                            {"NAME": "Cook County", "STATE": "17", "COUNTY": "031"}
                        ],
                        "Congressional Districts": [{"BASENAME": "7", "GEOID": "1707"}],
                    },
                }
            ]
        }
    }


class Geocoder:
    def __init__(self, payload):
        self.payload = payload
        self.addresses = []

    def lookup(self, address):
        self.addresses.append(address)
        if isinstance(self.payload, Exception):
            raise self.payload
        return self.payload


def test_salesforce_complete_address_takes_precedence_and_writes_metadata(tmp_path):
    account = {
        CertificationAccountField.ID: "001",
        CertificationAccountField.IMIS_ID: "IMIS-1",
        CertificationAccountField.NAME: "Salesforce Steel",
        CertificationAccountField.BILLING_STREET: "100 Billing Ave",
        CertificationAccountField.BILLING_CITY: "Chicago",
        CertificationAccountField.BILLING_STATE: "IL",
        CertificationAccountField.BILLING_POSTAL_CODE: "60601",
    }
    geocoder = Geocoder(_match())

    districts, reviews, failed = enrich_companies(
        _imis_csv(tmp_path), [account], geocoder, date(2026, 9, 28)
    )

    assert not reviews and not failed
    assert geocoder.addresses[0].source == "Salesforce Billing Address"
    assert districts[0].county_fips == "031"
    assert districts[0].congressional_district_geoid == "1707"
    assert districts[0].census_benchmark == "Public_AR_Current"
    assert districts[0].confidence == "census-single-match"


def test_incomplete_salesforce_address_falls_back_to_complete_imis_address(tmp_path):
    account = {
        CertificationAccountField.IMIS_ID: "IMIS-1",
        CertificationAccountField.BILLING_STREET: "Billing Ave",
        CertificationAccountField.BILLING_CITY: "Chicago",
        CertificationAccountField.BILLING_STATE: "IL",
        CertificationAccountField.BILLING_POSTAL_CODE: "60601",
    }
    geocoder = Geocoder(_match())

    districts, reviews, _ = enrich_companies(_imis_csv(tmp_path), [account], geocoder)

    assert districts and not reviews
    assert geocoder.addresses[0].source == "iMIS address"


def test_review_rows_cover_incomplete_no_match_multiple_and_missing_geography(tmp_path):
    _, reviews, _ = enrich_companies(
        _imis_csv(tmp_path, postal_code=""), geocoder=Geocoder(_match())
    )
    assert reviews[0].review_reason == "incomplete address"

    for payload, reason in (
        ({"result": {"addressMatches": []}}, "no Census address match"),
        ({"result": {"addressMatches": [{}, {}]}}, "multiple Census address matches"),
        (
            {"result": {"addressMatches": [{"coordinates": {}, "geographies": {}}]}},
            "incomplete Census geography",
        ),
    ):
        _, reviews, failed = enrich_companies(
            _imis_csv(tmp_path), geocoder=Geocoder(payload)
        )
        assert reviews[0].review_reason == reason
        assert not failed


def test_malformed_payload_and_service_outage_are_reviewed(tmp_path):
    _, reviews, failed = enrich_companies(
        _imis_csv(tmp_path), geocoder=Geocoder({"wrong": "shape"})
    )
    assert reviews[0].review_reason == "malformed Census response"
    assert not failed

    _, reviews, failed = enrich_companies(
        _imis_csv(tmp_path), geocoder=Geocoder(CensusServiceError("down"))
    )
    assert reviews[0].review_reason == "Census service error"
    assert failed


def test_csv_outputs_include_required_columns(tmp_path):
    districts, reviews, _ = enrich_companies(
        _imis_csv(tmp_path), geocoder=Geocoder(_match())
    )
    districts_path = tmp_path / "districts.csv"
    review_path = tmp_path / "review.csv"
    write_districts_csv(districts, districts_path)
    write_review_csv(reviews, review_path)

    assert {
        "address_source",
        "state_fips",
        "county_fips",
        "congressional_district_geoid",
        "census_benchmark",
        "confidence",
    } <= set(next(csv.reader(districts_path.open())))
    assert {"source_address", "review_reason", "candidate_information"} <= set(
        next(csv.reader(review_path.open()))
    )


def _aggregate_imis_csv(tmp_path):
    path = tmp_path / "imis.csv"
    path.write_text(
        "company name,state,city,iMIS ID\n"
        "One Steel,IL,Chicago,IMIS-1\n"
        "Two Steel,IL,Chicago,IMIS-2\n"
        "Three Steel,IL,Chicago,IMIS-3\n",
        encoding="utf-8",
    )
    return path


def _account(imis_id, account_id, employees):
    return {
        CertificationAccountField.IMIS_ID: imis_id,
        CertificationAccountField.ID: account_id,
        CertificationAccountField.BILLING_STATE: "IL",
        CertificationAccountField.CERTIFICATION_STATUS: CertificationStatus.CERTIFIED,
        CertificationAccountField.EMPLOYEE_COUNT: employees,
    }


def _district_row(imis_id, account_id, geoid="1707"):
    return DistrictRow(
        "", "both", imis_id, account_id, "", "", "", "IL", "IL", "17", "",
        "", geoid[-1], geoid, "", "", "", "", "", "matched", ""
    )


def test_aggregate_districts_counts_known_and_missing_employee_data(tmp_path):
    snapshot = tmp_path / "districts.csv"
    write_districts_csv(
        [_district_row("IMIS-1", "001"), _district_row("IMIS-2", "002")], snapshot
    )

    rows = aggregate_districts(
        _aggregate_imis_csv(tmp_path),
        snapshot,
        [
            _account("IMIS-1", "001", "10"),
            _account("IMIS-2", "002", 0),
            _account("IMIS-3", "003", "not a number"),
        ],
    )

    national, district = rows
    assert (national.included_company_count, national.known_jobs) == (3, 10)
    assert (national.companies_with_employee_data, national.companies_missing_employee_data) == (2, 1)
    assert (district.included_company_count, district.known_jobs) == (2, 10)
    assert district.congressional_district_geoid == "1707"


def test_aggregate_districts_treats_invalid_employee_counts_as_missing(tmp_path):
    snapshot = tmp_path / "districts.csv"
    write_districts_csv([_district_row("IMIS-1", "001")], snapshot)
    for value in ("", "3.5", -1, "not a number"):
        rows = aggregate_districts(
            _aggregate_imis_csv(tmp_path),
            snapshot,
            [_account("IMIS-1", "001", value)],
        )
        assert rows[0].companies_missing_employee_data == 3
        assert rows[0].known_jobs == 0


def test_aggregate_districts_rejects_duplicate_or_unknown_snapshot_rows(tmp_path):
    snapshot = tmp_path / "districts.csv"
    write_districts_csv(
        [_district_row("IMIS-1", "001"), _district_row("IMIS-1", "001")], snapshot
    )
    accounts = [_account("IMIS-1", "001", 10)]
    with pytest.raises(DistrictSnapshotError, match="duplicate"):
        aggregate_districts(_aggregate_imis_csv(tmp_path), snapshot, accounts)

    write_districts_csv([_district_row("UNKNOWN", "999")], snapshot)
    with pytest.raises(DistrictSnapshotError, match="outside"):
        aggregate_districts(_aggregate_imis_csv(tmp_path), snapshot, accounts)


def test_district_aggregate_csv_has_stable_national_then_district_rows(tmp_path):
    snapshot = tmp_path / "districts.csv"
    output = tmp_path / "aggregates.csv"
    write_districts_csv([_district_row("IMIS-1", "001")], snapshot)
    rows = aggregate_districts(
        _aggregate_imis_csv(tmp_path), snapshot, [_account("IMIS-1", "001", 10)]
    )
    write_district_aggregates_csv(rows, output)

    written = list(csv.DictReader(output.open()))
    assert list(written[0]) == [
        "scope", "state", "state_fips", "congressional_district",
        "congressional_district_geoid", "included_company_count", "known_jobs",
        "companies_with_employee_data", "companies_missing_employee_data",
    ]
    assert [row["scope"] for row in written] == ["national", "district"]

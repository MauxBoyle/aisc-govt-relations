"""Tests for conservative congressional-district enrichment."""

import csv
from datetime import date

from aisc_gr_statistics.districts import (
    CensusServiceError,
    enrich_companies,
    write_districts_csv,
    write_review_csv,
)
from aisc_gr_statistics.salesforce_fields import CertificationAccountField


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

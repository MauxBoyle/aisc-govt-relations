"""Tests for conservative congressional-district enrichment."""

import csv
from datetime import date

import pytest

from aisc_gr_statistics.address_normalization import normalize_street
from aisc_gr_statistics.districts import (
    CensusGeocoder,
    CensusServiceError,
    DistrictRow,
    DistrictSnapshotError,
    NormalizedAddress,
    ReviewedFallbackError,
    aggregate_districts,
    enrich_companies,
    load_reviewed_fallback_csv,
    validate_snapshot_metadata,
    write_address_conversions_csv,
    write_district_aggregates_csv,
    write_districts_csv,
    write_review_csv,
)
from aisc_gr_statistics.relationship_summary import relationship_summary
from aisc_gr_statistics.report import CombinedCompany, Company, CompanyClassification
from aisc_gr_statistics.salesforce_fields import (
    CertificationAccountField,
    CertificationField,
    CertificationRelationship,
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


def _imis_csv_rows(tmp_path, rows):
    path = tmp_path / "imis.csv"
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(
            handle,
            fieldnames=(
                "company name",
                "state",
                "city",
                "iMIS ID",
                "address",
                "postal code",
                "Submission Date",
            ),
        )
        writer.writeheader()
        writer.writerows(rows)
    return path


def _match(district_layers=None):
    if district_layers is None:
        district_layers = {
            "Congressional Districts": [{"BASENAME": "7", "GEOID": "1707"}]
        }
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
                        **district_layers,
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


def _fallback_csv(tmp_path, *, imis_id="IMIS-1", reviewed_date="2026-09-30"):
    path = tmp_path / "fallback.csv"
    path.write_text(
        "company_name,company_classification,imis_id,salesforce_account_id,state,state_fips,county,county_fips,congressional_district,congressional_district_geoid,map_reference_kind,map_reference_key,reviewer_source_note,reviewed_date\n"
        f"Example Steel,imis-only,{imis_id},,IL,17,Cook County,031,7,1707,county,031,Reviewed against official district office,{reviewed_date}\n",
        encoding="utf-8",
    )
    return path


@pytest.mark.parametrize("payload", [{"result": {"addressMatches": []}}, CensusServiceError("offline")])
def test_reviewed_fallback_confirms_each_unassigned_census_outcome(tmp_path, payload):
    districts, reviews, _, failed = enrich_companies(
        _imis_csv(tmp_path), geocoder=Geocoder(payload),
        fallback_csv=_fallback_csv(tmp_path), lookup_date=date(2026, 9, 30),
    )
    assert not reviews
    assert districts[0].assignment_source == "fallback-confirmed"
    assert districts[0].map_reference_kind == "county"
    assert failed is isinstance(payload, CensusServiceError)


def test_reviewed_fallback_rejects_duplicate_and_unsafe_map_reference(tmp_path):
    path = _fallback_csv(tmp_path)
    path.write_text(path.read_text(encoding="utf-8") + path.read_text(encoding="utf-8").split("\n", 1)[1], encoding="utf-8")
    with pytest.raises(ValueError, match="duplicate"):
        load_reviewed_fallback_csv(path)
    path = _fallback_csv(tmp_path)
    path.write_text(path.read_text(encoding="utf-8").replace("county,031", "coordinates,-87.62"), encoding="utf-8")
    with pytest.raises(ValueError, match="unsafe"):
        load_reviewed_fallback_csv(path)


@pytest.mark.parametrize(
    ("replacement", "expected"),
    [
        ("CA,17,Cook County,031,7,1707", "state IL and state FIPS 17"),
        ("IL,18,Cook County,031,7,1807", "state IL and state FIPS 17"),
    ],
)
def test_reviewed_fallback_requires_illinois_state_and_fips(
    tmp_path, replacement, expected
):
    path = _fallback_csv(tmp_path)
    path.write_text(
        path.read_text(encoding="utf-8").replace(
            "IL,17,Cook County,031,7,1707", replacement
        ),
        encoding="utf-8",
    )

    with pytest.raises(ReviewedFallbackError, match=expected):
        load_reviewed_fallback_csv(path)


def _relationship_company(
    *, client_type="Fabricator", status="Certified", active=True, membership=""
):
    certification = {
        CertificationField.NAME: "PRIVATE CHILD CERTIFICATION",
        CertificationField.STATUS: "Active" if active else "Inactive",
        CertificationField.START_DATE: "2026-01-01",
        CertificationField.END_DATE: "2026-12-31",
    }
    return CombinedCompany(
        CompanyClassification.BOTH,
        Company("Example Steel", "IL", membership_type=membership),
        {
            CertificationAccountField.CERTIFICATION_STATUS: status,
            CertificationAccountField.CLIENT_TYPE: client_type,
            CertificationRelationship.ACCOUNT_CHILD: {"records": [certification]},
        },
    )


@pytest.mark.parametrize(
    ("client_type", "expected"),
    [
        ("Fabricator", "AISC Certified Fabricator"),
        ("Erector", "AISC Certified Erector"),
        ("Fabricator/Erector", "AISC Certified Fabricator/Erector"),
    ],
)
def test_relationship_summary_requires_validated_certification(client_type, expected):
    assert (
        relationship_summary(
            _relationship_company(client_type=client_type), date(2026, 9, 30)
        )
        == expected
    )


def test_relationship_summary_falls_back_without_safe_certification_wording():
    assert (
        relationship_summary(
            _relationship_company(active=False, membership="Full AISC Member Erector"),
            date(2026, 9, 30),
        )
        == "Full AISC Member Erector"
    )
    assert (
        relationship_summary(
            _relationship_company(client_type="Unmapped code", membership=""),
            date(2026, 9, 30),
        )
        == "AISC relationship unavailable"
    )


def test_enrichment_selects_latest_submission_once_per_imis_id(tmp_path):
    imis_csv = _imis_csv_rows(
        tmp_path,
        [
            {
                "company name": "Earlier Steel",
                "state": "IL",
                "city": "Chicago",
                "iMIS ID": "IMIS-1",
                "address": "100 Earlier Road",
                "postal code": "60601",
                "Submission Date": "2025-01-01",
            },
            {
                "company name": "Latest Steel",
                "state": "IL",
                "city": "Chicago",
                "iMIS ID": " IMIS-1 ",
                "address": "200 Latest Road",
                "postal code": "60602",
                "Submission Date": "2025-02-01",
            },
        ],
    )
    geocoder = Geocoder(_match())

    districts, reviews, conversions, failed = enrich_companies(
        imis_csv, geocoder=geocoder
    )

    assert not reviews and not failed
    assert len(districts) == len(conversions) == len(geocoder.addresses) == 1
    assert conversions[0].company_name == "Latest Steel"
    assert conversions[0].original_street == "200 Latest Road"


def test_enrichment_keeps_first_tied_or_unrankable_row_and_separate_blank_ids(tmp_path):
    imis_csv = _imis_csv_rows(
        tmp_path,
        [
            {
                "company name": "First Tied Steel",
                "state": "IL",
                "city": "Chicago",
                "iMIS ID": "TIED",
                "address": "100 First Road",
                "postal code": "60601",
                "Submission Date": "2025-01-01",
            },
            {
                "company name": "Second Tied Steel",
                "state": "IL",
                "city": "Chicago",
                "iMIS ID": "TIED",
                "address": "200 Second Road",
                "postal code": "60601",
                "Submission Date": "2025-01-01",
            },
            {
                "company name": "First Unrankable Steel",
                "state": "IL",
                "city": "Chicago",
                "iMIS ID": "UNRANKABLE",
                "address": "300 First Road",
                "postal code": "60601",
                "Submission Date": "not a date",
            },
            {
                "company name": "Second Unrankable Steel",
                "state": "IL",
                "city": "Chicago",
                "iMIS ID": "UNRANKABLE",
                "address": "400 Second Road",
                "postal code": "60601",
                "Submission Date": "",
            },
            {
                "company name": "Blank ID One",
                "state": "IL",
                "city": "Chicago",
                "iMIS ID": "",
                "address": "500 Blank Road",
                "postal code": "60601",
                "Submission Date": "2025-03-01",
            },
            {
                "company name": "Blank ID Two",
                "state": "IL",
                "city": "Chicago",
                "iMIS ID": " ",
                "address": "600 Blank Road",
                "postal code": "60601",
                "Submission Date": "2025-04-01",
            },
        ],
    )
    geocoder = Geocoder(_match())

    _, reviews, conversions, failed = enrich_companies(imis_csv, geocoder=geocoder)

    assert not reviews and not failed
    assert len(conversions) == len(geocoder.addresses) == 4
    assert [row.company_name for row in conversions] == [
        "First Tied Steel",
        "First Unrankable Steel",
        "Blank ID One",
        "Blank ID Two",
    ]
    assert [row.original_street for row in conversions] == [
        "100 First Road",
        "300 First Road",
        "500 Blank Road",
        "600 Blank Road",
    ]


def test_street_suffixes_follow_usps_center_and_crescent_abbreviations():
    assert normalize_street("100 Cent") == "100 CTR"
    assert normalize_street("100 Crescent") == "100 CRES"


def test_census_geocoder_sends_normalized_parsed_address_fields():
    class Response:
        ok = True

        def json(self):
            return {"result": {"addressMatches": []}}

    class Session:
        def get(self, url, params, timeout):
            self.url = url
            self.params = params
            self.timeout = timeout
            return Response()

    session = Session()
    CensusGeocoder(session).lookup(
        NormalizedAddress("100 MAIN AVE STE 4", "CHICAGO", "IL", "60601", "ready", "")
    )

    assert session.url.endswith("/geographies/address")
    assert session.params["street"] == "100 MAIN AVE STE 4"
    assert session.params["city"] == "CHICAGO"
    assert session.params["state"] == "IL"
    assert session.params["zip"] == "60601"
    assert "address" not in session.params


def test_salesforce_complete_address_takes_precedence_and_writes_metadata(tmp_path):
    account = {
        CertificationAccountField.ID: "001",
        CertificationAccountField.IMIS_ID: "IMIS-1",
        CertificationAccountField.NAME: "Salesforce Steel",
        CertificationAccountField.BILLING_STREET: "100 Billing Ave",
        CertificationAccountField.BILLING_CITY: "Chicago",
        CertificationAccountField.BILLING_STATE: "IL",
        CertificationAccountField.BILLING_POSTAL_CODE: "60601",
        CertificationAccountField.CERTIFICATION_STATUS: CertificationStatus.CERTIFIED,
        CertificationAccountField.CLIENT_TYPE: "Fabricator",
        CertificationRelationship.ACCOUNT_CHILD: {
            "records": [
                {
                    CertificationField.NAME: "INTERNAL CERTIFICATION NAME",
                    CertificationField.STATUS: "Active",
                    CertificationField.START_DATE: "2026-01-01",
                    CertificationField.END_DATE: "2026-12-31",
                }
            ]
        },
    }
    geocoder = Geocoder(_match())

    districts, reviews, conversions, failed = enrich_companies(
        _imis_csv(tmp_path), [account], geocoder, date(2026, 9, 28)
    )

    assert not reviews and not failed
    assert conversions[0].address_source == "Salesforce Billing Address"
    assert conversions[0].original_street == "100 Billing Ave"
    assert districts[0].county_fips == "031"
    assert districts[0].congressional_district_geoid == "1707"
    assert districts[0].census_benchmark == "Public_AR_Current"
    assert districts[0].confidence == "census-single-match"
    assert districts[0].relationship_summary == "AISC Certified Fabricator"


def test_district_snapshot_records_the_certification_effective_date(tmp_path):
    path = tmp_path / "districts.csv"
    write_districts_csv([], path, as_of=date(2026, 9, 30))

    assert validate_snapshot_metadata(path, "districts")["as_of"] == "2026-09-30"


def test_incomplete_salesforce_address_falls_back_to_complete_imis_address(tmp_path):
    account = {
        CertificationAccountField.IMIS_ID: "IMIS-1",
        CertificationAccountField.BILLING_STREET: "Billing Ave",
        CertificationAccountField.BILLING_CITY: "Chicago",
        CertificationAccountField.BILLING_STATE: "IL",
        CertificationAccountField.BILLING_POSTAL_CODE: "60601",
    }
    geocoder = Geocoder(_match())

    districts, reviews, conversions, _ = enrich_companies(
        _imis_csv(tmp_path), [account], geocoder
    )

    assert districts and not reviews
    assert conversions[0].address_source == "iMIS address"


@pytest.mark.parametrize(
    ("address", "expected_zip"),
    [
        ("14100 S. Western Ave Posen, IL 60469", "60469"),
        ("14100 S. Western Ave Posen, IL 60469-1234", "60469"),
    ],
)
def test_enrichment_uses_only_trailing_imis_address_zip_when_postal_code_is_blank(
    tmp_path, address, expected_zip
):
    imis_csv = _imis_csv_rows(
        tmp_path,
        [
            {
                "company name": "Example Steel",
                "state": "IL",
                "city": "Posen",
                "iMIS ID": "IMIS-1",
                "address": address,
                "postal code": "",
                "Submission Date": "2025-01-01",
            }
        ],
    )
    geocoder = Geocoder(_match())

    districts, reviews, conversions, failed = enrich_companies(
        imis_csv, geocoder=geocoder
    )

    assert districts and not reviews and not failed
    assert geocoder.addresses[0] == NormalizedAddress(
        "14100 S WESTERN AVE", "POSEN", "IL", expected_zip, "ready", ""
    )
    assert conversions[0].original_street == "14100 S. Western Ave"
    assert conversions[0].original_postal_code == (
        "60469-1234" if "-" in address else "60469"
    )


def test_enrichment_uses_trailing_zip_from_multiline_imis_address_when_postal_code_is_blank(
    tmp_path,
):
    imis_csv = _imis_csv_rows(
        tmp_path,
        [
            {
                "company name": "Example Steel",
                "state": "IL",
                "city": "Posen",
                "iMIS ID": "IMIS-1",
                "address": "14100 S. Western Ave\nPosen, IL\n60469",
                "postal code": "",
                "Submission Date": "2025-01-01",
            }
        ],
    )
    geocoder = Geocoder(_match())

    districts, reviews, conversions, failed = enrich_companies(
        imis_csv, geocoder=geocoder
    )

    assert districts and not reviews and not failed
    assert geocoder.addresses[0] == NormalizedAddress(
        "14100 S WESTERN AVE", "POSEN", "IL", "60469", "ready", ""
    )
    assert conversions[0].original_street == "14100 S. Western Ave"
    assert conversions[0].original_city == "Posen"
    assert conversions[0].original_state == "IL"
    assert conversions[0].original_postal_code == "60469"
    assert conversions[0].normalized_street == "14100 S WESTERN AVE"
    assert conversions[0].normalized_city == "POSEN"
    assert conversions[0].normalized_state == "IL"
    assert conversions[0].normalized_postal_code == "60469"


@pytest.mark.parametrize("country", ["UNITED STATES", "US", "U.S.", "U.S.A.", "u.s.a."])
def test_enrichment_uses_zip_before_final_us_country_line_when_postal_code_is_blank(
    tmp_path, country
):
    imis_csv = _imis_csv_rows(
        tmp_path,
        [
            {
                "company name": "Michelmann Steel",
                "state": "IL",
                "city": "Quincy",
                "iMIS ID": "IMIS-1",
                "address": (
                    "137 N. SECOND ST.\nP.O. BOX 609\nQUINCY, IL\n"
                    f"62306-1234\n{country}"
                ),
                "postal code": "",
                "Submission Date": "2025-01-01",
            }
        ],
    )
    geocoder = Geocoder(_match())

    districts, reviews, conversions, failed = enrich_companies(
        imis_csv, geocoder=geocoder
    )

    assert districts and not reviews and not failed
    assert geocoder.addresses[0] == NormalizedAddress(
        "137 N SECOND ST P O BOX 609", "QUINCY", "IL", "62306", "ready", ""
    )
    assert conversions[0].original_street == "137 N. SECOND ST.\nP.O. BOX 609"
    assert conversions[0].original_postal_code == "62306-1234"


def test_enrichment_does_not_use_zip_with_unrelated_trailing_text(tmp_path):
    imis_csv = _imis_csv_rows(
        tmp_path,
        [
            {
                "company name": "Example Steel",
                "state": "IL",
                "city": "Quincy",
                "iMIS ID": "IMIS-1",
                "address": "137 N. Second St. Quincy, IL 62306 CANADA",
                "postal code": "",
                "Submission Date": "2025-01-01",
            }
        ],
    )
    geocoder = Geocoder(_match())

    districts, reviews, _, failed = enrich_companies(imis_csv, geocoder=geocoder)

    assert not districts and len(reviews) == 1 and not failed
    assert reviews[0].review_reason == "incomplete address"
    assert not geocoder.addresses


def test_enrichment_keeps_populated_imis_postal_code_when_address_has_trailing_zip(
    tmp_path,
):
    imis_csv = _imis_csv_rows(
        tmp_path,
        [
            {
                "company name": "Example Steel",
                "state": "IL",
                "city": "Posen",
                "iMIS ID": "IMIS-1",
                "address": "14100 S. Western Ave Posen, IL 60469",
                "postal code": "60601",
                "Submission Date": "2025-01-01",
            }
        ],
    )
    geocoder = Geocoder(_match())

    enrich_companies(imis_csv, geocoder=geocoder)

    assert geocoder.addresses[0].postal_code == "60601"


def test_session_qualified_congressional_district_layer_is_accepted(tmp_path):
    payload = _match(
        {"120th Congressional Districts": [{"BASENAME": "7", "GEOID": "1707"}]}
    )

    districts, reviews, _, failed = enrich_companies(
        _imis_csv(tmp_path), geocoder=Geocoder(payload)
    )

    assert not reviews and not failed
    assert districts[0].congressional_district == "7"
    assert districts[0].congressional_district_geoid == "1707"


def test_generic_congressional_district_layer_remains_supported(tmp_path):
    districts, reviews, _, failed = enrich_companies(
        _imis_csv(tmp_path), geocoder=Geocoder(_match())
    )

    assert not reviews and not failed
    assert districts[0].congressional_district_geoid == "1707"


def test_newest_numbered_congressional_district_layer_wins(tmp_path):
    payload = _match(
        {
            "119th Congressional Districts": [{"BASENAME": "6", "GEOID": "1706"}],
            "120th Congressional Districts": [{"BASENAME": "7", "GEOID": "1707"}],
        }
    )

    districts, reviews, _, _ = enrich_companies(
        _imis_csv(tmp_path), geocoder=Geocoder(payload)
    )

    assert not reviews
    assert districts[0].congressional_district_geoid == "1707"


def test_malformed_newest_district_layer_does_not_fall_back(tmp_path):
    payload = _match(
        {
            "Congressional Districts": [{"BASENAME": "5", "GEOID": "1705"}],
            "119th Congressional Districts": [{"BASENAME": "6", "GEOID": "1706"}],
            "120th Congressional Districts": [{"BASENAME": "7"}],
        }
    )

    districts, reviews, _, _ = enrich_companies(
        _imis_csv(tmp_path), geocoder=Geocoder(payload)
    )

    assert not districts
    assert reviews[0].review_reason == "incomplete Census geography"


@pytest.mark.parametrize(
    "district_layers",
    [
        {},
        {"120th State Legislative Districts": [{"BASENAME": "7", "GEOID": "1707"}]},
        {"Current 120th Congressional Districts": [{"BASENAME": "7", "GEOID": "1707"}]},
    ],
)
def test_missing_or_unrelated_district_layers_are_reviewed(tmp_path, district_layers):
    districts, reviews, _, _ = enrich_companies(
        _imis_csv(tmp_path), geocoder=Geocoder(_match(district_layers))
    )

    assert not districts
    assert reviews[0].review_reason == "incomplete Census geography"


def test_successful_session_layer_is_written_only_to_districts_csv(tmp_path):
    payload = _match(
        {"120th Congressional Districts": [{"BASENAME": "7", "GEOID": "1707"}]}
    )
    districts, reviews, _, _ = enrich_companies(
        _imis_csv(tmp_path), geocoder=Geocoder(payload)
    )
    districts_path = tmp_path / "districts.csv"
    review_path = tmp_path / "review.csv"

    write_districts_csv(districts, districts_path)
    write_review_csv(reviews, review_path)

    with districts_path.open(newline="", encoding="utf-8") as handle:
        assert [row["company_name"] for row in csv.DictReader(handle)] == [
            "Example Steel"
        ]
    with review_path.open(newline="", encoding="utf-8") as handle:
        assert list(csv.DictReader(handle)) == []


def test_review_rows_cover_incomplete_no_match_multiple_and_missing_geography(tmp_path):
    _, reviews, _, _ = enrich_companies(
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
        _, reviews, _, failed = enrich_companies(
            _imis_csv(tmp_path), geocoder=Geocoder(payload)
        )
        assert reviews[0].review_reason == reason
        assert not failed


def test_malformed_payload_and_service_outage_are_reviewed(tmp_path):
    _, reviews, _, failed = enrich_companies(
        _imis_csv(tmp_path), geocoder=Geocoder({"wrong": "shape"})
    )
    assert reviews[0].review_reason == "malformed Census response"
    assert not failed

    _, reviews, _, failed = enrich_companies(
        _imis_csv(tmp_path), geocoder=Geocoder(CensusServiceError("down"))
    )
    assert reviews[0].review_reason == "Census service error"
    assert failed


def test_csv_outputs_include_required_columns(tmp_path):
    districts, reviews, conversions, _ = enrich_companies(
        _imis_csv(tmp_path), geocoder=Geocoder(_match())
    )
    districts_path = tmp_path / "districts.csv"
    review_path = tmp_path / "review.csv"
    write_districts_csv(districts, districts_path)
    write_review_csv(reviews, review_path)
    conversions_path = tmp_path / "conversions.csv"
    write_address_conversions_csv(conversions, conversions_path)

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
    assert {"original_street", "normalized_street", "normalization_status"} <= set(
        next(csv.reader(conversions_path.open()))
    )


def test_normalized_lookup_uses_parsed_uppercase_fields_and_keeps_source_values(
    tmp_path,
):
    original = "100 Main Avenue; Suite #4"
    geocoder = Geocoder(_match())

    districts, reviews, conversions, failed = enrich_companies(
        _imis_csv(tmp_path, original, "60601-1234"), geocoder=geocoder
    )

    assert districts and not reviews and not failed
    lookup = geocoder.addresses[0]
    assert (lookup.street, lookup.city, lookup.state, lookup.postal_code) == (
        "100 MAIN AVE STE 4",
        "CHICAGO",
        "IL",
        "60601",
    )
    assert districts[0].source_address == f"{original}, Chicago, IL, 60601-1234"
    assert conversions[0].original_street == original
    assert conversions[0].normalized_street == "100 MAIN AVE STE 4"
    assert conversions[0].normalization_status == "ready"


def test_suffix_is_only_abbreviated_at_suffix_position_and_incomplete_is_a_conversion(
    tmp_path,
):
    geocoder = Geocoder(_match())
    _, reviews, conversions, _ = enrich_companies(
        _imis_csv(tmp_path, "100 Street Name Road", ""), geocoder=geocoder
    )

    assert not geocoder.addresses
    assert conversions[0].normalized_street == "100 STREET NAME RD"
    assert conversions[0].normalization_status == "incomplete"
    assert conversions[0].normalization_reason == "incomplete or unusable address"
    assert reviews[0].source_address == "100 Street Name Road, Chicago, IL"


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
        "",
        "both",
        imis_id,
        account_id,
        "",
        "",
        "",
        "IL",
        "IL",
        "17",
        "",
        "",
        geoid[-1],
        geoid,
        "",
        "",
        "",
        "",
        "",
        "matched",
        "",
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

    national, state, district = rows
    assert (national.included_company_count, national.known_jobs) == (2, 10)
    assert (
        national.companies_with_employee_data,
        national.companies_missing_employee_data,
    ) == (2, 0)
    assert state.included_company_count == 2
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
        assert rows[0].companies_missing_employee_data == 1
        assert rows[0].known_jobs == 0


def test_aggregate_districts_matches_salesforce_only_snapshot_by_salesforce_imis_id(
    tmp_path,
):
    snapshot = tmp_path / "districts.csv"
    write_districts_csv(
        [
            DistrictRow(
                "",
                "salesforce-only",
                "SALESFORCE-ONLY",
                "003",
                "",
                "",
                "",
                "IL",
                "17",
                "17",
                "",
                "",
                "7",
                "1707",
                "",
                "",
                "",
                "",
                "",
                "matched",
                "",
            )
        ],
        snapshot,
    )

    rows = aggregate_districts(
        _aggregate_imis_csv(tmp_path),
        snapshot,
        [_account("SALESFORCE-ONLY", "003", 10)],
    )

    assert rows[1].included_company_count == 1


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
        "scope",
        "state",
        "state_fips",
        "congressional_district",
        "congressional_district_geoid",
        "included_company_count",
        "known_jobs",
        "companies_with_employee_data",
        "companies_missing_employee_data",
    ]
    assert [row["scope"] for row in written] == ["national", "state", "district"]

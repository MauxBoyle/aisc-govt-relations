"""Safely enrich report companies with public Census congressional districts."""

import csv
import re
from collections.abc import Mapping
from dataclasses import dataclass, fields
from datetime import UTC, date, datetime
from pathlib import Path

import requests

from .report import (
    CombinedCompany,
    CompanyClassification,
    combine_companies,
    read_imis_companies,
)
from .salesforce_fields import CertificationAccountField, CertificationStatus

CENSUS_GEOGRAPHIES_URL = (
    "https://geocoding.geo.census.gov/geocoder/geographies/onelineaddress"
)
CENSUS_BENCHMARK = "Public_AR_Current"
CENSUS_VINTAGE = "Current_Current"


class CensusServiceError(RuntimeError):
    """The Census service could not be reached or returned an HTTP error."""


@dataclass(frozen=True)
class Address:
    source: str
    street: str
    city: str
    state: str
    postal_code: str

    @property
    def source_address(self) -> str:
        return ", ".join(
            part
            for part in (self.street, self.city, self.state, self.postal_code)
            if part
        )

    @property
    def complete(self) -> bool:
        return bool(
            self.street
            and _has_street_number(self.street)
            and self.city
            and self.state
            and _zip5(self.postal_code)
        )


@dataclass(frozen=True)
class DistrictRow:
    company_name: str
    company_classification: str
    imis_id: str
    salesforce_account_id: str
    address_source: str
    source_address: str
    standardized_address: str
    city: str
    state: str
    state_fips: str
    county: str
    county_fips: str
    congressional_district: str
    congressional_district_geoid: str
    latitude: str
    longitude: str
    census_benchmark: str
    census_vintage: str
    lookup_date: str
    status: str
    confidence: str


@dataclass(frozen=True)
class ReviewRow:
    company_name: str
    company_classification: str
    imis_id: str
    salesforce_account_id: str
    address_source: str
    source_address: str
    city: str
    state: str
    postal_code: str
    review_reason: str
    candidate_information: str


class CensusGeocoder:
    """Small client for Census's individual geographic-lookup endpoint."""

    def __init__(self, session=requests):
        self.session = session

    def lookup(self, address: Address) -> Mapping[str, object]:
        try:
            response = self.session.get(
                CENSUS_GEOGRAPHIES_URL,
                params={
                    "address": address.source_address,
                    "benchmark": CENSUS_BENCHMARK,
                    "vintage": CENSUS_VINTAGE,
                    "format": "json",
                },
                timeout=30,
            )
        except requests.RequestException as error:
            raise CensusServiceError(f"Could not reach Census: {error}") from error
        if not response.ok:
            raise CensusServiceError(f"Census returned HTTP {response.status_code}.")
        try:
            payload = response.json()
        except ValueError as error:
            raise ValueError("invalid Census JSON response") from error
        if not isinstance(payload, dict):
            raise ValueError("invalid Census JSON response")
        return payload


def enrich_companies(
    imis_csv: Path | str,
    salesforce_accounts=(),
    geocoder: CensusGeocoder | None = None,
    lookup_date: date | None = None,
) -> tuple[list[DistrictRow], list[ReviewRow], bool]:
    """Return successful district rows, review rows, and a service-failure flag.

    A result is deliberately accepted only for exactly one Census candidate
    containing both County and Congressional District geography.
    """
    geocoder = geocoder or CensusGeocoder()
    lookup_date = lookup_date or datetime.now(UTC).date()
    combined = combine_companies(read_imis_companies(imis_csv), salesforce_accounts)
    districts: list[DistrictRow] = []
    reviews: list[ReviewRow] = []
    service_failed = False
    for company in _report_population(combined):
        address = _preferred_address(company)
        identity = _identity(company)
        if not address.complete:
            reviews.append(_review(identity, address, "incomplete address"))
            continue
        try:
            payload = geocoder.lookup(address)
            row, reason, candidates = _district_from_payload(
                identity, address, payload, lookup_date
            )
        except CensusServiceError as error:
            service_failed = True
            reviews.append(
                _review(identity, address, "Census service error", str(error))
            )
            continue
        except ValueError as error:
            reviews.append(
                _review(identity, address, "malformed Census response", str(error))
            )
            continue
        if row:
            districts.append(row)
        else:
            reviews.append(_review(identity, address, reason, candidates))
    return districts, reviews, service_failed


def write_districts_csv(rows: list[DistrictRow], path: Path | str) -> None:
    _write_csv(rows, path, DistrictRow)


def write_review_csv(rows: list[ReviewRow], path: Path | str) -> None:
    _write_csv(rows, path, ReviewRow)


def _write_csv(rows, path, row_type) -> None:
    destination = Path(path)
    destination.parent.mkdir(parents=True, exist_ok=True)
    names = [field.name for field in fields(row_type)]
    with destination.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=names)
        writer.writeheader()
        writer.writerows({name: getattr(row, name) for name in names} for row in rows)


def _report_population(companies: list[CombinedCompany]) -> list[CombinedCompany]:
    return [
        company
        for company in companies
        if company.classification is not CompanyClassification.SALESFORCE_ONLY
        or _account_value(
            company.salesforce, CertificationAccountField.CERTIFICATION_STATUS
        )
        == CertificationStatus.CERTIFIED
    ]


def _preferred_address(company: CombinedCompany) -> Address:
    account = company.salesforce
    salesforce = Address(
        "Salesforce Billing Address",
        _account_value(account, CertificationAccountField.BILLING_STREET),
        _account_value(account, CertificationAccountField.BILLING_CITY),
        _account_value(account, CertificationAccountField.BILLING_STATE),
        _account_value(account, CertificationAccountField.BILLING_POSTAL_CODE),
    )
    if salesforce.complete:
        return salesforce
    imis = company.imis
    street = imis.address if imis else ""
    postal_code = imis.postal_code if imis else ""
    if not postal_code:
        postal_code = _zip_from_text(street)
        street = _remove_zip(street)
    return Address(
        "iMIS address",
        street,
        imis.city if imis else "",
        imis.state if imis else "",
        postal_code,
    )


def _identity(company: CombinedCompany) -> tuple[str, str, str, str]:
    imis = company.imis
    account = company.salesforce
    return (
        _account_value(account, CertificationAccountField.NAME)
        or (imis.name if imis else ""),
        company.classification.value,
        imis.imis_id
        if imis
        else _account_value(account, CertificationAccountField.IMIS_ID),
        _account_value(account, CertificationAccountField.ID),
    )


def _district_from_payload(identity, address, payload, lookup_date):
    result = payload.get("result")
    if not isinstance(result, dict) or not isinstance(
        result.get("addressMatches"), list
    ):
        raise ValueError("response did not contain result.addressMatches")
    matches = result["addressMatches"]
    if not matches:
        return None, "no Census address match", ""
    if len(matches) != 1:
        return None, "multiple Census address matches", _candidate_summary(matches)
    match = matches[0]
    if not isinstance(match, dict):
        raise ValueError("address match was not an object")
    geographies = match.get("geographies")
    coordinates = match.get("coordinates")
    if not isinstance(geographies, dict) or not isinstance(coordinates, dict):
        return None, "incomplete Census geography", _candidate_summary(matches)
    county = _one_geography(geographies, "Counties")
    district = _one_geography(geographies, "Congressional Districts")
    if county is None or district is None:
        return None, "incomplete Census geography", _candidate_summary(matches)
    state_fips = _text(county.get("STATE"))
    county_fips = _text(county.get("COUNTY"))
    district_geoid = _text(district.get("GEOID"))
    if not state_fips or not county_fips or not district_geoid:
        return None, "incomplete Census geography", _candidate_summary(matches)
    name, classification, imis_id, account_id = identity
    return (
        DistrictRow(
            name,
            classification,
            imis_id,
            account_id,
            address.source,
            address.source_address,
            _text(match.get("matchedAddress")),
            address.city,
            address.state,
            state_fips,
            _text(county.get("NAME")),
            county_fips,
            _text(district.get("BASENAME")),
            district_geoid,
            _text(coordinates.get("y")),
            _text(coordinates.get("x")),
            CENSUS_BENCHMARK,
            CENSUS_VINTAGE,
            lookup_date.isoformat(),
            "matched",
            "census-single-match",
        ),
        "",
        "",
    )


def _one_geography(geographies, name):
    items = geographies.get(name)
    return (
        items[0]
        if isinstance(items, list) and len(items) == 1 and isinstance(items[0], dict)
        else None
    )


def _review(identity, address, reason, candidates="") -> ReviewRow:
    name, classification, imis_id, account_id = identity
    return ReviewRow(
        name,
        classification,
        imis_id,
        account_id,
        address.source,
        address.source_address,
        address.city,
        address.state,
        address.postal_code,
        reason,
        candidates,
    )


def _candidate_summary(matches) -> str:
    if not isinstance(matches, list):
        return ""
    return " | ".join(
        _text(item.get("matchedAddress")) for item in matches if isinstance(item, dict)
    )


def _account_value(account, field) -> str:
    return (
        account.get(field, "").strip()
        if account and isinstance(account.get(field, ""), str)
        else ""
    )


def _text(value) -> str:
    return str(value).strip() if value is not None else ""


def _has_street_number(street: str) -> bool:
    return bool(re.search(r"(?:^|\n|\s)\d+\b", street))


def _zip5(value: str) -> str:
    match = re.search(r"\b\d{5}(?:-\d{4})?\b", value)
    return match.group(0) if match else ""


def _zip_from_text(value: str) -> str:
    return _zip5(value)


def _remove_zip(value: str) -> str:
    return re.sub(r"[ ,]*\b\d{5}(?:-\d{4})?\b", "", value).strip(" ,")

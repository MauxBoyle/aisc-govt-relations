"""Safely enrich report companies with public Census congressional districts."""

import csv
import re
from collections.abc import Mapping
from dataclasses import dataclass, fields
from datetime import UTC, date, datetime
from pathlib import Path

import requests

from .address_normalization import normalize_street, normalize_text, normalize_zip
from .report import (
    CombinedCompany,
    CompanyClassification,
    combine_companies,
    employee_count_decimal,
    read_imis_companies,
)
from .salesforce_fields import (
    CertificationAccountField,
    CertificationStatus,
)

CENSUS_GEOGRAPHIES_URL = (
    "https://geocoding.geo.census.gov/geocoder/geographies/address"
)
CENSUS_BENCHMARK = "Public_AR_Current"
CENSUS_VINTAGE = "Current_Current"


class CensusServiceError(RuntimeError):
    """The Census service could not be reached or returned an HTTP error."""


class DistrictSnapshotError(ValueError):
    """A saved district snapshot cannot safely be used for aggregation."""


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
class NormalizedAddress:
    """A derived address used only for a parsed Census lookup."""

    street: str
    city: str
    state: str
    postal_code: str
    normalization_status: str
    normalization_reason: str

    @property
    def complete(self) -> bool:
        return bool(
            self.street
            and _has_street_number(self.street)
            and self.city
            and self.state
            and self.postal_code
        )


@dataclass(frozen=True)
class AddressConversionRow:
    """Auditable record of the source address and its Census lookup form."""

    company_name: str
    company_classification: str
    imis_id: str
    salesforce_account_id: str
    address_source: str
    original_street: str
    original_city: str
    original_state: str
    original_postal_code: str
    normalized_street: str
    normalized_city: str
    normalized_state: str
    normalized_postal_code: str
    normalization_status: str
    normalization_reason: str


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


@dataclass(frozen=True)
class DistrictAggregateRow:
    """A national or safely assigned congressional-district job aggregate."""

    scope: str
    state: str
    state_fips: str
    congressional_district: str
    congressional_district_geoid: str
    included_company_count: int
    known_jobs: int
    companies_with_employee_data: int
    companies_missing_employee_data: int


class CensusGeocoder:
    """Small client for Census's individual geographic-lookup endpoint."""

    def __init__(self, session=requests):
        self.session = session

    def lookup(self, address: NormalizedAddress) -> Mapping[str, object]:
        try:
            response = self.session.get(
                CENSUS_GEOGRAPHIES_URL,
                params={
                    "street": address.street,
                    "city": address.city,
                    "state": address.state,
                    "zip": address.postal_code,
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
) -> tuple[list[DistrictRow], list[ReviewRow], list[AddressConversionRow], bool]:
    """Return district, review, conversion rows, and a service-failure flag.

    A result is deliberately accepted only for exactly one Census candidate
    containing both County and Congressional District geography.
    """
    geocoder = geocoder or CensusGeocoder()
    lookup_date = lookup_date or datetime.now(UTC).date()
    combined = combine_companies(read_imis_companies(imis_csv), salesforce_accounts)
    districts: list[DistrictRow] = []
    reviews: list[ReviewRow] = []
    conversions: list[AddressConversionRow] = []
    service_failed = False
    for company in _report_population(combined):
        address = _preferred_address(company)
        identity = _identity(company)
        normalized = _normalize_address(address)
        conversions.append(_conversion(identity, address, normalized))
        if not normalized.complete:
            reviews.append(_review(identity, address, "incomplete address"))
            continue
        try:
            payload = geocoder.lookup(normalized)
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
    return districts, reviews, conversions, service_failed


def write_districts_csv(rows: list[DistrictRow], path: Path | str) -> None:
    _write_csv(rows, path, DistrictRow)


def write_review_csv(rows: list[ReviewRow], path: Path | str) -> None:
    _write_csv(rows, path, ReviewRow)


def write_address_conversions_csv(
    rows: list[AddressConversionRow], path: Path | str
) -> None:
    """Write the selected source address and its derived Census lookup fields."""
    _write_csv(rows, path, AddressConversionRow)


def read_districts_csv(path: Path | str) -> list[DistrictRow]:
    """Read a prior ``company-districts.csv`` snapshot with required columns."""
    with Path(path).open(newline="", encoding="utf-8") as handle:
        reader = csv.DictReader(handle)
        names = [field.name for field in fields(DistrictRow)]
        if reader.fieldnames is None or set(names) - set(reader.fieldnames):
            raise DistrictSnapshotError("district snapshot is missing required columns")
        rows = [
            DistrictRow(
                **{name: (row.get(name) or "").strip() for name in names}
            )
            for row in reader
        ]
    return rows


def aggregate_districts(
    imis_csv: Path | str,
    districts_csv: Path | str,
    salesforce_accounts=(),
) -> list[DistrictAggregateRow]:
    """Aggregate the current report population using a saved district snapshot.

    Unassigned companies stay in the national result but deliberately do not
    appear in a district result.  This function performs no Census lookup.
    """
    population = _report_population(
        combine_companies(read_imis_companies(imis_csv), salesforce_accounts)
    )
    population_by_identity = _population_by_identity(population)
    snapshot_by_identity = _snapshot_by_identity(read_districts_csv(districts_csv))
    unknown = set(snapshot_by_identity) - set(population_by_identity)
    if unknown:
        raise DistrictSnapshotError(
            "district snapshot contains a company outside the current report population"
        )

    national = _aggregate_row("national", "", "", "", "", population)
    by_district: dict[tuple[str, str, str, str], list[CombinedCompany]] = {}
    for identity, snapshot in snapshot_by_identity.items():
        company = population_by_identity[identity]
        district_key = (
            snapshot.state,
            snapshot.state_fips,
            snapshot.congressional_district,
            snapshot.congressional_district_geoid,
        )
        if not all(district_key):
            raise DistrictSnapshotError("district snapshot contains incomplete district data")
        by_district.setdefault(district_key, []).append(company)
    districts = [
        _aggregate_row("district", *key, companies)
        for key, companies in sorted(by_district.items())
    ]
    return [national, *districts]


def write_district_aggregates_csv(
    rows: list[DistrictAggregateRow], path: Path | str
) -> None:
    """Write national and district aggregates with a stable, typed header."""
    _write_csv(rows, path, DistrictAggregateRow)


def _population_by_identity(
    companies: list[CombinedCompany],
) -> dict[tuple[str, str, str], CombinedCompany]:
    indexed: dict[tuple[str, str, str], CombinedCompany] = {}
    for company in companies:
        identity = _aggregate_identity(
            company.classification.value,
            company.imis.imis_id if company.imis else "",
            _account_value(company.salesforce, CertificationAccountField.ID),
        )
        if identity in indexed:
            raise DistrictSnapshotError("current report population has duplicate identities")
        indexed[identity] = company
    return indexed


def _snapshot_by_identity(
    rows: list[DistrictRow],
) -> dict[tuple[str, str, str], DistrictRow]:
    indexed: dict[tuple[str, str, str], DistrictRow] = {}
    for row in rows:
        identity = _aggregate_identity(
            row.company_classification, row.imis_id, row.salesforce_account_id
        )
        if identity in indexed:
            raise DistrictSnapshotError("district snapshot has duplicate identities")
        indexed[identity] = row
    return indexed


def _aggregate_identity(
    classification: str, imis_id: str, account_id: str
) -> tuple[str, str, str]:
    return (classification.strip(), imis_id.strip(), account_id.strip())


def _aggregate_row(
    scope: str,
    state: str,
    state_fips: str,
    district: str,
    geoid: str,
    companies: list[CombinedCompany],
) -> DistrictAggregateRow:
    counts = [
        employee_count_decimal(
            company.salesforce.get(CertificationAccountField.EMPLOYEE_COUNT)
            if company.salesforce
            else None
        )
        for company in companies
    ]
    known = [count for count in counts if count is not None]
    return DistrictAggregateRow(
        scope,
        state,
        state_fips,
        district,
        geoid,
        len(companies),
        int(sum(known)),
        len(known),
        len(companies) - len(known),
    )


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
    street = _remove_embedded_city_state(
        street, imis.city if imis else "", imis.state if imis else ""
    )
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


def _district_from_payload(identity, source_address, payload, lookup_date):
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
            source_address.source,
            source_address.source_address,
            _text(match.get("matchedAddress")),
            source_address.city,
            source_address.state,
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


def _normalize_address(address: Address) -> NormalizedAddress:
    """Format an address deterministically without modifying its source values."""
    street = normalize_street(address.street)
    city = normalize_text(address.city)
    state = normalize_text(address.state)
    postal_code = normalize_zip(address.postal_code)
    complete = bool(
        street and _has_street_number(street) and city and state and postal_code
    )
    return NormalizedAddress(
        street,
        city,
        state,
        postal_code,
        "ready" if complete else "incomplete",
        "" if complete else "incomplete or unusable address",
    )


def _conversion(identity, address, normalized) -> AddressConversionRow:
    name, classification, imis_id, account_id = identity
    return AddressConversionRow(
        name,
        classification,
        imis_id,
        account_id,
        address.source,
        address.street,
        address.city,
        address.state,
        address.postal_code,
        normalized.street,
        normalized.city,
        normalized.state,
        normalized.postal_code,
        normalized.normalization_status,
        normalized.normalization_reason,
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


def _remove_embedded_city_state(value: str, city: str, state: str) -> str:
    """Remove a repeated trailing ``city, state`` from an iMIS street field."""
    if not city or not state:
        return value.strip(" ,")
    pattern = rf"(?:\s+|,\s*){re.escape(city)}\s*,\s*{re.escape(state)}\s*$"
    return re.sub(pattern, "", value, flags=re.IGNORECASE).strip(" ,\r\n")

"""Safely enrich report companies with public Census congressional districts."""

import csv
import hashlib
import json
import re
from collections.abc import Mapping
from dataclasses import dataclass, fields
from datetime import UTC, date, datetime
from pathlib import Path

import requests

from .address_normalization import normalize_street, normalize_text, normalize_zip
from .census_map_references import (
    load_map_references,
    normalize_city_name,
)
from .relationship_summary import relationship_summary
from .report import (
    CombinedCompany,
    CompanyClassification,
    combine_companies,
    employee_count_decimal,
    filter_report_exclusions,
    load_report_exclusion_phrases,
    read_imis_companies,
    select_imis_companies_for_district_enrichment,
)
from .salesforce_fields import (
    CertificationAccountField,
    CertificationStatus,
)

CENSUS_GEOGRAPHIES_URL = "https://geocoding.geo.census.gov/geocoder/geographies/address"
CENSUS_BENCHMARK = "Public_AR_Current"
CENSUS_VINTAGE = "Current_Current"
DEFAULT_FALLBACK_CSV = (
    Path(__file__).resolve().parents[2] / "config" / "reviewed-geography-fallback.csv"
)


class CensusServiceError(RuntimeError):
    """The Census service could not be reached or returned an HTTP error."""


class DistrictSnapshotError(ValueError):
    """A saved district snapshot cannot safely be used for aggregation."""


class ReviewedFallbackError(ValueError):
    """The checked-in reviewed geography fallback is not safe to use."""


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
    relationship_summary: str = ""
    assignment_source: str = "census-confirmed"
    fallback_reviewer_source_note: str = ""
    fallback_reviewed_date: str = ""
    map_reference_kind: str = ""
    map_reference_key: str = ""


@dataclass(frozen=True)
class ReviewedFallbackRow:
    """A manually reviewed assignment used only after Census cannot assign one."""

    company_name: str
    company_classification: str
    imis_id: str
    salesforce_account_id: str
    state: str
    state_fips: str
    county: str
    county_fips: str
    congressional_district: str
    congressional_district_geoid: str
    map_reference_kind: str
    map_reference_key: str
    reviewer_source_note: str
    reviewed_date: str


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
    fallback_csv: Path | str = DEFAULT_FALLBACK_CSV,
) -> tuple[list[DistrictRow], list[ReviewRow], list[AddressConversionRow], bool]:
    """Return district, review, conversion rows, and a service-failure flag.

    A result is deliberately accepted only for exactly one Census candidate
    containing both County and Congressional District geography.
    """
    geocoder = geocoder or CensusGeocoder()
    lookup_date = lookup_date or datetime.now(UTC).date()
    fallbacks = load_reviewed_fallback_csv(fallback_csv)
    imis_companies = select_imis_companies_for_district_enrichment(
        read_imis_companies(imis_csv, preserve_source_order=True)
    )
    imis_companies, salesforce_accounts = filter_report_exclusions(
        imis_companies, salesforce_accounts, load_report_exclusion_phrases()
    )
    combined = combine_companies(imis_companies, salesforce_accounts)
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
            _assign_fallback_or_review(
                districts, reviews, fallbacks, identity, address, company,
                lookup_date, "incomplete address"
            )
            continue
        try:
            payload = geocoder.lookup(normalized)
            row, reason, candidates = _district_from_payload(
                identity,
                address,
                payload,
                lookup_date,
                relationship_summary(company, lookup_date),
            )
        except CensusServiceError as error:
            service_failed = True
            _assign_fallback_or_review(
                districts, reviews, fallbacks, identity, address, company,
                lookup_date, "Census service error", str(error)
            )
            continue
        except ValueError as error:
            _assign_fallback_or_review(
                districts, reviews, fallbacks, identity, address, company,
                lookup_date, "malformed Census response", str(error)
            )
            continue
        if row:
            districts.append(row)
        else:
            _assign_fallback_or_review(
                districts, reviews, fallbacks, identity, address, company,
                lookup_date, reason, candidates
            )
    return districts, reviews, conversions, service_failed


def load_reviewed_fallback_csv(path: Path | str = DEFAULT_FALLBACK_CSV) -> dict[tuple[str, str, str, str], ReviewedFallbackRow]:
    """Read explicit, reviewed assignments without permitting district guesses.

    Rows have a deliberately strict composite identity.  This makes a changed
    name, source classification, or identifier fail closed into the review CSV.
    """
    required = [field.name for field in fields(ReviewedFallbackRow)]
    source = Path(path)
    try:
        with source.open(newline="", encoding="utf-8") as handle:
            reader = csv.DictReader(handle)
            if reader.fieldnames is None or set(required) - set(reader.fieldnames):
                raise ReviewedFallbackError("reviewed fallback CSV is missing required columns")
            rows = [
                ReviewedFallbackRow(**{name: (row.get(name) or "").strip() for name in required})
                for row in reader
            ]
    except OSError as error:
        raise ReviewedFallbackError(f"could not read reviewed fallback CSV: {source}") from error
    references = load_map_references()
    indexed = {}
    for line_number, row in enumerate(rows, start=2):
        _validate_fallback_row(row, line_number, references)
        key = _fallback_identity(row.company_name, row.company_classification, row.imis_id, row.salesforce_account_id)
        if key in indexed:
            raise ReviewedFallbackError(f"reviewed fallback CSV has duplicate identity on line {line_number}")
        indexed[key] = row
    return indexed


def write_districts_csv(
    rows: list[DistrictRow], path: Path | str, *, as_of: date | str | None = None,
    unresolved_count: int = 0, included_company_count: int | None = None,
) -> None:
    _write_csv(rows, path, DistrictRow)
    census_count = sum(row.assignment_source == "census-confirmed" for row in rows)
    fallback_count = sum(row.assignment_source == "fallback-confirmed" for row in rows)
    _write_snapshot_metadata(
        path, "districts", len(rows), as_of=as_of,
        census_confirmed_count=census_count,
        fallback_confirmed_count=fallback_count,
        unresolved_count=unresolved_count,
        included_company_count=(len(rows) if included_company_count is None else included_company_count),
    )


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
            DistrictRow(**{name: (row.get(name) or "").strip() for name in names})
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
    # Match enrichment's repeat-ID rule exactly.  A district snapshot contains
    # the latest usable iMIS submission for a repeated nonblank ID, so using
    # every export row here would create duplicate identities and incompatible
    # totals.
    imis_companies = select_imis_companies_for_district_enrichment(
        read_imis_companies(imis_csv, preserve_source_order=True)
    )
    imis_companies, salesforce_accounts = filter_report_exclusions(
        imis_companies, salesforce_accounts, load_report_exclusion_phrases()
    )
    population = _report_population(
        combine_companies(imis_companies, salesforce_accounts)
    )
    population_by_identity = _population_by_identity(population)
    snapshot_by_identity = _snapshot_by_identity(read_districts_csv(districts_csv))
    unknown = set(snapshot_by_identity) - set(population_by_identity)
    if unknown:
        raise DistrictSnapshotError(
            "district snapshot contains a company outside the current report population; "
            "rerun enrichment and aggregation with the same available Salesforce data"
        )

    # The snapshot is the reviewed, confirmed population.  A missing snapshot
    # row is unresolved and must not leak into a national, state, district, or
    # public PDF total.
    included_population = [
        population_by_identity[identity] for identity in snapshot_by_identity
    ]
    national = _aggregate_row("national", "", "", "", "", included_population)
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
            raise DistrictSnapshotError(
                "district snapshot contains incomplete district data"
            )
        by_district.setdefault(district_key, []).append(company)
    districts = [
        _aggregate_row("district", *key, companies)
        for key, companies in sorted(by_district.items())
    ]
    # State totals describe the same confirmed Illinois population as the
    # snapshot; unresolved Illinois companies stay in review data instead.
    illinois_assigned = [
        company
        for identity, company in population_by_identity.items()
        if identity in snapshot_by_identity
        and snapshot_by_identity[identity].state == "IL"
    ]
    state_rows = (
        [_aggregate_row("state", "IL", "17", "", "", illinois_assigned)]
        if illinois_assigned
        else []
    )
    return [national, *state_rows, *districts]


def write_district_aggregates_csv(
    rows: list[DistrictAggregateRow], path: Path | str
) -> None:
    """Write national and district aggregates with a stable, typed header."""
    _write_csv(rows, path, DistrictAggregateRow)
    _write_snapshot_metadata(path, "aggregates", len(rows))


def validate_snapshot_metadata(
    path: Path | str, expected_kind: str
) -> dict[str, object]:
    """Validate the sidecar created with a saved enrichment output."""
    path = Path(path)
    metadata_path = path.with_suffix(path.suffix + ".metadata.json")
    if not metadata_path.is_file():
        raise DistrictSnapshotError(
            f"{expected_kind} snapshot metadata is missing; rerun the matching "
            "enrichment or aggregate command to create a verified snapshot"
        )
    try:
        metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise DistrictSnapshotError(
            f"{expected_kind} snapshot metadata is unreadable"
        ) from error
    if (
        metadata.get("kind") != expected_kind
        or metadata.get("sha256") != hashlib.sha256(path.read_bytes()).hexdigest()
    ):
        raise DistrictSnapshotError(
            f"{expected_kind} snapshot checksum does not match its CSV"
        )
    return metadata


def _population_by_identity(
    companies: list[CombinedCompany],
) -> dict[tuple[str, str, str], CombinedCompany]:
    indexed: dict[tuple[str, str, str], CombinedCompany] = {}
    for company in companies:
        identity = _aggregate_identity(
            company.classification.value,
            company.imis.imis_id
            if company.imis
            else _account_value(company.salesforce, CertificationAccountField.IMIS_ID),
            _account_value(company.salesforce, CertificationAccountField.ID),
        )
        if identity in indexed:
            raise DistrictSnapshotError(
                "current report population has duplicate identities"
            )
        indexed[identity] = company
    return indexed


def _snapshot_by_identity(
    rows: list[DistrictRow],
) -> dict[tuple[str, str, str], DistrictRow]:
    indexed: dict[tuple[str, str, str], DistrictRow] = {}
    for row in rows:
        if row.assignment_source not in {"census-confirmed", "fallback-confirmed"}:
            raise DistrictSnapshotError(
                "district snapshot contains an unconfirmed assignment"
            )
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


def _write_snapshot_metadata(
    path: Path | str, kind: str, row_count: int, *, as_of: date | str | None = None,
    **counts: int,
) -> None:
    destination = Path(path)
    metadata = {
        "kind": kind,
        "row_count": row_count,
        "retrieved_at": datetime.now(UTC).isoformat().replace("+00:00", "Z"),
        "sha256": hashlib.sha256(destination.read_bytes()).hexdigest(),
    }
    if as_of is not None:
        metadata["as_of"] = as_of.isoformat() if isinstance(as_of, date) else str(as_of)
    metadata.update(counts)
    destination.with_suffix(destination.suffix + ".metadata.json").write_text(
        json.dumps(metadata, indent=2) + "\n", encoding="utf-8"
    )


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


def _fallback_identity(name: str, classification: str, imis_id: str, account_id: str):
    return (name.strip(), classification.strip(), imis_id.strip(), account_id.strip())


def _validate_fallback_row(row: ReviewedFallbackRow, line_number: int, references) -> None:
    prefix = f"reviewed fallback CSV line {line_number}"
    if not row.company_name or row.company_classification not in {item.value for item in CompanyClassification}:
        raise ReviewedFallbackError(f"{prefix} has an incomplete or invalid identity")
    if not (row.imis_id or row.salesforce_account_id):
        raise ReviewedFallbackError(f"{prefix} must contain an iMIS ID or Salesforce account ID")
    if not all((row.state, row.state_fips, row.county, row.county_fips, row.congressional_district, row.congressional_district_geoid)):
        raise ReviewedFallbackError(f"{prefix} has incomplete geography")
    if not re.fullmatch(r"\d{2}", row.state_fips) or not re.fullmatch(r"\d{3}", row.county_fips):
        raise ReviewedFallbackError(f"{prefix} has invalid state or county FIPS")
    if not re.fullmatch(r"\d{1,2}", row.congressional_district) or not re.fullmatch(r"\d{4}", row.congressional_district_geoid):
        raise ReviewedFallbackError(f"{prefix} has invalid congressional district GEOID")
    if row.congressional_district_geoid != row.state_fips + row.congressional_district.zfill(2):
        raise ReviewedFallbackError(f"{prefix} has inconsistent FIPS and congressional district GEOID")
    if not row.reviewer_source_note:
        raise ReviewedFallbackError(f"{prefix} is missing reviewer/source provenance")
    try:
        reviewed = date.fromisoformat(row.reviewed_date)
    except ValueError as error:
        raise ReviewedFallbackError(f"{prefix} has an invalid reviewed_date") from error
    if reviewed > datetime.now(UTC).date():
        raise ReviewedFallbackError(f"{prefix} has a reviewed_date in the future")
    if bool(row.map_reference_kind) != bool(row.map_reference_key):
        raise ReviewedFallbackError(f"{prefix} has an incomplete map reference")
    if row.map_reference_kind:
        if row.map_reference_kind == "place":
            valid = row.map_reference_key == normalize_city_name(row.map_reference_key) and row.map_reference_key in references.places
        elif row.map_reference_kind == "county":
            valid = bool(re.fullmatch(r"\d{3}", row.map_reference_key)) and row.map_reference_key in references.counties
        else:
            valid = False
        if not valid:
            raise ReviewedFallbackError(f"{prefix} has an unsafe or unknown map reference")


def _assign_fallback_or_review(
    districts, reviews, fallbacks, identity, address, company, lookup_date, reason, candidates=""
):
    fallback = fallbacks.get(_fallback_identity(*identity))
    if fallback is None:
        reviews.append(_review(identity, address, reason, candidates))
        return
    districts.append(
        DistrictRow(
            identity[0], identity[1], identity[2], identity[3], address.source,
            address.source_address, "", address.city, fallback.state,
            fallback.state_fips, fallback.county, fallback.county_fips,
            fallback.congressional_district, fallback.congressional_district_geoid,
            "", "", "", "", lookup_date.isoformat(), "matched",
            "reviewed-fallback", relationship_summary(company, lookup_date),
            "fallback-confirmed", fallback.reviewer_source_note,
            fallback.reviewed_date, fallback.map_reference_kind,
            fallback.map_reference_key,
        )
    )


def _district_from_payload(
    identity, source_address, payload, lookup_date, relationship_summary_text
):
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
    district = _congressional_district(geographies)
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
            relationship_summary_text,
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


def _congressional_district(geographies):
    """Return one district from the newest available congressional session."""
    numbered_layers = []
    for name in geographies:
        if not isinstance(name, str):
            continue
        match = re.fullmatch(r"(\d+)(?:st|nd|rd|th) Congressional Districts", name)
        if match:
            numbered_layers.append((int(match.group(1)), name))

    if numbered_layers:
        _, layer_name = max(numbered_layers, key=lambda layer: layer[0])
    else:
        layer_name = "Congressional Districts"
    return _one_geography(geographies, layer_name)


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


_TRAILING_IMIS_ZIP = re.compile(
    r"\b(?P<zip>\d{5}(?:-\d{4})?)(?:\s+(?:UNITED\s+STATES|U\.?S\.?(?:A\.?)?))?\s*$",
    flags=re.IGNORECASE,
)


def _zip_from_text(value: str) -> str:
    """Return a trailing ZIP, optionally followed by a final U.S. country label."""
    match = _TRAILING_IMIS_ZIP.search(value)
    return match.group("zip") if match else ""


def _remove_zip(value: str) -> str:
    """Remove the same trailing ZIP and optional U.S. country label we extract."""
    match = _TRAILING_IMIS_ZIP.search(value)
    return value[: match.start()].rstrip(" ,\r\n") if match else value.strip(" ,")


def _remove_embedded_city_state(value: str, city: str, state: str) -> str:
    """Remove a repeated trailing ``city, state`` from an iMIS street field."""
    if not city or not state:
        return value.strip(" ,")
    pattern = rf"(?:\s+|,\s*){re.escape(city)}\s*,\s*{re.escape(state)}\s*$"
    return re.sub(pattern, "", value, flags=re.IGNORECASE).strip(" ,\r\n")

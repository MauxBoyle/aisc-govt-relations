"""Display rules for detailed Salesforce certification names.

Salesforce supplies the detailed certification names.  This module only
controls how those active names are grouped and ordered in the public PDF.
"""

from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from types import MappingProxyType
from typing import Literal

DisplaySection = Literal["Fab1", "Fab2", "Erec1", "Erec2"]


@dataclass(frozen=True)
class CertificationDisplayRule:
    """The public display group and position for one Salesforce name."""

    group: str
    section: DisplaySection
    display_order: int


# Keep this mapping alongside the code so a new Salesforce value remains
# visible in the report until its intended public grouping is reviewed here.
CERTIFICATION_GROUPS: Mapping[str, CertificationDisplayRule] = MappingProxyType(
    {
        "Building Fabricator": CertificationDisplayRule(
            "Building Fabricator", "Fab1", 1
        ),
        "Bridge Fabricator - Advanced": CertificationDisplayRule(
            "Bridge Fabricator", "Fab1", 2
        ),
        "Bridge Fabricator - Intermediate": CertificationDisplayRule(
            "Bridge Fabricator", "Fab1", 2
        ),
        "Bridge Fabricator - Simple": CertificationDisplayRule(
            "Bridge Fabricator", "Fab1", 2
        ),
        "Hydraulic Fabricator - Advanced": CertificationDisplayRule(
            "Hydraulic Fabricator", "Fab1", 3
        ),
        "Hydraulic Fabricator - Standard": CertificationDisplayRule(
            "Hydraulic Fabricator", "Fab1", 3
        ),
        "Highway Component Manufacturer": CertificationDisplayRule(
            "Highway Component Manufacturer", "Fab1", 4
        ),
        "Fracture Control Endorsement - Bridge": CertificationDisplayRule(
            "Fracture Control Endorsement", "Fab2", 1
        ),
        "Fracture Control Endorsement - Hydraulic": CertificationDisplayRule(
            "Fracture Control Endorsement", "Fab2", 1
        ),
        "Complex Coatings - Enclosed": CertificationDisplayRule(
            "Complex Coatings", "Fab2", 2
        ),
        "Complex Coatings - Covered": CertificationDisplayRule(
            "Complex Coatings", "Fab2", 2
        ),
        "Complex Coatings - Exposed": CertificationDisplayRule(
            "Complex Coatings", "Fab2", 2
        ),
        "Erector": CertificationDisplayRule("Erector", "Erec1", 1),
        "Bridge Endorsement": CertificationDisplayRule(
            "Bridge Endorsement", "Erec2", 1
        ),
        "Metal Deck Endorsement": CertificationDisplayRule(
            "Metal Deck Endorsement", "Erec2", 2
        ),
        "Seismic Endorsement": CertificationDisplayRule(
            "Seismic Endorsement", "Erec2", 3
        ),
    }
)


def format_certification_sentences(active_names: Iterable[str]) -> tuple[str, ...]:
    """Return concise public certification sentences for active Salesforce names.

    Known detailed names collapse into their configured groups.  Unknown names
    are deliberately retained in a final sentence so they can be spotted and
    added to ``CERTIFICATION_GROUPS`` after review.
    """
    mapped_sentences, fallback_sentence = _format_certification_parts(active_names)
    return (*mapped_sentences, *([fallback_sentence] if fallback_sentence else []))


def format_certification_paragraphs(active_names: Iterable[str]) -> tuple[str, ...]:
    """Return PDF paragraphs, keeping known groups separate from unknown names."""
    mapped_sentences, fallback_sentence = _format_certification_parts(active_names)
    paragraphs = [" ".join(mapped_sentences)] if mapped_sentences else []
    if fallback_sentence:
        paragraphs.append(fallback_sentence)
    return tuple(paragraphs)


def _format_certification_parts(
    active_names: Iterable[str],
) -> tuple[tuple[str, ...], str]:
    """Return mapped sentences and the optional unknown-name sentence."""
    groups: dict[str, CertificationDisplayRule] = {}
    unknown_names: dict[str, None] = {}
    for name in active_names:
        trimmed_name = name.strip()
        if not trimmed_name:
            continue
        rule = CERTIFICATION_GROUPS.get(trimmed_name)
        if rule is None:
            unknown_names.setdefault(trimmed_name, None)
        else:
            groups.setdefault(rule.group, rule)

    grouped_names = {
        section: [
            rule.group
            for rule in sorted(
                groups.values(), key=lambda rule: (rule.display_order, rule.group)
            )
            if rule.section == section
        ]
        for section in ("Fab1", "Fab2", "Erec1", "Erec2")
    }
    sentences: list[str] = []
    if sentence := _section_sentence(grouped_names["Fab1"], grouped_names["Fab2"]):
        sentences.append(sentence)
    if sentence := _section_sentence(grouped_names["Erec1"], grouped_names["Erec2"]):
        sentences.append(sentence)
    fallback_sentence = (
        f"AISC Certified {_serial_join(list(unknown_names))}." if unknown_names else ""
    )
    return tuple(sentences), fallback_sentence


def _section_sentence(primary: list[str], secondary: list[str]) -> str:
    """Format a Fabricator or Erector certification sentence."""
    if not primary:
        content = _serial_join(secondary)
    elif not secondary:
        content = _serial_join(primary)
    else:
        content = f"{_serial_join(primary)} with {_serial_join(secondary)}"
    return f"AISC Certified {content}." if content else ""


def _serial_join(values: list[str]) -> str:
    """Join values with commas and an Oxford comma when there are three or more."""
    if not values:
        return ""
    if len(values) == 1:
        return values[0]
    if len(values) == 2:
        return " and ".join(values)
    return f"{', '.join(values[:-1])}, and {values[-1]}"

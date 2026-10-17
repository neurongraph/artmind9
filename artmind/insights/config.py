"""Insights tuning: `<ARTMIND_HOME>/domains/insights/<domain>.yaml` (spec 2026-10-17 §4.3).

Every key is optional. Unknown keys and unknown detector names are errors, so a
typo never silently does nothing. Phase-2 blocks (outcome, transitions,
open_loops) are validated and normalised into `raw`; read them with `block()`.
"""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass, field
from pathlib import Path

import yaml

from artmind.insights.model import InsightsError

PERIODS = ("year", "quarter", "month")
KNOWN_DETECTORS = frozenset({
    "salience_trend", "type_trend", "dormancy", "communities", "brokers",
    "outcome_lift", "transitions", "open_loops", "same_as_candidates",
    "kind_mismatch", "vocabulary_sprawl", "relationship_usage", "attribution_suspects",
    "open_conflicts", "fragments", "split_signal",
})
_INTS = {  # key: (default, minimum)
    "min_evidence_days": (4, 1),
    "min_period_docs": (20, 1),
    "max_findings": (25, 1),
    "keep_runs": (12, 1),
    "narrate_top": (15, 0),
}
_POS_INT_KEYS = ("min_support", "window", "silence_multiple", "min_silence_days", "min_weight",
                 "min_circle_size", "min_period_edges", "min_count",
                 "cluster_days", "dominance_min_days", "stale_days", "min_targets", "min_size", "max_size", "min_days", "max_gap_periods")
_POS_NUM_KEYS = ("resolution", "min_log_ratio", "min_jaccard",
                 "min_z", "min_score", "max_share", "focus_share", "dominance_share", "max_name_distance",
                 "circle_min_shift", "fdr", "dominance_margin")
_DOMAIN_RE = re.compile(r"^[a-z][a-z0-9_]*(\.[a-z][a-z0-9_]*)*$")
_RAW_BLOCKS = ("outcome", "transitions", "open_loops")
KNOWN_KEYS = frozenset({"domain", "anchors", "period", "narrate", "detectors", *_INTS, *_RAW_BLOCKS})


@dataclass(frozen=True)
class InsightsConfig:
    domain: str
    anchors: tuple[str, ...] = ()
    period: str = "year"
    min_evidence_days: int = 4
    min_period_docs: int = 20
    max_findings: int = 25
    keep_runs: int = 12
    narrate_top: int = 15
    narrate: bool = True
    detectors: dict = field(default_factory=dict)
    raw: dict = field(default_factory=dict)

    def detector(self, name: str) -> dict:
        settings = dict(self.detectors.get(name) or {})
        settings.pop("enabled", None)
        return settings

    def enabled(self, name: str) -> bool:
        return bool((self.detectors.get(name) or {}).get("enabled", True))

    def block(self, name: str) -> dict | None:
        """A validated, normalised phase-2 block (outcome | transitions | open_loops), or None."""
        value = self.raw.get(name)
        return dict(value) if value is not None else None


def config_dir() -> Path:
    import paths

    return Path(paths.DOMAIN_INSIGHTS_DIR)


def config_path(domain: str) -> Path:
    if not isinstance(domain, str) or not _DOMAIN_RE.match(domain):
        raise InsightsError(f"invalid domain name {domain!r}")
    return config_dir() / f"{domain}.yaml"


def config_hash(domain: str) -> str:
    path = config_path(domain)
    return hashlib.sha256(path.read_bytes()).hexdigest() if path.is_file() else ""


def _strings(value, where: str, *, required: bool, lower: bool = True) -> list[str]:
    if value is None and not required:
        return []
    if not isinstance(value, list) or not all(isinstance(v, str) and v.strip() for v in value) or (required and not value):
        raise InsightsError(f"{where} must be a {'non-empty ' if required else ''}list of strings")
    return [v.strip().casefold() if lower else v.strip() for v in value]


def _mapping(value, where: str, allowed: set[str]) -> dict:
    if not isinstance(value, dict):
        raise InsightsError(f"{where} must be a mapping")
    unknown = set(value) - allowed
    if unknown:
        raise InsightsError(f"{where}: unknown key(s) {sorted(map(str, unknown))}")
    return value


def _name(value, where: str, default: str | None = None) -> str:
    if value is None and default is not None:
        return default
    if not isinstance(value, str) or not value.strip():
        raise InsightsError(f"{where} must be a non-empty string")
    return value.strip()


def _parse_outcome(value, where: str) -> dict:
    v = _mapping(value, where, {"class", "property", "positive", "negative"})
    block = {
        "class": _name(v.get("class"), f"{where}.class"),
        "property": _name(v.get("property"), f"{where}.property", default="type"),
        "positive": _strings(v.get("positive"), f"{where}.positive", required=True),
        "negative": _strings(v.get("negative"), f"{where}.negative", required=True),
    }
    both = set(block["positive"]) & set(block["negative"])
    if both:
        raise InsightsError(f"{where}: {sorted(both)} listed as both positive and negative")
    return block


def _parse_open_loops(value, where: str) -> dict:
    v = _mapping(value, where, {"class", "property", "urgency_property", "open", "closed"})
    return {
        "class": _name(v.get("class"), f"{where}.class"),
        "property": _name(v.get("property"), f"{where}.property", default="status"),
        "urgency_property": _name(v.get("urgency_property"), f"{where}.urgency_property", default="urgency"),
        "open": _strings(v.get("open"), f"{where}.open", required=True),
        "closed": _strings(v.get("closed"), f"{where}.closed", required=False),
    }


def _parse_transitions(value, where: str) -> dict:
    v = _mapping(value, where, {"extra_verbs", "classes"})
    return {
        "extra_verbs": [s.upper() for s in _strings(v.get("extra_verbs"), f"{where}.extra_verbs", required=False, lower=False)],
        "classes": _strings(v.get("classes"), f"{where}.classes", required=False, lower=False),
    }


_BLOCK_PARSERS = {"outcome": _parse_outcome, "open_loops": _parse_open_loops, "transitions": _parse_transitions}


def parse_config(data: dict | None, domain: str) -> InsightsConfig:
    where = f"insights config for {domain}"
    data = {} if data is None else data
    if not isinstance(data, dict):
        raise InsightsError(f"{where}: must be a mapping")
    unknown = set(data) - KNOWN_KEYS
    if unknown:
        raise InsightsError(f"{where}: unknown key(s) {sorted(map(str, unknown))}")
    if data.get("domain") not in (None, domain):
        raise InsightsError(f"{where}: domain is {data['domain']!r}, expected {domain!r}")
    period = data.get("period", "year")
    if period not in PERIODS:
        raise InsightsError(f"{where}: period must be one of {list(PERIODS)}")
    ints = {}
    for key, (default, minimum) in _INTS.items():
        value = data.get(key, default)
        if not isinstance(value, int) or isinstance(value, bool) or value < minimum:
            raise InsightsError(f"{where}: {key} must be an integer >= {minimum}")
        ints[key] = value
    anchors = data.get("anchors") or []
    if not isinstance(anchors, list) or not all(isinstance(a, str) and a.strip() for a in anchors):
        raise InsightsError(f"{where}: anchors must be a list of names or @<_id> strings")
    detectors = data.get("detectors") or {}
    if not isinstance(detectors, dict) or not all(v is None or isinstance(v, dict) for v in detectors.values()):
        raise InsightsError(f"{where}: detectors must map a detector name to a mapping of settings")
    bad = set(detectors) - KNOWN_DETECTORS
    if bad:
        raise InsightsError(f"{where}: unknown detector(s) {sorted(map(str, bad))}")
    detectors = {name: dict(settings or {}) for name, settings in detectors.items()}
    for name, settings in detectors.items():
        if "enabled" in settings and not isinstance(settings["enabled"], bool):
            raise InsightsError(f"{where}: detector {name} enabled must be true or false")
        for key, value in settings.items():
            if key in _POS_INT_KEYS and (not isinstance(value, int) or isinstance(value, bool) or value < 1):
                raise InsightsError(f"{where}: detector {name} {key} must be a positive integer")
            if key in _POS_NUM_KEYS and (not isinstance(value, (int, float)) or isinstance(value, bool) or value <= 0):
                raise InsightsError(f"{where}: detector {name} {key} must be a positive number")
            if key == "classes" and (not isinstance(value, list) or not all(isinstance(c, str) for c in value)):
                raise InsightsError(f"{where}: detector {name} classes must be a list of strings")
            if key == "aliases" and (not isinstance(value, list) or not all(
                    isinstance(g, list) and len(g) >= 2 and all(isinstance(n, str) and n.strip() for n in g)
                    for g in value)):
                raise InsightsError(f"{where}: detector {name} aliases must be a list of name groups (2+ names each)")
    narrate = data.get("narrate", True)
    if not isinstance(narrate, bool):
        raise InsightsError(f"{where}: narrate must be true or false")
    raw = {}
    for key in _RAW_BLOCKS:
        if key in data:
            raw[key] = _BLOCK_PARSERS[key](data[key], f"{where}: {key}")
    return InsightsConfig(
        domain=domain, anchors=tuple(a.strip() for a in anchors), period=period,
        narrate=narrate, detectors=dict(detectors), raw=raw, **ints,
    )


def load_config(domain: str) -> InsightsConfig:
    path = config_path(domain)
    if not path.is_file():
        return parse_config({}, domain)
    try:
        data = yaml.safe_load(path.read_text(encoding="utf-8"))
    except yaml.YAMLError as exc:
        raise InsightsError(f"{path} is not valid YAML: {exc}") from exc
    return parse_config(data, domain)

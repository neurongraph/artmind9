"""Insights tuning: `<ARTMIND_HOME>/domains/insights/<domain>.yaml` (spec 2026-10-17 §4.3).

Every key is optional. Unknown keys and unknown detector names are errors, so a
typo never silently does nothing. Phase-2 blocks (outcome, transitions,
open_loops) are validated as mappings and kept verbatim in `raw`.
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
    narrate = data.get("narrate", True)
    if not isinstance(narrate, bool):
        raise InsightsError(f"{where}: narrate must be true or false")
    raw = {}
    for key in _RAW_BLOCKS:
        if key in data:
            if not isinstance(data[key], dict):
                raise InsightsError(f"{where}: {key} must be a mapping")
            raw[key] = data[key]
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

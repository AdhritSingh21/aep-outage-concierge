"""The single scenario state every customer-facing output is a pure function of.

Nothing in the model layer reads Streamlit widgets; the UI builds a Scenario and hands it down. Changing crews or
access here changes the schedule, the restoration range, the explanation and the messages together.
"""
from __future__ import annotations

import hashlib
import json
from dataclasses import asdict, dataclass, replace

from . import config

DAMAGE_SOURCES = ("historical", "simulated")


@dataclass(frozen=True)
class Scenario:
    premise_nu: str
    severity: float = 1.0                 # multiplier on the Feb-2025 reference rates (risk; simulated damage)
    crews: int = 2                        # crews dedicated to the customer's circuit (Assumed)
    hours_to_landfall: float = 12.0       # hours until the storm arrives (drives alert timing and pre-storm wording)
    access_difficulty: bool = False       # ice / flooding: restricted-access adder on every task
    labor_multiplier: float = 1.0         # multiplies all task hours (uncertainty knob for the midpoint)
    damage_source: str = "historical"     # "historical": Feb-2025 records on the circuit (Observed set), "simulated": draws
    medical_need: bool | None = None      # None → use the account's declared life-support flag
    opted_in: bool = True                 # customer opted in (enrolled) to outage alerts
    crew_start_delay_h: float = 0.0       # hours after the storm ends before crews can safely start (added once, at display)
    seed: int = 7
    n_draws: int = 20                     # simulated damage sets per estimate
    needs: tuple[str, ...] = ()           # needs declared in this session (keys of config.NEEDS); nothing is inferred
    prioritize_critical: bool = False     # Assumed L10: weight life-support customers and critical facilities in the ordering

    def __post_init__(self):
        if self.damage_source not in DAMAGE_SOURCES:
            raise ValueError(f"damage_source must be one of {DAMAGE_SOURCES}")
        if self.crews < 1:
            raise ValueError("crews must be >= 1")
        if self.severity <= 0 or self.labor_multiplier <= 0:
            raise ValueError("severity and labor_multiplier must be positive")
        if self.crew_start_delay_h < 0:
            raise ValueError("crew_start_delay_h must be >= 0")
        bad = [n for n in self.needs if n not in config.NEEDS]
        if bad:
            raise ValueError(f"unknown needs {bad}; allowed: {sorted(config.NEEDS)}")
        object.__setattr__(self, "needs", tuple(sorted(set(self.needs))))

    @property
    def declares_medical(self) -> bool:
        """True when the medical need is declared explicitly (toggle) or through the needs list."""
        return bool(self.medical_need) or "medical" in self.needs

    def with_(self, **changes) -> "Scenario":
        return replace(self, **changes)

    def to_dict(self) -> dict:
        d = asdict(self)
        d["needs"] = list(self.needs)
        return d

    def key(self) -> str:
        """Stable hash used for caching; identical scenarios give identical keys."""
        return hashlib.sha1(json.dumps(self.to_dict(), sort_keys=True).encode()).hexdigest()[:16]

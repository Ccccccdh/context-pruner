"""Host-calibrated token thresholds for the three-arm experiments.

Problem this solves
-------------------
Every three-arm budget (``none`` / ``native_summary`` / ``pruner_v1``) is written
in ``context_pruner.types.estimate_tokens`` units, but the provider bills real
tokens, and the two differ by a **host-dependent** factor. Measured from the
recorded batches (``.tooling/calibrate_tokens.py``, zero API):

    host             provider/estimated (median)   estimator error
    openhands v52    0.900                          ~10% overestimate
    openai-agents    0.368                          2.7x overestimate
    crewai           0.389                          2.6x overestimate

So "soft limit = 1800" meant ~1600 provider tokens on OpenHands but ~700 on
CrewAI. An arm that triggers on the host's own accounting therefore triggers at a
different physical load on each host, and cross-host arm behaviour is not
comparable. The CrewAI v133 native-summary arm additionally lacked a summary
transport in the production path; its zero accepted summaries must not be
attributed to threshold calibration alone.

Policy
------
Budgets are declared in **provider tokens** (the unit the vendor bills and the
unit a reader can interpret), then converted to estimator units per host with the
measured ratio. Both numbers are written into the manifest, so a frozen protocol
records what it meant physically, not just locally.

Rules:
  * ratios come from measurement, never from a guess;
  * a host with no measurement must not be given a converted budget (fail loudly);
  * thresholds stay proportional to the measured budget they were derived from,
    so raising a threshold is an explicit, reviewable change.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Mapping

#: Measured provider-billed tokens per estimator token, per host.
#: Source: `.tooling/gates/token-calibration.json` (openhands v52 / agents v3arm /
#: crewai v133 batches). Update only from a new measurement, with the batch named.
CALIBRATION: Mapping[str, float] = {
    "openhands": 0.900,
    "openai_agents": 0.368,
    "crewai": 0.389,
}


@dataclass(frozen=True)
class Thresholds:
    """One arm's budget, expressed in both units."""

    host: str
    ratio: float
    soft_provider: int
    hard_provider: int
    target_provider: int

    @property
    def soft_estimated(self) -> int:
        return provider_to_estimated(self.host, self.soft_provider)

    @property
    def hard_estimated(self) -> int:
        return provider_to_estimated(self.host, self.hard_provider)

    @property
    def target_estimated(self) -> int:
        return provider_to_estimated(self.host, self.target_provider)

    def as_manifest(self) -> dict[str, object]:
        """Both unit systems, so a frozen protocol states its physical meaning."""
        return {
            "host": self.host,
            "calibration_ratio_provider_per_estimated": self.ratio,
            "provider_tokens": {
                "soft": self.soft_provider,
                "hard": self.hard_provider,
                "target": self.target_provider,
            },
            "estimated_tokens": {
                "soft": self.soft_estimated,
                "hard": self.hard_estimated,
                "target": self.target_estimated,
            },
            "note": "Budgets are declared in provider tokens and converted with a measured ratio.",
        }


def ratio_for(host: str) -> float:
    try:
        return CALIBRATION[host]
    except KeyError as error:  # pragma: no cover - guarded by callers' review
        raise KeyError(
            f"no measured calibration for host {host!r}; "
            "run .tooling/calibrate_tokens.py against a recorded batch first"
        ) from error


def provider_to_estimated(host: str, provider_tokens: int) -> int:
    """Convert a provider-token budget into the estimator unit the arms use."""
    ratio = ratio_for(host)
    return max(1, int(round(int(provider_tokens) / ratio)))


def estimated_to_provider(host: str, estimated_tokens: int) -> int:
    """Convert an estimator-unit budget back to provider tokens (for reporting)."""
    return max(1, int(round(int(estimated_tokens) * ratio_for(host))))


def thresholds(
    host: str,
    *,
    soft_provider: int,
    hard_provider: int,
    target_provider: int,
) -> Thresholds:
    if not soft_provider <= hard_provider:
        raise ValueError("soft budget must not exceed the hard budget")
    if not 0 < target_provider <= hard_provider:
        raise ValueError("target budget must be positive and within the hard budget")
    return Thresholds(
        host=host,
        ratio=ratio_for(host),
        soft_provider=int(soft_provider),
        hard_provider=int(hard_provider),
        target_provider=int(target_provider),
    )


def scaled(base_estimated: int, factor: float, host: str) -> int:
    """Scale an existing estimator-unit threshold by ``factor`` (ablation helper).

    Used by the OpenHands threshold ablation, which must change exactly one thing:
    how much context is allowed to accumulate before compressing. Returning
    estimator units keeps every other line of the runner untouched.
    """
    if factor <= 0:
        raise ValueError("threshold factor must be positive")
    return max(1, int(round(int(base_estimated) * float(factor))))

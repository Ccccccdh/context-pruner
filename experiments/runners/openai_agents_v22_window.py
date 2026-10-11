"""Window configuration for the literal-carrier line elision: one module per K.

Why this module exists
----------------------
The v21 round established, on a compliant batch, that the literal-carrier line elision saves
3.76 percent on the same-trajectory comparison against a pre-registered 5 percent line.  The one
parameter that governs how much non-carrier content is protected is the **window** K: the number
of lines kept before and after every line that carries a registered literal.

``integrations/openai_agents/V22_WINDOW_DOSE_PLAN_20261010.json`` therefore pre-registers K as a
dose over K in {3, 1, 0}, with K as the only variable between the three configurations.

How K is bound, and why it is bound this way
--------------------------------------------
The frozen v21 mechanism module reads its window from a module-level constant inside its own
module, so a subclass that rebinds the constant would still be elided with the ancestor's value -
the constant is captured by the function that uses it.  This module therefore does not subclass
for the window; it **specifies** the window for a dedicated filter class, and the filter class
holds it as an attribute that the inherited stage reads as its first source.  One attribute, one
number, asserted against the plan at build time and again in the gate.

The frozen v21 module is not edited by any of this: its ``WINDOW_LINES`` stays 3 and its digest
stays ``e9ecf4acdbf6fd48179ac7cbbf52102b2abcd5f296f6d3541b65e426222de020``.  Each configuration
below is a *new* module with its own digest, pinned in this round's revision ledger, so a batch can
be tied to the exact window it was produced with.
"""

from __future__ import annotations

from typing import Any

from experiments.runners import openai_agents_v21_carrier_elision as base

#: The K values this round pre-registered, in the order the dose is reported.
AUTHORISED_WINDOWS = (3, 1, 0)

MECHANISM_INSTANCE_PREFIX = "literal_carrier_preserving_line_elision_v22_k"


def configuration(window: int) -> dict[str, Any]:
    """The configuration record for one K, with the plan's invariants asserted.

    Raises ``ValueError`` for a K this round did not pre-register: an unregistered window may not
    be run, and the honest place to say so is here rather than in a report afterwards.
    """
    value = int(window)
    if value not in AUTHORISED_WINDOWS:
        raise ValueError(
            f"K={value} is not pre-registered: the v22 plan authorises {AUTHORISED_WINDOWS} and "
            "an interpolated window (for example K=2) may not be run without a new registration"
        )
    class_name = f"LiteralCarrierElisionFilterK{value}"
    declaration = (
        "self.v21_window_lines bound by the configuration class"
        if value != base.WINDOW_LINES
        else (
            "self.v21_window_lines bound by the configuration class, equal to the frozen "
            "mechanism module's own constant"
        )
    )
    return {
        "k": value,
        "class_name": class_name,
        "mechanism_instance": f"{MECHANISM_INSTANCE_PREFIX}{value}",
        "mechanism_schema": f"openai_agents_v22_window_k{value}",
        "composition_schema": f"duplicate_then_retention_then_line_elision_k{value}",
        "evidence_schema": f"openai_literal_carrier_elision_k{value}",
        "config_module": f"experiments/runners/openai_agents_v22_window_k{value}.py",
        "base_module": "experiments/runners/openai_agents_v21_carrier_elision.py",
        "base_window_lines": base.WINDOW_LINES,
        "declared_window_value": value,
        "effective_window_declaration": declaration,
        "note_form": base.NOTE_FORM,
    }


def make_class(window: int) -> type:
    """The filter class for one K: the frozen mechanism with this window bound as an attribute."""
    config = configuration(window)
    value = config["k"]

    def __init__(self: Any, *args: Any, **kwargs: Any) -> None:
        # Set before the ancestor constructor runs: the ancestor is the frozen v21 mechanism and
        # nothing in it reads this attribute during construction, but binding it first means the
        # window is in force for every call that can touch it.
        self.v21_window_lines = value
        super(cls, self).__init__(*args, **kwargs)

    cls = type(
        config["class_name"],
        (base.LiteralCarrierElisionFilter,),
        {
            "__init__": __init__,
            "__doc__": (
                f"The literal-carrier line elision with the protection window K={value}. "
                "Everything else - the duplicate rule, the evidence-safe retention, the literal "
                "invariant, the note form, the recency policy and the fail-closed rules - is the "
                "frozen v21 mechanism, unchanged."
            ),
            "WINDOW_LINES": value,
            "v22_k": value,
        },
    )
    cls.__module__ = str(config["config_module"]).removesuffix(".py").replace("/", ".")
    return cls

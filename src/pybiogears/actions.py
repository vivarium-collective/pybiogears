"""Builders for BioGears scenario <Action> XML elements.

Each returns one <Action> element as a string, in BioGears' own scenario-action
schema (share/xsd/cdm/PatientActions.xsd, ...), for BioGearsEngine.process_actions.
"""

from __future__ import annotations

from xml.sax.saxutils import quoteattr, escape


def hemorrhage(compartment: str, initial_rate_mL_per_min: float) -> str:
    return (
        f'<Action xsi:type="HemorrhageData" Compartment={quoteattr(compartment)}>'
        f'<InitialRate value="{float(initial_rate_mL_per_min)!r}" unit="mL/min"/>'
        "</Action>"
    )


def substance_compound_infusion(compound: str, bag_volume_mL: float, rate_mL_per_min: float) -> str:
    return (
        '<Action xsi:type="SubstanceCompoundInfusionData">'
        f"<SubstanceCompound>{escape(compound)}</SubstanceCompound>"
        f'<BagVolume value="{float(bag_volume_mL)!r}" unit="mL"/>'
        f'<Rate value="{float(rate_mL_per_min)!r}" unit="mL/min"/>'
        "</Action>"
    )

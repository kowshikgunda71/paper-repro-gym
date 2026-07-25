"""Derive a claim's tolerance from the paper by rule, not by judgement.

The sharpest objection to a pre-registered replication is not that the
tolerances moved after the run — pre-registration already prevents that — it is
that the reproducer *chose* them, and could have chosen values that made
interesting failures likely. Publishing a rationale does not answer that; the
rationale is also the reproducer's.

The answer is to make the tolerance a function of the paper. Three rules, applied
in a fixed order, with the rule that fired recorded on the claim so a reader can
audit it:

  R1  the paper reports dispersion for the quantity (a standard deviation,
      a confidence interval, error bars, a min/max over trials)
      -> tolerance = that dispersion.
      Rationale: the paper's own statement of how precisely it measured this.

  R2  the paper reports no dispersion, but the printed value carries an implied
      rounding interval ("38%" -> +/-0.5; "0.35" -> +/-0.005)
      -> tolerance = half the last printed significant digit.
      This is a FLOOR, never a ceiling: a tolerance tighter than the paper's own
      printing precision asks the replication to resolve a number the paper did
      not report.

  R3  neither is available
      -> tolerance = DEFAULT_RELATIVE x |claimed value|, a single constant
      declared once for the whole bench and applied to every such claim.

The final tolerance is max(R2, R1 or R3), so the rounding floor always applies.

What this does NOT do: choose the claim's *direction* (see bundle.evaluate_claim
for lower_bound/upper_bound/interval), or decide whether a claim is worth
registering. It removes one degree of freedom, not all of them.
"""

from __future__ import annotations

from decimal import Decimal
from statistics import stdev as _stdev

#: Applied to every claim where the paper reports no dispersion at all. Declared
#: once here rather than per claim, so it cannot be tuned to a target result.
#: Changing it changes every such claim in the bench simultaneously, which is the
#: point: it is a bench-wide constant, not a per-claim knob.
DEFAULT_RELATIVE = 0.10

#: Standard errors of the replication's own mean used as the tolerance when the
#: paper reports no dispersion (rule R4). 2 SE is the conventional ~95% band, so
#: a claim fails only on a disagreement larger than sampling noise explains.
K_STANDARD_ERRORS = 2.0


def rounding_floor(printed: str) -> float:
    """Half the last significant digit of a value AS THE PAPER PRINTED IT.

    '38' -> 0.5, '0.35' -> 0.005, '2.51' -> 0.005, '1.7e6' -> 50000.0.
    Takes a string, not a float, because 0.35 and 0.350 are the same float and
    different claims about precision."""
    d = Decimal(printed.strip().replace("%", "").replace(",", ""))
    return float(abs(d.scaleb(0)) * 0 + Decimal(1).scaleb(d.as_tuple().exponent) / 2)


def derive(printed_value: str, *, reported_dispersion: float | None = None,
           dispersion_kind: str = "", default_relative: float = DEFAULT_RELATIVE,
           deterministic: bool = False, observed_values: list[float] | None = None,
           k_se: float = K_STANDARD_ERRORS) -> dict:
    """Return {tolerance, rule, rationale, floor} for one claim.

    `printed_value` is the number as it appears in the paper, as a string.
    `reported_dispersion` is the paper's own dispersion for that quantity, in the
    claim's units, when it states one.
    `deterministic` marks a STRUCTURAL claim — a parameter count, a pruning
    schedule, an arithmetic identity — which has no measurement noise at all. The
    empirical default would be nonsense there: a 10% window on a pruning ladder
    that is fixed arithmetic would pass almost any implementation, which is
    exactly backwards, because structural claims are the ones that must be tight
    enough to license calling an empirical miss a paper defect."""
    claimed = float(Decimal(printed_value.strip().replace("%", "").replace(",", "")))
    floor = rounding_floor(printed_value)

    if deterministic:
        return {"tolerance": floor, "rule": "R0", "rounding_floor": floor,
                "claimed_value": claimed,
                "rationale": "structural/deterministic claim: no measurement noise exists, so the "
                             f"tolerance is exactly the rounding interval implied by the paper "
                             f"printing '{printed_value.strip()}'"}

    if reported_dispersion is not None:
        tol, rule = float(reported_dispersion), "R1"
        why = (f"the paper reports its own dispersion for this quantity"
               f"{f' ({dispersion_kind})' if dispersion_kind else ''}: {reported_dispersion}")
    elif observed_values and len(observed_values) >= 2:
        # R4. Preferred over the flat relative default, which has no statistical
        # content: a fixed 10% window on a quantity whose sampling noise is 30%
        # fails whether or not the paper is right, and passes trivially when the
        # noise is 1%. k standard errors of OUR OWN mean is still mechanical --
        # it is computed from data, not chosen -- but it actually tracks how well
        # the quantity can be measured at the sample size we can afford.
        n = len(observed_values)
        se = _stdev(observed_values) / (n ** 0.5)
        tol, rule = k_se * se, "R4"
        why = (f"the paper reports no dispersion; tolerance is {k_se} standard errors of the "
               f"replication's own {n}-seed mean (SE={se:.4g}), which is mechanical and "
               f"reflects the precision actually achievable at this sample size")
    elif claimed != 0:
        tol, rule = abs(claimed) * default_relative, "R3"
        why = (f"the paper reports no dispersion for this quantity; bench-wide default of "
               f"{default_relative:.0%} of the claimed value applies")
    else:
        tol, rule = floor, "R2"
        why = "claimed value is zero and no dispersion is reported; the rounding floor applies"

    if tol < floor:
        tol, rule = floor, f"{rule}->R2"
        why += (f"; raised to the rounding floor {floor} implied by the paper printing "
                f"'{printed_value.strip()}' — a tolerance below it would ask the replication "
                f"to resolve precision the paper never reported")

    return {"tolerance": tol, "rule": rule, "rationale": why, "rounding_floor": floor,
            "claimed_value": claimed}


def audit(claims: list[dict]) -> list[str]:
    """Report claims whose tolerance was NOT produced by the policy.

    A hand-set tolerance is allowed — some claims genuinely need one — but it has
    to be visible, so it cannot quietly become the norm."""
    out = []
    for c in claims:
        if not c.get("tolerance_rule"):
            out.append(f"{c.get('id')}: tolerance {c.get('tolerance')} is hand-set "
                       f"(no tolerance_rule recorded)")
    return out


def is_underpowered(observed_mean: float, tolerance: float, claimed: float,
                    null_value: float = 0.0) -> bool:
    """True when the uncertainty band is wider than the effect being claimed.

    Criterion: 2*tolerance > |claimed - null|. If the full width of our
    uncertainty exceeds the entire effect the paper asserts, the replication has
    not tested the claim -- it has failed to measure it. That is INCONCLUSIVE,
    not NOT_REPRODUCED.

    The distinction is not pedantic. Reporting an unfalsifiable claim as 'not
    reproduced' states evidence against a paper that the data does not contain,
    which is precisely the overclaim a replication bench exists to avoid.

    LTH's C1 is the worked example: at n=5 the 2-SE half-width is 21.1, so the
    band is 42.2 wide against a claimed effect of 38 points. Nothing was learned.
    An earlier version of this test asked whether the band contained both the
    claimed and null values; that turns on whether a bound lands at 0.3 or -0.1,
    which is noise, not power.
    """
    return 2 * abs(tolerance) > abs(claimed - null_value)

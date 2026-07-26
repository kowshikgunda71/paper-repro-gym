"""Check a paper's printed numbers against its own described architecture.

Zero compute. No dataset, no training, no GPU -- just arithmetic on numbers the
paper already prints. That is the point: re-implementing a paper to test it is
expensive and has been done at scale (Raff, NeurIPS 2019, N=255, six months).
Checking whether a paper's numbers agree with *themselves* is nearly free, so it
reaches an N that re-implementation never will.

Two real cases motivated this module, and they point in opposite directions:

  CONTRADICTORY -- Frankle & Carbin (LTH) print Conv-6 = "1.7M" parameters. The
  architecture their own appendix describes computes to 2,261,184, and no
  padding/pooling variant reaches 1.7M without breaking Conv-2 and Conv-4, which
  match exactly. The printed number cannot be reconciled with the paper's text.

  SELF-REPAIRING -- Zhang et al. never state their CIFAR-10 crop, but print
  MLP 1x512 = 1,209,866 and MLP 3x512 = 1,735,178. Those are reproduced exactly
  by a 28x28x3 input and by no other. The omitted detail is *recoverable* from
  the paper's own arithmetic.

Both are findings. The second is arguably more useful to authors, because the fix
is free advice: print your parameter counts -- they are a checksum on everything
else in the paper.

What this module deliberately does NOT do: parse papers. Extracting an
architecture from prose is a research problem, and a wrong extraction would
manufacture defects that are not there. The architecture is supplied by the
caller, who read the paper. This checks the arithmetic, which is where the errors
actually hide, because reviewers rarely multiply layer shapes.
"""

from __future__ import annotations

from dataclasses import dataclass, field

MATCH = "MATCH"
SELF_REPAIRING = "SELF_REPAIRING"
CONTRADICTORY = "CONTRADICTORY"


def mlp_params(in_dim: int, depth: int, width: int, classes: int, bias: bool = True) -> int:
    """Parameters of a `depth`-hidden-layer, `width`-wide MLP."""
    b = 1 if bias else 0
    n = in_dim * width + b * width
    n += (depth - 1) * (width * width + b * width)
    return n + width * classes + b * classes


def conv_params(spec: list[list[int]], in_ch: int, fc: list[int], classes: int,
                spatial: int, k: int = 3, bias: bool = True) -> int:
    """Parameters of a VGG-style conv stack + FC head.

    `spec` is a list of blocks, each a list of output channel counts; one 2x pool
    follows every block. `spatial` is the input side length."""
    b = 1 if bias else 0
    n, ch, side = 0, in_ch, spatial
    for block in spec:
        for out_ch in block:
            n += k * k * ch * out_ch + b * out_ch
            ch = out_ch
        side //= 2
    dims = [ch * side * side] + list(fc)
    for a, c in zip(dims, dims[1:]):
        n += a * c + b * c
    return n + dims[-1] * classes + b * classes


@dataclass
class Finding:
    label: str
    printed: int
    computed: int
    verdict: str
    detail: str
    recovered: dict = field(default_factory=dict)

    @property
    def is_defect(self) -> bool:
        return self.verdict == CONTRADICTORY

    def __str__(self) -> str:
        d = self.computed - self.printed
        return (f"{self.label}: printed {self.printed:,}, computed {self.computed:,} "
                f"({d:+,}) -> {self.verdict}\n    {self.detail}")


def check(label: str, printed: int, computed: int, *, tolerance: int = 0,
          alternatives: dict[str, int] | None = None) -> Finding:
    """Compare a printed count with the computed one.

    `alternatives` maps a description of a DIFFERENT reading of the paper to the
    count it produces. If exactly one alternative reproduces the printed value,
    the omission is self-repairing and that reading is recovered. If none does,
    the printed number contradicts every reading offered.
    """
    if abs(computed - printed) <= tolerance:
        return Finding(label, printed, computed, MATCH,
                       "the printed count matches the architecture as described")

    hits = {k: v for k, v in (alternatives or {}).items() if abs(v - printed) <= tolerance}
    if len(hits) == 1:
        reading = next(iter(hits))
        return Finding(label, printed, computed, SELF_REPAIRING,
                       f"the described architecture gives {computed:,}, but the printed "
                       f"count is reproduced exactly by: {reading}. The paper omits this "
                       f"detail from its text, yet its own arithmetic recovers it.",
                       recovered={reading: hits[reading]})
    if len(hits) > 1:
        return Finding(label, printed, computed, SELF_REPAIRING,
                       f"several readings reproduce the printed count ({', '.join(hits)}); "
                       f"the omission is recoverable but not uniquely.",
                       recovered=hits)
    tried = f"; tried {len(alternatives)} alternative reading(s)" if alternatives else ""
    return Finding(label, printed, computed, CONTRADICTORY,
                   f"no reading offered reproduces the printed count{tried}. The printed "
                   f"number cannot be reconciled with the architecture the paper describes.")


def solve_mlp_input_dim(printed: int, depth: int, width: int, classes: int,
                        bias: bool = True) -> int | None:
    """The input dimension that would make an MLP have exactly `printed` params.

    This is the move that recovered Zhang et al.'s 28x28 crop. Returns None when
    no integer input dimension works -- which is itself informative, because it
    means the discrepancy is not an input-size question at all."""
    b = 1 if bias else 0
    rest = (depth - 1) * (width * width + b * width) + width * classes + b * classes + b * width
    num = printed - rest
    if num <= 0 or num % width:
        return None
    return num // width


def report(findings: list[Finding]) -> str:
    order = {CONTRADICTORY: 0, SELF_REPAIRING: 1, MATCH: 2}
    lines = ["# Internal numerical consistency", ""]
    for f in sorted(findings, key=lambda f: order[f.verdict]):
        lines += [str(f), ""]
    counts = {v: sum(1 for f in findings if f.verdict == v) for v in (MATCH, SELF_REPAIRING, CONTRADICTORY)}
    lines += [f"{counts[MATCH]} match, {counts[SELF_REPAIRING]} self-repairing, "
              f"{counts[CONTRADICTORY]} contradictory (of {len(findings)} checked).", ""]
    if counts[CONTRADICTORY]:
        lines += ["A contradictory count is a defect in the paper's reporting, not "
                  "necessarily in its science: the experiments may be exactly as run. "
                  "It does mean a reader cannot rebuild the model from the text.", ""]
    return "\n".join(lines)

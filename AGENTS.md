# AGENTS.md — operating rules for agents working in this bench

This file is the contract for any automated agent (or person) doing work in
`paper-repro-gym` or in a replication repository it produces. It is not a style
guide. Most of it exists because a specific failure already happened here, and
each rule names the failure it prevents.

**The bench's product is a trustworthy verdict, not a passing verdict.** Every
rule below follows from that. If a rule and a deadline conflict, the rule wins;
if a rule and a nicer-looking result conflict, the rule wins.

---

## 0. The five rules that are never negotiable

1. **Never edit a claim, tolerance, or its rationale after any result exists.**
   Not to fix a typo, not to correct a defect. If a registration is wrong,
   publish an erratum beside it and leave the original in place. A
   pre-registration that can be rewritten afterwards is worth nothing, and the
   reader cannot tell a good-faith fix from a retrofit.
2. **Never claim containment you did not provide.** A run on external compute is
   `boundary: external:<where>`. Never fabricate a contained run, never omit
   that a run left the box.
3. **Never publish the paper's artifacts.** Publish the reproducer's harness and
   the evidence. Weights, datasets, and the authors' code stay out, and
   `ACQUISITION.md` tells readers how to get them legitimately.
4. **Never publish a secret, a real email, or a host path.** The scanner refuses
   the build; do not work around it. Uploading to third-party compute counts as
   publishing (see §4).
5. **Never report a result you did not observe.** No estimated numbers, no
   "expected" values presented as measured, no filling a gap with a plausible
   figure. A missing metric is INCONCLUSIVE — absence is not failure, and it is
   certainly not success.

---

## 1. Registering claims

- **Quote verbatim.** Every claim carries the paper's own words. Open the paper
  and copy them. Never reconstruct a quote from an abstract, a citation, a blog
  post, or memory. *A fabricated quote poisons a pre-registration permanently* —
  and unlike a wrong number, nobody downstream can detect it.
- **Register the claim's direction.** Papers say "more than X", "up to X",
  "between X and Y". Use `tolerance_kind` of `lower_bound` / `upper_bound` /
  `interval`. Scoring a one-sided claim two-sided invents a bound the paper never
  stated — this happened here, in the bench's own first published replication
  (see that repo's `lenet/ERRATUM.md`).
- **Derive tolerances by rule, not judgement** — `tolerance.derive()`. Record the
  rule that fired on the claim. A hand-set tolerance is allowed when a claim
  genuinely needs one, but `tolerance.audit()` will flag it, and it must be
  justified in writing. The objection this answers is *"you chose tolerances that
  gave you the answer you wanted"*, and a published rationale does not answer it,
  because the rationale is also yours.
- **Mark structural claims `deterministic=True`.** A parameter count or a pruning
  schedule has no measurement noise; an empirical tolerance would let almost any
  implementation pass, which is backwards — structural claims are exactly the ones
  that must be tight.
- **Register predicted failures explicitly.** If the arithmetic says a claim will
  miss, say so publicly *before* running. A failure predicted in advance is
  evidence; the same failure explained afterwards is a story.

## 2. Scoring and reporting

- **Underpowered ≠ refuted.** If the uncertainty band is wider than the claimed
  effect (`tolerance.is_underpowered`), the run did not test the claim. Report
  INCONCLUSIVE. Reporting it as NOT_REPRODUCED asserts evidence against a paper
  that the data does not contain.
- **Structural claims gate empirical ones.** Do not call an empirical miss a
  *paper defect* unless every structural claim in that arm passed. Structural
  passes are what distinguish "the paper is wrong" from "our harness is wrong".
- **Never attribute a miss to the paper when the protocol was under-specified.**
  If the paper does not state its preprocessing and ours disagrees, that is our
  assumption, and it is reported as ours.
- **Publish the negative result with the same prominence as the positive.** A
  NOT_REPRODUCED is the bench working, not the bench failing.
- **Correct your own errors in public.** When a defect is found in this bench's
  own work, publish it — including when it changes nothing. A defect that happens
  to be harmless is the easiest kind to quietly drop, and dropping it is the
  habit that eventually hides a harmful one.

## 3. Running

- **Calibrate before committing quota.** Measure cost on the real accelerator and
  project, before spending a budget you cannot get back. A 12 h session cap means
  a run sized wrong yields *nothing*, not partial results.
- **Assert the hardware you asked for.** A kernel can request a GPU and be
  scheduled on a CPU image; pin the accelerator and assert it on line one. Silent
  wrong-hardware runs produce numbers that look fine and are not.
- **Write results incrementally.** Flush `metrics.json` after every stage, so a
  run killed at the cap still yields scoreable evidence.
- **Never silently retry a failed run.** A deterministic harness that failed once
  fails again; retry loops quietly drain a weekly quota. Surface the failure.
- **Log what was skipped.** If coverage was bounded — seeds dropped, rungs cut,
  a dataset excluded — say so in the output. Silent truncation reads as full
  coverage.

## 4. External compute

- **Uploading is publishing.** A payload sent to third-party compute is out of
  your control on arrival. It passes the same secret scan as a public repo
  (`gym remote preflight`). No exceptions for "it's a private notebook".
- **The gym never handles provider credentials.** Auth is delegated wholly to the
  provider's CLI and its own credential store. Never read, copy, log, or embed a
  token. The bench cannot leak what it never holds.
- **Results come home through `import-run`**, which records the external boundary
  honestly.

## 5. Deriving use cases for a published replication

Every published replication should carry a `USE_CASES.md` — up to five things the
work is useful for that the original authors did not write it for. This is where
a replication earns value beyond confirming or denying one number.

Rules for that file, in order of importance:

1. **Label them as suggestions, not findings.** These are *unvalidated proposals*
   extrapolated from the replication's artifacts. The replication tested the
   paper's claims; it did **not** test these use cases. Say so at the top of the
   file, in plain language, not buried in a closing caveat.
2. **Each use case cites evidence that exists in the repo.** Point at the table,
   the metric, the control. A use case with nothing to point at is marketing.
3. **Distinguish procedures from measurements.** A *procedure* ("use the
   reinitialisation control as a CI canary") transfers to other setups. A
   *measurement* ("79% of weights are removable") is a number from one
   architecture, one dataset, one set of hyperparameters, and transfers to
   nothing without re-running. Mark which is which.
4. **Do not imply the original authors endorse them.** The paper was written for
   its own question.
5. **Fewer real ones beat five padded ones.** Three with evidence is a better
   file than five where two are invented.

## 6. Standard flow

```bash
# 1. REGISTER  — claims.json with verbatim quotes, directions, derived tolerances
#                Commit and PUBLISH this before any run exists.
gym --workdir $EXP/.gym approve $EXP && gym --workdir $EXP/.gym sign $EXP

# 2. CALIBRATE — measure real cost on the real hardware; size against the budget
gym remote preflight --path <payload>          # uploading is publishing

# 3. RUN       — contained locally, or externally with an honest boundary
gym --workdir $EXP/.gym run $EXP --require-hardened
gym --workdir $EXP/.gym import-run $EXP --metrics metrics.json --ran-on "Kaggle (T4)"

# 4. SCORE     — mechanical, against the registration; worst claim sets the verdict
gym --workdir $EXP/.gym bundle $EXP <run_id>

# 5. VISUALISE — figures and tables from the evidence (see report.py)
gym --workdir $EXP/.gym figures <bundle>

# 6. PUBLISH   — evidence + harness, secret-scanned, citing the original authors
gym publish "$B" <dest> --replication --paper-id ... --authors ...
gym publish-hf "$B" <dest> --repo-id ... --replication

# 7. CLEAN     — the published repos are the source of truth
gym clean $EXP --purge
```

**One repository per paper.** Not per experiment, per architecture, or per
dataset. Splitting a replication across repos makes the claim ledger harder to
read than the paper it checks.

## 7. When you are unsure

- Unsure whether a quote is verbatim → **open the paper**. Do not guess.
- Unsure whether a miss is the paper's fault or yours → **it is yours** until a
  structural claim says otherwise.
- Unsure whether to publish an inconvenient result → **publish it**.
- Unsure whether a tolerance is defensible → **derive it by rule**.
- Unsure whether something counts as a secret → **it does**.

The bench is only worth running if its verdicts can be trusted when they are
inconvenient. Everything above is in service of that one property.

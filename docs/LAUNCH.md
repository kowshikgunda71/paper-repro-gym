# Launch & growth plan

Working notes for taking paper-repro-gym from "a repo that exists" to a project
other people use and contribute to. Honest about what's real: right now the tool
works and has **zero published reproductions**. Nothing below should be posted
until that number is at least 3.

---

## 0. The one thing to do before any promotion

**Publish three real reproductions, and make at least one of them a failure.**

A reproducibility tool with no reproductions is a README. Three bundles — ideally
one `REPRODUCED`, one `PARTIAL` or `NOT_REPRODUCED`, one where the council
returned `DISPUTED` on your own work — turn every claim below from a pitch into a
demonstration. The negative result is the most valuable of the three: it proves
the tool can produce an answer you didn't want, which is the entire premise.

Pick papers where the artifact is small, public, permissively licensed, and
CPU-runnable. `docs/COMPUTE.md` and `docs/DATASETS.md` already cover sourcing.
Publish each with `gym publish` into its own repo, then `gym index ~/repro-repos
--write INDEX.md` for the board.

**Do not skip this step to post sooner.** The failure mode for this project is a
launch that gets attention pointing at an empty bench.

---

## 1. Positioning

One sentence: **paper-repro-gym is a workbench that makes reproducing a paper
mechanical and hard to fool yourself with — you register the tolerance before the
run, the run is contained and hash-bound, and the verdict is computed, not
eyeballed.**

The wedge, stated plainly: benchmarks like PaperBench and CORE-Bench grade
*agents* against curated papers. This grades *a reproduction* of any paper, and
it's the only tool doing pre-registration for computational reproduction — OSF
and AsPredicted do preregistration for study designs, not for "here is the exact
tolerance, hash-bound to the exact command, signed before I ran it."

Three audiences, different hooks:

| Audience | What they care about | Hook |
|---|---|---|
| ML researchers / PhD students | Reviewer 2 asks "did you actually check?" | An evidence bundle you can link in a rebuttal |
| Reproducibility & open-science community | Rigor, negative results, artifact badging | Pre-registration + ACM-aligned vocabulary |
| Agent/eval engineers | Agents that grade their own work | A judge that structurally cannot certify |

---

## 2. LinkedIn post

Post this **after** the three reproductions exist, and swap the bracketed parts
for the real paper and the real number. A concrete failure is what makes it land;
a generic version of this post is noise.

> I tried to reproduce a paper last month and caught myself doing something
> embarrassing.
>
> I ran the authors' code, got 0.87 where the paper said 0.91, thought "close
> enough, probably a seed thing," and moved on.
>
> That's not reproduction. That's negotiation. And I only noticed because I'd
> written the number down before the run.
>
> So I built the thing that stops me doing it: **paper-repro-gym**, an open
> workbench for reproducing research results.
>
> The core idea is one rule — you commit to what counts as success *before* you
> see the output:
>
> → You register the claim and the tolerance up front
> → That gets signed, hash-bound to the exact artifact and the exact command
> → The run happens in a locked-down container — no network, no capabilities,
>   non-root, capped
> → The verdict is computed against the tolerance you registered. Not eyeballed.
>
> Change one input byte, one flag, or the sandbox policy, and the signature
> breaks and the run is refused. You cannot move the goalposts after seeing the
> score, because the goalposts are cryptographically stapled to the run.
>
> Then there's the part I actually think is the interesting bit.
>
> A tolerance check is narrow. It can't see that you read the metric off the
> wrong split, or set a tolerance so wide nothing could fail, or that the harness
> quietly hard-coded the expected value. So the last stage is an adversarial
> council: five reviewers, each attacking the evidence from a different angle,
> then a judge that has to argue *against* the panel before ruling on it.
>
> With one constraint that took me a while to get right:
>
> **The council can dispute a reproduction. It can never certify one.**
>
> Not as a prompt instruction — structurally. The judge has no field in which to
> write a verdict, and the code never opens the results file for writing. An LLM
> panel can raise doubt about a number. It must never be able to manufacture one.
> Every objection also has to name a concrete check that would settle it; an
> objection nobody can run is an opinion, and opinions don't belong in an
> evidence bundle.
>
> First results are up. [N] papers, and [the honest breakdown — e.g. "one
> reproduced cleanly, one came in outside tolerance, and on one the council
> disputed my own work and it was right"].
>
> Negative results are first-class here. "I could not reproduce this" is a
> finding, and hitting a resource cap reports FAILED_SAFELY rather than a false
> pass. The whole point is a tool that can tell you something you didn't want to
> hear.
>
> MIT licensed, Python, standard library for everything except the review stage.
> Issues and reproductions welcome — especially ones that break it.
>
> 🔗 github.com/kowshikgunda71/paper-repro-gym
>
> #reproducibility #openscience #machinelearning #research #opensource

**Why it's shaped like that:** it opens with a specific personal failure rather
than a product claim, states the mechanism concretely enough to be checkable,
and puts the most opinionated design decision (dispute-never-certify) at the
center. The credibility comes from admitting the council caught *you*.

**Mechanics.** Post Tue–Thu, 8–10am in your audience's timezone. Put the link in
the first comment if you want reach, in the post if you want the right readers —
prefer the post. Reply to every comment in the first two hours. Don't edit the
post in the first hour.

---

## 3. Other channels, in order of expected value

1. **A reproduction repo, not the tool repo.** Post the *finding* ("I could not
   reproduce X's Table 2 within the tolerance I registered beforehand — here's
   the bundle"), with the tool as the method. This is the highest-signal move and
   the one most likely to reach the reproducibility community.
2. **MLRC 2026 at NeurIPS.** The Machine Learning Reproducibility Challenge is
   an *official NeurIPS track* for the first time in 2026, and it explicitly
   accepts "tools and methods improving reproducibility" and "AI-assisted
   reproducibility research" — this project is squarely both. Route is via TMLR
   acceptance then nomination; the 2026 expression-of-interest window has closed,
   so target the next cycle and start the TMLR write-up now. Negative and partial
   results are explicitly welcomed there.
   <https://blog.neurips.cc/2026/05/04/mlrc-2026-reproducibility-as-an-official-track-at-neurips/>
3. **Hugging Face.** `gym publish-hf` already tags reproductions with the arXiv
   id, which links them from the paper's HF page. Free distribution to exactly
   the people reading that paper — publish every reproduction this way.
4. **Show HN.** Title: "Show HN: A workbench that makes you register your
   tolerance before reproducing a paper". HN will attack the LLM-judge stage;
   the dispute-never-certify constraint is the answer and it holds up. Have the
   three bundles linked in the first paragraph.
5. **r/MachineLearning**, `[P]` flair. Lead with the negative result.
6. **Papers With Code / ML Reproducibility community, OpenReview forums** for the
   specific papers you reproduced — post the bundle as a comment on the paper.
7. **Conference artifact-evaluation committees.** ACM badging (Available /
   Functional / Reproduced, policy v1.1) is the vocabulary this tool already
   speaks. AE chairs at CCS, SPLASH, FCCM, PADS are a small, reachable audience
   who need exactly this tooling.

---

## 4. Making it a real open-source project

The repo has MIT, SECURITY.md, CITATION.cff, and CI. What's missing before a
launch that expects contributors:

- [ ] **CONTRIBUTING.md** — how to run tests, the "containment, not sandbox"
      framing rule, and the one hard rule for PRs: nothing may weaken the A1 gate
      or let the council touch a verdict.
- [ ] **Issue + PR templates**, including a "reproduction report" issue type that
      asks for the bundle link.
- [ ] **CODE_OF_CONDUCT.md** (Contributor Covenant).
- [ ] **A tagged release** (v0.1.0) so people can cite a version, plus a Zenodo
      DOI — the project should be as citable as it asks reproductions to be.
- [ ] **`pyproject.toml`** so `pip install paper-repro-gym` works and `gym` is a
      real entry point. Right now every doc says `PYTHONPATH=src python3 -m ...`,
      which is friction at exactly the wrong moment.
- [ ] **A 60-second asciinema** of `gym demo` → `gym council` → `gym index`. The
      flow is the product; a wall of text isn't.
- [ ] **Pin the INDEX.md board** in the repo README once there are entries.
- [ ] **Good first issues**: add a reviewer lens to the council, a non-Python
      example experiment, an SPDX SBOM of the run image, N-run variance for
      statistical claims.

---

## 5. What would make this fail

Worth writing down so it can be checked against later.

- **Launching on an empty bench.** Covered above; it's the main risk.
- **The council reading as AI theater.** Mitigated by the structural constraint
  and by `falsifiable_check`, but it has to be *led with*, not buried. If the
  first thing someone learns is "it uses LLMs to review reproductions," the
  reaction is skepticism; if it's "the judge structurally cannot certify," it's
  interest.
- **Overclaiming the boundary.** The repo is careful to say containment, not
  sandbox. Any post that drifts into "sandboxed" burns the credibility the
  careful framing bought.
- **Nobody has a paper they want reproduced.** The realistic answer is that
  *you* reproduce papers publicly and consistently, and the tool follows the
  bundles. The tool is the byproduct; the reproductions are the product.

# Primer design: tool + eval plan

Status: **DRAFT, awaiting sign-off.** Nothing here is implemented.

A new domain capability: Munin designs PCR, cloning and sequencing primers
against a DNA template, and the result is machine-verified rather than asserted
by the model.

Sibling tracks: [`../paper-track/`](../paper-track/) for the benchmark numbers,
[`TODO.md`](TODO.md) for open agent-track items.

## 1. Why this is not just a prompt

The published evidence says an LLM cannot do this unaided, and says exactly how
it fails:

| Source | Finding |
|---|---|
| LABBench2 (arXiv 2604.09554) | Primer design and residue identification scored **lowest of all subtasks across every model**. The authors attribute it to needing exact string-level fidelity when pulling a subsequence out of a long sequence, and explicitly recommend dedicated sequence tools. Tool access was "a great equalizer". |
| Bioinfoysis re-grade (arXiv 2609.03871) | Dominant residual error: assembly primer pairs with **no valid overlap**. Plausible-looking, non-functional junctions. Assembly primers scored 1/7 against 14/19 for plain primer design. |
| Cureus 2023 (PMC10676229) | GPT-3.5/4 designing PCR primers de novo: only a handful of generated pairs could even theoretically yield a product. Sequence output and Tm estimates passable, **GC-content calculation notably unreliable**. |
| CRISPR-GPT (Nat Biomed Eng 2025) | Generated guide sequences matching no real genomic locus, mitigated only by a BLAST check. |

So the failure mode is not chemistry, it is bookkeeping: primers that do not
occur in the template, wrong strand, off-by-one coordinates, junctions that do
not join. The design follows from that. Every number the user sees comes from
deterministic code, and the tool verifies its own output by simulating the
reaction before returning.

## 2. Scope

**In scope for v1:** standard PCR and genotyping, Gibson assembly primers,
restriction-site cloning primers, sequencing and colony-check primers.

**Out of scope for v1:** qPCR and RT-qPCR (no exon-junction logic, no MIQE
constraints), bisulfite primers, multiplex panels, degenerate primers.

Sequence input is phased:

| Phase | Input | Why this order |
|---|---|---|
| v1 | User-pasted sequence | No egress, no accession-resolution failures, fully testable offline |
| v2 | NCBI / Ensembl accession lookup | The way users actually ask, but adds a whole class of wrong-transcript errors |
| v3 | Local reference FASTA on hugin | Unlocks genome-wide specificity properly |

## 3. Architecture

### 3.1 Engine: `backend/retrieval/primer/`

Pure Python, stdlib plus numpy. No GPL dependency ships.

| Module | Contents |
|---|---|
| `thermo.py` | SantaLucia 1998 unified nearest-neighbour table; salt corrections (SantaLucia, Owczarzy 2004/2008); von Ahsen 2001 Mg-to-monovalent conversion `K_mM = mono + 120*sqrt(Mg - dNTP)`. **Must accept IUPAC ambiguity codes** and report Tm as a range over the degenerate set (see layer 6c) |
| `structure.py` | Hairpin, homodimer, heterodimer by DP alignment; 3' end stability |
| `rules.py` | The rule table as a config object, every threshold carrying its citation and a hard/soft flag |
| `select.py` | Candidate enumeration plus a weighted penalty score |
| `tails.py` | Gibson overlaps and restriction tails, per the NEB tables |
| `specificity.py` | BLAST+ subprocess, Primer-BLAST settings |
| `verify.py` | **In-silico PCR.** The tool's self-check and the eval's grader, one implementation |

These are published equations, which are facts rather than copyrightable code.
We implement them from the papers, not from Primer3's source.

**Pin one convention and document it.** The research turned up that
primer3-py 2.4.0 (2026-10-21) backported two fixes past Primer3 2.6.1: an
integer-division bug that dropped the length-dependent part of the Owczarzy
2008 Mg term (0.3 to 0.6 degrees on typical primers, several degrees on short
oligos), and three corrected mismatch-stack enthalpies. Expected values differ
by version, so the oracle pins one and says which.

### 3.2 Rule table

From the rules research. Thresholds live in `rules.py` with these sources
attached, and disagreements between sources are recorded as soft.

| Rule | Threshold | Source | Hard/soft |
|---|---|---|---|
| Length | 18-25 | Dieffenbach 1993 | soft |
| GC % | 40-60 | Dieffenbach, IDT | soft |
| Tm | 52-65 | various | soft |
| Pair delta-Tm | <= 5 as a ceiling, <= 2 preferred | IDT, Thermo | soft |
| GC clamp | 1-2 G/C in last 5, <= 3 | Premier Biosoft | soft |
| Mononucleotide runs | <= 4 | Premier Biosoft | hard above 5 |
| 3' end stability | dG of last 5 bases not too negative | Rychlik 1990 | soft |
| Dimer dG | >= -9 kcal/mol (IDT); Sigma says -5/-6 | IDT FAQ | soft, sources disagree |
| Hairpin/dimer Tm | <= 47 C | Primer3 convention | hard |
| Specificity | >= 2 mismatches in the last 5 bases at the 3' end against off-targets; ignore targets with >= 6 mismatches | Ye 2012 | hard |
| Gibson overlap | 15-20 nt for 2-3 fragments, 20-30 nt for 4-6; 20 beats 15 | NEB | hard minimum 15 |
| Restriction tail | 6 bp 5' of the site by default, per-enzyme table, AccI needs >= 13 | NEB cleavage-close-to-the-end | hard default 6 |

Note for implementers: Primer3's `PRIMER_MAX_SELF_ANY_TH` family (default 47)
are **structure melting temperatures in Celsius**, not dG thresholds. Do not
conflate them with IDT's -9 kcal/mol.

### 3.3 Tool surface

One tool, `design_primers`, with a `mode` parameter (`pcr` | `overlap_assembly` |
`restriction` | `sequencing`), following the `source` tool's mode pattern rather
than adding four entries to a registry that has deliberately been pruned.

**Mode naming, decided.** `gibson` is accepted as an alias for
`overlap_assembly`, because that is the word users and the model will reach for,
but **documentation and the canonical schema value use `overlap_assembly`**.
"GIBSON ASSEMBLY" is a registered trademark of Codex DNA (NEB brands its own as
"NEBuilder HiFi DNA Assembly"), so carrying the generic name as canonical keeps
the published artifact clean while the alias preserves discoverability. The
validator accepts both and normalises to `overlap_assembly`; a round-trip test
should pin that.

Wiring follows the `show_protein` precedent (commit `03512e4`): tool module,
`mcp/schemas.py` entry, `mcp/dispatchers.py` registration, `mcp/tools/__init__.py`
export, tests. Not a core tool; reachable through `tool_search`.

**The envelope is the load-bearing design decision.** Every call returns:

- the designed primers, with coordinates
- a `checks` block: every rule evaluated, with its value and pass/fail
- a `verified` block: in-silico PCR product count and size, plus the
  specificity verdict
- an explicit `outcome` (`designed` | `no_candidates` | `template_too_short` |
  `ambiguous_target` | `verification_failed`), the way `source` does

The model cannot report a success the tool did not confirm, and the eval reads
the same block the user does. This mirrors `compute`, which carries
`verify_level` in its envelope instead of trusting the model's word.

A resolver agent (natural-language target to coordinates, "the kinase domain of
human BRAF") is **deferred** until accession lookup exists in v2, since that is
where it becomes necessary rather than decorative.

### 3.4 Specificity: BLAST+, Primer-BLAST settings

BLAST+ is US-government public domain, so it ships without license friction.
Ye et al. 2012 (BMC Bioinformatics 13:134) documents the settings: word size 7,
expect value 30,000, followed by Needleman-Wunsch global realignment, detecting
targets with up to 35% mismatches.

When no reference database is available the tool says so in the envelope rather
than silently reporting an unchecked design as specific. That disclaimer becomes
an eval check of its own.

## 4. Eval, five layers

### Layer 1: thermodynamic unit tests (CI, hermetic, exact)

Expected values come from **Biopython's `MeltingTemp` doctests**, which are
permissively licensed and exact. For `CGTTCCAAAGATGTGGGCATGAGCTTAC`:

| Call | Expected |
|---|---|
| `Tm_Wallace` | 84.00 |
| `Tm_GC` | 58.97 |
| `Tm_NN` defaults | 60.32 |
| `Tm_NN` saltcorr 1-7 | 54.27, 54.02, 59.60, 60.64, 60.32, 59.78, 59.78 |
| `Tm_NN` Na 50, Tris 10 | 60.79 |
| `Tm_NN` Na 50, Tris 10, Mg 1.5 | 67.39 |
| `Tm_NN` Na 50, Tris 10, Mg 1.5, dNTP 0.6 | **66.46** at the default correction; the 66.04 first recorded here is its `saltcorr=7` |
| `salt_correction(Na=50, method=1)` | -21.60 |

**Layer 1b has now run, and layer 1 is validated. 2026-11-13.** With primer3,
BLAST+ and Biopython in the CI image, `thermo.py` was compared against three
independent implementations and shows **no discrepancies**:

| Oracle | Comparison | Worst disagreement |
|---|---|---|
| Biopython `Tm_NN` | 20 oligos x 2 salt corrections, 6 ion buffers | **0.0000 C** |
| primer3 `oligotm` | same 20-oligo panel | 0.005 C |
| primer3 `ntthal` | duplex Tm | 0.140 C |
| primer3 `ntthal` | duplex dH | **exactly 0 cal/mol on all 20** |

So the parameter table, initiation terms, concentration convention and both
salt formulas are independently confirmed. The open question about Biopython's
method numbering is **settled, and our labels were right**: our
`santalucia1998` is its `saltcorr=5` and our `owczarzy2004` is its `saltcorr=6`
(and 7, since the Owczarzy 2008 decision tree collapses onto 6 with Na+ only).
Declining to tune constants toward a half-remembered label was the correct call.

*Interop trap worth keeping on record: for a SELF-COMPLEMENTARY oligo, primer3
reads `-d` as the TOTAL strand concentration while we and Biopython use
`dnac1`. The aligned mapping is `tm_nn(dnac1=2C)` against `oligotm -d 2C`; the
natural-looking `dnac1=C` reads every palindrome 0.62 to 1.66 C low. This is
not a symmetry-term difference: both implement SantaLucia's palindrome
treatment.*

**Known limitation in `structure.py`, in the unsafe direction.** The differential
run found our structures are systematically less stable than `ntthal` (mean
offset: hairpin +1.58, heterodimer +1.55, homodimer +2.14 kcal/mol, less stable
in 43 of 53 cases). Two causes, both understood:

1. The hairpin loop penalty is 3.6 to 4.0 kcal/mol too destabilising. On a
   shared test case both tools pick the *same* 8 bp stem and 4 nt loop, which
   isolates the gap to the loop term and quantifies the module's own flagged
   unverified constant `HAIRPIN_LOOP_DG37_REF`.
2. `MAX_INTERNAL_LOOP = 4` stops a dimer chaining two helices across a longer
   internal loop. Confirmed by bisection: both arms pair at a loop of 4 or
   fewer and only one arm at 5 or more, so this is the documented cap behaving
   as designed rather than a bug. It still costs 16 kcal/mol on a realistic
   palindromic molecule.

**Why this matters for the rule table:** `rules.py` treats dimer and hairpin
checks as HARD. Underestimating structure stability means passing primers that
should fail, which is a false negative on a safety check, the worse direction.
Until a verified internal-loop energy table lands, structure-based rejections
are trustworthy but structure-based *acceptances* are weaker than they look.
The gap is pinned in a `KNOWN_STRUCTURE_GAPS` registry whose test asserts the
gap is still present and prints a loud notice if it closes, so it cannot
quietly become an excuse list.

**License note, important:** Primer3's `test/oligotm.txt` and
`thal_input`/`thal_output` are GPL-licensed test *data*. We do not copy their
expected values into this Apache-2.0 repo. We generate our own expected values
from the vendored binary at CI time instead (layer 1b), which is a different
act from redistributing their files.

### Layer 1b: differential oracle against primer3 (CI only, never shipped)

Per decision, the CI test image
(`backend/scripts/ci/retrieval-tests.Dockerfile`) vendors the `primer3_core`
and `ntthal` binaries plus BLAST+. GPL obligations attach to distribution, and
the CI image is not distributed; the production image carries neither. Tests
assert our Tm and dG agree with `ntthal` within a tolerance, and that whole
designs agree with `primer3_core` on the same input.

The hermetic tier runs `--network none`, so both binaries and a small vendored
FASTA must be baked into the image. This is the one piece of CI work with real
weight in it.

**This approach is explicitly blessed upstream.** Primer3's web help section 4,
"FAIR USE OF PRIMER3", says verbatim: *"If you only call Primer3 from your
software and interpret its output, you can use any license you want for your
software."* Calling the binary and interpreting its output is exactly what
layer 1b does.

**Two rules that follow, and they are not optional.** Primer3's own test files
are GPL-2.0 with no carve-out for `test/` (Debian's copyright file for 2.6.1-6
maps `Files: *` to GPL-2+, and the inputs carry no per-file header), and
primer3-py vendors about 70 of the same fixtures under the same license.

1. **Do not transcribe their expected values.** Generate ours by running the
   binary.
2. **Do not reuse their test inputs either.** Fetch our own templates from
   GenBank. The DNA strings are facts, but the selection and arrangement of
   primer3's test corpus is plausibly protectable, so we take neither half.

Goldens are then committed under our own Apache-2.0 header with the
regeneration command recorded beside them.

*What we are giving up by doing this: primer3's corpus contains named cases that
map onto our modes almost exactly (`primer_overhang_input` and
`primer_overhang_th_input` are tailed primers for assembly and restriction work,
plus `primer_must_overlap_point_input`, `primer_mispriming_*`,
`primer_start_codon_input`). We re-derive equivalents rather than copy them.*

*Open item: confirm with counsel before release that a non-distributed CI image
is the clean reading. The plan does not depend on it; layer 1 alone still
tests the thermodynamics, just less thoroughly on the structure side.*

**Second oracle, for assembly rather than thermodynamics: pydna**
(github.com/BjornFJohansson/pydna, **BSD-3-Clause**, so Apache-2.0 compatible
and shippable if we ever want it). pydna simulates Gibson assembly, homologous
recombination and restriction cloning, and has its own `primer_design`. That
covers exactly the modes `primer3_core` does not, and it is the differential
oracle for `tails.py`.

**NEB's tools cannot serve as oracles.** Every neb.com HTML host returns
HTTP 403 behind a Cloudflare security check to automated clients, so neither
NEBuilder nor the NEB Tm Calculator can be called from CI. NEB's static media
files (the pUC19 and pBR322 GenBank flat files) do fetch, but their sequences
are also in GenBank, so we take them from NCBI and the licensing question
disappears. NEB's own design rules are text and quotable; its worked Gibson
examples are **inside figure images**, with no ACGT string anywhere in the
manual's extracted text, so there is no machine-readable NEB example to test
against.

### Layer 2: property tests (hypothesis)

These target the documented failure class directly:

- every returned primer occurs verbatim in the template or its reverse complement
- the predicted amplicon equals the sequence actually extracted between the primers
- reverse complement is an involution; Tm is monotonic in GC at fixed length
- Gibson overlaps are exact junction matches between the fragments they join
- restriction sites are present, with adequate spacer per the NEB table
- a sequencing primer sits at the requested offset and reads toward the feature

### Layer 3: golden fixtures

Graded by the oracle in `verify.py`, **not by string equality**. Bioinfoysis
showed exact-match grading undercounts valid designs; LABBench2 grades
open-ended primer answers by running an in-silico PCR on the model's own
primers, which is the shape we copy.

**Fixture shape: (GenBank accession, exact primer variant, expected amplicon),
with the binding site computed by aligning the primer to the fetched sequence
rather than copied from anywhere.** This is the single most useful thing the
fixture research produced, for three reasons: it sidesteps a licensing problem,
it dodges a coordinate trap, and it makes each fixture self-verifying.

Vectors, verified against NCBI:

| Vector | Accession | Length |
|---|---|---|
| pUC19 | **L09137.2** | 2,686 bp |
| pBluescript II SK(+) | **X52328.1** | 2,961 bp |

GenBank records carry no copyright claim, so accession plus sequence is the
clean vendorable layer. **pET-28a(+) has no GenBank accession** that could be
found: vendor maps say 5,369 bp, one source says 5,365 bp, and the circulating
files have blank ACCESSION fields. Not a citable fixture, so it is dropped
rather than fudged.

Two traps this design avoids:

- **Annotation coordinates are file-dependent.** SnapGene places M13 forward at
  pUC19 379-395 and reverse at 465-481; a NovoPro pUC19 file places them at
  complement(317-333) and 231-247. Hard-coding either would bake in one
  vendor's numbering. Aligning instead gives one answer.
- **Vendors disagree on the universal primer sequences themselves.** M13/pUC
  forward circulates as the 24-mer `CGCCAGGGTTTTCCCAGTCACGAC` (Promega) and the
  23-mer `CCCAGTCACGACGTTGTAAAACG` (Thermo), and M13 forward also exists as -21
  (`TGTAAAACGACGGCCAGT`) and -40 (`GTTTTCCCAGTCACGAC`). A core-facility page
  warns these variants do not all hybridize well with all vectors. **Every
  fixture must pin the exact variant and cite it to its vendor source.**

**Addgene's sequencing-primer list is not vendorable.** Its ~150 primers would
have been convenient, but the Terms of Use restrict content to informational
noncommercial use, bar reproduction for commercial purposes without written
consent, and prohibit automated extraction. The page also carries real data
quality problems (duplicate sequences under different names, a direction marked
"Reverse?", a row with merged sequence and description text), and Addgene does
not distribute primers, so the list is reference material rather than validation
evidence. Cite primer variants to their original vendor sources instead of
copying Addgene's compilation.

No NEB "validated primer set" list could be verified to exist. Treat as absent.

#### Verified golden fixtures

These binding sites and amplicon lengths were confirmed by exact string match
against the fetched GenBank records, circular-aware and on both strands. They
are ready to encode as fixtures:

| Template | Primer pair | Amplicon | Note |
|---|---|---|---|
| pUC19 L09137 | M13 Fwd(-21) x M13/pUC Rev | **123 bp** | the canonical case |
| pUC19 L09137 | M13 Fwd(-40) x M13/pUC Rev | **142 bp** | |
| pBluescript II SK(+) X52328 | T7 x T3 **20-mer** `ATTAACCCTCACTAAAGGGA` | **165 bp** | corrected 2026-11-13, see below |
| pBluescript II SK(+) X52328 | T7 x T3 **21-mer** `GCAATTAACCCTCACTAAAGG` | **168 bp** | the original table value |
| pGEM-3Zf(-) X65307 | T7 x SP6 | **103 bp** | **wraps the origin**, the circular edge case |
| pGEX-4T-1 U13853 | pGEX 5' x pGEX 3' | **173 bp** | |
| pEGFP-N1 U55762 | CMV Fwd x EGFP-N | **227 bp** | |
| pUC19 L09137 | iGEM VF2 x VR | **845 bp** | also origin-wrapping |

pUC19 MCS positions (lacZa 396..452) are all unique sites: EcoRI 396, SacI 402,
KpnI 408, SmaI/XmaI 412, BamHI 417, XbaI 423, SalI 429, PstI 435, SphI 441,
HindIII 447. NcoI and NotI are **absent**, which makes them good negative cases.

Three sharp edge cases fall out, all worth explicit tests:

- **The origin-wrapping amplicons** (pGEM T7/SP6 at 103 bp, VF2/VR at 845 bp)
  break any implementation that treats a circular template as a flat string.
- **M13 alias names collapse by 3' end.** M13 fwd(-20) and M13 Forward(-21)
  share 3' end 395 and differ by a single 5' base; (-40) and (-47) share 3' end
  375. The -20/-21/-40/-47 labels do **not** describe the 3' end. If the tool
  keys primers by 3' end, these aliases merge, and a test should say so.
- **Cross-reactivity fixtures for `specificity.py`.** `pGEX 3'` also matches
  pUC19 and pGEM-3Zf(-); `QE-for`, `pQE promoter`, `pBRforEco`, `Amp-R`,
  `L4440` and `Tac promoter` each match multiple unrelated backbones. The iGEM
  Registry additionally documents a real **mispriming case**: VR misprimes
  inside BBa_B0010/B0015 and yields a 183 bp artefact alongside the expected
  508 bp band. That is a ready-made negative control with a published outcome.

**T3 primer caveat, and the amplicon-size correction it caused.** The commonly
circulated T3 primer `GCAATTAACCCTCACTAAAGG` (21-mer) does **not** match the T3
genome NC_003298; only its 18-mer core does. The variant
`ATTAACCCTCACTAAAGGGA` (20-mer) matches both NC_003298 and pBluescript, so it
is the better general-purpose choice.

The two primers anneal at **different offsets on pBluescript**, so they give
different products, and the original single table entry of 168 bp conflated
them. Verified twice on 2026-11-13, once by `verify.py` and once by an
independent plain string search over X52328: T7 sits at 625..645, the 20-mer's
footprint at 770..790 giving **165 bp**, and the 21-mer's at 772..793 giving
**168 bp**. Both numbers are correct, for different reactions. The fixtures pin
both rather than choosing, which also makes the pair a regression test against
exactly this class of mix-up. The two pUC19 goldens reproduced exactly, which
is what confirmed the conventions were sound and the discrepancy real.

Note on provenance honesty: for M13 the originating papers (Heidecker, Messing
& Gronenborn 1980; Anderson et al. 1980) could not be retrieved, so the practical
authority is the vector record plus converging vendor catalogs rather than a
verified paper quote. T7, T3 and SP6 are better off: their sequences are
directly verifiable in NC_001604, NC_003298 and NC_004831, which is stronger
evidence than a citation. CMV-F, BGH-R, EF-1a and U6 have vendor-catalog
provenance only.

#### pydna's test corpus: the best ready-made fixture set found

`github.com/BjornFJohansson/pydna`, **BSD-3-Clause**, vendorable with the
copyright notice retained. Its `tests/` directory holds:

- **`primers.fas`, 43 KB, roughly 650 named primer records**, whose convention
  is **lowercase 5' tail, uppercase annealing region**
  (`taattaaTTTGTAATTAAAACTTAGATTAGATTGC`). That is precisely the structure
  `tails.py` emits, so this doubles as ground truth for tail-versus-anneal
  segmentation, which nothing else found provides.
- Annotated GenBank vectors (`pUC19.gb`, `pBR322.gb`, `lambda.gb`, pGEX family)
  and the pYPK/pCAPs yeast pathway-assembly series, which are real homologous
  recombination and assembly products shipped with their parts.
- A `broken_genbank_files/` directory, which is a free parser-robustness suite.

*Repo note: the canonical home is `pydna-group/pydna`. GitHub reports SPDX
NOASSERTION there, but `LICENSE.txt` is verbatim BSD-3-Clause.*

#### The best standard-PCR fixture found: rdmlpython's untergasser experiment

`github.com/RDML-consortium/rdmlpython`, `experiments/untergasser/`, **MIT**
repo-wide with no per-directory carve-out. This is design-versus-outcome ground
truth on a single known template, which is exactly the shape mode `pcr` needs:

| File | What it gives us |
|---|---|
| `primers_FSTL1.fa` (63 KB) | the template, human FSTL1 locus |
| `primers_evaluated.tsv` (271 rows) | Primer-BLAST output over that template: sequence, strand, length, start, stop, Tm, GC%, self- and 3'-complementarity, product length |
| `primers_used.tsv` (24 rows) | the subset actually ordered and wet-lab tested, with coordinates and the full amplicon sequence per pair, targeting 50/100/200/400/800 bp |
| `amplicon_primer_mix.rdml` + `_results.tsv` | raw and analysed qPCR, so **which pairs actually worked** |

The 271-row evaluated table is a direct cross-check for our scoring (their Tm,
GC and complementarity against ours on identical sequences), and the 24-row
tested subset plus results is a real outcome label. Sibling directories
(`experiments/vermeulen/`, `experiments/technical/`, melt-curve fixtures in
`test/`) extend it.

*Context on the format: RDML is a schema, not a repository. No public RDML
dataset repository exists, and RTPrimerDB is dead (last updated 2008, no
successor). RDML files do carry primer, probe and amplicon sequences as
first-class schema elements, but all of them are `minOccurs="0"`, so many
real-world files carry only amplification curves. There is no amplicon-length
field; derive it from the amplicon sequence.*

#### ARTIC schemes: not outcome labels, but the best coordinate fixtures

Earlier in this plan ARTIC is listed under rejected sources, which is correct
**as a pass/fail dataset** and wrong as a fixture source. The repo
(`artic-network/primer-schemes`, SPDX **CC-BY-4.0**) bundles per scheme a
`*.reference.fasta` (the template), a TSV of name/pool/sequence/length/GC/Tm,
and `*.primer.bed` / `*.scheme.bed` / `*.insert.bed` giving **exact 0-based
coordinates, strand and insert size**. So amplicon sizes are exact rather than
estimated, across nCoV-2019 V1 to V5.3.2, ZaireEbola V1-V3 and Nipah. Use it
for coordinate and amplicon-size fixtures, not for outcome labels.

`quick-lab/primerschemes` has 81 scheme versions over ~50 pathogens, but the
license is per-scheme: **69 are CC BY-SA 4.0 and only 12 are CC-BY-4.0**.
Filter to the 12; the ShareAlike majority would attach copyleft to our vendored
data.

### Layer 4: live agent scenarios

New entries in `backend/retrieval/evals/run_eval.py`, beside `pong` and `lov`.
Same shape: a prompt, plus `(name, predicate)` checks over a parsed SSE run,
reported as pass-rates over N runs, advisory rather than gating.

Checks:

- called `design_primers` rather than emitting sequences from its own head
- every sequence in the prose appears in the tool's output (the anti-fabrication
  check, aimed at the Cureus and CRISPR-GPT failure mode)
- the Tm quoted in prose matches the tool's `checks` block
- asked for clarification when the target region is ambiguous
- did not claim specificity when the envelope says it was unchecked
- refused cleanly when the template is too short

#### What layer 4 found on its first live run (2026-11-13)

Run against an isolated sidecar (`munin-retrieval-primer`, port 8083,
production image with the worktree's primer files bind-mounted read-only, its
own `CHATS_DB_PATH`), backbone `sage-a1.5`.

**Discovery is not the problem.** `tool_search` returns `design_primers` for
every phrasing tried ("design PCR primers", "primer design", "PCR", "amplify a
DNA sequence", "cloning primers Gibson"), and the capabilities block in the
system prompt already advertises it. An instrumented run confirmed the model
finds it: `tool_search` -> `design_primers` x3.

**The tool overflowed the context window.** The turn then died on a vLLM
`400 Bad Request`, the documented context-overflow signature, before producing
an answer. Measured cause: the envelope sent all 26 evaluated rules per
design, each with label, threshold, citation and detail.

| designs | envelope before | after |
|---|---|---|
| 1 | 2,663 tokens | **735** |
| 3 (default) | 7,524 | **1,748** |
| 5 | 12,379 | **2,755** |

At the default, three calls in one turn spent ~22,500 tokens on tool results
alone, against a 60,000 budget already holding a 600 bp sequence, the system
prompt and other tool output.

Fixed by sending `summary` plus only the checks that FAILED or could not be
computed, with a note saying the full list is in the widget. The honesty
contract is unchanged: `summary` still counts every rule, so the model cannot
claim a clean design while `hard_failures` or `not_computed` is non-zero. A
token-budget assertion guards the regression.

**Why this justifies the layer.** Every unit test passed, the tool was correct
in isolation, and the feature was still unusable in a real conversation. No
amount of offline testing would have surfaced it.

**Two things to carry forward:**

- A research-profile turn reached 174k cumulative prompt tokens, 378 s and 8
  tool calls. That is a backbone/profile cost issue independent of this tool.
- The grounding check passes VACUOUSLY when the answer contains no DNA, which
  is what an errored turn produces. It must only ever be read alongside the
  liveness checks (`no error SSE`, `non-empty final assistant content`), never
  alone. Observed: 2/2 "every DNA sequence grounded" on runs that produced no
  output at all.

### Layer 5: scored benchmark track

A `primer` track in `munin_bench`, with a committed scorecard and a
`certification_thresholds.json` entry, so a model or harness swap regresses
loudly. Two arms:

**Arm A, LAB-Bench SeqQA.** 320 public PCR items (8 subtasks x 40: restriction
primers, Gibson primers for linearized pUC19, enzyme selection, amplicon length,
pair selection). CC-BY-SA-4.0, machine-readable from the HF rows API,
deterministic multiple-choice grading. Caveats that must be honoured:

- public since July 2024, so near-certainly in training data. **Report tool-on
  versus tool-off delta, not raw accuracy.** Raw score is an upper bound.
- keep the canary string `lab-bench:5c41:...` in prompts
- drop or repair the 6 of 40 malformed items in the enzyme-selection subtask
  (duplicated ideal answer among distractors; inconsistent SstI reference)

**Arm B, our own templated held-out set.** Both LAB-Bench and LABBench2 are
templated (15 types x 40, 20 types x 20), so equivalents can be generated over
fresh accessions with `verify.py` as the oracle. Contamination-free, accepts any
valid design rather than one reference string. **This is the arm that belongs in
the paper.** Arm A is the cheap regression suite.

LABBench2's SeqQA2 (400 tasks) and CloningQA (14 tasks) are the best-matched
public benchmark, being open-answer and verifier-graded, but the HF repo is
gated and the cloning validator needs Go 1.21+. Deferred, not rejected.

### Layer 6: wet-lab calibration (real outcomes, not just self-consistency)

Layers 1 to 5 prove the engine is internally correct and that the agent uses it.
They do **not** prove our scoring predicts what happens at a bench. Two
permissively licensed datasets with real experimental labels close that gap, both
CC BY 4.0, both small enough to vendor, both with a published baseline number to
sit against.

**6a. Döring et al. 2019, amplification success/failure.**
Sci Rep 9:10748, doi:10.1038/s41598-019-47173-w. The data lives in two figshare
deposits under CC BY 4.0: `10.6084/m9.figshare.6736232` (feature matrix) and
`10.6084/m9.figshare.6736175` (raw scorer CSVs).

- `feature_matrix.csv`, 203 KB, **829 rows**: 20 primers x 47 IGHV templates
- label `Experimental_Coverage` in {`Amplified`, `Unamplified`}, 365 / 464,
  from a gel-electrophoresis majority vote of 5 human scorers
- carries `Primer_Sequence` plus precomputed features (mismatches, annealing dG,
  Tm, self-dimer dG, GC clamp, runs, repeats) we can cross-check our engine against
- the 10 small per-scorer CSVs (~38 KB total) give inter-rater disagreement, so a
  high-confidence stratum (all 5 scorers agreeing) falls out naturally
- published baseline: **AUC 0.953** (logistic regression on these features)

Test shape: feed each primer plus template to our amplification scorer, assert
AUC above a pinned floor; separately assert hard pass/fail on the unanimous subset.

*Caveat to record in the data README: the paper says 908 pairs after filtering,
the published matrix has 829 rows. Unexplained.*

*Scope caveat: these are forward IGHV primers against named templates, so this
validates primer-versus-template amplification scoring, not full pair design.*

**6b. SADDLE, primer-dimer ground truth.**
Nat Commun 13:1881, doi:10.1038/s41467-022-29500-4, CC BY 4.0. Supplementary
Data 1 (sequences), 2 (predicted dimer "badness") and 3 (**observed NGS dimer
read counts**), as 192x192 matrices over three primer sets.

- ~18,336 unique pairs per matrix, measured rather than predicted
- Supplementary Data 2 is a published predictor over the same index space, so we
  have both ground truth and a baseline to beat
- published baseline: **AUROC 0.9577**

Test shape: run our dimer predictor over each pair, assert ROC-AUC against
"dimer reads above threshold" stays above a floor.

*Blocker to resolve before use: the 192x192 matrices have no header row, so the
index order (f1,r1,f2,r2,... versus all-forwards-then-all-reverses) must be
confirmed against Supplementary Data 1 before the join means anything.*

**Explicitly rejected, with reasons**, so nobody re-litigates these later:

| Source | Why not |
|---|---|
| PrimerBank **the website** | The largest pass/fail collection in existence (~26,855 pairs tested, 82.6% success, so ~4.7k failures), but Copyright MGH, no bulk download, validation viewable only per-primer. Terms now read verbatim: free research use with acknowledgement, written permission needed to build a similar database or to include **more than 1000 pairs** in a commercial project. Scraping is out. **But see layer 6e: the 2008 murine validation paper is CC BY 2.0 and carries explicit negatives.** An email to bioinformatics@ccib.mgh.harvard.edu is still the clean route to more. |
| openPrimeR `.rda` files | GPL-2. The same Döring data is CC BY 4.0 on figshare; use that path. |
| primer3 / primer3-py test fixtures | GPL-2, and they are self-consistency regression fixtures with no wet-lab ground truth anyway. |
| PrimerAST | CC BY-NC-ND, no license on the repo, negatives are **synthetic** (primers deliberately pushed outside accepted ranges, not observed failures), and the CSV has **no primer sequence column at all**. Unusable. |
| Klindworth / SILVA | CC BY-NC, and the numbers are in-silico coverage against a 2013 SILVA version, not amplification outcomes. |
| Cordaro et al. 2021 | 290 benchtop PCRs across 6 labs, the most *representative* data found, but the repo has no license file (default all-rights-reserved). **Worth an email** to the CU Boulder authors; one sentence from being usable. |
| ARTIC primer-schemes | CC BY 4.0 but scheme definitions only, with no per-amplicon pass/fail table published as data. **Rejected as an outcome dataset, adopted as a coordinate fixture source**; see layer 3. |
| CDC 2019-nCoV RT-PCR panel | US federal work and therefore public domain under 17 USC 105, template NC_045512. Usable, with two caveats: the CDC Stacks rights field could not be fetched first-hand, and the document carries a regulatory restriction on reagents manufactured from these sequences, so **these fixtures must never be described as diagnostic-grade**. |
| qPrimerDB 2.0, BOLD | License unverifiable in both cases (qPrimerDB serves none and the NAR papers state no terms; BOLD has no license statement anywhere). Treat both as all-rights-reserved until established. |
| Addgene's primer table | Terms restrict content to informational noncommercial use and bar automated extraction, which Apache-2.0 cannot sublicense. Independently, it contains a **confirmed annotation error**: `hU6-F` is byte-identical to the table's own `pBR322ori-F` and matches pBR322-origin vectors rather than the human U6 gene, contradicting Addgene's own plasmid pages. Good reason to keep it out on quality grounds too. |
| SnapGene | Three independent blockers: maps and annotations are copyrighted, commercial use needs GraphPad permission, and the site terms forbid storing material in any retrieval system. |
| NCBI Probe database | **Retired.** The web interface was withdrawn in April 2020 and the URL now returns HTTP 410. The FTP archive is a 2012 freeze of probes and assay IDs, partly of proprietary origin, not primer pairs against a stated template. |
| PrimerCast (NeurIPS 2025 AI4Science workshop) | Announces 50,760 experimentally measured primer-target reactions with quantitative efficiency labels, which would be the best benchmark on this list. **The dataset is not released**: the MIT-licensed code at github.com/broadinstitute/PrimerCast loads it from an internal path. Re-check in 3 to 6 months, or email the Sabeti lab. |

**6c. Dryad eDNA specificity set, in-silico prediction versus bench.**
doi:10.5061/dryad.41ns1rnb7, **CC0-1.0** (no attribution obligation at all),
one 119,687-byte zip. Paper: So, Fong, Lam & Dudgeon, Ecosphere 2020;11(7):e03193.

**BUILT 2026-11-13, and deliberately NOT committed.** The dataset is not ours
to redistribute: the labels are CC0 but the primer sequences come from a
CC BY 4.0 paper, and the derived set is built from both. So it is **locally
reproducible instead of vendored**, which also respects
`backend/benchmarks/.gitignore`'s existing blanket `data/` rule rather than
carving the first exception into it.

What IS committed is the machinery:

| Path | Role |
|---|---|
| `backend/benchmarks/scripts/build_primer_edna_dataset.py` | the extractor; both input paths are required arguments with no defaults |
| `backend/benchmarks/tests/test_primer_edna_dataset.py` | 19 gold-value tests that SKIP when the data is absent |

```
python backend/benchmarks/scripts/build_primer_edna_dataset.py \
    --dryad <dir holding Table S1/S2/S3 .xlsx> \
    --paper-txt <pdftotext -layout output of the paper>
```

Both inputs are downloaded by hand because neither can be fetched
automatically: Dryad gates downloads behind OAuth and the publisher 403s
automated clients. The script's docstring carries the full acquisition
instructions including the archive checksum, since the generated README lives
under the gitignored `data/` and so is not in the repository.

A fresh clone therefore has a green suite with all 19 tests skipped, and
`MUNIN_PRIMER_EDNA_DIR` points them at the data once built. Verified: the
build is deterministic, reproducing three of four CSVs byte-identically on a
clean rebuild, with the fourth differing only in the two columns that the
optional `--templates-fasta` network step fills in.

**Downloaded and verified 2026-11-13.** Inner file sha256
`b205efb7b36b642e6f77c437a203908573ea70fe4fa2822551f87648aeabd7bd`, matching the
Dryad API, 119,687 bytes. Three xlsx files, all machine-readable, no figures:

| File | Contents |
|---|---|
| Table S1 | **105** GenBank accessions (species, abbreviation, taxa group, accession, gene), so **every template is recoverable from NCBI**. The 120 first recorded here was the spreadsheet row count, 15 of which are blank |
| Table S2 | the test matrix, primer pair against each tested species |
| Table S3 | **the labeled reactions**, two sheets |

Table S3 confirmed exactly as hoped, with **no missing values in any row**:

- `cPCRdata_results`: **679 rows, 38 distinct primer pairs**
- `qPCRdata_results`: **226 rows, 12 distinct primer pairs**
- per row: primer pair id, taxa group, locus, target species, tested species,
  sample id, 3' end stability, primer length, GC content, six mismatch
  complementarity counts (total / terminal / 3' end, each in CM and NCM form),
  template concentration, annealing temperature, and for qPCR also the chemistry
  (SYBR Green or TaqMan) and probe mismatch count
- **`PCR outcomes (amplified = 1)`** is the bench label
- **`Primer-BLAST prediction` carries TWO columns**, Scenario 1 and Scenario 2,
  the two stringency settings from the paper

**The headroom is large, which is the point.** Measured directly from the file:

| Arm | Baseline | n | Accuracy | FP | FN |
|---|---|---|---|---|---|
| cPCR | Primer-BLAST scenario 1 | 679 | **0.722** | 60 | 129 |
| cPCR | Primer-BLAST scenario 2 | 679 | **0.672** | 119 | 104 |
| qPCR | Primer-BLAST scenario 1 | 226 | **0.748** | 33 | 24 |
| qPCR | Primer-BLAST scenario 2 | 226 | **0.704** | 49 | 18 |

The two scenarios trade error types rather than improving: scenario 1 misses
real amplification 129 times, scenario 2 pushes false positives to 119. Neither
stringency is good, which is precisely the paper's argument and precisely the
risk our `specificity.py` inherits by adopting Primer-BLAST's settings.

**Where the signal lives.** All 93 cPCR and all 24 qPCR **on-target** rows
amplified, 100% both times, so they carry no information. Every bit of signal is
in the **off-target cross-species rows**, where **182 of 586 amplified anyway
(31%)**. So this is a cross-amplification benchmark: it asks whether a tool can
tell that a primer pair will promiscuously amplify a non-target species, and it
shows Primer-BLAST answering that question barely better than chance-adjusted
guessing.

**Primer sequences: resolved 2026-11-13.** Table S3 carries pair identifiers and
derived features but no ACGT, so the sequences had to come from the paper. Ten
automated routes all failed (six Wiley and ESA URLs 403, HKU Scholars Hub 403,
DOAJ 404, CORE 403, doi.org 302 into the blocked page), and the PDF was
downloaded manually. License **confirmed CC BY 4.0** via the Lingnan University
repository record.

**Table 1 holds all 38 primer pairs as selectable text**, forward above reverse,
with 3' end stability, min GC, max length, amplicon size and annealing
temperature. Table 2 holds the probe sequences for the 7 TaqMan assays.

Three integrity checks, all passing:

1. **The join key works.** `GA-CYTB-1`'s Table 1 features (3' stability 6.7,
   GC 55, length 20, Ta 55.5) match its Table S3 row exactly.
2. **The qPCR subsets reconcile.** Table 1 marks 5 pairs for SYBR Green and 7
   for TaqMan, totalling 12, which is exactly the 12 distinct pairs in S3's
   qPCR sheet. Table 2's "seven qPCR assays" are the TaqMan subset.
3. **36 of 38 pairs parse unambiguously.** Two adjacent rows interleaved in the
   PDF layout, leaving `GA-CYTB-3` with one sequence and `MA-CYTB-1` with three.
   The assignment is **forced by the published feature columns, not guessed**:
   of the three possible splits, only one satisfies both pairs' stated max
   length and min GC simultaneously. `GA-CYTB-3`'s reverse is
   `GGCGGTTATTATGACTAGAAGA` (22 nt, 40.9% GC, matching its published 40.9%),
   leaving `MA-CYTB-1` with the 21-mer and 24-mer (max length 24, min GC 33.3%,
   both matching). Confirm against the GenBank template before relying on it;
   S1 gives the accession and the alignment is the definitive check.

**A design requirement fell out of this data: 7 pairs carry IUPAC ambiguity
codes** (R, Y, M, S, K) in one or both primers, for example
`RARRGGRATTTTGTCSGKGT` in `SB-CYTB-3`. Degenerate primers are normal in eDNA
and barcoding work, so `thermo.py` and `verify.py` must handle IUPAC codes
rather than assume plain ACGT: Tm over a degenerate primer is a range, and
in-silico PCR must expand or match ambiguously. **This is now an engine
requirement, not an optional extra**, and it was invisible until the data was
opened.

**6d. PerformFISH validated primer atlas.** Zenodo doi:10.5281/zenodo.7271303,
**CC BY 4.0**, one 219 KB xlsx, roughly 350 pairs. Columns give pathway,
protein, gene, accession (so the template is recoverable), species, forward and
reverse primer, Tm, amplicon length, PCR efficiency and r-squared.

What makes it unusually good for a test suite is that **it ships failures**.
The validation status field is a four-level label whose bottom class is, verbatim:
*"STOP, primers reported in the literature but not appear to be specific when it
aligned in silico"*, above *"Confirmed in the Literature and 'in silico' but not
confirmed by PCR"*, then PCR-verified, then PCR-verified and sequenced. Most
primer collections publish only what worked.

**6e. PrimerBank's murine validation, via the paper rather than the website.**
doi:10.1186/1471-2164-9-633 (BMC Genomics 2008, PMC2631021) is **CC BY 2.0**,
which permits unrestricted reproduction. Its 17 additional files include the
tested-pair table with **explicit negatives** (five successful, five that failed
on gel, five that failed on BLAST), a revalidation of 96 failures, and sequenced
amplicons.

This partially reopens PrimerBank, which the rejection table below rules out as
a website. Two caveats: every additional file is a **PDF**, so extraction is
real work, and the paper's CC BY 2.0 sits in tension with the website's terms.
Use the paper as the legal basis and stay far below any bulk threshold. The
companion NAR paper (PMC2808898) is **CC BY-NC**, so it is off-limits.

**6f. Pink1 rat qPCR.** doi:10.1016/j.dib.2016.05.056 (Data in Brief,
PMC4909782), **CC BY 4.0**. Small but exceptionally clean: two JATS tables, no
PDF parsing, giving accession to primer to temperature to product length, plus
Sanger sequencing of the qPCR product with the aligned amplicon. A tidy
end-to-end fixture.

*Scalable harvest route for more of these, if we ever want them: Europe PMC's
REST API exposes a per-article license field and a `LICENSE:` filter, which
combined with `efetch db=pmc` and a regex over `<table-wrap>` captions matching
primer lets us gather primer tables in JATS with provable per-article
licensing. Data in Brief is a rich seam. PLOS also mirrors every table to
plos.figshare.com with its own DOI and a machine-readable CC BY 4.0 license
object; figshare's HTML 403s but its v2 API answers.*

Unexplored lead worth a look later: **eDNAssay** (Kronenberger et al., USDA
Forest Service), a published ML tool predicting qPCR cross-amplification. USDA
output is US-government public domain, and a model like that implies a sizeable
labeled cross-amplification training set behind it.

## 5. Build order

1. `thermo.py` + `structure.py` + layer 1 tests. Nothing else is trustworthy
   until these are.
2. CI image work: vendor primer3 and BLAST+, wire layer 1b.
3. `verify.py` in-silico PCR + layer 2 property tests. The oracle before the
   thing it grades.
4. `select.py` + `rules.py`, mode `pcr`. Layer 3 fixtures.
5. Tool wiring, schema, dispatcher. Layer 4 scenarios.
6. `tails.py`, modes `overlap_assembly` (alias `gibson`) / `restriction` /
   `sequencing`.
7. `specificity.py` + BLAST wiring.
8. Layer 5 benchmark track, arm B first.
9. Layer 6 calibration. Vendor in license tiers under
   `backend/benchmarks/data/primer/` with a citation README per source:
   - **tier 1, permissive, vendor directly:** rdmlpython `experiments/untergasser/`
     (MIT), pydna `tests/` (BSD-3-Clause)
   - **tier 2, vendor with attribution in NOTICE:** Döring feature matrix and
     SADDLE (CC BY 4.0), PerformFISH atlas, ARTIC schemes, the 12 CC-BY-4.0
     quick-lab schemes, CDC N1/N2/RP (public domain)
   - **tier 3, generate rather than copy:** primer3 goldens, per layer 1b
   Then wire the AUC floors.

Note that step 3's property tests and step 4's fixtures now have real expected
values to hit from day one (the amplicon table above), so the oracle can be
validated before anything depends on it.

## 6. Open items

- Counsel check on the CI-image reading of GPL (section 4, layer 1b).
- Resolve the SADDLE matrix index order before the dimer join is trusted.
- Two emails that would materially improve the benchmark: MGH for the PrimerBank
  validation table, and the Cordaro authors for an explicit license on their 290
  benchtop PCRs. Both are cheap asks with a high payoff.
- **Dryad zip: decided, user downloads, then wire layer 6c.** Awaiting the
  119,687-byte `ECS19-0803_Additional_supporting_information.zip` from
  doi:10.5061/dryad.41ns1rnb7 (browser button; the API needs an OAuth token).
  On arrival: confirm Table S3's column names and encoding, then build layer 6c.
  905 CC0 labeled reactions carrying a Primer-BLAST baseline column is the best
  specificity benchmark available to us.
- Check eDNAssay (USDA Forest Service) for a public-domain cross-amplification
  training set.
- PrimerBank's terms are now verified verbatim and are narrower than feared:
  free research use with acknowledgement, written permission required only to
  build a competing database or to include **more than 1000 pairs** in a
  commercial project. A 20 to 50 pair subset under an explicit grant is a
  realistic ask, which strengthens the case for the email above.
- One benchmark name remains ambiguous. One thread found the NeurIPS 2025
  workshop entry as **PrimerCast** (OpenReview GJaNhMEZGM, Sabeti lab, dataset
  not released); another refers to a **"PrimerAgent DB"** billed as an openly
  shareable primer and probe dataset. These may be two entries or one
  misremembered. Resolve before relying on either.
- MIQE 2.0 (Clin Chem 2025) criteria unverified. Only matters if qPCR comes
  back into scope.
- Frontend: whether a primer result deserves a widget like `ProteinViewer`, or
  whether a markdown table in the message is enough. Leaning table for v1.

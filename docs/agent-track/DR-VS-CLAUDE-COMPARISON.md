# Deep Research: Munin vs Claude Research — comparison and target format

> **Status (2026-07-25):** work items **R1-R5 all shipped** (report structure,
> wider funnel + snowball, contradiction synthesis, quantitative notes, and now
> the Recommendations section — R1-R4 in archived
> `done/AUTONOMOUS-SESSION-LOG.md`, R5 in `deep_research_agent.py`
> `_recommendations`). The report-format arc is complete. The remaining live
> item is the breadth question (Munin ~5 sources vs Claude ~50), which the
> corpus-coverage investigation in `TODO.md` settled as corpus-bounded, not a
> machinery defect.

Purpose: use the Claude Research reference (same prompt: membrane lipid
composition × kinase-inhibitor partitioning) — committed alongside this doc as
[`CLAUDE-DR-REFERENCE-REPORT.md`](CLAUDE-DR-REFERENCE-REPORT.md) to shape Munin's Deep Research
**report format** and **agent behaviour**. This is the target we build toward;
the recommendations at the end are the work items.

## 1. What Claude produced (the reference)

A single ~90-line report with a deliberate multi-layer structure:

| Layer | Content |
|---|---|
| **Title** | Specific and descriptive, not the raw question |
| **TL;DR** | 3 bolded headline claims, each already carrying a hard number + citation |
| **Key Findings** | 8 numbered findings, each a claim + inline citation (author, journal, year, vol, PMID) |
| **Details** | Three subsections that **mirror the question's facets** (Molecular Mechanisms / Methods / Efficacy & Resistance), each dense prose with dozens of distinct papers |
| **Recommendations** | 5 actionable items, each with a decision *threshold* |
| **Caveats** | 6 honest limitations: evidence-strength variation, named contradictions, an explicit "this table was not located and remains an open gap" |

Characteristics:
- **Breadth of sourcing:** ~40-60 distinct papers cited, by author/year/journal, many with PMIDs and exact figures (K ≈ 84,000 M⁻¹; IC50 1.4-2.3 µmol/L; LD50 256 nM).
- **Cross-source synthesis:** it *contrasts* sources rather than listing them — e.g. "anionic lipids increase binding for cationic species but *decrease* it for the neutral lipophile lapatinib," and it names the Skoupá 2019 counterpoint to lysosomal-trapping resistance.
- **Quantitative and specific** throughout; **honest about gaps and controversies**.
- **Verification caveat (important):** it says outright "existing values should be verified against primary PDFs" — i.e. these citations are model-recalled and some may be imprecise. That is the crack Munin's design is built to avoid.

## 2. What Munin produces today

```
# {question}
## {sub_question_1}
  <2-5 sentences drafted from notes, papers cited by short title>
## {sub_question_2}
  ...
## Sources
  1. [title](https://doi.org/DOI) _(read: full_text)_
  ...
---
N/M sub-questions resolved from K grounded notes; C sources cited.
```

Funnel per run (defaults): 2-5 sub-questions × (search → screen to ~12 → read up
to `read_cap=4` with `source(qa)`) → ~8-16 papers **actually read in full**, each
note carrying a **verbatim quote + DOI + read_depth**. Synthesis is per-sub-question
prose drafted from those notes.

## 3. Dimension-by-dimension

| Dimension | Claude | Munin today | Gap |
|---|---|---|---|
| **Report structure** | TL;DR + Key Findings + Details + Recommendations + Caveats | flat sections = sub-questions + Sources | large |
| **Sourcing breadth** | ~40-60 papers cited | ~8-16 read | large |
| **Sourcing depth per claim** | recalled citation, "verify against PDF" | **verbatim quote from a PDF actually read** | **Munin wins** |
| **Cross-source synthesis** | contrasts, names controversies | per-sub-question prose, no cross-linking | large |
| **Contradiction handling** | explicit ("Skoupá counterpoint") | none surfaced | medium |
| **Quantitative extraction** | numbers + PMIDs everywhere | single-sentence claim + quote | medium |
| **Citation style** | inline author/year/journal | short-title in prose + DOI list | medium |
| **Recommendations / Caveats** | yes, with thresholds | none | medium |
| **Honesty about gaps** | inside a rich report | "No supporting evidence" per sub-question | Munin blunter |
| **Provenance / auditability** | none (recalled) | **read_depth per citation, trace log, every claim → a read source** | **Munin wins** |
| **Corpus** | web/training | consortium corpus first, then OA/web | different mission |

## 4. Where Munin already wins (keep these)

1. **Grounding discipline.** Every note is a verbatim quote from a PDF `source`
   actually downloaded and read, tagged with `read_depth`. Claude's report is
   polished but self-admittedly needs its numbers verified against primary PDFs.
   Munin's per-claim trustworthiness is higher even where its breadth is lower.
2. **Provenance.** `read_depth: full_text|abstract` per citation, the durable
   event log, and the agent trace mean a reader can audit exactly which source
   produced which claim. Claude offers none of this.
3. **Honest abstention.** Munin says "no evidence" rather than confabulating — the
   right default, though it should be softened into a proper Caveats section
   rather than a blunt per-section line (see recs).
4. **The group's own corpus** is searched first, so contributed/unpublished work
   is citeable — a mission Claude cannot serve.

The goal is **breadth + structure up, grounding discipline unchanged.** Do not
trade quotes-from-read-PDFs for recalled-citation breadth.

## 5. Gaps to close — recommendations (prioritized)

### R1. Report structure (rendering; cheap, high impact)  — DONE
Reshape the document builder in `deep_research_agent.py` to emit the Claude-style
skeleton, populated only from grounded notes:
- **TL;DR** — 3-5 bold headline claims, generated from the highest-confidence
  notes (each must carry its quote's citation).
- **Key Findings** — numbered, one per strong note, claim + inline citation.
- **Details** — keep the per-sub-question sections (they already mirror the
  question's facets, like Claude's Details), but draft richer prose.
- **Sources** — already added; add author/year when available.
- **Caveats** — generated from `unresolvable` sub-questions + `thin_evidence`
  flags + `read_depth: abstract` citations ("X cited from abstract only"). This
  is where "no supporting evidence" belongs, as one honest section, not scattered.
This is a synthesis-prompt + template change; no new agent capability needed.

### R2. Widen the funnel (agent config + snowball)  — DONE
Claude's breadth comes from reading far more. Raise the ceilings for DR runs
(they are config, D9): more sub-questions, `read_cap` ~6-8, `screen_keep` higher,
and turn on **depth-1 snowball** (references of the strongest papers, back through
the screener). Now affordable because the **OA full-text lever** landed reads at
`read_depth: full_text` — more candidates are actually readable. Measure cost vs
resolution on the off-corpus eval before committing widths.

### R3. Cross-source synthesis + contradiction surfacing (D12)  — DONE
Two notes on the same `sub_question_id` with conflicting claims should be handed
to the synthesiser *together* with an instruction to name the split (the design
already specified structured notes `{claim, value?, unit?, quote, ref}` for
exactly this). This is what turns "a list of findings" into "a synthesis." Add a
lightweight contradiction pass over notes grouped by sub-question.

### R4. Quantitative extraction in notes  — DONE
Bias `source(qa)` toward pulling the specific number/unit (K, IC50, fold-change)
into the note's `value`/`unit` fields, not just a prose claim. The quote already
anchors it. This makes the report read like Claude's (hard numbers) while staying
grounded.

### R5. Recommendations section  — DONE (2026-07-25)
Final grounded synthesis pass (`_recommendations` in `deep_research_agent.py`)
over all confirmed notes, emitting 3-5 actionable recommendations, each a bold
imperative headline that names its supporting finding + source and gives a
concrete decision threshold where the evidence supports one. Sits between
Sources and Caveats. Gated on `_MIN_NOTES_FOR_RECS` (3) so thin runs skip the
section rather than pad it. Draws ONLY from notes (no new claims), same
grounding discipline as `_tldr`. Gate is unit-tested; prose validated live.

## 6. Mapping to the build

| Rec | Where | Type |
|---|---|---|
| R1 report structure | `deep_research_agent.py` document builder + `_synthesise_section` + a new TL;DR/Key-Findings/Caveats pass | rendering + prompts |
| R2 wider funnel + snowball | funnel-width config + a snowball step in the loop | agent config + feature |
| R3 contradiction synthesis | note grouping + a contradiction pass; needs the structured-note schema | agent feature |
| R4 quantitative notes | `source(qa)` prompt + note schema `value`/`unit` | prompt + schema |
| R5 recommendations | final synthesis pass | prompt |
| (have) inline progress | the durable event log already emits plan/tool/notes | done |
| (have) provenance | `read_depth`, traces, quotes | done |

## 7. One-line takeaway

Claude's report wins on **breadth, structure, and polish** (at the cost of
verifiability); Munin wins on **grounded, auditable per-claim trust**. The work is
to adopt Claude's *structure* (R1) and *breadth* (R2) and *synthesis* (R3) while
keeping Munin's quote-from-a-read-PDF discipline — i.e. make Munin's reports look
like the reference **without** loosening what makes each claim trustworthy.

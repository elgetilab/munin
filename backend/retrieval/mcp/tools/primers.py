"""
MCP tool: design_primers (deterministic primer design, with its own verdict).

One tool, four modes (plan section 3.3): `pcr`, `overlap_assembly`
(alias `gibson`), `restriction`, `sequencing`. The engine lives in
`backend/retrieval/primer/`; this module only calls it, shapes the envelope and
stores the viewer spec. Nothing here computes chemistry.

Why the envelope is the design
------------------------------
The published failure mode is bookkeeping, not chemistry: models emit plausible
primers that do not occur in the template, on the wrong strand, with junctions
that do not join (plan section 1). So every call returns:

  * `outcome`, one of designed | no_candidates | template_too_short |
    ambiguous_target | verification_failed | invalid_input, with a `detail`
    saying WHY when nothing came back;
  * `designs`, only ever designs the engine verified. A design that fails
    in-silico PCR, its assembly simulation or any HARD rule is dropped, not
    returned with a warning;
  * `checks`, every rule evaluated, with value, threshold, severity and source;
  * `verified`, straight out of `primer.verify`.

The model cannot report a success the engine did not grant, because a failure
returns no designs at all.

Why `detail` matters on an empty result
---------------------------------------
`select._cheap_reject` bounds the candidate search at the CONSTRAINT window
(length, GC, Tm) even though `rules.py` classes several of those as SOFT. That
is deliberate and documented there, but it means an empty result can mean "this
template has no window inside GC 40-60% here", not "this template cannot be
amplified". An empty `designs` list is otherwise mute, so a failed `pcr` call
re-runs enumeration to count what survived each stage and says so.

The widget artifact
-------------------
Following `structures.py`: the artifact holds a small JSON spec, not bulk data,
under `application/vnd.munin.widget+json`. The spec shape is a fixed contract
with the frontend renderer (`kind: "primer_design"`, `version: 1`); the richer
tool envelope is a separate shape and may carry more.
"""

from __future__ import annotations

import asyncio
import json
from dataclasses import replace
from typing import Any, Optional

from primer import rules, select, structure, tails, thermo, verify
from primer.rules import CheckResult, Constraints
from primer.thermo import SequenceError

from .artifacts import _require_persistent_conversation

WIDGET_CONTENT_TYPE = "application/vnd.munin.widget+json"
WIDGET_KIND = "primer_design"
WIDGET_VERSION = 1

#: Canonical mode names. `gibson` is accepted and normalised to
#: `overlap_assembly` by `rules.normalise_mode`: "GIBSON ASSEMBLY" is a
#: registered trademark of Codex DNA, so the generic name is canonical and the
#: familiar one is the alias (plan section 3.3).
MODES = ("pcr", "overlap_assembly", "restriction", "sequencing")
MODE_CHOICES = ("pcr", "overlap_assembly", "gibson", "restriction", "sequencing")

DIRECTIONS = ("forward", "reverse")

# Outcomes. The vocabulary is the plan's, plus `invalid_input`.
DESIGNED = "designed"
NO_CANDIDATES = "no_candidates"
TEMPLATE_TOO_SHORT = "template_too_short"
AMBIGUOUS_TARGET = "ambiguous_target"
VERIFICATION_FAILED = "verification_failed"
INVALID_INPUT = "invalid_input"

#: Hard cap on pasted sequence. Pair selection is O(forward x reverse), so a
#: whole chromosome would hang the worker rather than fail.
MAX_TEMPLATE_BP = 100_000

#: Above this, `pcr` mode needs a `target`: an untargeted search is quadratic
#: in template length and takes seconds already at 3 kb.
MAX_UNTARGETED_BP = 3_000

#: Fragment count for an assembly. NEB's published overlap table stops at 6;
#: `tails.recommended_overlap` carries the 4-6 row forward and says so.
MAX_FRAGMENTS = 12

#: The widget spec is a spec, not a data dump. A longer amplicon is truncated
#: and the truncation is stated in `notes`.
MAX_AMPLICON_CHARS = 4_000

#: How much of an amplicon the MODEL sees. It does not need the sequence to
#: report a design, and a few kB of ACGT per design is pure prefill.
AMPLICON_PREVIEW_FLANK = 40

MAX_DESIGNS = 5

# The rule ids `rules.evaluate_primer` produces, so the tail-specific checks a
# `TailedPrimer` carries can be told apart from the per-primer ones and the
# latter recomputed with structure values filled in.
_PRIMER_RULE_IDS = frozenset(
    c.rule for c in rules.evaluate_primer("ACGTACGTACGTACGTACGT")
)

_SCOPE_LABELS = {
    "forward": "forward primer",
    "reverse": "reverse primer",
    "primer": "primer",
    "pair": "pair",
    "junction_in": "upstream junction",
    "junction_out": "downstream junction",
    "cloning": "cloning",
}

# `computed` must be here. A rule whose input was never supplied carries
# passed=True so it cannot disqualify a primer, so without this field the
# renderer cannot tell "checked and fine" from "never checked" except by
# pattern-matching the prose in `detail`. That distinction is the most
# consequential one in the whole widget, and it must not ride on wording.
_WIDGET_CHECK_KEYS = ("rule", "label", "severity", "passed", "value",
                      "threshold", "source", "detail", "computed")
_WIDGET_VERIFIED_KEYS = ("outcome", "product_count", "product_sizes",
                         "single_clean_product")
_WIDGET_SUMMARY_KEYS = ("total", "passed", "hard_failures", "soft_failures",
                        "not_computed", "usable")

_NOT_COMPUTED_NOTE = (
    "Some checks report 'not computed' rather than a value; they are counted "
    "in summary.not_computed and must not be read as passes."
)
_SPECIFICITY_NOTE = (
    "Specificity was checked against this template only. No reference genome "
    "or vector backbone was searched, so off-target priming elsewhere in a "
    "real sample has NOT been ruled out."
)
_STRUCTURE_NOTE = (
    "Hairpin and dimer melting temperatures are computed on the full oligo, "
    "tail included; length, GC and Tm rules are judged on the annealing "
    "region, which is the part that binds on the first cycle."
)


# --------------------------------------------------------------------------
# Input handling
# --------------------------------------------------------------------------


class InputError(ValueError):
    """Bad arguments. The message is written to be shown to the model."""


def _clean_sequence(text: Any, label: str) -> tuple[str, str]:
    """
    `(name, sequence)` from pasted text.

    FASTA headers become the name, and digits are dropped so a GenBank ORIGIN
    block or a numbered alignment pastes without the user reformatting it.
    """
    if not isinstance(text, str) or not text.strip():
        raise InputError(f"{label} is required and must be a DNA sequence")
    name = ""
    kept: list[str] = []
    for line in text.splitlines():
        stripped = line.strip()
        if stripped.startswith((">", ";")):
            if not name:
                name = stripped.lstrip(">;").strip()
            continue
        kept.append("".join(ch for ch in stripped if not ch.isdigit()))
    try:
        seq = thermo.normalise("".join(kept))
    except SequenceError as exc:
        raise InputError(f"{label}: {exc}") from exc
    if len(seq) > MAX_TEMPLATE_BP:
        raise InputError(
            f"{label} is {len(seq):,} bp; this tool takes at most "
            f"{MAX_TEMPLATE_BP:,} bp. Paste the region of interest instead."
        )
    return name, seq


def _as_span(value: Any, label: str) -> Optional[tuple[int, int]]:
    if value is None:
        return None
    if isinstance(value, dict):
        value = [value.get("start"), value.get("end")]
    if not isinstance(value, (list, tuple)) or len(value) != 2:
        raise InputError(f"{label} must be a two-element array [start, end]")
    try:
        lo, hi = int(value[0]), int(value[1])
    except (TypeError, ValueError):
        raise InputError(f"{label} must be two integers") from None
    return lo, hi


def _as_int(value: Any, label: str, *, minimum: int, maximum: int,
            default: Optional[int] = None) -> int:
    if value is None:
        if default is None:
            raise InputError(f"{label} is required")
        return default
    try:
        out = int(value)
    except (TypeError, ValueError):
        raise InputError(f"{label} must be an integer") from None
    if not (minimum <= out <= maximum):
        raise InputError(f"{label} must be between {minimum} and {maximum}")
    return out


# --------------------------------------------------------------------------
# Checks, primers, designs
# --------------------------------------------------------------------------


def _scope(checks, scope: str) -> list[CheckResult]:
    """
    Namespace a rule list so a pair's checks stay distinguishable.

    `rules.py` returns the same rule ids for both primers of a pair, so a flat
    `checks` block would carry `primer_length` twice with different values.
    The id gains a `forward.` / `reverse.` / `pair.` prefix and the label gains
    a matching human prefix; nothing else changes.
    """
    human = _SCOPE_LABELS.get(scope, scope)
    return [replace(c, rule=f"{scope}.{c.rule}", label=f"{human}: {c.label}")
            for c in checks]


def _summary(checks: list[CheckResult]) -> dict:
    return rules.summarise(list(checks))


def _hard_failures(checks: list[CheckResult]) -> list[CheckResult]:
    return [c for c in checks if c.severity == rules.HARD and not c.passed]


def _primer_record(
    *,
    sequence: str,
    anneal: str,
    tail: str,
    strand: str,
    start: Optional[int],
    end: Optional[int],
    tm: Optional[float],
    gc: Optional[float],
) -> dict:
    """
    One primer, in the widget's primer shape.

    `start`/`end` are the ANNEALING footprint in template coordinates (0-based,
    half-open, plus strand, as everywhere in `primer/verify.py`). A 5' tail is
    not present in the template, so it has no coordinates and
    `end - start` is the annealing length, while `length` is the whole oligo.
    `display` is pydna's convention: lowercase tail, uppercase annealing region.
    """
    return {
        "sequence": sequence,
        "display": tail.lower() + anneal.upper(),
        "anneal": anneal,
        "tail": tail,
        "strand": strand,
        "start": start,
        "end": end,
        "length": len(sequence),
        "tm": None if tm is None else round(tm, 2),
        "gc_percent": None if gc is None else round(gc, 1),
    }


def _tailed_record(primer: tails.TailedPrimer, con: Constraints, *,
                   strand: str, start: Optional[int],
                   end: Optional[int]) -> dict:
    return _primer_record(
        sequence=primer.sequence,
        anneal=primer.anneal,
        tail=primer.tail,
        strand=strand,
        start=start,
        end=end,
        tm=_safe_tm(primer.anneal, con),
        gc=_safe_gc(primer.anneal),
    )


def _safe_tm(seq: str, con: Constraints) -> Optional[float]:
    try:
        if thermo.degeneracy(seq) != 1:
            return None
        return thermo.tm_nn(seq, **con.tm_kwargs())
    except (SequenceError, ValueError):
        return None


def _safe_gc(seq: str) -> Optional[float]:
    try:
        return 100.0 * thermo.gc_fraction(seq)
    except (SequenceError, ValueError):
        return None


def _amplicon_preview(amplicon: str) -> str:
    flank = AMPLICON_PREVIEW_FLANK
    if len(amplicon) <= 2 * flank + 3:
        return amplicon
    return f"{amplicon[:flank]}...{amplicon[-flank:]}"


def _design_record(
    *,
    name: str,
    forward: Optional[dict],
    reverse: Optional[dict],
    product_size: int,
    amplicon: str,
    checks: list[CheckResult],
    verified: dict,
    penalty: Optional[float] = None,
) -> dict:
    """The rich per-design record. `_widget_pair` trims it to the contract."""
    tm_f = (forward or {}).get("tm")
    tm_r = (reverse or {}).get("tm")
    tm_diff = 0.0 if tm_f is None or tm_r is None else round(abs(tm_f - tm_r), 2)
    return {
        "name": name,
        "forward": forward,
        "reverse": reverse,
        "product_size": int(product_size),
        "amplicon": amplicon,
        "tm_difference": tm_diff,
        "penalty": None if penalty is None else round(float(penalty), 3),
        "checks": checks,
        "verified": verified,
    }


def _envelope_design(record: dict) -> dict:
    """What the MODEL sees: everything but the raw amplicon sequence."""
    out = dict(record)
    amplicon = out.pop("amplicon", "")
    out["amplicon_preview"] = _amplicon_preview(amplicon)
    out["checks"] = [c.as_dict() for c in record["checks"]]
    out["summary"] = _summary(record["checks"])
    return out


def _widget_pair(record: dict) -> tuple[dict, bool]:
    """
    The contract shape, and whether the amplicon had to be truncated.

    Exactly the fields in the frontend contract and no others: the widget is a
    spec, and the tool envelope is where the extra detail lives.
    """
    amplicon = record["amplicon"]
    truncated = len(amplicon) > MAX_AMPLICON_CHARS
    verified = {k: record["verified"].get(k) for k in _WIDGET_VERIFIED_KEYS}
    checks = []
    for check in record["checks"]:
        as_dict = check.as_dict()
        checks.append({k: as_dict.get(k) for k in _WIDGET_CHECK_KEYS})
    pair = {
        "name": record["name"],
        "forward": record["forward"],
        "reverse": record["reverse"],
        "product_size": record["product_size"],
        "amplicon": amplicon[:MAX_AMPLICON_CHARS] if truncated else amplicon,
        "tm_difference": record["tm_difference"],
        "penalty": record["penalty"],
        "checks": checks,
        "verified": verified,
    }
    return pair, truncated


def _widget_spec(*, mode: str, template_name: str, template_length: int,
                 circular: bool, target: Optional[tuple[int, int]],
                 records: list[dict], notes: list[str]) -> dict:
    pairs = []
    truncated = False
    for record in records:
        pair, cut = _widget_pair(record)
        truncated = truncated or cut
        pairs.append(pair)
    all_checks: list[CheckResult] = []
    for record in records:
        all_checks.extend(record["checks"])
    full = _summary(all_checks)
    summary = {k: full[k] for k in _WIDGET_SUMMARY_KEYS}
    spec_notes = list(notes)
    if truncated:
        spec_notes.append(
            f"An amplicon longer than {MAX_AMPLICON_CHARS:,} bases was "
            "truncated in this view; product_size is the full length."
        )
    return {
        "kind": WIDGET_KIND,
        "version": WIDGET_VERSION,
        "mode": mode,
        "template": {"name": template_name, "length": template_length,
                     "circular": bool(circular)},
        "target": list(target) if target else None,
        "pairs": pairs,
        "summary": summary,
        "notes": spec_notes,
    }


def _result(outcome: str, *, mode: str, detail: str = "",
            records: Optional[list[dict]] = None,
            notes: Optional[list[str]] = None, **extra) -> dict:
    """The internal build result. `design_primers` turns it into the envelope."""
    return {
        "outcome": outcome,
        "mode": mode,
        "detail": detail,
        "records": records or [],
        "notes": notes or [],
        **extra,
    }


# --------------------------------------------------------------------------
# Mode: pcr
# --------------------------------------------------------------------------


def _pcr_regions(seq_len: int, target: Optional[tuple[int, int]],
                 pmax: int) -> tuple[tuple[int, int], tuple[int, int]]:
    if target is None:
        return (0, seq_len), (0, seq_len)
    return ((max(0, target[0] - pmax), target[0]),
            (target[1], min(seq_len, target[1] + pmax)))


def _pcr_diagnosis(seq: str, con: Constraints, target: Optional[tuple[int, int]],
                   pmin: int, pmax: int) -> str:
    """
    Why `design_pairs` came back empty, in terms a user can act on.

    `select._cheap_reject` bounds the SEARCH at the constraint window even
    where the RULE is soft, so an empty result frequently means the window was
    too narrow for this stretch of sequence rather than that no primer exists.
    Counting what survived each stage is the only way to tell the two apart,
    and saying nothing would leave the model to guess.
    """
    fwd_region, rev_region = _pcr_regions(len(seq), target, pmax)
    forwards = select.enumerate_candidates(
        seq, strand=verify.PLUS, constraints=con, region=fwd_region,
        evaluate_structure=False)
    reverses = select.enumerate_candidates(
        seq, strand=verify.MINUS, constraints=con, region=rev_region,
        evaluate_structure=False)
    window = (
        f"length {con.length_min}-{con.length_max} nt, GC {con.gc_min:.0f}-"
        f"{con.gc_max:.0f}%, Tm {con.tm_min:.0f}-{con.tm_max:.0f} C"
    )
    if not forwards or not reverses:
        missing = ("neither strand" if not forwards and not reverses
                   else "the forward side" if not forwards
                   else "the reverse side")
        return (
            f"No candidate primer could be enumerated on {missing}. The search "
            f"is bounded at the constraint window ({window}), and primer/"
            "select.py applies that window as a HARD boundary on the search "
            "even though rules.py treats length, GC and Tm as SOFT: a template "
            "stretch whose GC or Tm lies outside the window yields nothing "
            "here, and the empty result is a property of the search space, not "
            "proof that the region cannot be amplified. A GC-poor, GC-rich or "
            "repetitive region is the usual cause."
        )
    return (
        f"{len(forwards)} forward and {len(reverses)} reverse candidates passed "
        f"the constraint window ({window}), but no combination of them survived "
        f"pairing: every candidate pair either fell outside the "
        f"{pmin}-{pmax} bp product range, failed the pair Tm or primer-dimer "
        "rules, or did not give exactly one product when the PCR was simulated "
        "against this template. Widening the product range, or moving the "
        "target, is the usual fix."
    )


def _build_pcr(seq: str, *, target: Optional[tuple[int, int]],
               product_range: Optional[tuple[int, int]], circular: bool,
               n: int) -> dict:
    con = rules.constraints_for("pcr")
    pmin, pmax = product_range or (con.product_min, con.product_max)

    if len(seq) < 2 * con.length_min:
        return _result(TEMPLATE_TOO_SHORT, mode="pcr", detail=(
            f"The template is {len(seq)} bp and two primers of at least "
            f"{con.length_min} nt will not fit on it."))
    if not circular and pmin > len(seq):
        return _result(TEMPLATE_TOO_SHORT, mode="pcr", detail=(
            f"The smallest requested product is {pmin} bp but the template is "
            f"only {len(seq)} bp, so no product in that range can exist. Lower "
            "product_size_range, or pass circular=true if this is a plasmid."))
    if target is None and len(seq) > MAX_UNTARGETED_BP:
        return _result(AMBIGUOUS_TARGET, mode="pcr", detail=(
            f"The template is {len(seq):,} bp and no target region was given, "
            "so there is nothing to aim at and the search would be quadratic "
            f"in its length. Pass `target` as [start, end) for templates over "
            f"{MAX_UNTARGETED_BP:,} bp."))

    pairs = select.design_pairs(
        seq, constraints=con, target=target,
        product_range=(pmin, pmax), circular=circular, n=n)
    if not pairs:
        return _result(NO_CANDIDATES, mode="pcr",
                       detail=_pcr_diagnosis(seq, con, target, pmin, pmax))

    records: list[dict] = []
    for index, pair in enumerate(pairs, start=1):
        checks = (_scope(pair.forward.checks, "forward")
                  + _scope(pair.reverse.checks, "reverse")
                  + _scope(pair.checks, "pair"))
        if _hard_failures(checks):
            # select.py drops these already; refusing again costs nothing and
            # makes the guarantee local rather than inherited.
            continue
        forward = _primer_record(
            sequence=pair.forward.seq, anneal=pair.forward.seq, tail="",
            strand=verify.PLUS, start=pair.forward.start,
            end=pair.forward.end, tm=pair.forward.tm, gc=pair.forward.gc)
        reverse = _primer_record(
            sequence=pair.reverse.seq, anneal=pair.reverse.seq, tail="",
            strand=verify.MINUS, start=pair.reverse.start,
            end=pair.reverse.end, tm=pair.reverse.tm, gc=pair.reverse.gc)
        verified = dict(pair.verified)
        if not verified.get("single_clean_product"):
            continue
        records.append(_design_record(
            name=f"pair_{index}", forward=forward, reverse=reverse,
            product_size=pair.product_size, amplicon=pair.amplicon,
            checks=checks, verified=verified, penalty=pair.penalty))

    if not records:
        return _result(VERIFICATION_FAILED, mode="pcr", detail=(
            "Every candidate pair failed its own verification: the in-silico "
            "PCR did not return exactly one product of the predicted size. No "
            "design is reported."))
    return _result(DESIGNED, mode="pcr", records=records,
                   notes=[_SPECIFICITY_NOTE])


# --------------------------------------------------------------------------
# Tailed modes: shared machinery
# --------------------------------------------------------------------------


def _recheck_tailed(primer: tails.TailedPrimer, con: Constraints
                    ) -> list[CheckResult]:
    """
    The engine's tail checks, plus the per-primer rules re-run WITH structure.

    `tails.py` evaluates the annealing region without hairpin or self-dimer
    values, so those come back "not computed". They are computable, and a
    cloning oligo is exactly where a tail-driven hairpin bites, so they are
    computed here on the full oligo and the per-primer rule list is rebuilt.
    The tail-specific checks the engine produced (spacer length, site position,
    oligo length) are kept untouched.
    """
    extra = [c for c in primer.checks if c.rule not in _PRIMER_RULE_IDS]
    full = primer.sequence
    try:
        hairpin_tm = structure.hairpin(full).tm
        self_tm = structure.homodimer(full).tm
        end_dg = structure.end_stability(full).dg
    except (SequenceError, ValueError):
        hairpin_tm = self_tm = end_dg = None
    primer_checks = rules.evaluate_primer(
        primer.anneal, con, hairpin_tm=hairpin_tm, self_dimer_tm=self_tm,
        end_stability_dg=end_dg)
    return list(primer_checks) + extra


_TM_DIFF_RELAXED = (
    "SOFT here, not HARD: these are tailed cloning primers, so the annealing "
    "regions govern only the first cycle and the full oligos anneal "
    "thereafter. tails.py applies no pair rule in these modes, and the "
    "annealing region is pinned to the end of the fragment, so neither the "
    "designer nor the caller can act on this number the way they can in PCR "
    "mode. It is reported rather than enforced."
)


def _tailed_pair_checks(forward: tails.TailedPrimer, reverse: tails.TailedPrimer,
                        con: Constraints) -> list[CheckResult]:
    """
    Pair rules for two tailed primers, with two deliberate adjustments.

    Product size is switched off (`product_min/max = 0`, the same mechanism
    `rules.SEQUENCING` uses) because a cloning product is whatever the insert
    is; the PCR product-size preference is not a cloning constraint and would
    otherwise fail every insert over 1 kb on a HARD rule.

    The pair Tm difference is reported but downgraded to SOFT, for the reason
    spelled out in `_TM_DIFF_RELAXED` and carried in the check's own `detail`
    so the change is visible in the envelope rather than hidden here. Enforcing
    it would reject most real assemblies over a number the design process has
    no freedom to change. The primer-dimer check stays HARD: that one IS
    actionable, by moving a boundary or choosing another enzyme.
    """
    pair_con = replace(con, product_min=0, product_max=0)
    try:
        hetero = structure.heterodimer(forward.sequence, reverse.sequence).tm
    except (SequenceError, ValueError):
        hetero = None
    out: list[CheckResult] = []
    for check in rules.evaluate_pair(forward.anneal, reverse.anneal, pair_con,
                                     hetero_dimer_tm=hetero):
        if check.rule == "pair_tm_diff" and check.severity == rules.HARD:
            detail = (f"{check.detail} {_TM_DIFF_RELAXED}" if check.detail
                      else _TM_DIFF_RELAXED)
            check = replace(check, severity=rules.SOFT, detail=detail)
        out.append(check)
    return out


def _anneal_sites(forward: str, reverse: str, template: str, *,
                  circular: bool) -> tuple[Optional[int], Optional[int], dict,
                                           verify.PcrResult]:
    """`(forward_start, reverse_end, verified, result)` for an annealing pair."""
    result = verify.simulate_pcr(forward, reverse, template, circular=circular)
    verified = verify.verify_pair(forward, reverse, template, circular=circular)
    product = result.product
    if product is None:
        return None, None, verified, result
    return (product.forward_site.start, product.reverse_site.end, verified,
            result)


# --------------------------------------------------------------------------
# Mode: restriction
# --------------------------------------------------------------------------


def _build_restriction(seq: str, template_name: str, *,
                       target: Optional[tuple[int, int]], circular: bool,
                       enzyme_names: list[str]) -> dict:
    con = rules.constraints_for("restriction")
    insert = seq if target is None else seq[target[0]:target[1]]
    if len(insert) < 2 * con.length_min:
        return _result(TEMPLATE_TOO_SHORT, mode="restriction", detail=(
            f"The insert is {len(insert)} bp and two annealing regions of at "
            f"least {con.length_min} nt will not fit on it."))

    try:
        fwd_enzyme = tails.lookup_enzyme(enzyme_names[0])
        rev_enzyme = tails.lookup_enzyme(enzyme_names[-1])
    except KeyError as exc:
        return _result(INVALID_INPUT, mode="restriction",
                       detail=str(exc).strip("'"))

    prefix = (template_name or "").split()
    design = tails.design_restriction_tails(
        fwd_enzyme, rev_enzyme, insert, constraints=con,
        name_prefix=prefix[0][:20] if prefix else "")

    forward, reverse = design.forward_primer, design.reverse_primer
    checks = (_scope(_recheck_tailed(forward, con), "forward")
              + _scope(_recheck_tailed(reverse, con), "reverse")
              + _scope(_tailed_pair_checks(forward, reverse, con), "pair")
              + _scope(design.checks, "cloning"))

    start, end, verified, result = _anneal_sites(
        forward.anneal, reverse.anneal, seq, circular=circular)
    if not verified.get("single_clean_product"):
        return _result(VERIFICATION_FAILED, mode="restriction", detail=(
            "The annealing regions of this pair do not amplify the insert "
            f"cleanly: the in-silico PCR gave {verified['product_count']} "
            f"product(s) {list(verified['product_sizes'])} against the "
            f"{len(seq)} bp template. No design is reported."))

    product = result.product
    assert product is not None
    amplicon = forward.tail + product.sequence + reverse.tail_homology()
    hard = _hard_failures(checks)
    if hard:
        return _result(VERIFICATION_FAILED, mode="restriction", detail=(
            "The design was produced and then rejected by its own HARD checks: "
            + "; ".join(f"{c.label} ({c.detail or 'failed'})" for c in hard)
            + ". No design is reported."))

    record = _design_record(
        name=f"{fwd_enzyme.name}_{rev_enzyme.name}",
        forward=_tailed_record(forward, con, strand=verify.PLUS,
                               start=start, end=start + forward.anneal_length),
        reverse=_tailed_record(reverse, con, strand=verify.MINUS,
                               start=end - reverse.anneal_length, end=end),
        product_size=len(amplicon), amplicon=amplicon, checks=checks,
        verified=verified)
    notes = [
        "The PCR was simulated with the ANNEALING regions only: a 5' tail is "
        "not present in the template, so verified.product_sizes are the "
        "untailed product, while product_size is the full product with both "
        f"tails ({forward.tail_length} + {reverse.tail_length} nt).",
        "Reading frame was NOT checked. For an N- or C-terminal fusion, "
        "confirm the insert stays in frame with the vector yourself.",
        _STRUCTURE_NOTE,
        _SPECIFICITY_NOTE,
    ]
    if not fwd_enzyme.spacer_sourced or not rev_enzyme.spacer_sourced:
        notes.append(
            "The spacer 5' of the site is the 6 bp default for at least one of "
            "these enzymes, not a published per-enzyme figure; see the "
            "spacer_length check's source."
        )
    return _result(DESIGNED, mode="restriction", records=[record], notes=notes)


# --------------------------------------------------------------------------
# Mode: overlap_assembly
# --------------------------------------------------------------------------


def _is_rotation(candidate: str, expected: str) -> bool:
    return len(candidate) == len(expected) and candidate in (expected + expected)


def _build_overlap(fragments: list[str], names: list[str], *, circular: bool,
                   overlap_length: Optional[int]) -> dict:
    con = rules.constraints_for("overlap_assembly")
    count = len(fragments)
    assembly = "".join(fragments)

    shortest = min(len(f) for f in fragments)
    want = overlap_length or tails.recommended_overlap(count)[2]
    if shortest < max(want, 2 * con.length_min):
        return _result(TEMPLATE_TOO_SHORT, mode="overlap_assembly", detail=(
            f"The shortest fragment is {shortest} bp, which cannot carry both "
            f"a {want} nt overlap and two annealing regions of at least "
            f"{con.length_min} nt."))

    # One design per junction, including the closing one. For a linear
    # assembly the closing design is built anyway and used only for the
    # annealing regions of the two outer, untailed primers, so their length is
    # chosen by the same engine code as every other primer here.
    try:
        junctions = [
            tails.design_overlap_tails(
                fragments[i], fragments[(i + 1) % count],
                fragment_count=count, overlap_length=overlap_length,
                name_prefix=f"j{i + 1}")
            for i in range(count)
        ]
    except (SequenceError, ValueError) as exc:
        return _result(INVALID_INPUT, mode="overlap_assembly", detail=str(exc))

    def _untailed(primer: tails.TailedPrimer, name: str, role: str
                  ) -> tails.TailedPrimer:
        return tails.TailedPrimer(
            anneal=primer.anneal, tail="", name=name, role=role,
            mode="overlap_assembly",
            purpose="outer end of a linear assembly; no junction here, so no "
                    "homology tail",
            checks=tuple(rules.evaluate_primer(primer.anneal, con)))

    records: list[dict] = []
    amplicons: list[str] = []
    offset = 0
    for index, fragment in enumerate(fragments):
        incoming = junctions[(index - 1) % count]
        outgoing = junctions[index]
        has_incoming = circular or index > 0
        has_outgoing = circular or index < count - 1
        forward = (incoming.forward_primer if has_incoming else
                   _untailed(incoming.forward_primer,
                             f"{names[index]}_fwd", "forward"))
        reverse = (outgoing.reverse_primer if has_outgoing else
                   _untailed(outgoing.reverse_primer,
                             f"{names[index]}_rev", "reverse"))
        amplicon = forward.tail + fragment + reverse.tail_homology()
        amplicons.append(amplicon)

        checks = (_scope(_recheck_tailed(forward, con), "forward")
                  + _scope(_recheck_tailed(reverse, con), "reverse")
                  + _scope(_tailed_pair_checks(forward, reverse, con), "pair"))
        if has_incoming:
            checks += _scope(incoming.checks, "junction_in")
        if has_outgoing:
            checks += _scope(outgoing.checks, "junction_out")

        verified = verify.verify_pair(
            forward.anneal, reverse.anneal, fragment, circular=False,
            expected_size=len(fragment))
        if not verified.get("single_clean_product"):
            return _result(VERIFICATION_FAILED, mode="overlap_assembly", detail=(
                f"The primers for {names[index]} do not amplify it cleanly: "
                f"the in-silico PCR gave {verified['product_count']} product(s) "
                f"{list(verified['product_sizes'])} on a {len(fragment)} bp "
                "fragment, so an annealing region occurs more than once. No "
                "design is reported."))
        hard = _hard_failures(checks)
        if hard:
            return _result(VERIFICATION_FAILED, mode="overlap_assembly", detail=(
                f"The design for {names[index]} was rejected by its own HARD "
                "checks: "
                + "; ".join(f"{c.label} ({c.detail or 'failed'})" for c in hard)
                + ". No design is reported."))

        records.append(_design_record(
            name=names[index],
            forward=_tailed_record(
                forward, con, strand=verify.PLUS, start=offset,
                end=offset + forward.anneal_length),
            reverse=_tailed_record(
                reverse, con, strand=verify.MINUS,
                start=offset + len(fragment) - reverse.anneal_length,
                end=offset + len(fragment)),
            product_size=len(amplicon), amplicon=amplicon,
            checks=checks, verified=verified))
        offset += len(fragment)

    # The whole point of the mode: do these amplicons actually join?
    try:
        joined = tails.assemble(amplicons, circular=circular)
    except tails.AssemblyError as exc:
        return _result(VERIFICATION_FAILED, mode="overlap_assembly", detail=(
            f"The simulated assembly refused to close: {exc}. This is the "
            "documented failure mode for assembly primers (junctions that look "
            "right and do not join), so no design is reported."))
    # A circular assembly is only defined up to rotation: the construct starts
    # wherever the first amplicon's tail starts.
    rebuilt = (_is_rotation(joined, assembly) if circular else joined == assembly)
    if not rebuilt:
        return _result(VERIFICATION_FAILED, mode="overlap_assembly", detail=(
            f"The simulated assembly produced {len(joined)} bp that are not "
            f"the {len(assembly)} bp of the fragments joined in order. No "
            "design is reported."))

    overlap_sizes = sorted({j.overlap_length for j in junctions})
    notes = [
        f"{count} fragment(s), {count if circular else count - 1} junction(s), "
        f"overlap {'/'.join(str(s) for s in overlap_sizes)} nt. The assembly "
        "was simulated: joining these products on their exact shared ends "
        f"reproduces the {len(assembly)} bp input"
        + (" (up to rotation, as a circle has no start)." if circular else "."),
        "Coordinates are on the assembled construct, and cover the annealing "
        "region only; a homology tail belongs to the neighbouring fragment.",
        "'Gibson Assembly' is a registered trademark of Codex DNA; this mode "
        "is named overlap_assembly for that reason and covers NEBuilder HiFi "
        "and other homology-based assembly chemistries equally.",
        _STRUCTURE_NOTE,
        _SPECIFICITY_NOTE,
    ]
    return _result(DESIGNED, mode="overlap_assembly", records=records,
                   notes=notes, assembly_length=len(assembly))


# --------------------------------------------------------------------------
# Mode: sequencing
# --------------------------------------------------------------------------


def _build_sequencing(seq: str, *, feature_position: int, direction: str,
                      offset: int, circular: bool,
                      primer_length: Optional[int]) -> dict:
    con = rules.constraints_for("sequencing")
    size = primer_length or con.length_opt
    if len(seq) < size:
        return _result(TEMPLATE_TOO_SHORT, mode="sequencing", detail=(
            f"The template is {len(seq)} bp, shorter than the {size} nt "
            "primer asked for."))
    if not (0 <= feature_position <= len(seq)):
        return _result(AMBIGUOUS_TARGET, mode="sequencing", detail=(
            f"feature_position {feature_position} is outside the {len(seq)} bp "
            "template. Coordinates are 0-based."))

    try:
        design = tails.design_sequencing_primer(
            seq, feature_position, direction=direction, offset=offset,
            length=primer_length, circular=circular, constraints=con)
    except SequenceError as exc:
        return _result(AMBIGUOUS_TARGET, mode="sequencing", detail=(
            f"{exc} A sequencing primer must sit inside the template, "
            "`offset` bases before the feature."))
    except ValueError as exc:
        return _result(INVALID_INPUT, mode="sequencing", detail=str(exc))

    checks = _scope(list(design.primer.checks) + list(design.checks), "primer")
    hard = _hard_failures(checks)
    if hard:
        return _result(VERIFICATION_FAILED, mode="sequencing", detail=(
            "The primer was placed and then rejected by its own HARD checks: "
            + "; ".join(f"{c.label} ({c.detail or 'failed'})" for c in hard)
            + ". No design is reported."))

    sites = verify.find_binding_sites(
        design.primer.sequence, seq, circular=circular)
    verified = {
        "outcome": (verify.SINGLE_PRODUCT if len(sites) == 1
                    else verify.NO_PRODUCT if not sites
                    else verify.MULTIPLE_PRODUCTS),
        "product_count": len(sites),
        "product_sizes": [],
        "single_clean_product": len(sites) == 1,
        "binding_sites": [s.describe() for s in sites[:4]],
        "template_length": len(seq),
        "circular": bool(circular),
    }
    if not verified["single_clean_product"]:
        return _result(VERIFICATION_FAILED, mode="sequencing", detail=(
            f"The primer binds the template {len(sites)} times, so the trace "
            "would be an unreadable mixture. No design is reported."))

    record = _design_record(
        name="seq_primer",
        forward=(_tailed_record(design.primer, con, strand=design.strand,
                                start=design.start, end=design.end)
                 if design.strand == verify.PLUS else None),
        reverse=(_tailed_record(design.primer, con, strand=design.strand,
                                start=design.start, end=design.end)
                 if design.strand == verify.MINUS else None),
        product_size=0, amplicon="", checks=checks, verified=verified)
    notes = [
        f"A sequencing primer is a single oligo: it reads {direction} toward "
        f"position {feature_position} from {offset} nt away and makes no "
        "amplicon, so product_size is 0 and only the "
        f"{'forward' if design.strand == verify.PLUS else 'reverse'} slot is "
        "filled.",
        "verified here counts PRIMING SITES on this template, not PCR "
        "products: one site is what a readable trace needs.",
        _SPECIFICITY_NOTE,
    ]
    return _result(DESIGNED, mode="sequencing", records=[record], notes=notes)


# --------------------------------------------------------------------------
# The build, off the event loop
# --------------------------------------------------------------------------


def _build(*, mode: str, sequence: str, template_name: str,
           target: Optional[tuple[int, int]],
           product_range: Optional[tuple[int, int]], circular: bool, n: int,
           enzyme_names: list[str], fragments: list[str],
           fragment_names: list[str], overlap_length: Optional[int],
           feature_position: Optional[int], direction: str, offset: int,
           primer_length: Optional[int]) -> dict:
    """Pure, synchronous, CPU-bound. Called through `asyncio.to_thread`."""
    if mode == "pcr":
        return _build_pcr(sequence, target=target, product_range=product_range,
                          circular=circular, n=n)
    if mode == "restriction":
        return _build_restriction(sequence, template_name, target=target,
                                  circular=circular, enzyme_names=enzyme_names)
    if mode == "overlap_assembly":
        return _build_overlap(fragments, fragment_names, circular=circular,
                              overlap_length=overlap_length)
    return _build_sequencing(
        sequence, feature_position=feature_position or 0, direction=direction,
        offset=offset, circular=circular, primer_length=primer_length)


# --------------------------------------------------------------------------
# The tool
# --------------------------------------------------------------------------


def _parse(mode: Any, template: Any, target: Any, product_size_range: Any,
           n: Any, enzymes: Any, fragments: Any, overlap_length: Any,
           feature_position: Any, direction: Any, offset: Any,
           primer_length: Any, template_name: Any, circular: Any) -> dict:
    """Validated arguments, or `InputError`. No chemistry happens here."""
    try:
        canonical = rules.normalise_mode(mode if isinstance(mode, str) else "")
    except ValueError as exc:
        raise InputError(str(exc)) from None

    name = ""
    sequence = ""
    frags: list[str] = []
    frag_names: list[str] = []

    if canonical == "overlap_assembly":
        if not isinstance(fragments, (list, tuple)) or len(fragments) < 2:
            raise InputError(
                "overlap_assembly needs `fragments`: an ordered array of at "
                "least two DNA sequences, each one a fragment to be joined, in "
                "the order they appear in the finished construct.")
        if len(fragments) > MAX_FRAGMENTS:
            raise InputError(
                f"at most {MAX_FRAGMENTS} fragments; got {len(fragments)}")
        for index, fragment in enumerate(fragments, start=1):
            frag_name, frag_seq = _clean_sequence(fragment, f"fragment {index}")
            frags.append(frag_seq)
            frag_names.append(frag_name.split()[0][:40] if frag_name
                              else f"fragment_{index}")
        sequence = "".join(frags)
        name = (template_name or "").strip() or "assembly"
    else:
        name, sequence = _clean_sequence(template, "template")
        if isinstance(template_name, str) and template_name.strip():
            name = template_name.strip()

    span = _as_span(target, "target")
    if span is not None:
        lo, hi = span
        if not (0 <= lo < hi <= len(sequence)):
            raise InputError(
                f"target {list(span)} is not a [start, end) window inside the "
                f"{len(sequence)} bp sequence. Coordinates are 0-based and "
                "half-open, like a Python slice.")

    product_range = _as_span(product_size_range, "product_size_range")
    if product_range is not None:
        pmin, pmax = product_range
        if pmin < 1 or pmax < pmin:
            raise InputError(
                f"product_size_range {list(product_range)} must be "
                "[minimum, maximum] with 1 <= minimum <= maximum")

    enzyme_names: list[str] = []
    if canonical == "restriction":
        if isinstance(enzymes, str):
            enzymes = [part.strip() for part in enzymes.split(",") if part.strip()]
        if not isinstance(enzymes, (list, tuple)) or not 1 <= len(enzymes) <= 2:
            raise InputError(
                "restriction mode needs `enzymes`: one or two enzyme names, "
                "forward end first. Known: "
                + ", ".join(sorted(e.name for e in tails.ENZYMES.values())))
        enzyme_names = [str(e).strip() for e in enzymes]

    if canonical == "sequencing" and feature_position is None:
        raise InputError(
            "sequencing mode needs `feature_position`: the 0-based coordinate "
            "of the region you want to read.")

    return {
        "mode": canonical,
        "sequence": sequence,
        "template_name": name,
        "target": span,
        "product_range": product_range,
        "circular": bool(circular),
        "n": _as_int(n, "n", minimum=1, maximum=MAX_DESIGNS, default=3),
        "enzyme_names": enzyme_names,
        "fragments": frags,
        "fragment_names": frag_names,
        "overlap_length": (None if overlap_length is None else
                           _as_int(overlap_length, "overlap_length",
                                   minimum=tails.OVERLAP_MIN_NT, maximum=80)),
        "feature_position": (None if feature_position is None else
                             _as_int(feature_position, "feature_position",
                                     minimum=0, maximum=MAX_TEMPLATE_BP)),
        "direction": (direction if direction in DIRECTIONS else "forward"),
        "offset": _as_int(offset, "offset", minimum=0, maximum=10_000,
                          default=tails.READ_OFFSET_DEFAULT),
        "primer_length": (None if primer_length is None else
                          _as_int(primer_length, "primer_length",
                                  minimum=10, maximum=60)),
    }


async def design_primers(
    mode: str = "pcr",
    template: Optional[str] = None,
    target: Any = None,
    product_size_range: Any = None,
    circular: Any = False,
    n: Any = 3,
    enzymes: Any = None,
    fragments: Any = None,
    overlap_length: Any = None,
    feature_position: Any = None,
    direction: str = "forward",
    offset: Any = None,
    primer_length: Any = None,
    template_name: Optional[str] = None,
) -> dict:
    """Design and verify primers. See the module docstring for the contract."""
    try:
        args = _parse(mode, template, target, product_size_range, n, enzymes,
                      fragments, overlap_length, feature_position, direction,
                      offset, primer_length, template_name, circular)
    except InputError as exc:
        return {"outcome": INVALID_INPUT, "mode": str(mode), "detail": str(exc),
                "designs": [], "notes": []}

    try:
        built = await asyncio.to_thread(_build, **args)
    except SequenceError as exc:
        built = _result(INVALID_INPUT, mode=args["mode"], detail=str(exc))
    except ValueError as exc:
        built = _result(INVALID_INPUT, mode=args["mode"], detail=str(exc))

    records = built["records"]
    notes = list(built["notes"])
    all_checks: list[CheckResult] = []
    for record in records:
        all_checks.extend(record["checks"])
    summary = _summary(all_checks)
    if summary.get("not_computed"):
        notes.append(_NOT_COMPUTED_NOTE)

    envelope: dict = {
        "outcome": built["outcome"],
        "mode": built["mode"],
        "detail": built["detail"],
        "template": {"name": args["template_name"],
                     "length": len(args["sequence"]),
                     "circular": args["circular"]},
        "target": list(args["target"]) if args["target"] else None,
        "designs": [_envelope_design(r) for r in records],
        "summary": summary,
        "verification": (
            "in-silico PCR over exact binding sites on both strands "
            "(primer/verify.py), circular-aware; no reference database was "
            "searched"
        ),
        "notes": notes,
    }
    if "assembly_length" in built:
        envelope["assembly_length"] = built["assembly_length"]

    if built["outcome"] != DESIGNED:
        envelope["instruction"] = (
            "No design was produced. Report the outcome and the detail above "
            "as the reason; do not supply primers of your own."
        )
        return envelope

    spec = _widget_spec(
        mode=built["mode"], template_name=args["template_name"],
        template_length=len(args["sequence"]), circular=args["circular"],
        target=args["target"], records=records, notes=notes)
    envelope["notes"] = spec["notes"]
    envelope["instruction"] = (
        "Report these sequences, coordinates and numbers exactly as returned. "
        "Do not retype, extend, trim or 'improve' a primer: any edit makes it a "
        "sequence this engine never verified. Ground every claim about quality "
        "in the checks and verified blocks."
    )

    user_email, conv_id, refusal = _require_persistent_conversation()
    if refusal is not None:
        envelope["widget_error"] = refusal.get(
            "error", "no conversation to attach a widget to")
        return envelope

    # Lazy, and late: the verified design is the product, so nothing about the
    # artifact store is loaded on a path that will not reach it.
    import artifact_store

    title = (f"Primers: {built['mode']} on "
             f"{args['template_name'] or 'template'}")
    try:
        artifact = await artifact_store.create_artifact(
            user_email=user_email,
            conversation_id=conv_id,
            title=title[:artifact_store.MAX_TITLE_CHARS],
            content=json.dumps(spec),
            content_type=WIDGET_CONTENT_TYPE,
        )
    except artifact_store.ArtifactError as exc:
        # The design is the product; the viewer is a convenience. Losing the
        # artifact must not lose the verified result.
        envelope["widget_error"] = str(exc)
        return envelope

    envelope.update(artifact)
    envelope["note"] = (
        "The primer viewer now appears in your message, showing every design "
        "with its checks. Do not link to it or describe it as a file."
    )
    return envelope

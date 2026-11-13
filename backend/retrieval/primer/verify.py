"""
In-silico PCR: the primer engine's self-check and the eval suite's grader.

One implementation serves both roles on purpose (plan section 3.1). The
published evidence says LLMs fail at primer design through *bookkeeping*, not
chemistry: primers that do not occur in the template at all, the wrong strand,
off-by-one coordinates, assembly junctions that do not join. LAB-Bench grades
open-ended primer answers by running an in-silico PCR on the model's own
primers, and that is the shape copied here. Nothing in the tool envelope may
claim a product this module did not find.

Written from first principles. Not ported from Primer3, isPcr or MFEprimer,
all of which are GPL; this service is Apache-2.0.

Coordinate convention
---------------------
**0-based, half-open, always on the plus strand.** A site is `[start, end)`
with `end - start == len(primer)`, exactly like a Python slice. This holds for
both strands and for sites that wrap the origin.

The plus strand is the sequence as given. A site on the minus strand is still
reported in plus-strand coordinates, so `template[start:end]` is the primer's
reverse complement, never the primer. Amplicon length is `end - start` of the
span the two primers bracket, so no +1 ever appears and the classic off-by-one
has nowhere to live.

On a circular template a site or an amplicon may run past the end of the
string and continue at position 0. Such a span is reported with `start` in
`[0, length)` and `end > length`, which keeps `end - start` equal to the true
span length. `wraps` says whether that happened, and `end_in_template` gives
`end % length` for callers that want a position rather than a span.

Strand convention
-----------------
`strand` describes which strand the primer's own 5'->3' sequence reads along,
which is the convention isPcr and BLAST report and the one users mean by
"forward" and "reverse":

- `"+"`: the primer sequence occurs verbatim on the plus strand. It therefore
  ANNEALS TO THE MINUS STRAND and extends towards higher coordinates. This is
  a forward primer.
- `"-"`: the primer's reverse complement occurs on the plus strand. It
  ANNEALS TO THE PLUS STRAND and extends towards lower coordinates. This is a
  reverse primer.

Getting this backwards is the classic error, so say it once more plainly: a
reverse primer does NOT appear in the template as written. Its reverse
complement does. `simulate_pcr` therefore looks for `forward` on `"+"` and
`reverse` on `"-"`, and a pair handed over the wrong way round yields zero
products rather than a plausible-looking wrong number.

IUPAC ambiguity
---------------
Degenerate primers are normal in eDNA and barcoding work (7 of the 38 pairs in
the Dryad specificity benchmark carry R/Y/M/S/K), and templates carry `N` runs
routinely. `bases_compatible` is the single base-comparison function used
everywhere: two codes match when the base sets they denote intersect. So
primer `R` matches template `A` or `G`, template `N` matches any primer base,
and primer `N` matches any template base. There is no separate "expand the
primer and try every oligo" path to drift out of sync with it.

3' end anchoring
----------------
A primer with a mismatched 3' terminal base generally does not extend, because
the polymerase needs a correctly paired 3'-OH. That is a parameter here,
`three_prime_anchor`, not a hardcoded rule: it is the number of terminal
primer bases that must match exactly. The default of 1 is the conservative
reading; 0 disables anchoring; Ye et al. 2012 effectively uses a window of 5
for specificity screening. With `max_mismatches=0` the setting cannot matter,
which is the usual case.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Iterator, Optional

from .thermo import IUPAC_CODES, SequenceError, normalise, reverse_complement

__all__ = [
    "PLUS",
    "MINUS",
    "BindingSite",
    "Amplicon",
    "PcrResult",
    "bases_compatible",
    "find_binding_sites",
    "simulate_pcr",
    "verify_pair",
]

PLUS = "+"
MINUS = "-"

# Outcome vocabulary, mirroring the envelope style the plan sets out.
NO_PRODUCT = "no_product"
SINGLE_PRODUCT = "single_product"
MULTIPLE_PRODUCTS = "multiple_products"


# --------------------------------------------------------------------------
# Base comparison: the single source of truth for "do these two match?"
# --------------------------------------------------------------------------

def _build_compatibility() -> frozenset[tuple[str, str]]:
    """Every (primer base, template base) pair whose base sets intersect."""
    pairs = set()
    for p, p_bases in IUPAC_CODES.items():
        for t, t_bases in IUPAC_CODES.items():
            if set(p_bases) & set(t_bases):
                pairs.add((p, t))
    return frozenset(pairs)


_COMPATIBLE = _build_compatibility()


def bases_compatible(primer_base: str, template_base: str) -> bool:
    """
    True when a primer base can pair with a template base.

    IUPAC codes denote sets of bases, and two codes are compatible when those
    sets intersect. Primer `R` (A or G) matches template `A`; template `N`
    matches anything; primer `N` matches anything. Compatibility is symmetric,
    but the argument names are kept distinct because every caller has a
    primer and a template and mixing them up is the kind of thing this module
    exists to prevent.

    Raises SequenceError on a character that is not an IUPAC DNA code.
    """
    p = primer_base.upper()
    t = template_base.upper()
    if p not in IUPAC_CODES:
        raise SequenceError(f"not an IUPAC DNA code: {primer_base!r}")
    if t not in IUPAC_CODES:
        raise SequenceError(f"not an IUPAC DNA code: {template_base!r}")
    return (p, t) in _COMPATIBLE


# --------------------------------------------------------------------------
# Result types
# --------------------------------------------------------------------------

@dataclass(frozen=True)
class BindingSite:
    """
    One place a primer anneals, in plus-strand 0-based half-open coordinates.

    Attributes
    ----------
    primer:
        The primer sequence as supplied, normalised.
    strand:
        `"+"` if the primer reads along the plus strand (a forward primer),
        `"-"` if its reverse complement does (a reverse primer). See the
        module docstring.
    start, end:
        The footprint on the plus strand, `[start, end)`. `start` is always in
        `[0, template_length)`; `end` may exceed the template length on a
        circular template, which means the site wraps the origin.
    mismatches:
        How many primer bases are incompatible with the template underneath.
    mismatch_positions:
        Those positions as indices into the PRIMER, 5'->3'. Primer index
        `len - 1` is the 3' terminal base on either strand, so these read the
        same way regardless of which strand the site is on.
    three_prime_match:
        Whether the 3' terminal base matched. False means the primer is very
        unlikely to extend.
    template_footprint:
        The template bases the primer pairs with, written 5'->3' ALONG THE
        PRIMER. For a `"+"` site that is `template[start:end]`; for a `"-"`
        site it is the reverse complement of that slice. So
        `zip(primer, template_footprint)` lines up base for base on either
        strand, which is what makes the mismatch bookkeeping checkable.
    template_length:
        Length of the template this site was found in, kept so the site can
        answer `wraps` and `end_in_template` without the template.
    """

    primer: str
    strand: str
    start: int
    end: int
    mismatches: int
    mismatch_positions: tuple[int, ...]
    three_prime_match: bool
    template_footprint: str
    template_length: int

    @property
    def length(self) -> int:
        """Footprint length, which is always the primer length."""
        return self.end - self.start

    @property
    def wraps(self) -> bool:
        """True if this site runs past the origin of a circular template."""
        return self.end > self.template_length

    @property
    def end_in_template(self) -> int:
        """`end` brought back inside the template, for a position rather than a span."""
        return self.end % self.template_length

    @property
    def three_prime_position(self) -> int:
        """
        Plus-strand position of the primer's 3' terminal base.

        On `"+"` that is the right-hand end of the footprint; on `"-"` it is
        the left-hand end, because the primer reads the other way. Several of
        the plan's M13 aliases collapse onto a shared 3' end, so this is the
        key to compare primers by.
        """
        if self.strand == PLUS:
            return (self.end - 1) % self.template_length
        return self.start

    def describe(self) -> str:
        """One-line human-readable summary, for envelopes and test failures."""
        span = f"{self.start}..{self.end}"
        if self.wraps:
            span += f" (wraps, ends at {self.end_in_template})"
        mm = "exact" if not self.mismatches else f"{self.mismatches} mismatch(es)"
        return f"{self.strand}{span} {mm}"


@dataclass(frozen=True)
class Amplicon:
    """
    One PCR product: the sequence between and including both primer sites.

    `sequence` is read off the plus strand from the forward primer's 5' end to
    the reverse primer's 5' end, wrapping the origin when it has to. `length`
    is what a gel would report. Both binding sites are carried along so a
    caller can show the coordinates it was derived from rather than assert a
    number.

    Note on mismatched or degenerate primers: `sequence` is the TEMPLATE
    sequence over the amplified span. A real product carries the primer's own
    bases at its two ends, so where a primer mismatches or is degenerate the
    first round's product differs from this at those positions. With exact,
    unambiguous primers (the normal case, and every golden fixture) the
    distinction does not arise, and `starts_with_forward` / `ends_with_reverse`
    hold.
    """

    sequence: str
    length: int
    forward_site: BindingSite
    reverse_site: BindingSite

    @property
    def wraps(self) -> bool:
        """True if the product spans the origin of a circular template."""
        return self.forward_site.start + self.length > self.forward_site.template_length

    @property
    def starts_with_forward(self) -> bool:
        """The product's 5' end is the forward primer's footprint."""
        return self.sequence[: self.forward_site.length] == self.forward_site.template_footprint

    @property
    def ends_with_reverse(self) -> bool:
        """
        The product's 3' end is the reverse complement of the reverse primer's
        footprint, which is the thing people get backwards.
        """
        tail = self.sequence[len(self.sequence) - self.reverse_site.length:]
        return reverse_complement(tail) == self.reverse_site.template_footprint


@dataclass(frozen=True)
class PcrResult:
    """
    Everything a simulated reaction produced, with zero products a first-class
    answer rather than an exception.

    `outcome` is one of `no_product`, `single_product`, `multiple_products`,
    which is the distinction that decides whether a design may be reported as
    usable.
    """

    forward: str
    reverse: str
    template_length: int
    circular: bool
    forward_sites: tuple[BindingSite, ...]
    reverse_sites: tuple[BindingSite, ...]
    products: tuple[Amplicon, ...]

    @property
    def count(self) -> int:
        return len(self.products)

    @property
    def outcome(self) -> str:
        if not self.products:
            return NO_PRODUCT
        if len(self.products) == 1:
            return SINGLE_PRODUCT
        return MULTIPLE_PRODUCTS

    @property
    def is_single(self) -> bool:
        """One product and one only: the thing a clean PCR means."""
        return len(self.products) == 1

    @property
    def sizes(self) -> tuple[int, ...]:
        """Product lengths, smallest first, as a gel would read them."""
        return tuple(sorted(p.length for p in self.products))

    @property
    def product(self) -> Optional[Amplicon]:
        """The single product, or None when there is not exactly one."""
        return self.products[0] if len(self.products) == 1 else None


# --------------------------------------------------------------------------
# Scanning
# --------------------------------------------------------------------------

def _prepare_template(template: str, circular: bool, query_length: int) -> tuple[str, str, int]:
    """
    (template, search text, number of start positions).

    A circular template is searched by appending its own first `n-1` bases, so
    a site that runs off the end simply continues at the start. Start
    positions are still capped at the true length, so nothing is found twice.
    """
    seq = normalise(template)
    if query_length > len(seq):
        return seq, seq, 0
    if circular:
        return seq, seq + seq[: query_length - 1], len(seq)
    return seq, seq, len(seq) - query_length + 1


def _scan(query: str, text: str, starts: int, max_mismatches: int) -> Iterator[tuple[int, tuple[int, ...]]]:
    """Yield (start, mismatch positions in query coordinates) for each hit."""
    n = len(query)
    for start in range(starts):
        window = text[start: start + n]
        positions: list[int] = []
        for i in range(n):
            if not bases_compatible(query[i], window[i]):
                positions.append(i)
                if len(positions) > max_mismatches:
                    break
        else:
            yield start, tuple(positions)


def find_binding_sites(
    primer: str,
    template: str,
    *,
    circular: bool = False,
    max_mismatches: int = 0,
    three_prime_anchor: int = 1,
    strands: str = "both",
) -> list[BindingSite]:
    """
    Every place `primer` anneals to `template`, on both strands.

    Coordinates are 0-based half-open on the plus strand; see the module
    docstring, which also states the strand convention and what happens at the
    origin of a circular template.

    Parameters
    ----------
    circular:
        Treat the template as a closed circle, so a primer may straddle the
        origin. Plasmids are circles; defaulting this to False would be a
        convenience that silently loses real sites, so it is explicit.
    max_mismatches:
        How many incompatible bases to tolerate anywhere in the primer.
        0 (the default) means exact, which is what a golden fixture wants.
    three_prime_anchor:
        How many 3'-terminal primer bases must match exactly for the site to
        count. 1 is the conservative default, because a mismatched 3' terminal
        base generally blocks extension. 0 disables the requirement and
        returns thermodynamically doomed sites too, which is occasionally what
        a specificity screen wants to see.
    strands:
        `"both"` (default), `"+"` for forward-orientation hits only, or `"-"`
        for reverse-orientation hits only.

    Returns a list ordered by start, then by strand with `"+"` first. The
    ordering is stable so that fixtures can index into it.
    """
    query_plus = normalise(primer)
    if three_prime_anchor < 0:
        raise ValueError("three_prime_anchor cannot be negative")
    if max_mismatches < 0:
        raise ValueError("max_mismatches cannot be negative")
    if strands not in ("both", PLUS, MINUS):
        raise ValueError(f"strands must be 'both', '+' or '-', not {strands!r}")

    n = len(query_plus)
    seq, text, starts = _prepare_template(template, circular, n)
    if starts <= 0:
        if n > len(seq):
            raise SequenceError(
                f"primer ({n} nt) is longer than the template ({len(seq)} nt)"
            )
        return []

    anchor = min(three_prime_anchor, n)
    wanted = (PLUS, MINUS) if strands == "both" else (strands,)
    sites: list[BindingSite] = []

    for strand in wanted:
        # The string that must be found on the plus strand. For a reverse
        # primer that is its reverse complement, which is the whole point.
        query = query_plus if strand == PLUS else reverse_complement(query_plus)

        for start, hits in _scan(query, text, starts, max_mismatches):
            # Translate query-coordinate mismatches into primer coordinates.
            # On the minus strand the query is the primer reversed, so primer
            # index i sits at query index n-1-i.
            if strand == PLUS:
                primer_positions = hits
            else:
                primer_positions = tuple(sorted(n - 1 - i for i in hits))

            # The anchor covers primer indices n-anchor .. n-1; a mismatch in
            # that window means the 3'-OH is unpaired and nothing extends.
            if any(p >= n - anchor for p in primer_positions):
                continue

            footprint = text[start: start + n]
            if strand == MINUS:
                footprint = reverse_complement(footprint)

            sites.append(
                BindingSite(
                    primer=query_plus,
                    strand=strand,
                    start=start,
                    end=start + n,
                    mismatches=len(primer_positions),
                    mismatch_positions=primer_positions,
                    three_prime_match=(n - 1) not in primer_positions,
                    template_footprint=footprint,
                    template_length=len(seq),
                )
            )

    sites.sort(key=lambda s: (s.start, 0 if s.strand == PLUS else 1))
    return sites


# --------------------------------------------------------------------------
# Amplification
# --------------------------------------------------------------------------

def _slice_circular(seq: str, start: int, length: int) -> str:
    """Read `length` bases from `start`, continuing at the origin if needed."""
    end = start + length
    if end <= len(seq):
        return seq[start:end]
    copies = -(-end // len(seq))          # ceiling division
    return (seq * copies)[start:end]


def simulate_pcr(
    forward: str,
    reverse: str,
    template: str,
    *,
    circular: bool = False,
    max_mismatches: int = 0,
    three_prime_anchor: int = 1,
    max_product_size: Optional[int] = None,
) -> PcrResult:
    """
    Simulate a PCR and return every product it would give.

    The forward primer is sought in `"+"` orientation and the reverse primer
    in `"-"`, per the module docstring. A product runs from the forward site's
    5' end to the reverse site's 5' end inclusive, and its length is simply
    `end - start` in the half-open convention.

    A pair amplifies when the two sites face each other with room for both
    primers, that is when the span is at least as long as each primer. On a
    circular template every forward site reaches every reverse site by going
    once round, so a product that wraps the origin is found rather than
    missed; on a linear template a reverse site upstream of the forward site
    simply yields nothing.

    `max_product_size` caps the length, the way a polymerase extension time
    does. None (the default) means no cap, which is what an oracle wants.

    Returns a `PcrResult` whose `outcome` is `no_product`, `single_product` or
    `multiple_products`. Zero products is an answer, not an error.
    """
    seq = normalise(template)
    fwd = normalise(forward)
    rev = normalise(reverse)

    common = dict(
        circular=circular,
        max_mismatches=max_mismatches,
        three_prime_anchor=three_prime_anchor,
    )
    forward_sites = find_binding_sites(fwd, seq, strands=PLUS, **common)
    reverse_sites = find_binding_sites(rev, seq, strands=MINUS, **common)

    products: list[Amplicon] = []
    for f_site in forward_sites:
        for r_site in reverse_sites:
            span = r_site.end - f_site.start
            if circular:
                span %= len(seq)
                if span == 0:
                    # The two 5' ends meet exactly: the product is the whole
                    # circle, not a zero-length nothing.
                    span = len(seq)
            if span < f_site.length or span < r_site.length:
                # Primers overlap past each other; no polymerase product.
                continue
            if max_product_size is not None and span > max_product_size:
                continue
            sequence = (
                _slice_circular(seq, f_site.start, span)
                if circular else seq[f_site.start: f_site.start + span]
            )
            products.append(
                Amplicon(
                    sequence=sequence,
                    length=span,
                    forward_site=f_site,
                    reverse_site=r_site,
                )
            )

    products.sort(key=lambda p: (p.forward_site.start, p.length))

    return PcrResult(
        forward=fwd,
        reverse=rev,
        template_length=len(seq),
        circular=circular,
        forward_sites=tuple(forward_sites),
        reverse_sites=tuple(reverse_sites),
        products=tuple(products),
    )


def verify_pair(
    forward: str,
    reverse: str,
    template: str,
    *,
    circular: bool = False,
    max_mismatches: int = 0,
    three_prime_anchor: int = 1,
    max_product_size: Optional[int] = None,
    expected_size: Optional[int] = None,
) -> dict:
    """
    A compact verdict on a primer pair, shaped for a tool envelope.

    This is the `verified` block of the `design_primers` envelope (plan
    section 3.3): flat, JSON-serialisable, and derived entirely from
    `simulate_pcr`, so the model cannot report a product the simulation did
    not find. `expected_size`, when given, adds a `size_matches` flag so an
    eval can grade a claimed amplicon without re-deriving it.
    """
    result = simulate_pcr(
        forward, reverse, template,
        circular=circular,
        max_mismatches=max_mismatches,
        three_prime_anchor=three_prime_anchor,
        max_product_size=max_product_size,
    )
    sizes = list(result.sizes)
    single = result.product
    verdict = {
        "outcome": result.outcome,
        "product_count": result.count,
        "product_sizes": sizes,
        "single_clean_product": result.is_single,
        "forward_site_count": len(result.forward_sites),
        "reverse_site_count": len(result.reverse_sites),
        "template_length": result.template_length,
        "circular": circular,
        "product_size": single.length if single else None,
        "wraps_origin": bool(single.wraps) if single else False,
        "forward_start": single.forward_site.start if single else None,
        "reverse_end": single.reverse_site.end if single else None,
    }
    if expected_size is not None:
        verdict["expected_size"] = expected_size
        verdict["size_matches"] = expected_size in sizes
    return verdict

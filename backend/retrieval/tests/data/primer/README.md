# Vendored vector sequences for the primer tests

Fixtures for `backend/retrieval/tests/test_primer_verify.py`, the in-silico PCR
oracle (`primer/verify.py`). The hermetic CI tier runs with `--network none`,
so these are fetched once, committed, and read from disk thereafter. **No test
may fetch at run time.**

## Provenance

| File | Accession | Length | Topology | Retrieved |
|---|---|---|---|---|
| `pUC19_L09137.fasta` | `L09137.2` | 2,686 bp | circular | 2026-11-13 |
| `pBluescriptIISKplus_X52328.fasta` | `X52328.1` | 2,961 bp | circular | 2026-11-13 |

Both were fetched from NCBI E-utilities, unmodified, on 2026-11-13:

```
curl "https://eutils.ncbi.nlm.nih.gov/entrez/eutils/efetch.fcgi?db=nuccore&id=L09137&rettype=fasta&retmode=text"
curl "https://eutils.ncbi.nlm.nih.gov/entrez/eutils/efetch.fcgi?db=nuccore&id=X52328&rettype=fasta&retmode=text"
```

sha256 as committed:

```
656a4ed4597020eb58266ddd4d65f1778abff1a74655abf25ec943dcc14c152a  pUC19_L09137.fasta
b22dc1a73d6d9c4f8c728aa9d696a14e4d2dd68f86119230e6ec80c68ee4f9ef  pBluescriptIISKplus_X52328.fasta
```

The lengths were checked against the plan's table
(`docs/agent-track/PRIMER-DESIGN-PLAN.md`, section 4 layer 3) before anything
was asserted against them, and both match exactly. The test suite re-asserts
the lengths on load, so a silent re-fetch of a different record fails loudly.

## Licensing

GenBank records carry no copyright claim. NCBI's own statement is that it
places no restrictions on the use or distribution of the data it holds, other
than that some third-party records may be protected. These two are plain
sequence records with no such claim, so redistributing them inside this
Apache-2.0 repository is clean.

Note the deliberate choice of source: the plan (section 4) records that NEB
distributes a pUC19 GenBank flat file too, but taking the sequence from NCBI
removes the licensing question entirely. These files are **not** derived from
Primer3's or primer3-py's GPL test corpus, neither their expected values nor
their choice of inputs.

## Topology, which matters

Both records are circular plasmids, and the FASTA format cannot say so. The
tests pass `circular=True` explicitly. Two of the plan's golden amplicons wrap
the origin, which is the case any flat-string implementation gets wrong, so
treating these as linear is a real bug and not a formality.

## Primer variants: pin the exact one

Vendors circulate several sequences under each universal primer name, and they
do not all give the same amplicon. Every fixture names the exact variant:

| Name | Variant used | Note |
|---|---|---|
| M13 Forward (-21) | `TGTAAAACGACGGCCAGT` | plus strand of pUC19 at 377 (0-based) |
| M13 Forward (-40) | `GTTTTCCCAGTCACGAC` | plus strand of pUC19 at 358 |
| M13/pUC Reverse | `AGCGGATAACAATTTCACACAGG` | minus strand, footprint ends at 500 |
| T7 promoter | `TAATACGACTCACTATAGGG` | plus strand of pBluescript at 625 |
| T3, 20-mer | `ATTAACCCTCACTAAAGGGA` | minus strand, footprint 770..789 |
| T3, 21-mer | `GCAATTAACCCTCACTAAAGG` | minus strand, footprint 772..792 |

**The two T3 variants give different amplicons against the same T7 primer, and
the test asserts both:** 165 bp with the 20-mer, 168 bp with the 21-mer. The
plan's table lists 168 bp for "T7 x T3" while its sequence caveat recommends
the 20-mer; those two statements are not consistent, and the 3 bp difference is
exactly the 21-mer's extra 5' `GC` plus the one-base register shift. The plan's
preference for the 20-mer stands on its own grounds (only the 20-mer also
matches the T3 phage genome NC_003298), so the suite pins both numbers rather
than picking one and burying the discrepancy.

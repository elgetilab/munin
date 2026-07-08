"""Generate the frozen routing-eval PARAPHRASE set (A5).

WHAT
----
For each hand-authored anchor in routing_eval.SEED_ITEMS, ~15-20 paraphrases
that INHERIT the anchor's Expected (same predicates, same gate, same category).
Only the query text varies. This makes the routing score robust to phrasing:
one brittle wording can't swing a category.

WHY HAND-AUTHORED (not model-generated)
---------------------------------------
A paraphrase must PRESERVE the anchor's correct route. Hand-authoring
guarantees that and lets us keep the literals the anchor's predicates assert
(the "Leipzig" in the weather regex, the exact arxiv URL / DOIs, "DNP", three
DOIs for the bibtex export, ...). The frozen JSON gives the same determinism a
one-time model generation would, without the route-drift risk. (done/A5-PLAN.md.)

DISJOINTNESS (the bidirectional invariant)
------------------------------------------
The routing-eval TEST set (anchors + these paraphrases) and the router's
labelled TRAIN set (router_examples.json) must stay DISJOINT, normalised.
  - This script asserts no paraphrase normalises to any TRAIN example.
  - It also re-asserts no paraphrase collides with another anchor's query.
  - router_examples_build.py's guard is extended to check TRAIN vs these
    paraphrases too (so a later train edit can't introduce a leak).

Run:  python backend/benchmarks/munin_bench/routing/routing_paraphrases_build.py
Output: routing_paraphrases.json (committed; small, provenance-tagged).
"""
from __future__ import annotations

import json
import re
import sys
from pathlib import Path

_HERE = Path(__file__).resolve().parent
_OUT = _HERE / "routing_paraphrases.json"
_REPO = _HERE.parents[3]
_TRAIN = _REPO / "backend" / "retrieval" / "router_examples.json"


# ===========================================================================
# Paraphrases keyed by anchor id. Each list entry reroutes to the SAME
# Expected as its anchor (inherited at load time). Literals the anchor's
# predicates assert are PRESERVED verbatim (marked inline where it matters).
# ===========================================================================
_PARAPHRASES: dict[str, list[str]] = {

    # simple_lookup -- live fact -> web_search. Predicate: query REGEX leipzig.
    # KEEP "Leipzig" in every paraphrase.
    "weather_with_location": [
        "What's the weather in Leipzig right now?",
        "Is it raining in Leipzig at the moment?",
        "How warm is it in Leipzig today?",
        "Give me today's forecast for Leipzig.",
        "Will I need a jacket in Leipzig today?",
        "What's the temperature in Leipzig this afternoon?",
        "Current conditions in Leipzig, please.",
        "Is it sunny in Leipzig right now?",
        "What's it like outside in Leipzig today?",
        "Tell me the weather for Leipzig today.",
        "How's the weather looking in Leipzig this evening?",
        "Any rain expected in Leipzig today?",
        "What's the wind like in Leipzig right now?",
        "Is it cold in Leipzig today?",
        "Weather in Leipzig today?",
        "What should I wear for the weather in Leipzig today?",
    ],

    # clarify -- weather with NO location -> solo ask_clarification.
    # KEEP the location ABSENT.
    "weather_no_location": [
        "What's the weather right now?",
        "Is it going to rain later?",
        "How warm is it outside today?",
        "Do I need an umbrella?",
        "What's the forecast looking like?",
        "Is it cold out at the moment?",
        "Will it be sunny this afternoon?",
        "What's the temperature outside?",
        "Should I bring a coat today?",
        "Is it windy right now?",
        "What's it like outside?",
        "Give me the weather, please.",
        "Tell me today's forecast.",
        "Is it snowing at the moment?",
        "What are the conditions like today?",
        "How's the weather?",
    ],

    # no_tool -- settled science answered parametrically. Forbidden: searches.
    # Distinct from the anchor (NMR) AND the TRAIN chat definitions
    # (Fourier transform, p-value, entropy, Avogadro, pH, catalyst, ...).
    "define_nmr": [
        "In a paragraph, explain what a chemical bond is.",
        "Briefly, what is an atom?",
        "What is diffusion? One paragraph.",
        "In simple terms, what does temperature measure?",
        "In a few sentences, what is electromagnetic radiation?",
        "What does the term isotope mean?",
        "Explain what a protein is, briefly.",
        "What is the difference between a solid and a liquid?",
        "In one paragraph, what is gravity?",
        "Describe what an electron is.",
        "What is a wavelength? Keep it short.",
        "Explain photosynthesis in a paragraph.",
        "What is osmosis, briefly?",
        "In simple terms, what is a magnetic field?",
        "Explain what density is, in one paragraph.",
        "What does the word viscosity mean?",
    ],

    # compute -- arithmetic -> calculate (forbid run_python). 8 (shared cat).
    "percent_calc": [
        "What's 23% of 1,840?",
        "Compute 7.5% of 3,200.",
        "What is 12 percent of 950?",
        "How much is 35% of 60?",
        "What's 8% of 12,500?",
        "Calculate 64 times 39.",
        "What's 1,728 divided by 12?",
        "What is 18% of 2,750?",
    ],

    # compute -- unit conversion -> calculate(mode=physical). 8 (shared cat).
    "unit_convert_physical": [
        "How many joules is 2.5 eV?",
        "Convert 300 K to degrees Celsius.",
        "What is 1 tesla in gauss?",
        "Express 5 angstroms in nanometres.",
        "How many electronvolts is k_B times 298 K?",
        "Convert 13.6 eV to kilojoules per mole.",
        "What's 500 MHz expressed in inverse centimetres?",
        "How many nanoseconds is 2 microseconds?",
    ],

    # deep_research -- substantive 'state of the art' -> deep_research FIRST.
    "sota_phip": [
        "Give me a comprehensive overview of the state of the art in dissolution DNP for clinical imaging.",
        "Do a thorough survey of recent advances in SABRE catalyst design.",
        "I need a deep dive into the current state of hyperpolarized 13C metabolic MRI.",
        "Pull together a thorough state-of-the-art on singlet-order lifetimes in biomolecules.",
        "Comprehensively review where the field stands on photo-CIDNP in proteins.",
        "Do a broad survey of quantum-sensing approaches for biomedical NMR.",
        "Give me an in-depth landscape of parahydrogen catalysis for aqueous systems.",
        "What's the comprehensive picture of the field on hyperpolarized xenon lung imaging?",
        "Survey thoroughly the recent progress in DNP polariser hardware.",
        "I want a deep, wide-ranging review of cryogenic dissolution DNP methods.",
        "Give me the full state of the art on in-vivo singlet-state preservation.",
        "Do an extensive overview of the field on metabolic flux imaging with hyperpolarized pyruvate.",
        "Thoroughly summarise where research stands on long-lived nuclear spin states.",
        "Give me a comprehensive synthesis of recent work on SABRE-SHEATH at microtesla fields.",
        "I need a broad, in-depth survey of hyperpolarization for drug-metabolism studies.",
        "Comprehensively map out the current frontier of parahydrogen-induced polarization in catalysis.",
    ],

    # corpus_qa -- 'our group' + #group tag -> LOCAL paper_search FIRST.
    # KEEP the 'our group/lab/we' framing (in-corpus topics).
    "group_corpus_qa": [
        "What has our group reported on parahydrogen polarization efficiency?",
        "Summarise our lab's findings on 13C relaxation times.",
        "In our own papers, what did we measure for SABRE enhancement at low field?",
        "According to our group's work, which catalysts did we test?",
        "What does our published work say about membrane protein dynamics?",
        "What did our lab find about singlet-state preservation?",
        "Across our papers, what polarization levels have we reported?",
        "What has our group published on hyperpolarized pyruvate metabolism?",
        "Summarise what we found about parahydrogen ortho-para conversion.",
        "In our group's studies, how did field strength affect enhancement?",
        "What did we conclude about catalyst poisoning in our own work?",
        "What does our lab's research say about T1 of hyperpolarized agents?",
        "From our publications, what substrates did we hyperpolarize?",
        "What has our group documented about SABRE solvent effects?",
        "Summarise our own work on lipid-protein coupling in membranes.",
        "According to our papers, what enhancement factors did we achieve?",
    ],

    # citation_graph -- get_citations via tool_search. Predicate: doi REGEX
    # 10.\d{4,}. KEEP a matching DOI literal in every paraphrase.
    "citing_papers": [
        "List the papers that cite 10.1038/s41586-021-03456-2.",
        "Who cites doi 10.1021/jacs.1c01234?",
        "Show me the citation network around 10.1002/anie.202012345.",
        "Which works reference 10.1073/pnas.2101234118?",
        "Find everything citing the paper with doi 10.1126/science.abc1234.",
        "What papers build on 10.1039/d0cp01234a?",
        "Give me the works that cite 10.1021/acs.jpclett.0c01234.",
        "Trace the papers citing doi 10.1038/nature12373.",
        "Which articles cite 10.1063/5.0012345?",
        "Show citations of the review at 10.1021/cr500426x.",
        "List who has cited 10.1002/mrm.28765.",
        "What cites doi 10.1016/j.jmr.2020.106789?",
        "Find the citing papers for 10.1038/s41557-021-00789-x.",
        "Who references 10.1021/jacs.0c05678 in the literature?",
        "Map the citation graph of doi 10.1126/sciadv.abd1234.",
        "Which papers cite 10.1073/pnas.1912345117?",
    ],

    # direct_ref -- known URL -> web_fetch. Predicate: url CONTAINS
    # arxiv.org/abs/2406.12045. KEEP the exact URL. 8 (shared cat).
    "known_url_fetch": [
        "Pull up https://arxiv.org/abs/2406.12045 and give me the gist.",
        "What's on this page? https://arxiv.org/abs/2406.12045",
        "Summarise the content at https://arxiv.org/abs/2406.12045 for me.",
        "Read https://arxiv.org/abs/2406.12045 and tell me the main result.",
        "Can you digest https://arxiv.org/abs/2406.12045 for me?",
        "Give me the highlights of https://arxiv.org/abs/2406.12045.",
        "Open https://arxiv.org/abs/2406.12045 and summarise it.",
        "TL;DR of https://arxiv.org/abs/2406.12045 please.",
    ],

    # direct_ref -- known DOI -> read_paper. Predicate: doi REGEX
    # 10.1021/jacs.0c01234. KEEP the exact DOI. 8 (shared cat).
    "known_doi_read": [
        "Open doi:10.1021/jacs.0c01234 and tell me what method they used.",
        "Read 10.1021/jacs.0c01234 and summarise the hyperpolarization approach.",
        "What technique does doi:10.1021/jacs.0c01234 report? Read it.",
        "Pull the full text of 10.1021/jacs.0c01234 and give me the methods.",
        "Go through doi:10.1021/jacs.0c01234 and tell me the key result.",
        "Read the paper 10.1021/jacs.0c01234 and explain their setup.",
        "What experimental conditions are in doi:10.1021/jacs.0c01234? Read it.",
        "Fetch and read 10.1021/jacs.0c01234, then summarise the findings.",
    ],

    # memory -- durable user fact -> remember. Predicate: value REGEX (?i)dnp.
    # KEEP "DNP" in every paraphrase.
    "remember_research_area": [
        "For future reference, my work centres on dissolution DNP.",
        "Please remember that I specialise in DNP.",
        "Note for next time: I work with DNP hyperpolarization.",
        "Keep in mind going forward, my field is DNP.",
        "Just so you have it on file, I research DNP.",
        "Going forward, remember my focus is dissolution DNP.",
        "Make a note that my area is DNP.",
        "For the record, I do DNP work.",
        "Remember this about me: I work on DNP.",
        "From now on, know that my speciality is DNP.",
        "Save this: my research is in DNP.",
        "Bear in mind for later that I focus on DNP.",
        "I'd like you to remember I work in DNP.",
        "Note that, going forward, my work is DNP-based.",
        "Keep on record that my lab does DNP.",
        "Please file away that I specialise in dissolution DNP.",
    ],

    # citation_export -- BibTeX for >=3 DOIs -> export_citations. Predicate:
    # format=bibtex AND dois LEN_GTE 3. KEEP >=3 DOIs + "bibtex".
    "export_bibtex": [
        "BibTeX for 10.1/a, 10.2/b, 10.3/c please.",
        "Export these in BibTeX: 10.1021/x1, 10.1038/y2, 10.1002/z3.",
        "Give me BibTeX entries for 10.1/aa, 10.2/bb, 10.3/cc, 10.4/dd.",
        "I need BibTeX for these DOIs: 10.1/p, 10.2/q, 10.3/r.",
        "Format 10.1126/s1, 10.1063/s2, 10.1039/s3 as BibTeX.",
        "Produce BibTeX records for 10.1/m1, 10.2/m2, 10.3/m3.",
        "Make BibTeX out of 10.1073/n1, 10.1021/n2, 10.1002/n3, 10.1038/n4.",
        "Citations in BibTeX, please: 10.1/e, 10.2/f, 10.3/g.",
        "Give me BibTeX for these four: 10.1/w, 10.2/x, 10.3/y, 10.4/z.",
        "Export 10.1016/a, 10.1021/b, 10.1126/c to BibTeX.",
        "BibTeX entries for 10.1/k1, 10.2/k2, 10.3/k3 if you can.",
        "Turn 10.1/t, 10.2/u, 10.3/v into BibTeX for me.",
        "I want BibTeX for 10.1038/g1, 10.1039/g2, 10.1063/g3.",
        "Generate BibTeX for these three DOIs: 10.1/h, 10.2/i, 10.3/j.",
        "Please give BibTeX for 10.1002/c1, 10.1021/c2, 10.1126/c3.",
        "BibTeX, please, for 10.1/d1, 10.2/d2, 10.3/d3, 10.4/d4.",
    ],

    # artifact -- self-contained deliverable -> create_artifact.
    "html_poster_artifact": [
        "Turn these three results into a printable one-page HTML handout.",
        "Build a slide summarising these findings.",
        "Make an HTML one-pager I can print for the poster session.",
        "Create a single-page summary document of these numbers.",
        "Put these results into a clean HTML poster.",
        "Make me a printable fact sheet from these three results.",
        "Design an HTML page that lays out these findings nicely.",
        "Generate a one-slide summary of these measurements.",
        "Assemble these into a tidy printable handout.",
        "Make a self-contained HTML summary of the three results.",
        "Build a poster-style page for these numbers.",
        "Lay these findings out as a one-page printable.",
        "Create an HTML card summarising these three measurements.",
        "Produce a printable summary sheet of these results.",
        "Make a single HTML page presenting these findings.",
        "Turn these into a clean one-page poster I can hand out.",
    ],

    # multi_turn -- prior turn was research; THIS turn is a plot -> run_python.
    # Implicit-plot (no 'python'/'code' word). prior_turns inherited.
    "reroute_research_to_compute": [
        "Now graph those enhancement values against magnetic field.",
        "Plot the numbers you just gave me versus temperature.",
        "Chart those polarization levels for me.",
        "Make a figure of those values against field strength.",
        "Show me a plot of those results over time.",
        "Visualise those numbers as a scatter plot.",
        "Draw a line through those data points for me.",
        "Plot those enhancement factors versus catalyst concentration.",
        "Turn those numbers into a bar chart.",
        "Graph the values you just listed.",
        "Give me a plot of those measurements.",
        "Make a quick figure from those numbers.",
        "Plot those against the field and show me.",
        "Chart the trend in those values.",
        "Overlay those two sets of numbers on one plot.",
        "Render those results as a figure.",
    ],

    # abstain -- out-of-corpus 'our group' ask -> paper_search FIRST, do NOT
    # fabricate. KEEP an out-of-domain topic + the 'our group/lab/we' framing.
    "corpus_absent_abstain": [
        "What does our group's published work say about dark matter detection?",
        "Summarise our lab's publications on stock-market prediction.",
        "According to our papers, what did we find about medieval poetry?",
        "What has our group reported on jet-engine turbine design?",
        "In our own publications, what did we conclude about coral reef ecology?",
        "What does our lab's work say about Roman military history?",
        "Across our papers, what have we found on quantum chromodynamics confinement?",
        "What did our group publish about exoplanet atmospheres?",
        "Summarise our lab's research on supply-chain logistics.",
        "According to our work, what did we measure for glacier retreat rates?",
        "What has our group documented about Renaissance painting techniques?",
        "From our publications, what did we find about plate tectonics?",
        "What does our group's research say about cryptocurrency markets?",
        "In our papers, what did we report on songbird migration?",
        "What has our lab studied about volcanic ash dispersal?",
        "Across our own work, what did we conclude about ancient trade routes?",
    ],

    # robustness -- colloquial / codeswitched weather -> web_search.
    # Distinct cities from the TRAIN chat weather (which uses Munich).
    "weather_paraphrase": [
        "berlin wetter morgen, brauch ich ne jacke?",
        "is it raining in Hamburg right now",
        "quel temps fait-il a Paris aujourd'hui",
        "wie is het weer in Amsterdam vandaag",
        "che tempo fa a Roma adesso",
        "koln wetter jetzt, regnet es?",
        "what's it doing weather-wise in Dresden today",
        "hace frio en Madrid ahora mismo?",
        "weather in Vienna right now please",
        "regnet es gerade in Frankfurt",
        "what's the temp in Zurich this morning",
        "il pleut a Lyon en ce moment?",
        "how's the weather over in Hamburg today",
        "stuttgart wetter heute nachmittag?",
        "is it sunny in Brussels right now",
        "what's the forecast for Geneva today",
    ],
}


# ===========================================================================
# Normalisation + leakage guard (shared shape with router_examples_build).
# ===========================================================================
def _normalise(q: str) -> str:
    return re.sub(r"\s+", " ", re.sub(r"[^\w\s]", " ", q.lower())).strip()


def _anchor_ids() -> set[str]:
    sys.path.insert(0, str(_HERE.parent))
    from routing.routing_eval import SEED_ITEMS  # noqa: E402
    return {it.id for it in SEED_ITEMS}, {_normalise(it.query) for it in SEED_ITEMS}


def _train_queries() -> set[str]:
    if not _TRAIN.exists():
        print(f"  (train-leakage check skipped: {_TRAIN} absent)")
        return set()
    doc = json.loads(_TRAIN.read_text())
    return {_normalise(e["query"]) for e in doc.get("examples", [])}


def main() -> int:
    valid_ids, anchor_norms = _anchor_ids()
    train = _train_queries()

    paraphrases: list[dict] = []
    seen_norm: set[str] = set()
    leaks_train: list[str] = []
    leaks_anchor: list[str] = []
    dups: list[str] = []

    for anchor_id, queries in _PARAPHRASES.items():
        if anchor_id not in valid_ids:
            raise SystemExit(f"unknown anchor id in _PARAPHRASES: {anchor_id!r}")
        for i, q in enumerate(queries, start=1):
            q = q.strip()
            n = _normalise(q)
            if n in train:
                leaks_train.append(q)
            if n in anchor_norms:
                leaks_anchor.append(q)
            if n in seen_norm:
                dups.append(q)
            seen_norm.add(n)
            paraphrases.append({"anchor_id": anchor_id, "pid": f"p{i:02d}", "query": q})

    problems = []
    if leaks_train:
        problems.append("TRAIN leakage (paraphrase == labelled example):\n  "
                        + "\n  ".join(sorted(set(leaks_train))))
    if leaks_anchor:
        problems.append("ANCHOR collision (paraphrase == a seed query):\n  "
                        + "\n  ".join(sorted(set(leaks_anchor))))
    if dups:
        problems.append("DUPLICATE paraphrases (normalised):\n  "
                        + "\n  ".join(sorted(set(dups))))
    if problems:
        raise SystemExit("ROUTING PARAPHRASE BUILD FAILED:\n" + "\n\n".join(problems))

    by_anchor: dict[str, int] = {}
    for p in paraphrases:
        by_anchor[p["anchor_id"]] = by_anchor.get(p["anchor_id"], 0) + 1

    doc = {
        "meta": {
            "purpose": "A5 frozen routing-eval paraphrase set. Each entry inherits "
                       "its anchor's Expected (same predicates/gate/category); only "
                       "the query varies.",
            "method": "hand-authored, literals preserved, leakage-guarded",
            "disjoint_from": "backend/retrieval/router_examples.json (router TRAIN set)",
            "counts": {"total": len(paraphrases), "by_anchor": by_anchor},
        },
        "paraphrases": paraphrases,
    }
    _OUT.write_text(json.dumps(doc, indent=2, ensure_ascii=False) + "\n")
    print(f"wrote {_OUT}: {len(paraphrases)} paraphrases across {len(by_anchor)} anchors")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

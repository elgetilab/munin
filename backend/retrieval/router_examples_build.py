"""Generate the router's labelled example set (A3 step 3; expanded 2026-06-25).

Sources:
  1. A hand-authored, curated set (`_CURATED`) of ~80 examples per profile,
     covering each profile's sub-patterns with domain-appropriate phrasings
     (the group works on hyperpolarization / NMR / molecular biophysics).
  2. persona `prompt_suggestions` (curated for cross-profile label noise).

NOT a source any more: routing-eval SEED ITEMS. Including them leaked the
TEST queries into the labelled (train) set, so the eval measured memorisation
instead of generalisation. The labelled set and the routing-eval test set are
now DISJOINT by construction. Examples here use generalised phrasings of each
pattern, never the verbatim test queries.

Output: router_examples.json, committed (small, provenance-tagged). The router
embeds these with BGE at load. K=8 KNN means LOCAL density matters: ~12-16
examples per sub-pattern give a near-unanimous neighbourhood for clear queries.

Run:  python backend/retrieval/router_examples_build.py
"""
from __future__ import annotations

import json
from pathlib import Path

_REPO = Path(__file__).resolve().parents[2]
_PERSONAS = _REPO / "shared" / "personas"
_OUT = Path(__file__).resolve().parent / "router_examples.json"

_PROFILES = {"chat", "research", "code"}

# Drop chat prompt_suggestions that collide with near-identical research ones
# (paper-finding / scientific web-search lean RESEARCH; keep the KNN labels
# clean). Matched by a substring of the query.
_EXCLUDE_SUBSTRINGS = {
    "chat": [
        "Find papers about cholesterol",
        "Search the web for the latest developments in cryo-EM",
    ],
}

# ===========================================================================
# Curated examples. Generalised phrasings of each profile's sub-patterns;
# deliberately NOT the verbatim routing-eval test queries (no train/test leak).
# ===========================================================================
_CURATED: dict[str, list[str]] = {
    # -------------------------------------------------------------------
    "code": [
        # implicit plotting (no "python"/"code" word) - the reroute pattern
        "plot these values against field strength",
        "graph the polarization versus temperature",
        "chart the results from that titration",
        "now plot those numbers for me",
        "make a figure showing intensity versus time",
        "visualize this dataset as a scatter plot",
        "draw a bar chart of the measured rates",
        "plot the time series of the NMR signal",
        "show me a histogram of those values",
        "make a line plot of signal decay over time",
        "plot the data points and fit a curve through them",
        "create a heatmap of the correlation matrix",
        "graph these two columns against each other",
        "plot the spectrum with the peaks labelled",
        "overlay the two curves on one plot",
        "make a publication-quality figure of these results",
        # explicit python / scripting
        "write Python code to create a matplotlib figure with two subplots",
        "write a Python script to read a CSV and plot the columns",
        "use matplotlib to plot the MD trajectory RMSD",
        "write code using MDAnalysis to compute the radius of gyration",
        "Python script to load experimental data and compute statistics",
        "write a function that fits a Gaussian to the spectral peak",
        "code to numerically integrate this rate equation",
        "implement a Savitzky-Golay smoothing filter in Python",
        "write a Python class to hold the experiment metadata",
        "script to batch-rename these data files by timestamp",
        # data analysis
        "analyze this CSV file and summarise the statistics",
        "compute the mean and standard deviation of these measurements",
        "load the dataset and find the outliers",
        "calculate the correlation between these two variables",
        "run a linear regression on these points and report the slope",
        "do a Fourier transform of this signal",
        "fit an exponential decay to the relaxation data",
        "integrate the area under each peak in this spectrum",
        "normalise these intensities and tabulate them",
        "bin this data and compute a per-bin average",
        # scientific computing
        "simulate the MD trajectory and extract the RMSD over time",
        "compute the radial distribution function from this trajectory",
        "solve this system of differential equations numerically",
        "estimate the diffusion coefficient from the mean squared displacement",
        "build a kinetic model of the SABRE polarization buildup",
        "estimate the activation energy from these rate constants",
        "propagate the spin density matrix over the pulse sequence",
        "Monte Carlo sample the conformational ensemble",
        # dev / tooling
        "build a REST API with FastAPI for the sample database",
        "automate downloading these datasets from the server",
        "create a command-line tool that parses the log files",
        "set up a SLURM submission script for this job array",
        "write a Snakemake workflow for the analysis pipeline",
        "containerise this analysis script with Docker",
        # ML / DL
        "train a neural network to classify these spectra",
        "build a graph neural network for molecular property prediction",
        "set up a PyTorch training loop for this regression model",
        "implement a random forest on this feature table",
        "cluster these samples with k-means and plot the result",
        # LaTeX / docs (code persona owns LaTeX/typesetting compute)
        "create a LaTeX Beamer presentation template with a title slide",
        "write the LaTeX for a three-panel figure with subcaptions",
        "generate a LaTeX table from this CSV",
        "compile this LaTeX document and fix the errors",
        # games / misc programming
        "code me a simple Pong game in Python with Pygame",
        "write a Tetris clone in Python",
        "make a matplotlib animation of the simulation",
        "build a small Streamlit dashboard for these results",
        # debugging
        "fix this Python error: IndexError list index out of range",
        "why does my matplotlib plot come out blank",
        "debug this NumPy broadcasting mismatch",
        "my script crashes on the second iteration, help me find why",
        "this pandas groupby returns NaN, what is wrong",
        "optimise this slow loop with vectorised NumPy",
        # more implicit plotting / compute to densify the sub-pattern
        "plot field strength on the x axis and enhancement on the y axis",
        "make a scatter plot coloured by sample group",
        "render a contour plot of the energy surface",
        "plot residuals from the fit",
        "generate a box plot comparing the conditions",
        "draw the calibration curve and report R squared",
        "compute and plot the autocorrelation of the signal",
        "tabulate and plot the buildup rates per catalyst",
    ],
    # -------------------------------------------------------------------
    "research": [
        # find papers / literature search
        "find papers on parahydrogen-induced polarization for in-vivo imaging",
        "search for studies about SABRE catalyst lifetime",
        "look for recent work on dissolution DNP of pyruvate",
        "find literature on lipid raft organisation in membranes",
        "search the corpus for papers on hyperpolarized xenon MRI",
        "what papers discuss singlet order lifetimes",
        "find studies measuring T1 relaxation of hyperpolarized substrates",
        "look up papers on iridium SABRE catalysts",
        "search for work on cryo-EM of membrane proteins",
        "find papers about cholesterol-lipid interactions in cell membranes",
        "which studies report polarization transfer efficiencies",
        "search for reviews on quantum sensing with NV centres",
        # literature review / synthesis
        "review the literature on molecular dynamics of membrane proteins",
        "summarise the research on PHIP polarization mechanisms",
        "give me an overview of the field of hyperpolarized MRI",
        "synthesise what is known about SABRE-SHEATH",
        "write a literature review on AlphaFold and structure prediction",
        "summarise recent progress in dissolution DNP",
        "what does the literature say about catalyst poisoning in SABRE",
        "pull together the evidence on lipid-protein coupling",
        # citations / citation graph
        "which papers cite the Zeitler 2021 review",
        "explore the citation network of this hyperpolarization paper",
        "find papers that reference this DOI on SABRE",
        "what are the most cited works on parahydrogen chemistry",
        "trace the references of this molecular dynamics study",
        "who has built on this dissolution DNP method",
        "show the papers citing the original PHIP discovery",
        # compare methods / approaches
        "compare AlphaFold predictions with experimental crystallography",
        "compare SABRE and dissolution DNP for metabolic imaging",
        "contrast PHIP and DNP polarization mechanisms",
        "how do different SABRE catalysts compare in efficiency",
        "weigh the trade-offs between cryo-EM and X-ray crystallography",
        "compare relaxation behaviour across these hyperpolarized agents",
        # state of the art / recent advances
        "what is the current state of the art in hyperpolarized imaging",
        "what are the latest advances in cryo-electron microscopy",
        "where does the field stand on in-vivo PHIP",
        "what is new in singlet-state NMR over the past few years",
        "summarise recent breakthroughs in DNP polariser hardware",
        "what is the frontier in quantum-enhanced MRI",
        # explain scientific concepts
        "explain the current understanding of lipid rafts",
        "explain how SABRE transfers polarization to a substrate",
        "what is the mechanism of dissolution DNP",
        "explain the physics behind singlet order protection",
        "what is parahydrogen and why does it matter for NMR",
        "explain the spin dynamics of the SABRE-SHEATH experiment",
        "describe the theory of nuclear Overhauser enhancement",
        # read / analyse a specific paper
        "read this DOI and tell me what polarization method they used",
        "summarise the methods section of this paper",
        "what catalyst did this SABRE study use",
        "extract the reported enhancement factors from this paper",
        "what were the main findings of this dissolution DNP study",
        "pull the experimental conditions out of this paper",
        # corpus / group questions
        "what did our group report about catalyst lifetime",
        "summarise our lab's work on hyperpolarized pyruvate",
        "what have we published on SABRE-SHEATH",
        "find our group's papers on singlet lifetimes",
        # domain QA grounded in literature
        "what enhancement factors are typical for SABRE at low field",
        "what substrates work best with dissolution DNP",
        "which nuclei are commonly hyperpolarized for imaging",
        "what limits the lifetime of hyperpolarization in vivo",
        "how does field strength affect SABRE polarization",
        "what are the reported T1 values for hyperpolarized 13C pyruvate",
        # more find/review density
        "find the seminal papers on parahydrogen-induced polarization",
        "search for methods papers on building a DNP polariser",
        "what reviews cover quantum sensing for biomedicine",
        "find recent preprints on hyperpolarized metabolic imaging",
        "look for papers benchmarking SABRE catalysts",
        "survey the literature on membrane protein dynamics simulations",
        "find studies on the biodistribution of hyperpolarized agents",
        "what is published on photo-CIDNP hyperpolarization",
    ],
    # -------------------------------------------------------------------
    "chat": [
        # weather / live facts / news
        "what's the weather in Leipzig today",
        "is it going to rain this afternoon",
        "what's the weather like right now",
        "do I need an umbrella today",
        "what's the forecast for the weekend",
        "what time is sunset today",
        "what's in the news about science funding",
        "any recent headlines on the EU research budget",
        "what's the current exchange rate for dollars to euros",
        "what's today's date",
        # writing
        "write a scientific abstract for a paper on molecular dynamics",
        "draft the opening paragraph of a grant proposal",
        "write a cover letter for this manuscript submission",
        "help me write a short bio for a conference",
        "draft an email to a collaborator about scheduling a meeting",
        "write a clear summary of this project for a general audience",
        "compose a thank-you note to a reviewer",
        "write a tweet announcing our new paper",
        "draft a paragraph describing the significance of this work",
        "help me write the acknowledgements section",
        "write a one-sentence summary of this result",
        "draft a response to a peer reviewer's comments",
        "write a short blurb for the lab website",
        "compose an out-of-office reply",
        # editing / revising
        "edit this text for clarity and conciseness",
        "make this paragraph more concise",
        "proofread this sentence for grammar",
        "tighten up this abstract, it's too long",
        "rephrase this so it sounds less formal",
        "fix the flow of this paragraph",
        "make this more readable for a broad audience",
        "shorten this to fit a 150-word limit",
        "improve the wording of this title",
        "check this passage for awkward phrasing",
        # definitions / quick knowledge (parametric, no tool)
        "in one paragraph, what is nuclear magnetic resonance",
        "what does the term enantiomer mean",
        "briefly, what is a Fourier transform",
        "explain what a p-value is in simple terms",
        "what is the difference between accuracy and precision",
        "define entropy in one sentence",
        "what is Avogadro's number",
        "what does pH measure",
        "give me a quick definition of a catalyst",
        "what is the boiling point of water in Fahrenheit",
        # general help / conversation
        "can you help me brainstorm a title for this talk",
        "give me a few ideas for a project name",
        "summarise this text I'm pasting in",
        "help me outline the structure of this presentation",
        "what's a good analogy to explain spin to a child",
        "rephrase this in plain language",
        "translate this sentence into German",
        "what's a polite way to decline this invitation",
        "suggest a subject line for this email",
        "help me phrase this feedback constructively",
        # quick conversions / everyday calc (chat-level, not coding)
        "convert 25 degrees Celsius to Fahrenheit",
        "how many millilitres are in a cup",
        "what's 15 percent of 240",
        "how many days until the end of the month",
        "convert 5 micromolar to nanomolar",
        # misc everyday
        "set a reminder tone for the meeting",
        "what's a good greeting for a formal email",
        "recommend a structure for a cover letter",
        "help me make a packing list for a conference trip",
        "suggest a few icebreaker questions for a lab social",
        "what should I say to introduce a seminar speaker",
        "give me a short motivational message for the team",
        "draft a friendly reminder about the group meeting",
    ],
}


def _from_curated() -> list[dict]:
    out = []
    for profile, queries in _CURATED.items():
        for q in queries:
            out.append({"query": q.strip(), "profile": profile, "source": "curated"})
    return out


def _from_prompt_suggestions() -> list[dict]:
    out = []
    for pid in ("chat", "research", "code"):
        data = json.loads((_PERSONAS / f"{pid}.json").read_text())
        excludes = _EXCLUDE_SUBSTRINGS.get(pid, [])
        for s in data.get("prompt_suggestions") or []:
            query = (s.get("content") or s.get("title") or "").strip()
            if not query or any(ex in query for ex in excludes):
                continue
            out.append({"query": query, "profile": pid,
                        "source": f"prompt_suggestion:{pid}"})
    return out


def main() -> int:
    examples = _from_curated() + _from_prompt_suggestions()
    # de-dup on (query, profile)
    seen, deduped = set(), []
    for e in examples:
        k = (e["query"].lower(), e["profile"])
        if k not in seen:
            seen.add(k)
            deduped.append(e)

    by_profile: dict[str, int] = {}
    for e in deduped:
        by_profile[e["profile"]] = by_profile.get(e["profile"], 0) + 1

    doc = {
        "meta": {
            "purpose": "A3 router KNN labelled example set (profile = chat|research|code)",
            "sources": ["hand-authored curated set", "persona prompt_suggestions (curated)"],
            "note": ("Committed queries only; router embeds with BGE at load. "
                     "DISJOINT from the routing-eval test items (no train/test leak); "
                     "seed items are NOT a source."),
            "counts": {"total": len(deduped), **by_profile},
        },
        "examples": deduped,
    }
    _OUT.write_text(json.dumps(doc, indent=2, ensure_ascii=False) + "\n")
    print(f"wrote {_OUT}: {len(deduped)} examples {by_profile}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

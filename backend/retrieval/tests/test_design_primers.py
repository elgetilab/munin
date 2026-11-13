"""design_primers returns only designs the engine verified, and a widget spec.

Run in the retrieval image; see tests/README.md. Standalone: prints [PASS]/[FAIL]
and exits non-zero on failure. Offline: the vectors are the vendored FASTAs
under tests/data/primer/ and the artifact store is faked.

What these assert, and why
--------------------------
The published failure mode for primer design is bookkeeping, not chemistry
(docs/agent-track/PRIMER-DESIGN-PLAN.md section 1): primers that do not occur
in the template, wrong strand, off-by-one coordinates, junctions that do not
join. So the load-bearing tests here are the boring ones:

  * every returned primer's annealing region really is at the coordinates
    reported, on the strand reported;
  * the product size is re-derived by running `verify.simulate_pcr` again,
    independently of the design path;
  * every failure `outcome` is reachable AND returns no designs, so the model
    can never see a design next to a failure;
  * no returned design carries an unresolved HARD check failure;
  * the widget spec matches the frontend contract field for field, which is
    checked by a validator in this file rather than by eyeball.
"""

import asyncio
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from mcp.context import current_conversation_id, current_user_email  # noqa: E402
from mcp.schemas import CORE_TOOLS, MCP_TOOLS  # noqa: E402
from mcp._dispatch import get_dispatcher  # noqa: E402
from mcp.tools import primers  # noqa: E402
from primer import rules, thermo, verify  # noqa: E402

DATA = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data", "primer")
failures = 0


def check(name, ok, detail=""):
    global failures
    failures += 0 if ok else 1
    suffix = f" --- {detail}" if detail and not ok else ""
    print(f"[{'PASS' if ok else 'FAIL'}] {name}{suffix}")


def load(filename):
    with open(os.path.join(DATA, filename), encoding="utf-8") as handle:
        return "".join(line.strip() for line in handle
                       if not line.startswith(">")).upper()


PUC19 = load("pUC19_L09137.fasta")
PBLUESCRIPT = load("pBluescriptIISKplus_X52328.fasta")
check("the vendored vectors are the lengths the plan records",
      len(PUC19) == 2686 and len(PBLUESCRIPT) == 2961,
      f"{len(PUC19)}, {len(PBLUESCRIPT)}")


# ---- running the tool with a faked artifact store
def run(conversation="c1", user="a@b.c", **arguments):
    """(envelope, stored widget specs). No network, no database."""
    stored = []

    async def fake_create(**kwargs):
        stored.append(kwargs)
        return {"id": "art_1", "artifact_id": "art_1", "title": kwargs["title"],
                "content_type": kwargs["content_type"], "version": 1,
                "conversation_id": kwargs["conversation_id"]}

    import artifact_store

    real = artifact_store.create_artifact
    artifact_store.create_artifact = fake_create
    email_token = current_user_email.set(user)
    conv_token = current_conversation_id.set(conversation)
    try:
        envelope = asyncio.run(primers.design_primers(**arguments))
    finally:
        current_user_email.reset(email_token)
        current_conversation_id.reset(conv_token)
        artifact_store.create_artifact = real
    specs = [json.loads(call["content"]) for call in stored]
    return envelope, specs


# ---- the widget contract, as a validator rather than an eyeball
SPEC_KEYS = {"kind", "version", "mode", "template", "target", "pairs",
             "summary", "notes"}
TEMPLATE_KEYS = {"name", "length", "circular"}
PAIR_KEYS = {"name", "forward", "reverse", "product_size", "amplicon",
             "tm_difference", "penalty", "checks", "verified"}
PRIMER_KEYS = {"sequence", "display", "anneal", "tail", "strand", "start",
               "end", "length", "tm", "gc_percent"}
CHECK_KEYS = {"rule", "label", "severity", "passed", "value", "threshold",
              "source", "detail", "computed"}
VERIFIED_KEYS = {"outcome", "product_count", "product_sizes",
                 "single_clean_product"}
SUMMARY_KEYS = {"total", "passed", "hard_failures", "soft_failures",
                "not_computed", "usable"}


def validate_spec(spec):
    """Every way the spec can deviate from the fixed frontend contract."""
    problems = []

    def want(condition, message):
        if not condition:
            problems.append(message)

    want(isinstance(spec, dict) and set(spec) == SPEC_KEYS,
         f"top-level keys {sorted(spec) if isinstance(spec, dict) else spec}")
    if problems:
        return problems
    want(spec["kind"] == "primer_design", f"kind {spec['kind']!r}")
    want(spec["version"] == 1, f"version {spec['version']!r}")
    want(spec["mode"] in primers.MODES, f"mode {spec['mode']!r}")
    want(set(spec["template"]) == TEMPLATE_KEYS,
         f"template keys {sorted(spec['template'])}")
    want(isinstance(spec["template"]["name"], str), "template.name is not a string")
    want(isinstance(spec["template"]["length"], int), "template.length is not an int")
    want(isinstance(spec["template"]["circular"], bool),
         "template.circular is not a bool")
    want(spec["target"] is None or (isinstance(spec["target"], list)
                                    and len(spec["target"]) == 2),
         f"target {spec['target']!r}")
    want(isinstance(spec["notes"], list)
         and all(isinstance(note, str) for note in spec["notes"]),
         "notes is not a list of strings")
    want(set(spec["summary"]) == SUMMARY_KEYS,
         f"summary keys {sorted(spec['summary'])}")
    want(isinstance(spec["summary"].get("usable"), bool), "summary.usable")
    want(isinstance(spec["pairs"], list) and spec["pairs"], "pairs is empty")

    for pair in spec["pairs"] if isinstance(spec["pairs"], list) else []:
        want(set(pair) == PAIR_KEYS, f"pair keys {sorted(pair)}")
        if set(pair) != PAIR_KEYS:
            continue
        want(isinstance(pair["name"], str) and pair["name"], "pair.name")
        want(isinstance(pair["product_size"], int), "pair.product_size")
        want(isinstance(pair["amplicon"], str), "pair.amplicon")
        want(len(pair["amplicon"]) <= primers.MAX_AMPLICON_CHARS,
             "pair.amplicon is over the cap")
        want(isinstance(pair["tm_difference"], (int, float)), "pair.tm_difference")
        want(pair["penalty"] is None or isinstance(pair["penalty"], (int, float)),
             "pair.penalty")
        want(set(pair["verified"]) == VERIFIED_KEYS,
             f"verified keys {sorted(pair['verified'])}")
        want(pair["verified"]["single_clean_product"] is True,
             "a pair was shown that is not a single clean product")
        want(isinstance(pair["checks"], list) and pair["checks"], "pair.checks")
        for item in pair["checks"]:
            want(set(item) == CHECK_KEYS, f"check keys {sorted(item)}")
            if set(item) != CHECK_KEYS:
                continue
            want(item["severity"] in (rules.HARD, rules.SOFT),
                 f"severity {item['severity']!r}")
            want(isinstance(item["passed"], bool), "check.passed")
            want(item["value"] is None or isinstance(item["value"], (int, float)),
                 "check.value")
            want(isinstance(item["source"], str) and item["source"],
                 "check.source is empty")
        want(pair["forward"] is not None or pair["reverse"] is not None,
             "a pair with neither primer")
        for role in ("forward", "reverse"):
            primer = pair[role]
            if primer is None:
                continue
            want(set(primer) == PRIMER_KEYS, f"{role} keys {sorted(primer)}")
            if set(primer) != PRIMER_KEYS:
                continue
            want(primer["strand"] in (verify.PLUS, verify.MINUS),
                 f"{role}.strand {primer['strand']!r}")
            want(primer["sequence"] == primer["tail"] + primer["anneal"],
                 f"{role}.sequence is not tail + anneal")
            want(primer["length"] == len(primer["sequence"]), f"{role}.length")
            # pydna's convention: lowercase 5' tail, uppercase annealing region.
            want(primer["display"] == primer["tail"].lower() + primer["anneal"].upper(),
                 f"{role}.display is not the tail/anneal rendering")
            want(primer["display"].upper() == primer["sequence"].upper(),
                 f"{role}.display is not the same oligo as sequence")
            if not primer["tail"]:
                want(primer["display"] == primer["sequence"],
                     f"untailed {role}.display differs from its sequence")
            want(isinstance(primer["start"], int) and isinstance(primer["end"], int),
                 f"{role} coordinates are not integers")
            want(primer["end"] - primer["start"] == len(primer["anneal"]),
                 f"{role} coordinates do not span the annealing region")
    return problems


# ---- a successful PCR design, checked against the template itself
envelope, specs = run(mode="pcr", template=PUC19, target=[400, 450],
                      product_size_range=[100, 300], circular=True, n=2,
                      template_name="pUC19")
check("a targeted PCR design succeeds", envelope["outcome"] == "designed",
      envelope.get("detail", ""))
check("n is honoured", len(envelope["designs"]) == 2,
      f"{len(envelope['designs'])} designs")

occurs = True
sizes_agree = True
contains_target = True
for design in envelope["designs"]:
    forward, reverse = design["forward"], design["reverse"]
    occurs &= PUC19[forward["start"]:forward["end"]] == forward["sequence"]
    occurs &= thermo.reverse_complement(
        PUC19[reverse["start"]:reverse["end"]]) == reverse["sequence"]
    # Re-run the simulation from the returned strings alone, with nothing
    # carried over from the design path.
    result = verify.simulate_pcr(forward["sequence"], reverse["sequence"],
                                 PUC19, circular=True)
    sizes_agree &= result.is_single
    sizes_agree &= result.product is not None
    sizes_agree &= result.product.length == design["product_size"]
    contains_target &= forward["end"] <= 400 and reverse["start"] >= 450
check("every returned primer occurs in the template at the coordinates given",
      occurs)
check("an independent in-silico PCR reproduces the product size", sizes_agree)
check("the product brackets the requested target", contains_target)

check("the design carries the engine's verdict, not a claim",
      all(d["verified"]["single_clean_product"] is True
          and d["verified"]["outcome"] == verify.SINGLE_PRODUCT
          for d in envelope["designs"]))
check("the envelope states what was and was not verified",
      "no reference database" in envelope["verification"]
      and any("Specificity" in note for note in envelope["notes"]))
check("the envelope tells the model to report the engine's values",
      "Do not retype" in envelope.get("instruction", ""))

no_hard = []
for design in envelope["designs"]:
    no_hard.extend(c for c in design["checks_notable"]
                   if c["severity"] == rules.HARD and not c["passed"])
check("no returned design carries an unresolved HARD failure", not no_hard,
      str(no_hard[:1]))

# The envelope lists only the checks the model must act on. Everything that
# passed is accounted for by `summary`, so the model still cannot claim a
# clean design that the engine did not grant.
check("the envelope carries only failed or uncomputed checks",
      all(not c["passed"] or not c["computed"]
          for d in envelope["designs"] for c in d["checks_notable"]))
check("the summary still counts EVERY rule, not just the listed ones",
      envelope["summary"]["total"]
      > sum(len(d["checks_notable"]) for d in envelope["designs"])
      and envelope["summary"]["usable"] is True)
check("the envelope says where the full rule list went",
      all("widget" in d["checks_note"] for d in envelope["designs"]))

# THE regression guard. Sending every rule to the model cost ~2,600 tokens per
# design, so a default call ran to ~12,400 and a turn that called the tool
# three times overflowed the 60k context and died on a vLLM 400 before it
# could answer. Observed live on 2026-11-13. This bound is generous against
# the ~2,800 tokens a 5-design envelope now costs, and tight enough that
# putting the full checks back would trip it.
envelope_tokens = len(json.dumps(envelope)) // 3
check("the envelope stays well inside the context budget",
      envelope_tokens < 6000, f"~{envelope_tokens} tokens")
check("the model is not handed kilobases of amplicon",
      all("amplicon" not in d and len(d["amplicon_preview"]) < 200
          for d in envelope["designs"]))

# ---- the widget artifact
check("exactly one widget artifact is stored", len(specs) == 1)
problems = validate_spec(specs[0]) if specs else ["no spec stored"]
check("the widget spec matches the frontend contract", not problems,
      "; ".join(problems[:3]))
# A rule whose input was never supplied carries passed=True so it cannot
# disqualify a primer. If `computed` is dropped on the way to the widget, the
# renderer can only tell "checked and fine" from "never checked" by matching
# prose in `detail`, and a wording change would silently turn an unevaluated
# rule into a green tick. It was dropped once; this is the guard.
check("every widget check carries the `computed` field",
      bool(specs) and all(
          "computed" in c and isinstance(c["computed"], bool)
          for p in specs[0]["pairs"] for c in p["checks"]))

check("the widget carries the whole amplicon the envelope only previewed",
      specs[0]["pairs"][0]["amplicon"]
      == verify.simulate_pcr(specs[0]["pairs"][0]["forward"]["sequence"],
                             specs[0]["pairs"][0]["reverse"]["sequence"],
                             PUC19, circular=True).product.sequence)
check("the widget spec stays small",
      len(json.dumps(specs[0])) < 64 * 1024, f"{len(json.dumps(specs[0]))} bytes")
check("the artifact has the widget content type",
      envelope.get("content_type") == primers.WIDGET_CONTENT_TYPE)
check("the widget's target is the requested window",
      specs[0]["target"] == [400, 450]
      and specs[0]["template"] == {"name": "pUC19", "length": 2686,
                                   "circular": True})

# ---- JSON round-trip of the whole envelope
restored = json.loads(json.dumps(envelope))
check("the whole envelope survives a JSON round-trip", restored == envelope)

# ---- the other three modes
seq_envelope, seq_specs = run(mode="sequencing", template=PUC19,
                              feature_position=1200, direction="forward",
                              offset=100, circular=True)
check("sequencing mode designs a primer",
      seq_envelope["outcome"] == "designed", seq_envelope.get("detail", ""))
check("the sequencing widget validates",
      not validate_spec(seq_specs[0]) if seq_specs else False,
      "; ".join(validate_spec(seq_specs[0])[:3]) if seq_specs else "no spec")
seq_design = seq_envelope["designs"][0]
check("a forward sequencing primer fills the forward slot only",
      seq_design["reverse"] is None and seq_design["forward"]["strand"] == "+")
check("the sequencing primer is where it says it is",
      PUC19[seq_design["forward"]["start"]:seq_design["forward"]["end"]]
      == seq_design["forward"]["sequence"])
check("the sequencing primer reads toward the feature from the offset",
      seq_design["forward"]["end"] == 1200 - 100)
check("a sequencing primer makes no amplicon",
      seq_design["product_size"] == 0
      and seq_design["verified"]["product_sizes"] == [])

rev_envelope, _ = run(mode="sequencing", template=PUC19, feature_position=1200,
                      direction="reverse", offset=100, circular=True)
rev_design = rev_envelope["designs"][0]
check("a reverse sequencing primer fills the reverse slot only",
      rev_envelope["outcome"] == "designed"
      and rev_design["forward"] is None
      and rev_design["reverse"]["strand"] == "-"
      and thermo.reverse_complement(
          PUC19[rev_design["reverse"]["start"]:rev_design["reverse"]["end"]])
      == rev_design["reverse"]["sequence"])

res_envelope, res_specs = run(mode="restriction", template=PUC19,
                              target=[400, 1000], enzymes=["EcoRI", "XhoI"],
                              circular=True)
check("restriction mode designs a tailed pair",
      res_envelope["outcome"] == "designed", res_envelope.get("detail", ""))
check("the restriction widget validates",
      not validate_spec(res_specs[0]) if res_specs else False,
      "; ".join(validate_spec(res_specs[0])[:3]) if res_specs else "no spec")
res_design = res_envelope["designs"][0]
check("the restriction tails carry the requested sites",
      res_design["forward"]["tail"].endswith("GAATTC")
      and res_design["reverse"]["tail"].endswith("CTCGAG"))
check("the restriction annealing regions sit on the insert",
      PUC19[res_design["forward"]["start"]:res_design["forward"]["end"]]
      == res_design["forward"]["anneal"]
      and thermo.reverse_complement(
          PUC19[res_design["reverse"]["start"]:res_design["reverse"]["end"]])
      == res_design["reverse"]["anneal"])
check("the restriction product is the insert plus both tails",
      res_design["product_size"]
      == 600 + len(res_design["forward"]["tail"])
      + len(res_design["reverse"]["tail"]))
check("the tail convention is pydna's, lowercase tail then uppercase anneal",
      res_design["forward"]["display"]
      == res_design["forward"]["tail"].lower()
      + res_design["forward"]["anneal"].upper())
check("the restriction envelope says the tails were not in the template",
      any("ANNEALING regions only" in note for note in res_envelope["notes"]))

FRAGMENTS = [PUC19[:900], PUC19[900:1800], PUC19[1800:]]
asm_envelope, asm_specs = run(mode="overlap_assembly", fragments=FRAGMENTS,
                              circular=True, template_name="pUC19 rebuild")
check("overlap assembly designs one pair per fragment",
      asm_envelope["outcome"] == "designed"
      and len(asm_envelope["designs"]) == 3, asm_envelope.get("detail", ""))
check("the assembly widget validates",
      not validate_spec(asm_specs[0]) if asm_specs else False,
      "; ".join(validate_spec(asm_specs[0])[:3]) if asm_specs else "no spec")
assembly = "".join(FRAGMENTS)
junctions_real = True
for design in asm_envelope["designs"]:
    forward, reverse = design["forward"], design["reverse"]
    junctions_real &= (assembly[forward["start"]:forward["end"]]
                       == forward["anneal"])
    junctions_real &= (thermo.reverse_complement(
        assembly[reverse["start"]:reverse["end"]]) == reverse["anneal"])
check("every assembly primer anneals where the envelope says it does",
      junctions_real)
check("the homology tails are at least the published minimum",
      all(len(d["forward"]["tail"]) >= 15 for d in asm_envelope["designs"]))
check("the assembly was simulated, not asserted",
      any("reproduces" in note for note in asm_envelope["notes"]))

# The common vector + insert case, which is linear: the outer two primers
# carry no homology because there is no junction at the ends of a line.
linear_envelope, linear_specs = run(mode="overlap_assembly",
                         fragments=[PUC19[:1300], PUC19[1300:]], circular=False)
check("a two-fragment linear assembly designs",
      linear_envelope["outcome"] == "designed",
      linear_envelope.get("detail", ""))
check("the outer ends of a linear assembly carry no homology tail",
      linear_envelope["designs"][0]["forward"]["tail"] == ""
      and linear_envelope["designs"][-1]["reverse"]["tail"] == ""
      and linear_envelope["designs"][1]["forward"]["tail"] != "")

# The one severity this module changes, kept visible rather than hidden.
# Read from the WIDGET spec, which keeps every rule: these checks pass, so
# they are correctly absent from the envelope's notable-only list.
_linear_pair_checks = linear_specs[0]["pairs"][1]["checks"]
tm_checks = [c for c in _linear_pair_checks
             if c["rule"] == "pair.pair_tm_diff"]
check("a tailed pair reports its Tm difference as SOFT, and says why",
      len(tm_checks) == 1 and tm_checks[0]["severity"] == rules.SOFT
      and "SOFT here, not HARD" in tm_checks[0]["detail"],
      str(tm_checks[:1])[:200])
check("the primer-dimer check stays HARD for a tailed pair",
      any(c["rule"] == "pair.hetero_dimer_tm" and c["severity"] == rules.HARD
          for c in _linear_pair_checks))

# ---- the alias, and that it normalises
alias_envelope, alias_specs = run(mode="gibson", fragments=FRAGMENTS,
                                  circular=True)
check("the gibson alias normalises to overlap_assembly",
      alias_envelope["mode"] == "overlap_assembly"
      and alias_specs[0]["mode"] == "overlap_assembly")
check("the alias designs the same primers as the canonical name",
      [d["forward"]["sequence"] for d in alias_envelope["designs"]]
      == [d["forward"]["sequence"] for d in asm_envelope["designs"]])
check("the canonical name is what the engine answers with",
      rules.normalise_mode("GIBSON") == "overlap_assembly"
      and "overlap_assembly" in MCP_TOOLS["design_primers"]["inputSchema"]
      ["properties"]["mode"]["enum"])

# ---- every failure outcome is reachable, and none returns a design
cases = [
    ("an unknown mode", {"mode": "qpcr", "template": PUC19}, "invalid_input"),
    ("a non-DNA template", {"mode": "pcr", "template": "hello world"},
     "invalid_input"),
    ("an empty template", {"mode": "pcr", "template": "  "}, "invalid_input"),
    ("an inverted target",
     {"mode": "pcr", "template": PUC19, "target": [900, 400]}, "invalid_input"),
    ("a target off the end",
     {"mode": "pcr", "template": PUC19, "target": [2600, 9000]}, "invalid_input"),
    ("an unknown enzyme",
     {"mode": "restriction", "template": PUC19, "target": [400, 1000],
      "enzymes": ["NotAnEnzyme"]}, "invalid_input"),
    ("restriction with no enzymes",
     {"mode": "restriction", "template": PUC19}, "invalid_input"),
    ("assembly with one fragment",
     {"mode": "overlap_assembly", "fragments": [PUC19]}, "invalid_input"),
    ("sequencing with no feature",
     {"mode": "sequencing", "template": PUC19}, "invalid_input"),
    ("a template too short for two primers",
     {"mode": "pcr", "template": "ACGTACGTACGTACGTACGT"}, "template_too_short"),
    ("a product range larger than a linear template",
     {"mode": "pcr", "template": PUC19[:500], "product_size_range": [800, 900]},
     "template_too_short"),
    ("an assembly fragment too short to carry a junction",
     {"mode": "overlap_assembly", "fragments": [PUC19[:900], "ACGTACGTACGTACGT"]},
     "template_too_short"),
    ("a long template with nothing to aim at",
     {"mode": "pcr", "template": PUC19 + PBLUESCRIPT}, "ambiguous_target"),
    ("a feature outside the template",
     {"mode": "sequencing", "template": PUC19, "feature_position": 99999},
     "ambiguous_target"),
    ("a sequencing primer that would fall off a linear template",
     {"mode": "sequencing", "template": PUC19, "feature_position": 10,
      "offset": 100}, "ambiguous_target"),
    ("a template with no window inside the GC range",
     {"mode": "pcr", "template": "AT" * 300, "product_size_range": [100, 400]},
     "no_candidates"),
    ("an enzyme that also cuts inside the insert",
     {"mode": "restriction", "template": PUC19, "target": [400, 1000],
      "enzymes": ["EcoRI", "HindIII"]}, "verification_failed"),
    ("a sequencing primer that binds twice",
     {"mode": "sequencing", "template": "ACGTTGCAGGTCATTCGA" * 6,
      "feature_position": 100, "offset": 0, "circular": False},
     "verification_failed"),
]
seen = set()
for label, arguments, expected in cases:
    result, stored = run(**arguments)
    seen.add(result["outcome"])
    check(f"{label} returns {expected}", result["outcome"] == expected,
          f"got {result['outcome']}: {result.get('detail', '')[:160]}")
    check(f"{label} returns no design and no widget",
          not result["designs"] and not stored)
    check(f"{label} says why", len(result.get("detail", "")) > 20,
          result.get("detail", ""))

check("every documented failure outcome is reachable",
      seen == {"invalid_input", "template_too_short", "ambiguous_target",
               "no_candidates", "verification_failed"},
      f"reached {sorted(seen)}")

# ---- the empty-result caveat select.py cannot state for itself
barren, _ = run(mode="pcr", template="AT" * 300,
                product_size_range=[100, 400])
check("an empty search explains the constraint window that bounded it",
      "GC" in barren["detail"] and "SOFT" in barren["detail"]
      and "select.py" in barren["detail"], barren["detail"][:200])

# ---- a design is never returned next to a failure
check("a failure never carries a design",
      all(not run(**arguments)[0]["designs"]
          for _, arguments, expected in cases if expected != "designed"))

# ---- the tool is wired in, and is a specialist tool
check("design_primers has a schema entry", "design_primers" in MCP_TOOLS)
check("design_primers has a dispatcher", get_dispatcher("design_primers") is not None)
check("design_primers is NOT a core tool", "design_primers" not in CORE_TOOLS)
check("the schema offers exactly the modes the tool accepts",
      tuple(MCP_TOOLS["design_primers"]["inputSchema"]["properties"]["mode"]["enum"])
      == primers.MODE_CHOICES)
check("the schema marks the tool as a mutator, like show_protein",
      MCP_TOOLS["design_primers"]["is_concurrency_safe"] is False)
description = MCP_TOOLS["design_primers"]["description"]
check("the description closes the invented-primer door",
      "NEVER write, guess" in description and "verbatim" in description
      and "outcome" in description)

# ---- the widget is a convenience; the verified design is the product
ephemeral, stored = run(conversation="ephemeral-1", mode="pcr", template=PUC19,
                        target=[400, 450], product_size_range=[100, 300],
                        circular=True, n=1)
check("an ephemeral chat still gets its verified design",
      ephemeral["outcome"] == "designed" and ephemeral["designs"]
      and not stored and "ephemeral" in ephemeral.get("widget_error", ""))

print(f"\n{failures} failure(s)")
sys.exit(1 if failures else 0)

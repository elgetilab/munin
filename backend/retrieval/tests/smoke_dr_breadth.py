"""Live smoke: run the DR loop end-to-end and report breadth signals
(web reads, multi-note ratio, snowball depth reached, citation read_depths).
Not a unit test - hits vLLM + live search. Run from retrieval/."""
import asyncio
import collections
import deep_research_agent as D


async def main():
    q = ("How does the lipid membrane environment influence the structure, "
         "conformational dynamics and activation of G-protein-coupled receptors?")
    # Compact budget so the smoke finishes quickly but still exercises every lever.
    env = await D.deep_research(
        q, depth="deep", max_subq=2, screen_keep=8, read_cap=4,
        snowball_reads=2, snowball_depth=2)
    plan = env["plan"]
    notes = [n for node in plan for n in node["notes"]]
    refs = [r for node in plan for r in node["evidence_refs"]]
    web = [r for r in refs if (r.get("ref") or {}).get("url")]
    per_doc = collections.Counter()
    for n in notes:
        ref = n.get("ref") or {}
        per_doc[ref.get("doi") or ref.get("url") or "?"] += 1
    depths = collections.Counter(r.get("read_depth") for r in refs)
    print("=== DR breadth smoke ===")
    print("sub-questions:", len(plan))
    print("documents read:", len(refs))
    print("  web (url) reads:", len(web))
    print("total notes:", len(notes))
    multi = [d for d, c in per_doc.items() if c > 1]
    print("docs yielding >1 note (multi-note lever):", len(multi))
    print("read_depth breakdown:", dict(depths))
    print("statuses:", collections.Counter(n["status"] for n in plan))


if __name__ == "__main__":
    asyncio.run(main())

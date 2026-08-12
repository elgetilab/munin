# Writing benchmark questions for Munin

A short guide for group members authoring items for the internal benchmark.
Format follows LitQA2 (FutureHouse, arXiv 2409.13740) so our numbers are
comparable to published work, with two additions that make our set stronger
than theirs.

**Ask:** 10-20 items each, from papers you know well. Target 100-200 total.

---

## The format

One JSON object per item, one per line:

```json
{
  "qid": "mb-014",
  "question": "By what factor does the lateral diffusion coefficient of DPPC change when cholesterol is raised from 0 to 30 mol% at 323 K?",
  "ideal": "roughly 2-fold decrease",
  "distractors": ["roughly 2-fold increase", "roughly 10-fold decrease", "no significant change"],
  "source_doi": "10.1016/j.bpj.2007.01.023",
  "answer_location": "table",
  "author": "your-email",
  "field": "membrane biophysics"
}
```

The harness adds "Insufficient information to answer this question" as an
option automatically. Do not write it yourself.

`answer_location` is one of `abstract` / `results-text` / `table` / `figure` /
`methods` / `si`. We already know table- and figure-bound values are where the
system struggles, so this field lets us report *where* it fails, not just that
it does.

---

## Six rules

1. **One paper, one DOI, and it must be in the Munin corpus.** Check with a
   `paper_lookup` before writing the item. If it is not in the corpus, send it
   for ingestion first.

2. **The answer must require that paper.** This is the rule that matters most.
   If a competent person in your field could answer from general knowledge, or
   from the abstract, the item measures the model's memory rather than our
   retrieval and reading. Prefer a specific value buried in results, a table
   cell, a methods parameter, or a control condition.

3. **Not the headline finding.** The headline is in the abstract, in every
   citing paper, and almost certainly in the model's training data.

4. **Self-contained.** No "in this study", "the authors", "Figure 3". The
   system has to *find* the paper from the question alone, so the question must
   read like something you would type into a search box.

5. **Exactly one defensible option.** Four distractors, same type, same units,
   same order of magnitude. A distractor that is obviously wrong on units makes
   the item free.

6. **Stable over time.** No "recent", "current", "the latest". The item should
   score the same in three years.

---

## Two examples

**Good.**

> By what factor does the lateral diffusion coefficient of DPPC change when
> cholesterol is raised from 0 to 30 mol% at 323 K?

Specific, sits in a table, needs the paper, stands alone, distractors are all
plausible factors.

**Bad, and it is the most common failure.**

> Does cholesterol reduce the fluidity of lipid bilayers?

Answerable by anyone in the field without any paper. It will score as a
success for the wrong reason and tells us nothing.

The fix is almost always: **add a condition and ask for a number.** Which
lipid, what temperature, what concentration, what did the control show.

---

## The blind check (please do this, it is the point)

For each item, a second person answers it **without** the paper, using only
general knowledge and a web search.

- They get it right → **discard or rewrite the item.** It does not require the
  corpus.
- They get it wrong or say they cannot tell → **keep it.**

Record the outcome in the item as `blind_check: "failed"` (good) or
`"passed"` (discard). Roughly half of first-draft items get discarded here, so
write more than you need.

This is the addition that makes our set worth publishing. LitQA2 does not do
it, and it is precisely why a bare model with no retrieval still scores 0.30 on
LitQA2. Items that survive a blind check measure the system we actually built.

---

## Publishing

We intend to release the set, so:

- **Ship questions, options, DOIs and answers. Never paper text.** No abstracts,
  no figures, no excerpts. DOIs are metadata and are safe to redistribute.
- **Put your name on your items** if you want authorship credit on the dataset;
  `author` is the field.
- Say if an item is under embargo or comes from unpublished work, and we will
  hold it out of the public release while still using it internally.

---

## Also useful, if you have ten spare minutes

Separately from the QA items, we need **real search queries**: things you have
actually typed, or would type, when looking for a paper. Just the query text
plus the DOIs you would have been happy to get back. Three or four per person
is plenty. These feed the retrieval benchmark, which is currently blocked on
having too few real queries (42, and we need 100+).

# Docs sync and crediting Munin: plan

Status: IMPLEMENTED on branch `varghele/credit-and-API-update` (2026-11-21, all defaults taken), NOT deployed. Written 2026-11-21.

## Implementation notes (2026-11-21)

- Done as planned: A1 profile keys, B1 history plus usage-row fields plus Deep Research usage, A2 `/api/system` (retrieval and a cached, public gateway copy), A3 docs, A4 `scripts/check_docs_drift.py` (in CI and the frontend deploy steps), B2 summary and the Settings panel, B3 `/cite` (landing site, public), B4 `CITATION.cff`. The README links the arXiv paper.
- Found while building:
  - Caddy's `httpInclude` runs the included body as a template, so a BibTeX `{{Title}}` failed the page. The gateway serves `/api/system` with every `{{` written as `{\u007b`.
  - A failed include fails the whole page, so `handle_errors` answers `/_facts.json` with `{}`.
  - The `munin` model alias was added to raw mode and `/v1/models`, so the examples name no backbone.
  - `record_usage` dropped later calls from the total when a call had no `total_tokens`; fixed.
  - `docs.css` moved to `/shared/docs.css`, so the landing-site cite page can use it.
- Deviation: the `qwen3.8-27b` defaults in `backend/docker/docker-compose.yml`, `.env.example` and `database.py` stay as they are. They are the open-source install's reference configuration (the paper's model, used when an installer sets nothing), not text describing this deployment, and production overrides them from the active profile. Removing them would break a `--mode all` install. The gateway and Settings literals were removed.
- Deploy, in order:
  1. hugin: `sudo ./deploy.sh vllm` (installs the profiles with the new keys).
  2. hugin: `sudo ./deploy.sh agents` (merges `model-history.jsonl`).
  3. hugin: `sudo ./deploy.sh model activate sage-a1.5`, so `active-model.env` carries the citation keys. It is the same slug, so no history entry is added.
  4. hugin: redeploy retrieval and llm-failover.
  5. VPS: rsync `frontend/` and rebuild the gateway, then reload Caddy.

  Until step 3, `/api/system` reports the model without citation fields.
- Pre-existing, not touched:
  - `ArtifactPanel.test.tsx` "reports a structure it cannot fetch" also fails on the base commit.
  - `MetricsTab.tsx:99` groups by `direction`, but the label is `kind`.

Two tasks:

1. **Docs show what is running, and stay that way.** The public docs (`frontend/static/docs/*.html`) are a snapshot from the Qwen3.8 era and have drifted.
2. **Crediting Munin in papers.** A user asked how to credit Munin. We need guidance on how to cite or disclose an institute-hosted open LLM, and usage data that can back that statement.

## 1. Findings

### 1.1 What is live on hugin (checked 2026-11-21)

| Thing | Live value | Source |
|---|---|---|
| Chat backbone | `sage-a1.5`: Sage-A1.5, an agentic fine-tune of Qwen3.6-35B-A3B (MoE, 35B total / 3B active), cyankiwi AWQ-INT4 g32, revision pinned `12e664cc`, 64k context, text-only, thinking budget 8192 | `/opt/munin/config/active-model.env`, `vllm:8000/v1/models` |
| Serving | vLLM 0.33.0, single GPU (`gpu:vllm`, GPU 0), max-num-seqs 2 | job 1095 `vllm-service` |
| GPU 1 | eval instance (`vllm-inst-eval`, job 1092); Basalt is not running and nothing routes to it | `squeue` |
| Fallback | `llm-failover` sends requests to Haiku (`claude-haiku-5-5`), then Sonnet, when vLLM is down | compose, `llm-failover/main.py` |
| Paper embeddings | BGE-large-en-v1.5, 1024-dim, `papers_bge` (SPECTER is kept only for rollback) | `munin.env.template:91-97` |
| Upload / user-doc embeddings | BGE-base-en-v1.5, 768-dim | compose |

The repo has fallen behind as well. `backend/README.md:65` and `docs/ops/CLUSTER-RECONNECT-2026-11.md:50` still say Qwen3.8 is in production. Compose and code defaults fall back to `qwen3.8-27b`.

### 1.2 What the docs claim

`architecture.html`:
- Qwen3.8-27B, "~17GB", tensor parallel across both 5090s with 8 slots. A separate line says "GPU 1, 90% VRAM", which contradicts that.
- SPECTER, 768-dim.
- Port 8070 is listed as GROBID only (the gateway also uses it).

`guide.html`:
- `qwen3.8-27b` appears in about 12 API examples. The model is described as Qwen3.8-27B. It states a context of 65,536 and output of 16,384.

`index.html` names no model. The landing page's model tabs are paper results and stay as they are.

The whole list, with line numbers, is in the research notes of this session. The key point is that every one of these is a hand-typed literal.

### 1.3 Machinery that already exists

- **One profile per backbone** in `backend/config/models/<slug>.env`. `deploy.sh model activate <slug>` writes `active-model.env`. That file is the single source of truth for what vLLM serves.
- **Runtime endpoints:**
  - Retrieval `GET /api/models` and `GET /api/status` report the served model, `max_model_len`, the thinking settings, the fallback state, and `paper_space` (encoder, collection, dims).
  - Gateway `GET /v1/models` proxies a trimmed version.
- **Caddy `templates`** is already on for the docs. Only `MUNIN_DOMAIN`, `MUNIN_URL_*` and the support email are passed in. Caddy can also `httpInclude` an internal URL, but nothing uses that yet.

### 1.4 Usage tracking today

The gateway SQLite table `usage_log` (`frontend/gateway/main.py:246`) holds one row per request: user key, timestamp, source (browser or api_key), key id, endpoint, `tokens_total`, tools, duration, status, model.

Gaps:
- `model` is effectively never filled. The retrieval `done` event carries no model, and `/v1` rows never set it. **This is the critical gap for crediting:** the backbone changed on 2026-11-12, so a row with no model cannot say which model answered.
- `tokens_input` and `tokens_output` are never filled. `persona` is never passed.
- No group is stored: Caddy sends `X-Munin-Group` and the gateway drops it.
- No conversation id is stored.
- Deep Research never calls `record_usage`, so its tokens are missing.
- Fallback turns (Haiku) cannot be told apart from vLLM turns.
- The only read endpoints are `/api/usage/me` and `/api/usage/admin`, and both are fixed to the current month. There is no history, date range or export.
- Possible bug: `MetricsTab.tsx:99` groups by `direction`, but the label is `kind`.

### 1.5 Citation state

- Every "Cite Munin" footer link points to `CITATION.cff` on GitHub. No page on the site says how to credit Munin.
- `CITATION.cff` is stale: `preferred-citation` is `status: in-preparation` with no identifier, while the landing page already links arXiv 2610.23480.

### 1.6 External guidance (researched 2026-11-21, sources at the end)

- **Universal:** an LLM is never an author (ICMJE, COPE, Springer Nature, Elsevier, Wiley, PLOS, IEEE, NeurIPS). Use is disclosed by naming the tool and the purpose, and the authors state that they take responsibility.
- **Where the disclosure goes:**
  - Use in the research itself goes in Methods (Elsevier, COPE, PLOS, NeurIPS).
  - Writing help goes in a separate AI declaration (Elsevier), in the Acknowledgments (IEEE, ACL), or in the body (Wiley, PLOS).
  - Springer Nature's 2026 tiered policy now asks for a description even for "green" language-editing use. Elsevier, Wiley and PLOS still exempt pure grammar checks.
- **What to report:** the model name and version, the provider or host, dates, purpose, and how outputs were verified. Reproducibility papers add the weights revision, quantization, sampling parameters and serving software version, because local outputs vary with hardware, batch size and GPU count.
- **What to cite:**
  - The model's technical report or model card.
  - The Hugging Face repo at its revision (with a DOI if the owner minted one).
  - The serving engine: Kwon et al., SOSP '23, doi:10.1145/3600006.3613165.
  - The platform: its paper or Zenodo DOI, per the FORCE11 software-citation principles.
  - APA style makes the model developer the "author" of a model reference, not the host.
- **Institutional precedent:**
  - GWDG Chat AI, the closest analogue (an institute-hosted, SLURM-native LLM service), asks users to cite its platform paper.
  - LRZ asks for a standard acknowledgment sentence.
  - ScaDS.AI, KIT and Helmholtz Blablador publish no template.

**Synthesis: three layers**

1. A disclosure statement in Methods, or in the AI declaration if the use was writing-only.
2. References: the model report, the vLLM paper and the Munin paper.
3. An acknowledgment of the lab or institute that hosts the service.

None of this needs Munin to track anything beyond **which model, which dates, and roughly how much**. The user supplies the purpose and the verification.

## 2. Plan A: docs that cannot drift

**Principle:** the model facts live in one place (the profile, plus a small amount of extra metadata). The docs render from it at request time. A check catches any hand-typed literal that slips back in.

### A1. Richer model profiles (source of truth)

Add citation and display fields to every `backend/config/models/*.env`, and make them required in `model-env.sh:42-45`:
- `MODEL_DISPLAY`: for example "Sage-A1.5".
- `MODEL_DEVELOPER`: for example "InternScience / Shanghai AI Lab". For a fine-tune, also give the base model.
- `MODEL_BASE`: "Qwen3.6-35B-A3B".
- `MODEL_ARCH`: "MoE, 35B total / 3B active".
- `MODEL_QUANT`: "AWQ-INT4 g32 (cyankiwi)".
- `MODEL_REVISION`: the HF commit.
- `MODEL_LICENSE`.
- `MODEL_URL`: the HF card.
- `MODEL_CITATION`: the tech report or card URL or DOI.

`MODEL_DESC` already exists, and the new fields subsume it.

### A2. A public "system facts" endpoint

Add `GET /api/system` on the gateway. It is public, needs no auth and is cached for 60 s. It is built from retrieval `/api/status` and `/api/models`, plus the new profile fields, which retrieval reads from `active-model.env` (it is already mounted or env-injected; to be checked when implementing).

It returns:
- the backbone (display name, slug, developer, base, architecture, quantization, revision, context, output cap, license, citation URL);
- the vLLM version;
- the encoder for papers and for uploads;
- the fallback model and whether it is currently active;
- `since`, the activation date from the model-history table in B1.

It exposes no secrets, hostnames or ports.

### A3. The docs render from it

**Recommended: server-side through Caddy `httpInclude`.** The HTML then already contains the facts, with no flash of placeholder text, and it also works with JS disabled.
- Add a tiny internal route, for example `/_facts/<key>`, or a single `/_facts.json` read with Caddy's `{{ (httpInclude ...) | fromJson }}` if the Caddy version supports it.
- If the cluster is unreachable, it falls back to a neutral phrase ("the current backbone, see Settings").
- Fallback design: a small JS file fills elements marked `data-fact="backbone.display"` from `/api/system`. This is simpler, but the text changes after load.

Rewrite these pages to use it:
- `architecture.html`: the model table, the GPU-layout node, embeddings, the tech stack and the service ports. The ports move to deployment-independent wording, or are dropped from public docs, since they are internal.
- `guide.html`: the API examples use `"model": "munin"`, if the gateway accepts an alias (to be checked), or the templated slug. The context and output numbers come from the facts.
- Add a short **"Currently running"** box at the top of the docs index: backbone, since date, embeddings, and fallback status.

Code fallbacks and the rest:
- The `qwen3.8-27b` defaults in `docker-compose.yml`, `database.py`, `gateway/main.py:1146` and `Settings.tsx:66` change to "no default": they read the profile or show "unknown". A silent stale default is the same drift bug.
- `DemoDeck.tsx:178` gets the same treatment.
- Fix `backend/README.md:65` and the "still Qwen3.8" lines in the reconnect ops doc.

### A4. A drift guard

`scripts/check-docs-drift.sh` runs in CI and as a pre-deploy step in `deploy.sh frontend`:
- It fails if any served-path file (`frontend/static/**`, `frontend/webui/src/**`) contains a literal model slug or display name taken from `backend/config/models/*.env`, an encoder name, or a GPU-layout phrase.
- Exception: lines in an allowlist with a reason, mainly the landing page's paper-results tabs.
- On hugin, `deploy.sh model activate` also prints a reminder if the new profile lacks any A1 field.

### A5. Out of scope

The landing page's benchmark tabs stay. They describe the paper's runs, not production.

## 3. Plan B: crediting Munin

### B1. Record the model on every usage row

- **Model-history table** `served_models` on the retrieval side: slug, model id, revision, vLLM version, `activated_at`, `deactivated_at`. `deploy.sh model activate` appends to it.
  - Backfill from git and ops docs: Qwen3.6-35B-A3B until 2026-08-26, Qwen3.8-27B from 2026-08-26 to 2026-11-12 (minus the Oct 21 to Nov 12 outage, when Lite/Haiku served), Sage-A1.5 from 2026-11-12. Exact cutover times come from the ops docs.
  - This also answers "which model did I use in March" for old rows that have no model.
- **The `done` event carries `model`.** That is the model that actually answered: the vLLM slug, or the fallback's Claude model when `llm-failover` routed the turn. The gateway stores it in `usage_log.model`. The `/v1` path fills it from the upstream response.
- **Also fill:**
  - `tokens_input` and `tokens_output`: `usage_tracker` already has both.
  - `persona`: the routed profile.
  - `group_id`: from `X-Munin-Group`.
  - `conversation_id`.
  - Done as an additive migration on `usage_log`.
- **Deep Research** calls `record_usage`, and its job emits one usage row on completion.

### B2. A self-service "Usage for publications" panel

- Add `GET /api/usage/me/summary?from=YYYY-MM-DD&to=YYYY-MM-DD`. It returns per-model rows: requests, conversations, tokens, first and last use. Gaps in rows that have no model are filled from `served_models` by timestamp and flagged as "inferred".
- Add the same endpoint for leaders over their group, and a CSV export.
- Settings gets a new section, "Credit Munin in a paper", with:
  - a date-range picker and the usage table;
  - a **generated disclosure statement**, pre-filled with the model name, developer, revision, quantization, the vLLM version, "served on the Munin platform hosted by the Elgeti Lab, Leipzig University", and the dates. The user fills in the purpose and how outputs were verified;
  - BibTeX for the Munin paper, the model report and the vLLM paper, plus a suggested acknowledgment sentence;
  - a copy button.
- No chat content ever goes into it. It is metadata only.

### B3. A public "Cite Munin" page

Add `frontend/static/docs/cite.html`, and point every footer "Cite Munin" link at it instead of GitHub. It contains:
- **Short answer:** Munin is not an author. Disclose use in Methods, or in the AI declaration if the use was writing-only. Cite the three references. Acknowledge the host.
- **Templates:**
  - a Methods sentence, a writing-only declaration and an acknowledgment, filled with the *current* model from `/api/system`;
  - BibTeX and APA entries for the Munin paper (arXiv 2610.23480 plus the Zenodo concept DOI), the current model's report, and vLLM (Kwon et al. 2023).
- **Publisher notes:** a short table of where each major publisher wants the disclosure (Springer Nature, Elsevier/Cell, Wiley, PLOS, IEEE, ACL, NeurIPS, ICMJE), each linked to its policy page, with the date it was checked.
- **Reproducibility notes:** report the revision, quantization, dates and sampling settings, and keep the raw outputs. Local outputs vary run to run even at temperature 0.
- **Finding your own usage:** a pointer to the Settings panel.

### B4. Fix `CITATION.cff`

- `preferred-citation` gets the arXiv identifier and `status` is dropped. The authors stay as they are.
- Leave the software `version` alone unless a release is cut.

### B5. Out of scope

- A per-paper usage registry: users report their own papers.
- Admin-side attribution dashboards beyond the leader CSV.

## 4. Order of work

| Step | Content | Deploy surface |
|---|---|---|
| 1 | A1 profile fields + B1 `served_models` + backfill | hugin (Hugin/munin `deploy.sh`) |
| 2 | B1 usage-row fields (done event `model`, tokens in/out, group, conversation, DR) | retrieval + gateway |
| 3 | A2 `/api/system` | gateway |
| 4 | A3 docs rewrite + "Currently running" box + code defaults | VPS frontend rsync |
| 5 | B3 cite page + footer links + B4 CITATION.cff | VPS frontend |
| 6 | B2 summary endpoint + Settings panel + leader CSV | gateway + webui |
| 7 | A4 drift guard in CI and the deploy script | CI |

Each step is its own commit. Tests:
- the gateway migration on a copy of `gateway.db`;
- the summary endpoint (including inferred-model rows);
- `/api/system` when the cluster is down;
- the drift script on a planted literal.

## 5. Open questions (defaults in bold)

1. **Render path for docs facts:** **Caddy `httpInclude`, server-side** or the JS fill-in?
2. **Should the public `/api/system` show the fallback state live** ("currently served by Claude Haiku, fallback") or only name the fallback model? **Show it live.** It is honest, and it matters for anyone writing a disclosure during an outage.
3. **Disclosure wording when a turn was served by the Haiku fallback:** the statement generator **lists Claude Haiku as a separate model row with its own dates**. This tells users that part of their use went to an external API, which is relevant for data-protection statements too.
4. **Acknowledgment sentence:** **"We thank the Elgeti Lab (Leipzig University Medical Faculty) for providing the Munin research assistant and the compute it runs on."** Does the lab want funder or grant numbers in it (for example HYP*MOL)?
5. **Leader CSV:** do group leaders get per-member usage, or **only group totals**? Per-member is a privacy step up.
6. **Port table in the public architecture page:** **drop it**: internal ports are not user documentation and they drift. The alternative is to keep it, templated.
7. **Model alias:** do API examples use **`munin`** (needs the gateway to accept and map it; checked in step 3) or the live slug?
8. **CITATION.cff and Zenodo:** should updating `preferred-citation` also cut a v1.0.1 Zenodo release, or **just edit the file on main**?

## Sources (external guidance, checked 2026-11-21)

Springer Nature AI policy (nature.com/nature-portfolio/editorial-policies/ai); Elsevier generative-AI policy (June 2026); Wiley ethics guidelines (July 2026); PLOS ethical publishing practice; IEEE AI-generated text guidelines; ICMJE "AI use by authors"; COPE position "Authorship and AI tools" (2023); ACL publication ethics + ARR Responsible NLP checklist; NeurIPS 2025 LLM policy; APA Style blog "Citing generative AI" (Sep 2025); MLA "Citing generative AI"; FORCE11 Software Citation Principles (doi:10.7717/peerj-cs.86); vLLM PagedAttention (doi:10.1145/3600006.3613165); GWDG Chat AI docs + SAIA paper (doi:10.1007/s11227-026-08508-3); LRZ acknowledgment page; TRIPOD-LLM (doi:10.1038/s41591-024-03425-5); CHART statement (EQUATOR).

**Not verified** (primary page unreachable): Science/AAAS, ACS, Cell Press's own page, ICML author policy, NeurIPS 2026.

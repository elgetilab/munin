# Lab analysis: harness, helpers and eval suites

Status: APPROVED 2026-11-13. Phase 0 built and tested 2026-11-13, not yet
deployed (see "Phase 0 status" below).

Several research groups use GraphPad Prism (and some ChimeraX) for fitting and
plotting. We want Munin to replace that through chat: users upload instrument
exports, ask for fits and plots, iterate on figures by chat, run longer
analyses, and download clean, uncorrupted results. This plan covers the harness
fixes that make that possible, a vetted helper library, and the eval suites that
prove it works.

## Decisions taken in discussion

| Topic | Decision |
|---|---|
| Prism parity | No parity score. Correctness against a reference only. |
| Helper library | Build one (`labkit`), but measure both arms: ad hoc code first (baseline), then with `labkit`. |
| Heavy / long jobs | Run on sleipnir (two-node plan, `munin-jobs` partition), not via a longer in-sandbox cap. |
| Fixtures from github.com/varghele | Owned by the user. Copy data freely into munin; rewrite the old code. The repos are inspiration for which helpers we need, not code to port. |
| Structure viewer | Extend the existing 3Dmol.js `show_protein`. No Mol* switch, no ChimeraX in any image (its license forbids redistribution). Static images via open-source PyMOL. |
| NMR priority | Solution NMR titrations (CSP, Kd) and relaxation first; solid-state NMR also in scope. |
| Figure grading | An automatic figure manifest is the primary grader. Vision judge is a secondary, separately reported score, run on the local backbone. |
| Manifest in production | Yes: the model sees a manifest summary after every code tool call. |
| House style | None defined. Groups have their own journal requirements; evals check what the user asked for, not a default style. |
| Models | Functionality first, on the production backbone. Re-running on all available models is a heavy TODO (see the end). Haiku is a fallback only and never an eval target. |
| Real usage | Later: mine cluster chat history for real tasks (expect little) and refine suites and tools from it. |

## Current state (verified 2026-11-13)

| # | Gap | Where |
|---|---|---|
| H1 | Uploads accept only pdf/txt/md/docx/images, go to RAG, and never reach the sandbox | `retrieval/document_store.py:39-40`, `frontend/webui/src/components/ChatInput.tsx` |
| H2 | Artifacts are tracked by filename: re-saving `fit.png` surfaces nothing new and the old link serves the new bytes | `sandbox/app/kernel.py:152,429,446` |
| H3 | No checksums; subdirectories of `/scratch/{cid}` ignored; no volume for `/scratch`, so links 404 after a rebuild | `kernel.py`, `docker/docker-compose.yml` |
| H4 | Downloads are per file; the proxy buffers the whole body with a 15 s timeout; no bundle | `retrieval/main.py:2730-2780`, `ArtifactPanel.tsx` |
| H5 | Single exec capped at 120 s, no job queue | `sandbox/app/main.py:48` |
| H6 | Plot feedback to the model and the `artifact_created` SSE event only fire for `run_python` | `vision.py:312-400`, `chat_service.py:2818` |
| H7 | No lmfit, statsmodels, nmrglue, gemmi etc. Compute agent allowlists and recommends statsmodels anyway | `sandbox/requirements.txt`, `compute_agent.py:52,64` |
| H8 | Background turns: more than 2 per user get cancelled, resume does not survive a restart, the test hangs | `STREAM-RELIABILITY-TODO.md` items 2, 4, 8 |
| H9 | `show_protein` only takes a PDB or UniProt id: no user or sandbox structure, no per-residue values, no surface or labels | `retrieval/mcp/tools/structures.py`, `widgets/proteinRuntime.js` |

No coding, plotting or curve-fitting benchmark exists today.

## Phase 0: harness plumbing (tests first)

Write the S0 suite (below) first; it should fail on today's code. Then fix:

1. **Uploads into the sandbox.** Accept any file type for chat data uploads
   (already approved in the v1.1 review). Data files bypass RAG and land
   read-only in `/scratch/{cid}/inputs/`. A `.zip` is extracted there, which is
   how Bruker experiment folders arrive. The model is told the path. Data uploads
   are capped at 100 MB each.
2. **Immutable, content-addressed artifacts.** After each exec, scan
   `/scratch/{cid}` recursively (depth and count limits) and key by
   `(path, sha256)`. A changed file becomes a new artifact version; the bytes of
   every earlier version are copied into the artifact store outside the
   kernel's reach and never change. Each artifact records sha256, size, the
   hash of the code that produced it, and the turn.
3. **Persistence.** Volume for scratch and the artifact store; artifacts live
   as long as the chat and are deleted with it.
4. **Downloads.** Stream through the proxy instead of buffering. Add a bundle
   endpoint (all artifacts of a turn or chat, or a selection) that returns a
   zip with `MANIFEST.json` (paths, sha256, sizes, versions), the code that
   produced them, the input files, and the environment (package versions).
   Frontend: "download all" plus a per-version history.
5. **Figure manifest.** A hook loaded at kernel startup catches every
   `savefig()` and displayed figure, walks the matplotlib objects, and emits a
   JSON manifest via a custom MIME type, captured by the kernel manager and
   attached to the artifact. Contents: figure size and dpi, export format, per
   axes scales, limits, labels, legend, per series kind, colour, x/y data,
   error-bar values, bar heights, text annotations, and layout problems
   (overlapping or clipped text, legend over data) from bounding boxes. Works
   for plain matplotlib and seaborn, so it grades both arms identically.
6. **Feedback to the model.** After every code tool (`run_python`,
   `edit_python`, `compute`), the model gets a short manifest summary, and the
   `artifact_created` event fires for all three.
7. **Sandbox image.** Add lmfit, statsmodels, pingouin, scikit-posthocs,
   lifelines, nmrglue, pynmrstar, xlrd, tifffile, gemmi, biopython, freesasa,
   brokenaxes, pymol-open-source. Bring the compute agent's allowlist and prompt
   in line with what is installed.
8. **Background turns (H8).** Fix the hanging test; queue rather than cancel a
   third background turn. Coordinate with `STREAM-RELIABILITY-TODO.md`.

Acceptance: S0 green in CI, deployed, live smoke test of upload, overwrite and
bundle download.

### Phase 0 status (2026-11-13)

Built and tested, not deployed:

| Item | Where | Tests |
|---|---|---|
| 1 uploads into the sandbox | `document_store.is_data_file`, `chat_service._attach_to_sandbox`, sandbox `POST /inputs`, `store.sync_inputs`, webui ChatInput | `test_lab_plumbing.py`, `test_artifacts_live.py`, ChatInput tests |
| 2 immutable artifacts | `sandbox/app/store.py` (blobs, versions, recursive scan, LaTeX outputs too) | `test_artifacts_live.py`, `test_isolation_live.py` |
| 3 persistence | compose volumes under `data/sandbox/`, `deploy.sh dirs`, purge on chat delete, Lite tmpfs resized | `check_compose.py`, purge checks in the live suite |
| 4 downloads | streamed proxy, `GET /api/bundles/{cid}`, `GET /api/artifacts/{cid}`, gateway streams downloads, "Download all" and "With code and data" in the panel | live suite (bundle verify + reproduce), `test_download_stream.py`, ArtifactPanel tests |
| 5 figure manifest | `sandbox/app/fighook.py` (savefig and inline), `store.summarize_figure` | live suite |
| 6 feedback | `figure_summary` in tool results, plot feedback and `artifact_created` for `run_python`, `edit_python`, `compute` | `test_lab_plumbing.py` |
| 7 packages | `requirements.txt` + refrozen `constraints.txt`; compute allowlist and prompts updated | import check in the image |
| 8 background turns | item 8 already resolved; item 2 already persists the partial answer; a queue needs Phase 6 | see STREAM-RELIABILITY-TODO.md |

pymol-open-source and molviewspec were not added; they belong with the
viewer work in Phase 5. CI runs the
artifact suite in a standalone container (`sandbox-live` job,
`backend/scripts/ci/run_sandbox_live_tests.sh`).

## Phase 1: fixtures and eval harness

**Fixtures** in `backend/benchmarks/fixtures/lab/`, each with a `truth.json`
and a provenance line in the README:

- *Synthetic, generated by seeded scripts*: dose-response (3PL/4PL/5PL),
  Michaelis-Menten and inhibition, Morrison tight binding, one- and two-phase
  exponentials, standard curves with unknowns, global datasets with shared
  parameters, unidentifiable fits that must be flagged, outlier-contaminated
  sets. Multiple seeds per model so CI coverage can be measured.
- *Instrument-format mimics* built from the same truths: NanoDrop TSV (UTF-8,
  UTF-16, de-DE decimal commas), Tecan xlsx with "OVER" wells, Gen5 kinetics,
  BMG table view, MicroCal `.itc`, Jasco CD text (LOCALE 1031), Bruker 1D and
  pseudo-2D/3D relaxation series, HSQC titration peak lists.
- *Proprietary binaries* (`.twbk`, `.pcrd`, `.sda`, `.nt`): the correct answer
  is to ask for a text or Excel export.
- *Public*: NIST StRD nonlinear (all three difficulty tiers), linear and ANOVA
  sets with certified values; nmrglue example data; pytc ITC demos.
- *User data*: NOESY buildup integrals (1H1H-NOESY), 31P spectra and reference
  overlay (31P-siml), 2H order profiles (GNN2H), Markov-model matrices.
  Reference values are recomputed with fresh code.
- *Held out*: a private set (real exports, perturbed synthetic variants) stored
  outside the public repo, never sent to an external service. Its scores are
  the ones we trust over time.

**Harness** in `backend/benchmarks/munin_bench/labanalysis/`, following the
`retrieval/evals/run_eval.py` pattern (persistent eval user; non-ephemeral
chats, because the sandbox refuses ephemeral ones):

- upload fixtures, drive scripted single or multi-turn conversations over SSE,
  collect artifacts, manifests and downloads;
- graders per check type (below);
- scorecards in the existing `scorecard.py` format, copied to
  `docs/paper-kit/scorecards/` in the same commit.

## Phase 2: baseline arm (no `labkit`)

Run S1, S2, S3, S5 and the S6 subset on the production backbone with the
plumbing fixed but without `labkit`. Pass thresholds are set after this run,
not before.

## Phase 3: `labkit` helper library

A tested package in `backend/sandbox/labkit/`, installed in the sandbox image,
documented to the `code` persona. No new agent: the `code` persona gets fitting
and plotting guidance and the `labkit` reference; the compute agent gets the
same allowlist.

- **Fitting**: `fit()` with Prism-style options (replicates vs means, weighting
  by the curve's Y, constraints, shared parameters for global fits), Wald and
  profile-likelihood CIs, confidence and prediction bands, a dependency
  (ambiguous fit) check that warns loudly, model comparison (extra
  sum-of-squares F test, AICc), ROUT outliers, standard-curve interpolation with
  CIs and blank-outside-range rules. A model library covering the Prism
  families used by these groups.
- **Statistics**: two-way ANOVA with Type III sums of squares, post-hoc tests
  on the pooled error term, repeated measures and mixed models, the p-value to
  asterisk mapping, contingency tests without silent Yates correction.
- **Readers**: the instrument formats above, `.pzfx` and `.prism` (data only),
  each returning a tidy DataFrame plus the metadata it found, and raising on
  anything ambiguous instead of guessing.
- **NMR**: Bruker processing chain with explicit phase parameters (no silent
  auto-phase), CSP with α as a required argument and missing residues kept as
  missing, global Kd fit across residues with ligand depletion, relaxation
  (T1, T2, hetNOE) fits, DOSY. Solid state: NOESY buildup with leakage, 31P
  powder lineshapes, 2H order profiles.
- **Structure**: residue numbering helper (auth vs label), superposition with
  reported pruned and full RMSD, SASA and buried area, writing per-residue
  values for the viewer.
- **Export**: `save_bundle()` writing figure, data, script and environment.

Every function is unit-tested; the fitter is tested against NIST certified
values.

## Phase 4: helper arm

Re-run the Phase 2 set with `labkit` available. Paired comparison against the
baseline (`pipelines/compare.py`). The delta is a paper result.

## Phase 5: structure viewer and S7

Extend `show_protein`: load a PDB or mmCIF artifact from the sandbox or an
upload; `residue_values` with a colour bar and grey for missing residues;
surfaces, labels, distance lines, a second superposed structure. Then run S7.

## Phase 6: long jobs on sleipnir

Depends on the two-node plan (sleipnir, `munin-jobs` partition, remote
sandbox). Add a `run_python_job` tool (submit, poll, cancel, progress in chat)
whose backend runs on sleipnir with the chat's inputs and returns artifacts
through the same content-addressed store. Then run S4.

## Eval suites

| Suite | Runs | Scope | Graded by |
|---|---|---|---|
| **S0 Plumbing** | CI, no model | upload into the sandbox (all types, zip folders, de-DE files); artifact round trip byte-identical; overwrite makes a new version and the old one is unchanged; subdirectories; bundle zip and its manifest; large files; persistence across restart; timeout recovery; manifest hook output for known figures | sha256, by construction |
| **S1 Fit correctness** | live | NIST StRD; synthetic Prism-family fits; trap items (replicates, weighting, unidentifiable fit, outliers) | parameter error vs truth; CI coverage over seeds; ambiguous fits flagged; no extrapolated interpolations |
| **S2 Instrument parsing** | live | each fixture format, encodings and locales; proprietary binaries | extracted values vs truth; asks for an export on binaries |
| **S3 Iterative plot editing** | live, 8-10 turns | log axes, SD to SEM, significance brackets, palette, size in mm, dpi, format, dropping a series, relabelling | manifest assertions; manifest diff (only the requested change happened); earlier artifacts byte-identical; exported file metadata (dpi, pixel size, vector PDF, embedded fonts); vision judge secondary |
| **S4 Long jobs** | live | jobs past the in-turn limit; disconnect mid-job; background promotion; reconnect | completion, artifact hashes, no lost results |
| **S5 Reproducibility** | live and CI | download the bundle, re-run its script in a fresh kernel | outputs match numerically |
| **S6 Domain workflows** | live | first: HSQC titration CSP and global Kd, T1/T2/hetNOE; then: NOESY buildup, 31P lineshape, 2H order profiles, DOSY, Prism-style statistics | values vs truth, plus manifest checks on figures |
| **S7 Structure** | live, small | CSP or pLDDT on a user structure, RMSD, SASA | viewer spec values, numbers vs gemmi/freesasa reference |

The vision judge (local backbone) only scores readability and overall
appearance, is reported separately, and never overrides a mechanical check.

## Resolved questions

1. **Before sleipnir exists**, nothing runs longer than 120 s. S4 waits for
   Phase 6; until then tasks are sized to fit, and one S3 case checks the model
   handles the limit gracefully (chunks the work or says so).
2. **Data upload cap**: 100 MB per upload, with a per-chat quota.
3. **Artifact retention**: every version kept for the life of the chat,
   deleted with it.
4. **Held-out set**: under `/opt/munin/data/evals/` on hugin, outside git.
5. **`labkit`**: `backend/sandbox/labkit/`, Apache-2.0.

## Heavy TODO: re-run on every available model

Everything above is measured on the production backbone only. Before any claim
about model capability, re-run S1-S7 (both arms) on every backbone we can
serve, starting with gpt-oss, and compare paired against the backbone results.
This is required, not optional, and tracked in `TODO.md`. Haiku is excluded
because it is only a fallback.

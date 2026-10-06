# Persona consolidation: one user-facing Munin, three internal routing profiles

Status: PLAN (for sign-off). Follows Part A (router migration). Separate from
Part B (eval suite).

## Goal

One user-facing identity (**Munin**), no model picker. The three routing
profiles (chat / code / research) survive as INTERNAL routing targets the router
selects per turn. Experienced users steer with slash commands
(`/chat` `/code` `/research`); everyone else never picks a model - the router
auto-routes.

## Decisions (locked 2026-06-29)

- **Three files** `munin-chat.json` / `munin-code.json` / `munin-research.json`,
  all `name: "Munin"`. Internal profile **ids stay `chat`/`code`/`research`**
  (the router, `router_examples.json`, slash parser all depend on them); only the
  filenames + the `name` field change.
- **Keep "Munin" visible**: a single name + one logo in the UI; just no picker.
- **Slash commands stay** (tier-1 absolute route; independent of the picker).
- Mitigation for the triplicated frame: a **drift-guard test** asserting the
  three files' frame (prefix+suffix) is byte-identical.

## The three profiles are NOT just names

They differ in: **fragment** (task-guidance prompt section), **sampling**
(code temp 0.6 vs 1.0), **resident_tools** (research = citation/S2 tools, code =
LaTeX/sandbox, chat = web_fetch/remember/recall). All three must persist
per-profile; this is a consolidation of IDENTITY, not a collapse to one prompt
(A5 showed mixing the guidance causes routing misfires).

## Work breakdown

### Backend (substantive + gated)
1. **Reconcile the frame.** Today the prefix (CORE RULES + OUTPUT STYLE) and
   suffix (TASK PLANNING + DISCOVERING TOOLS) differ across the three. Merge into
   ONE canonical Munin frame: union of the CORE RULES (artifact-URL / no-fabricate
   / announcement rules apply to all), the shared OUTPUT STYLE, shared TASK
   PLANNING, and a unified DISCOVERING TOOLS (generic, no profile-specific tool
   examples). Behavior-affecting -> **gate: re-run the anchor routing eval, anchor
   mean must hold ~0.95 with no item regressed.**
2. **Rename files** to `munin-*.json`, set `name: "Munin"`, keep `id` =
   chat/code/research. Update the ~filename references: `router_examples_build.py`
   reads `{pid}.json` (-> read `munin-{pid}.json`); check deploy.sh glob (installs
   `*.json`, unaffected) and the prompt-split test.
3. **`public_personas()` / `/api/personas`**: return a SINGLE `Munin` descriptor
   (id `munin`, name, logo, merged prompt_suggestions) for branding, while
   `get_persona(profile)` still serves the three internally. API-contract change
   -> update BACKEND-API.md.
4. **Auto-route default**: when the request sends no persona (or `munin`), treat
   as no-pin -> the router auto-routes (KNN + chat fallback). With identical
   frames, the served prompt = Munin frame + routed fragment regardless of pin,
   so `compose_system_prompt` needs no structural change (pin defaults to the
   routed profile when absent).
5. **Drift-guard test** (frames byte-identical across the three).
6. One logo for Munin (or reuse one); drop the per-persona logos from the picker
   path.

### Frontend (webui) — SCOPED (2026-06-29)

Important: after the backend change the CURRENT frontend already works
(`/api/personas` returns one Munin -> `selectedPersona` defaults to "munin" ->
the send already auto-routes). So this is cleanup + branding, NOT critical path.
Build target `frontend/static/chat/` (vite `outDir`); served by Caddy at
chat.muninai.org. Website Munin logo: `/shared/munin_logo_without_script.webp`.

Files + changes:
1. **ChatInput.tsx** - remove `<PersonaSelector>` (composer bottom bar). Replace
   with a small static "Munin" mark (the shared logo + label), non-interactive.
   Keep sending `selectedPersona` (== "munin") so the backend auto-routes.
2. **PersonaSelector.tsx** - delete (sole consumer was ChatInput).
3. **MessageList.tsx** - remove `PersonaDivider` + `DelegationNote` + the
   `effectivePersonas` divider logic. CRITICAL UX: per-turn routing now changes
   the per-message profile, which would otherwise render as bogus "switched to
   Code/Research" dividers. (MessageList has no per-message avatars, so nothing
   else to swap.)
4. **PersonaDivider.tsx** - delete (only used by MessageList).
5. **chatStore.ts** - remove `case 'delegated'` and `case 'persona_changed'` (dead
   since A4) + the `delegations` / `streaming.delegations` state.
6. **types.ts** - drop the `delegated` + `persona_changed` StreamEvent variants
   and the now-unused `Delegation` type.
7. **App.tsx** - leave the `conversationPersona -> selectedPersona` sync (harmless:
   "munin" for new convs; legacy convs still sync their stored profile).
8. **Branding** - reference `/shared/munin_logo_without_script.webp` (the website
   logo, per varghele; NOT a persona logo).
9. **Tests** - update/trim the suites that reference PersonaSelector /
   PersonaDivider / delegated / persona_changed so `npm run test` + `build` pass.
10. Optional (low priority, likely skip): a composer placeholder hint that
    `/research //code //chat` exist; handling the `routing` SSE event to show a
    subtle routed-profile chip. Default: skip - keeps the "no model selection"
    UX clean.

Deploy (I have VPS access: `varghele@<vps-host>`, key `~/.ssh/munin_admin`,
rsync-based): `cd frontend/webui && npm run build`; rsync the tree + prune
`frontend/static/chat/assets/` with `--delete`; Caddy serves the static files
directly (no container rebuild for a webui-only change). Backend stays
backward-compatible so nothing breaks mid-deploy.

### Docs
12. DECISIONS.md dated entry (personas consolidated to one Munin identity; three
    internal routing profiles; picker removed; slash override retained).
13. BACKEND-API.md `/api/personas` shape update.

## Backend gate result (2026-06-29) — PASSED

Frame reconciliation is **behavior-neutral**: anchor mean **0.950**, identical to
the pre-consolidation v4 baseline, no real regression. Critical checks held:
- `define_nmr` (chat `no_tool`) stayed 1.00 - the relocated research-integrity
  rule ("never answer from general knowledge alone") did NOT leak into chat.
- research items (sota_phip / group_corpus_qa / known_doi_read / citing_papers)
  all 1.00 - the relocation preserved research behaviour.
- `percent_calc` showed 0.60 at reps=5 but **10/10 at reps=10** (noise, not a
  frame effect; it answers trivial percentages inline, calculate cue is in the
  unchanged chat fragment).

Commit bba3b01 (consolidation) + the drift-guard test.

### Backend step 2 done + verified (commit 7b777e8)

`/api/personas` returns a single "Munin" entry (default_persona "munin");
`persona:"munin"` or unset -> no-pin auto-route (router decides from the query);
a real profile id still pins (backward-compatible; the routing eval sends "chat"
and is unaffected). New conversations persist "munin" so reopening re-routes.
Live probe: munin -> {python:code, papers:research, weather:chat} all pin=null;
chat -> pin="chat". Backend fully done.

### Frontend DONE + DEPLOYED (commit 846ba45, 2026-06-29)

Picker removed (PersonaSelector + PersonaDivider deleted); composer shows a static
Munin mark (`/shared/munin_logo_without_script.webp`) + slash hint placeholder;
per-assistant-message routed-profile chip (subtle pill, shown only for
code/research) from `message.persona` / the `routing` SSE event; dead
`delegated`/`persona_changed` handlers + `Delegation` type removed; msw mock +
tests aligned to single-Munin. `npm run build` + 206 tests pass. Built to
`frontend/static/chat/`, rsynced to the VPS (varghele@<vps-host>), stale
bundle pruned; new bundle live, Caddy serves it (302 -> auth confirms the gate).

## Status: CONSOLIDATION COMPLETE (backend + frontend). Meitner/Turing/Curie ->
one Munin, three internal routing profiles, auto-route, slash-command override.

## Sequencing

Backend first (consolidate + frame reconcile -> `deploy.sh retrieval` ->
routing-eval gate confirms no regression), THEN frontend (picker removal +
branding -> frontend deploy). Decoupled deploys; the backend stays
backward-compatible (still accepts a `chat`/`code`/`research` persona value) so
the old frontend keeps working until the new one ships.

## Open detail-questions (non-blocking; defaults noted)
- `/api/personas` exact fields for the single Munin entry (default: id `munin`,
  name "Munin", one logo, description, prompt_suggestions = curated merge).
- Routing-eval entry persona: keep `A0_PERSONA="chat"` for A0 comparability, or
  switch to no-pin to match prod? (Default: keep chat for comparability; note it.)
- Munin logo asset: new art or reuse an existing one? (Needs a file from varghele.)

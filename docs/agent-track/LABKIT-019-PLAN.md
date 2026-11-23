# LABKIT-019: EasySpin slice 1, round 2 (plan)

Status: PLAN 2026-11-23, waiting for "proceed". Parent: `EASYSPIN-PORT-PLAN.md`
(slice 1 deployed 2026-11-23, labkit 0.3.0).

## Result that prompts this round

`es1-labkit` (deployed labkit 0.3.0, the five new S8 cases x 3, `eval`
instance): 5/15, against the `es1-baseline` 3/15 (1/9 on the regenerated
files).

| Case | Pass | What went wrong |
|---|---|---|
| Cu(II) frozen | 2/3 | #2 started A_par at 120 MHz with a +-100 range; the fit ended at its bound (160 MHz) |
| units | 3/3 | |
| rigid nitroxide | 0/3 | #1 and #3: `fit_spectrum` ended Azz at its bound; the model then guessed a "typical" 1.4 or 1.5 mT. #2 never fitted; 2.70 mT is the isotropic-looking splitting |
| rhombic heme | 0/3 | `fit_spectrum` never called; g read from absorption peaks and "typical heme values" |
| pyrazine radical | 0/3 | #2 fitted, aH ended at its bound (0.3); #3 hand-measured; #1 quoted literature values |

Every recipe in `guide('epr-sim')` gives the right answer on these files
when it is run as printed, so the physics holds. The failures are in how the
model reaches it.

1. **The wrong hint fires.** On the rhombic, radical and nitroxide #2 runs,
   the hint that fired is `epr_lineshape` or `epr_g`. Those point to
   `fit_line` and `nitroxide_hyperfine`, the tools for isotropic single
   lines. The new `epr_powder` hint needs a hand-written orientation grid
   (theta, phi), which no run wrote. `epr_iso_fit` lost to `epr_lineshape`,
   which comes earlier in the order.
2. **Fits end at their bounds, and the model then invents a value.** Four
   runs got the "ended at its bound: widen its range" warning and reported
   a bound or a literature number instead of refitting.
3. **The API trips on first use.** Errors seen:
   - `SpinSystem(A_MHz=...)`, where A_MHz belongs on a `Nucleus`;
   - `vary` shapes (one entry per nucleus, half-ranges);
   - `fit_spectrum` without `vary`;
   - `EPRData.field` and `.signal`, 4 runs; the real names are `field_mT`
     and `y`.

## Changes

labkit 0.3.1:
- **Bounds that widen.** When a varied parameter ends within 2% of its
  range edge, `fit_spectrum` itself moves the range (centred on the
  current value, the same width, then twice the width) and refits, up to
  two times within `max_time_s`. The result says so in a note. The
  warning stays only if the parameter is still at an edge after that.
- **`vary` is optional.** Default ranges come from the start values: g
  +-0.05 (axial or rhombic per component), A +-50% or 50 MHz (whichever is
  larger), fwhm +-80%, strains 0 to 0.02. `vary="all"` is the same.
- **Wider `vary` shapes.** It also accepts a flat list for a single nucleus
  (`"A_MHz": [30, 100]`) and a scalar for every component.
- **Clearer errors.** A misplaced keyword says where it belongs:
  `SpinSystem(A_MHz=...)` says "hyperfine goes on a Nucleus:
  nuclei=[epr.Nucleus('63Cu', A_MHz=[...])]".
- **Aliases on `EPRData`**: `field` for `field_mT`, `signal` and `intensity`
  for `y` (read-only; `ppms` in nmr is the precedent).
- **Powder checks in the line tools.**
  - `nitroxide_hyperfine` and `fit_line` check whether the spectrum looks
    like a powder pattern: outer extrema more than 4 mT apart for a
    nitroxide, or more than 3 turning points over more than 20 mT.
  - When it does, they add a warning: "this looks like a frozen/powder
    spectrum: aN here is not Azz and peaks are not g-values; fit it with
    epr.fit_spectrum(model='powder'), see guide('epr-sim')".
- **Start estimates in the guide.** `guide('epr-sim')` says how to estimate
  each start from the spectrum, so the fit starts in the right basin:
  - Cu A_par from the spacing of the low-field parallel lines;
  - nitroxide Azz as half the outer extrema splitting;
  - rhombic g at the three turning points.

Retrieval hints:
- **New `epr_sim_context` hint, checked before `epr_lineshape` and
  `epr_g`.** It fires when the code is CW-EPR and any of these appears:
  - words: frozen, 77 K, powder, immobili(s|z)ed, rigid, rhombic,
    principal, g_par/g_perp/g_max/g_mid/g_min, Azz, A_par;
  - two or more nuclei, or a hyperfine pattern with aN and aH.
  It names the fitting recipe for that context. While it applies,
  `epr_lineshape` and `epr_g` do not fire.
- **`epr_powder` no longer needs an orientation grid.**
- **New `epr_bound_guess` hint.** It fires when code writes a number into
  results right after a `fit_spectrum` call whose output had "ended at its
  bound". It says to refit from the estimated start, not to report a
  typical or literature value.
  - Open: is this second one worth it? It needs the previous tool output,
    which the hint code sees for the save round only. Default: build it
    only if the plumbing is already there, otherwise drop it.

Persona: one sentence in the LAB DATA rule. "For frozen, powder or
multi-nucleus spectra use epr.fit_spectrum; never report a typical or
literature value in place of a fitted one."

## Validation

- Unit tests:
  - bound widening on synthetic Cu with A_par started at 120 MHz;
  - nitroxide Azz started at 1.5 mT;
  - the radical with aH started at 0.3 mT.
  Each must recover the truth within the S8 tolerances without the caller
  changing `vary`.
- Replay the 10 failing `es1-labkit` bundles' fit calls against 0.3.1:
  - the Cu #2, nitroxide #1/#3 and radical #2 calls must now land on the
    truth;
  - hints are checked on the stored code with `fit_hint` (rhombic, radical,
    nitroxide #2 must get `epr_sim_context`).
- Test image `munin-sandbox:r10-test`: labkit, sandbox live, isolation,
  fighook. Retrieval hermetic tier.
- After deploy:
  - `es1-labkit` cases x 3 (tag `es2-labkit`), paired against `es1-labkit`;
  - the 21 older S8 cases x 3 as a regression check;
  - fresh `_f1` variants of the five new cases, generated after the freeze.

## Open questions (defaults taken unless you say otherwise)

| # | Question | Default |
|---|---|---|
| Q1 | Should widening be automatic, or only a stronger warning? | Automatic, with a note; the model ignored the warning in 4 of 4 runs |
| Q2 | `vary` defaults when omitted | Yes, as above |
| Q3 | `EPRData` aliases (`field`, `signal`, `intensity`) | Yes |
| Q4 | The `epr_bound_guess` hint | Only if the plumbing exists |

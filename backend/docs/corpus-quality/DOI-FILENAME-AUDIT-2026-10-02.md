# DOI filename audit, 2026-10-02

Read-only audit by `scripts/pipeline/audit_doi_filenames.py` (commit 47d3751).
`doi_*.pdf` filenames map `/` and `:` to `_`; until 2026-10 the decoder restored
only the first `_` and its result overrode GROBID's DOI, so papers were keyed to
DOIs that do not exist (`10.1093/molehr_3.5.431` for `10.1093/molehr/3.5.431`).
The decoder is fixed (d7850de); this report covers the papers already ingested.

Collection `papers_bge`: 68,919 points, 1,311 with `_` after the DOI prefix, all
1,311 checked against Crossref (2,996 lookups). A row is `mis-keyed` only when a
`/` or `:` reading of its DOI resolves on Crossref with a title matching the
stored one (token Jaccard >= 0.3, the pipeline's own guard).

| status | count |
|---|---|
| candidate-title-mismatch | 63 |
| mis-keyed | 921 |
| mismatch | 5 |
| ok | 309 |
| unresolved | 13 |

Mis-keyed whose proposed DOI is already another point: 20


Mis-keyed by registrant: 10.1093 (Oxford) 478, 10.1002 (Wiley SICI) 182,
10.1023 (Kluwer `A:`) 138, 10.1088 (IOP) 37, 10.1051 33, other 53. 916 carry no
`_ingest_source` (pre-provenance bulk ingests), 5 are `crawler`.

## Repair dry run (repair_doi_filenames.py, Qdrant only, 2026-10-02)

| action | count | meaning |
|---|---|---|
| repair | 872 | mis-keyed with proposed_sim >= 0.5 and no point under the corrected DOI |
| merge | 20 | the corrected DOI is already another point: the same paper twice |
| review | 110 | 29 mis-keyed with proposed_sim < 0.5, plus 63 candidate-title-mismatch, 13 unresolved, 5 mismatch |

A repair also re-keys 17,259 chunk payloads in `papers_chunks`. None of these
PDFs has a state sidecar (they predate them), and PDFs keep their names
(retrieval's `get_pdf_path` finds them under the corrected DOI). The Neo4j part
of the plan (rename vs merge into an existing stub node) needs the dry run with
`NEO4J_PASSWORD`, which this one did not have.

The merge and review rows are in `DOI-FILENAME-REVIEW-2026-10-02.csv`; the full
audit (the repair's input) is `DOI-FILENAME-AUDIT-2026-10-02.json`.

## Applying (not done; needs approval)

    set -a; . /opt/hugin/config/cluster.env; set +a
    cd /opt/cluster/scripts/pipeline   # or backend/scripts/pipeline
    A=.../DOI-FILENAME-AUDIT-2026-10-02.json
    ./repair_doi_filenames.py --audit $A --report dry.json                  # full dry run, with graph
    ./repair_doi_filenames.py --audit $A --apply --limit 20 --snapshot --journal doi-repair.jsonl
    # spot-check 5 of the 20 (paper_lookup, /citations, get_pdf_path)
    ./repair_doi_filenames.py --audit $A --apply --journal doi-repair.jsonl
    ./audit_doi_filenames.py --markdown after.md                           # verify

Tested end to end against throwaway Qdrant + Neo4j
(`tests/test_repair_doi_filenames_live.py`, 17 checks: re-key with vector and
provenance, chunk update, graph rename and merge-into-stub with every
relationship moved, sidecar, restorable journal, idempotent re-run).

## Mis-keyed (first 50)

| stored | proposed | sim | dup | title |
|---|---|---|---|---|
| `10.1093/nar_10.21.6553` | `10.1093/nar/10.21.6553` | 1.0 |  | Recent developments in the chemical synthesis of polynucleotides |
| `10.1002/(SICI)1097-0061(20000115)16_1<11__AID-YEA502>3.0.CO;2-K` | `10.1002/(SICI)1097-0061(20000115)16:1<11::AID-YEA502>3.0.CO;2-K` | 1.0 |  | Functional coupling of mammalian receptors to the yeast mating pathway |
| `10.1023/A_1008289724077` | `10.1023/A:1008289724077` | 0.95 |  | Nuclear magnetic resonance characterization of a paramagnetic DNA-drug |
| `10.1002/(sici)1097-0215(19960904)67_5<676__aid-ijc14>3.0.co;2-3` | `10.1002/(sici)1097-0215(19960904)67:5<676::aid-ijc14>3.0.co;2-3` | 0.875 |  | REVERSAL OF ADRIAMYCIN RESISTANCE WITH CHIMERIC ANTI-GANGLIOSIDE Gh. 1 |
| `10.1002/1521-4141(200001)30_1<164__AID-IMMU164>3.0.CO;2-X` | `10.1002/1521-4141(200001)30:1<164::AID-IMMU164>3.0.CO;2-X` | 1.0 |  | Molecular modeling and site-directed mutagenesis of CCR5 reveal residu |
| `10.1093/nar_24.4.596` | `10.1093/nar/24.4.596` | 1.0 |  | Transfecting mammalian cells: optimization of critical parameters affe |
| `10.1093/hmg_11.10.1153` | `10.1093/hmg/11.10.1153` | 1.0 |  | The sense of smell: genomics of vertebrate odorant receptors |
| `10.1002/(SICI)1099-0534(1997)9_5<299__AID-CMR2>3.0.CO;2-U` | `10.1002/(SICI)1099-0534(1997)9:5<299::AID-CMR2>3.0.CO;2-U` | 1.0 |  | Pulsed-Field Gradient Nuclear Magnetic Resonance as a Tool for Studyin |
| `10.1093/nar_11.23.8369` | `10.1093/nar/11.23.8369` | 0.462 |  | Nucleic Acids Research Rapid synthesis of long-chain deoxyribooDgonade |
| `10.1093/toxsci_kfv201` | `10.1093/toxsci/kfv201` | 1.0 |  | High Throughput Measurement of Ca<sup>++</sup>Dynamics in Human Stem C |
| `10.1023/A_1008238009056` | `10.1023/A:1008238009056` | 0.889 |  | Some NMR experiments and a structure determination employing a { 15 N, |
| `10.1023/A_1025401026441` | `10.1023/A:1025401026441` | 1.0 |  | Modulation of cardiac remodeling by adenosine: In vitro and in vivo ef |
| `10.1016/B978-012402380-2_50010-1` | `10.1016/B978-012402380-2/50010-1` | 1.0 |  | Multiple Display of Foreign Peptide Epitopes on Filamentous Bacterioph |
| `10.1093/emboj_16.15.4549` | `10.1093/emboj/16.15.4549` | 1.0 |  | Molecular architecture of the ER translocase probed by chemical crossl |
| `10.1002/(SICI)1521-3773(20000317)39_6<1049__AID-ANIE1049>3.0.CO;2-2` | `10.1002/(SICI)1521-3773(20000317)39:6<1049::AID-ANIE1049>3.0.CO;2-2` | 1.0 |  | Molecular Beacons: A Novel Approach to Detect Protein ± DNA Interactio |
| `10.1088/0031-8949_1990_T33_013` | `10.1088/0031-8949/1990/T33/013` | 1.0 |  | Computer Simulation of Binary Hard-Disc Mixtures |
| `10.1093/nar_27.1.229` | `10.1093/nar/27.1.229` | 1.0 |  | SMART: identification and annotation of domains from signalling and ex |
| `10.1093/geronb_57.5.p396` | `10.1093/geronb/57.5.p396` | 1.0 |  | Ethnic Variation in the Impact of Negative Affect and Emotion Inhibiti |
| `10.1051/jphyslet_0198400450206900` | `10.1051/jphyslet:0198400450206900` | 1.0 |  | Cylindrical microemulsions: a polymer-like phase ? |
| `10.1023/A_1022244025351` | `10.1023/A:1022244025351` | 1.0 |  | Switched-angle spinning applied to bicelles containing phospholipid-as |
| `10.1093/nar_12.7.3257` | `10.1093/nar/12.7.3257` | 0.786 |  | Nucleic Acids Research A simple and convenient synthesis of 3-5'-or 2  |
| `10.1023/B_PRES.0000036883.56959.a9` | `10.1023/B:PRES.0000036883.56959.a9` | 1.0 |  | Rings, ellipses and horseshoes: how purple bacteria harvest solar ener |
| `10.1093/protein_1.4.305` | `10.1093/protein/1.4.305` | 1.0 |  | The determination of the three-dimensional structure of barley serine  |
| `10.1023/A_1018302105638` | `10.1023/A:1018302105638` | 1.0 |  | Protein φ φ and ψ ψ dihedral restraints determined from multidimension |
| `10.1051/jphys_019750036010099100` | `10.1051/jphys:019750036010099100` | 1.0 |  | Calcul des densités spectrales résultant d'un mouvement aléatoire de t |
| `10.1088/0034-4885_48_11_002` | `10.1088/0034-4885/48/11/002` | 1.0 |  | Diffuse x-ray scattering and models of disorder |
| `10.1023/A_1008301016608` | `10.1023/A:1008301016608` | 0.923 |  | Local mobility of 15 N labeled biomolecules characterized through cros |
| `10.1023/A_1008309220156` | `10.1023/A:1008309220156` | 1.0 |  | Simulations of NMR pulse sequences during equilibrium and non-equilibr |
| `10.1093/nar_22.3.514` | `10.1093/nar/22.3.514` | 1.0 |  | Dynamics of transfer RNAs analyzed by normal mode calculation |
| `10.1023/A_1018311013338` | `10.1023/A:1018311013338` | 0.889 |  | Improved labeling strategy for 13 C relaxation measurements of methyl  |
| `10.1051/jp2_1996208` | `10.1051/jp2:1996208` | 0.3 |  | Monoolein/Water System |
| `10.1093/emboj_18.5.1270` | `10.1093/emboj/18.5.1270` | 1.0 |  | Activation of the Raf/MAP kinase cascade by the Ras-related protein TC |
| `10.1093/protein_12.2.85` | `10.1093/protein/12.2.85` | 1.0 |  | Twilight zone of protein sequence alignments |
| `10.1093/dnares_3.3.109` | `10.1093/dnares/3.3.109` | 1.0 |  | Sequence Analysis of the Genome of the Unicellular Cyanobacterium Syne |
| `10.1023/A_1018314808361` | `10.1023/A:1018314808361` | 1.0 |  | Alterations in chemical shifts and exchange broadening upon peptide bo |
| `10.1093/molehr_3.5.431` | `10.1093/molehr/3.5.431` | 1.0 |  | Comparison of gonosomal aneuploidy in spermatozoa of normal fertile me |
| `10.1093/nar_17.7.2379` | `10.1093/nar/17.7.2379` | 0.6 |  | Solid-phase synthesis of oligoribonucleotides using 9-fluorenylmethoxy |
| `10.1002/1097-0282(200101)58_1<78__AID-BIP80>3.0.CO;2-C` | `10.1002/1097-0282(200101)58:1<78::AID-BIP80>3.0.CO;2-C` | 0.905 |  | A 13 C NMR Study on [3-13 C]-, [1-13 C]Ala-, or [1-13 C]Val-Labeled Tr |
| `10.1093/bioinformatics_btn221` | `10.1093/bioinformatics/btn221` | 1.0 |  | OCTOPUS: improving topology prediction by two-track ANN-based preferen |
| `10.1093/nar_27.1.343` | `10.1093/nar/27.1.343` | 1.0 |  | Olfactory Receptor Database: a database of the largest eukaryotic gene |
| `10.1093/infdis_157.4.738` | `10.1093/infdis/157.4.738` | 1.0 |  | Use of Synthetic Peptides to Map Antigenic Sites of Bordetella pertuss |
| `10.1023/A_1006199814795` | `10.1023/A:1006199814795` | 1.0 |  | Regulation of biosynthesis and cellular localization of Sp32 annexins  |
| `10.1155/2016_9098523` | `10.1155/2016/9098523` | 1.0 |  | Microtissues in Cardiovascular Medicine: Regenerative Potential Based  |
| `10.1023/A_1006030221445` | `10.1023/A:1006030221445` | 0.857 |  | Pulsed ENDOR of the photoexcited triplet states of bacteriochlorophyll |
| `10.1093/nar_17.13.5125` | `10.1093/nar/17.13.5125` | 0.818 |  | Highly efficient synthesis of oUgodeoxyribonucleotides using a-phenyl  |
| `10.1093/emboj_18.9.2489` | `10.1093/emboj/18.9.2489` | 1.0 |  | Inhibition of the receptor-binding function of clathrin adaptor protei |
| `10.1088/0953-8984_9_26_007` | `10.1088/0953-8984/9/26/007` | 1.0 |  | Equation-of-state data for CsCl-type alkali halides |
| `10.1385/ENDO_21_3_201` | `10.1385/ENDO:21:3:201` | 1.0 |  | Is Calcitonin an Important Physiological Substance? |
| `10.1051/jphys_01988004904060500` | `10.1051/jphys:01988004904060500` | 1.0 |  | Phase behaviour of an ensemble of nonintersecting random fluid films |
| `10.1023/A_1008329124672` | `10.1023/A:1008329124672` | 0.929 |  | Deuterium isotope effects on the central carbon metabolism of Escheric |

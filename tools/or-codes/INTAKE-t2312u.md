# OR-CODES intake (t2312u)

Lightweight harvest of unique **code and documentation** from the lab-local OR-CODES tree (WPS-cloud sibling of already-absorbed OR-RULES / OR-SKILLS). Source left in place; this is a copy, not a move.

Landed at `tools/or-codes/` (do not merge into `modules/` or overwrite `tools/habitat-pipeline/`).

## Included

| Source (relative to OR-CODES) | Action |
|---|---|
| `README.md`, `tools/README.md`, `external/README.md` | INCLUDE, C1-sanitized (`{or_codes}` / `{lab_root}` placeholders) |
| `entry/` | INCLUDE (shared launchers) |
| `habitat_core/` | INCLUDE (evolved shared library; same *basenames* as `tools/habitat-pipeline/` but **different SHA-256**, not a duplicate) |
| `tools/**` unique scripts and their READMEs | INCLUDE, minus the exclusions below |
| `tools/lxf_LUNG/timesfm/files.zip` | INCLUDE unzipped (`run_lung_recist_timesfm.py`, `extract_lung_features.py`, `config.yaml`, `README.md`); zip itself not committed |

Public-copy edits (C1 / `test_public_path_policy.py`): lab-absolute roots rewritten to `{or_codes}` / `{lab_root}`; remaining module-level drive-letter defaults (for example `L:` / `G:` / `E:`) rewritten to `os.environ.get(<NAME>, "")`. `tools/audit/l_path_map.py` scan roots and the 20261121 project-rename map were stubbed to `[]` / `{}` (the map stays in the source tree).

## Excluded

| Path | Reason |
|---|---|
| `tools/audit/db-trim/` | Audit unpack data (~5.31 GiB, 2,235 files): `.unpacked` tars, large `.xlsx` / `.csv`. Not library code. |
| `external/chromedriver/` | Third-party browser binary (~34.35 MiB). Documented in `external/README.md`. |
| `external/MM-Skin/` | Upstream third-party tree (~15.65 MiB, including eval result JSON / test CSV). Declare the dependency; do not vendor. Source: `github.com/ZwQ803/MM-Skin` (arXiv 2505.06152). |
| `external/TimesFM/timesfm-master.zip` | Upstream Apache-2.0 archive, timesfm 2.0.1 (~2.69 MiB). Declare the dependency; do not vendor. |
| `matlab/t1vbm/` (and `matlab/README.md`) | Already in `tools/preprocess-imaging/t1vbm/`. 11 files are exact SHA-256 matches; the remaining diffs show **MY-CODES is ahead** (V2.5 TIV covariates, extra `cat12_xml_extract.py`). Do not overwrite with the older OR snapshot. |
| `tools/roi/xlm_LUNG/expand_roi_5mm_methods_only.docx` | Exact SHA-256 match of `tools/habitat-pipeline/utility/expand_roi_5mm_methods_only.docx`. |
| `LOCAL-OVERRIDES.md` | Lab path-override table (C1). Left at the source. |
| `tools/lxf_LUNG/clinical/query_patient.py` | File itself says it is machine-private and must not enter any repo; contains a hospital HIS login URL with encoded credentials. Left at the source. |
| `__pycache__/` | Bytecode. |

## Dedup notes (already in MY-CODES, not copied or not overwritten)

- `tools/preprocess-imaging/t1vbm/` — see above; live copy is newer.
- `tools/habitat-pipeline/` — historical Desktop.7z intake (t1727u). `habitat_core/` here is a later shared-core + ini refactor, kept as a separate tree.
- Same *basename*, different SHA (thin shells or evolved copies, kept): `delete_files_by_keyword.py`, `match_img_ior_updated.py`, `expand_roi_5mm.py`, `log_reader.py`, and the `lxf_LUNG/radiomics/` helpers vs `tools/habitat-pipeline/utility/`.

## Registry

Same pattern as `tools/habitat-pipeline/INTAKE-t1727u.md`. No separate harvest LEDGER row: `_Docs/harvest/LEDGER.tsv` is the skill/rule-round ledger, not a code-intake register.

Source on WPS cloud was not moved or deleted.

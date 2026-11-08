# habitat-pipeline (t1727u)

Imported from `Downloads/Desktop.7z` (2026-11-07). **Not** part of the stats engine under `modules/` — keep here under `tools/habitat-pipeline/`.

## Layout
- `habitat_pipeline/` — multi-sequence habitat pipeline (config, load, preproc, register, habitat, radiomics, test)
- `delta_habitat_pipeline/` — delta/timepoint habitat pipeline (paired timepoints)
  - 历史分叉：与 `habitat_pipeline/` 同名的 7 个模块（config/data_loader/habitat/pipeline/preprocessing/radiomics/registration）已各自演化，**暂不合并**；新改动只进 `habitat_pipeline/`，delta_ 仅保留历史行为（t1823u 记录）。
- `utility/` — helpers (ROI expand, matching, viz, LASSO helper, ROC line, log reader, validate_delta_habitat, …)

## Dedup note
Compared against MY-CODES `modules/` and `pre-submit/lxf_PP` by filename + SHA-256.
- No exact SHA matches.
- Same *basename* only: `pipeline.py` / `radiomics.py` exist under `modules/` but are a different package (stats console engine) — **do not merge into modules/**.

## lxf_PP
Related note parked at `pre-submit/lxf_PP/ref/code-gates/Desktop7z-habitat-pipeline-t1727u.md`
(relative to the 0Grok workspace root).
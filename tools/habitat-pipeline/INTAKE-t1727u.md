# Desktop.7z intake inventory (t1727u)

| Path | Action | Reason |
|---|---|---|
| delta_habitat_pipeline/* | INCLUDE → tools/habitat-pipeline/delta_habitat_pipeline/ | New; no SHA match in MY-CODES |
| habitat_pipeline/* | INCLUDE → tools/habitat-pipeline/habitat_pipeline/ | New; no SHA match |
| utility/* | INCLUDE → tools/habitat-pipeline/utility/ | New helpers |
| *.pyc | SKIP | bytecode |
| modules/ dump | SKIP | not generic stats engine; keep under tools/ |

Same-name-different-SHA (not duplicates):
- pipeline.py / radiomics.py vs modules/pipeline.py and modules/stats/radiomics.py
# lab-reference (fine_id: fill-protocol)



Rehomed 2026-10-23 from `_code\_infra\lab-reference\`.



Scripts that build (a) lab reference-ranges workbook and (b) imaging-scanner

parameter registry under `%GROK_ROOT%\0doc\_library\`.



| Script | Role | Also used by |

|--------|------|--------------|

| `lab_ref_ranges_*.py` | Build `lab-reference-ranges.xlsx` (peer-review numeric plausibility) | `self-review` (numeric checks; path_shared helper, not a new fine_id) |

| `scanner_registry_*.py` | Build `scanner-parameter-registry.xlsx` | library method / fill-protocol form-registry builders |



```text

python %GROK_ROOT%\0doc\_code\03-research\fill-protocol\lab-reference\scanner_registry_extract.py --stage all

python %GROK_ROOT%\0doc\_code\03-research\fill-protocol\lab-reference\scanner_registry_build.py

```



Text extraction reuses `05-manuscript\write-paper\docx_extract.py`.


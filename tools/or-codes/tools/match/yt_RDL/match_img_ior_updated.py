# -*- coding: utf-8 -*-
"""yt_RDL 的影像↔ROI 配对（薄壳）。

实现在 `_Codes\tools\match\_shared\shape_d.py`；配置读工程 `settings.ini` 的 `MATCH_CONFIG`。
用法：python match_img_ior_updated.py --project yt_RDL [--dry-run] [--output-dir ...]
"""
from __future__ import annotations

import os
import sys

_CORE = os.environ.get("HABITAT_CORE_ROOT") or r"{or_codes}"
_SHARED = os.path.join(_CORE, "tools", "match", "_shared")
for _p in (_SHARED, _CORE):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from shape_d import main  # noqa: E402

if __name__ == "__main__":
    main()

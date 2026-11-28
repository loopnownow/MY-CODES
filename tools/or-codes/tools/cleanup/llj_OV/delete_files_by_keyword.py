# -*- coding: utf-8 -*-
"""llj_OV 的 checkpoints 清理（薄壳）。

实现在 `_Codes\\tools\\cleanup\\_shared\\cleanup_core.py`；
配置读工程 `settings.ini` 的 [tools] 段（CLEANUP_TARGET_DIR / CLEANUP_ERR_CSV / CLEANUP_KEYWORDS）。
用法：python delete_files_by_keyword.py --project llj_OV [--execute]
"""
from __future__ import annotations

import os
import sys

_CORE = os.environ.get("HABITAT_CORE_ROOT") or r"{or_codes}"
_SHARED = os.path.join(_CORE, "tools", "cleanup", "_shared")
for _p in (_SHARED, _CORE):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from cleanup_core import main  # noqa: E402

if __name__ == "__main__":
    main()

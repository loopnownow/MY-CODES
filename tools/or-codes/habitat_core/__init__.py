# -*- coding: utf-8 -*-
"""habitat_core：多参数生境影像组学流水线（8 个 G: 工程共用的一份实现）。

原来每个工程各存一份完全相同的 habitat_pipeline/*.py，只有 config.py 的 3 行路径不同。
现在代码只在这里一份，工程根只留 settings.ini。

用法（工程目录内）：
    python -m habitat_core.pipeline --data <工程>\\habitat_pipeline\\combined.csv
    python -m habitat_core.test --csv <工程>\\habitat_pipeline\\combined.csv
或直接跑工程根的 run_pipeline.py / run_smoke_test.py。

配置器页面：python -m habitat_core.web.config_server --ini <工程>\\settings.ini
"""
from __future__ import annotations

__all__ = [
    "config", "ini_config", "data_loader", "preprocessing",
    "registration", "habitat", "radiomics", "pipeline", "test",
]
__version__ = "1.0.0"

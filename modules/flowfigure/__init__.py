# -*- coding: utf-8 -*-
"""Figure 1（STROBE 研究流程图）单一源包。

spec 是唯一输入；本包负责 schema 归一（``spec``）、fail-closed n 审计
（``audit``）、版式（``layout``）、渲染（``render_mpl``）、手稿预填
（``prefill``）、Results 回填（``results``）与 CLI（``cli``）。

模块边界：``spec`` / ``audit`` 只做纯数据变换，不读项目文件、不画图。
"""
from __future__ import annotations

from modules.flowfigure import audit, spec

__all__ = ["audit", "spec"]

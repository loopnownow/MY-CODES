"""QC 只在「数据预览」页手动跑：运行统计不再写 ref/qc.html。

守三件事：
1. `modules/pipeline.py` 不再引用任何 QC 写入（`write_merged_qc` / `_maybe_write_qc_report`）。
2. 起始页开服务后进「数据预览」（`tab=preview`）。
3. 前端手动入口仍在：预览页 `#btnDataQc` → `runDataQc()` → `/api/qc`。
"""
from __future__ import annotations

import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
CONSOLE = ROOT / "modules" / "console"


class TestQcIsManualOnly(unittest.TestCase):
    def test_pipeline_writes_no_qc_report(self) -> None:
        src = (ROOT / "modules" / "pipeline.py").read_text(encoding="utf-8")
        for token in ("_maybe_write_qc_report", "write_merged_qc", "QC REPORT"):
            self.assertNotIn(token, src, f"pipeline.py 仍在跑 QC: {token}")

    def test_start_page_lands_on_preview(self) -> None:
        proj = (CONSOLE / "project.html").read_text(encoding="utf-8")
        self.assertIn('"tab", "preview"', proj)
        self.assertNotIn('"tab", "config"', proj)

    def test_preview_page_has_manual_qc_button(self) -> None:
        html = (CONSOLE / "console.html").read_text(encoding="utf-8")
        api = (CONSOLE / "console_api.js").read_text(encoding="utf-8")
        ui = (CONSOLE / "console_ui.js").read_text(encoding="utf-8")
        self.assertIn('id="btnDataQc"', html)
        self.assertIn("async function runDataQc", api)
        self.assertIn('api("/api/qc"', api)
        self.assertIn('$("btnDataQc").onclick', ui)


if __name__ == "__main__":
    unittest.main()

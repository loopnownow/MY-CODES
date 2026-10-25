# stats-analysis（算法库主库）

2026-10-23 由 `0doc\GitHub\stats-analysis\` 提升为**独立主库**（用户方案 P1）。
只做**本地 git**，**未配远端**（不联网、不推送）。

## 内容

| 目录 | 角色 |
|---|---|
| `modules\` | 可复用算法库：`config` / `console`（含 `strobe/` 页）/ `stats` / `tests` / `utils` |
| `model-validation\` | 模型验证（校准 / DCA / NRI 等）流程与脚本 |

## 与 `_code` 的关系（2026-10-25 起：本仓即唯一真源）

```
D:\0Grok\0doc\GitHub\stats-analysis\modules              ← 运行时真源 + 本地 git（唯一）
D:\0Grok\0doc\_code\02-data-processing\stats-analysis\   ← 已退役，只留指针 README
                                                             （原 80 文件 → D:\0del\...\stats-analysis-mirror\）
```

- **本仓是运行时的唯一真源**：22 个课题的 `ref\settings.ini` 里 `MODULES_DIR` 全部指向
  `D:\0Grok\0doc\GitHub\stats-analysis\modules`（2026-10-25 全量改写）。`MODULES_DIR` 不是目录时
  console 与 `import modules` 直接报错，不回退。
- 改算法 → 直接改本仓 → 在本仓 `git commit`（只本地，无远端）。
- **不再有 `--promote` / 镜像同步**：旧 `sync_modules.py --promote` 已删除（`_code` 副本不存在，无双份可同步）。

## 校验

```powershell
cd D:\0Grok\0doc\GitHub\stats-analysis
$env:PYTHONDONTWRITEBYTECODE='1'   # 别在树里留 pyc
python -B -m pytest modules\tests -q -p no:cacheprovider
```

2026-10-25 实测基线：**160 passed / 0 failed**。

2026-10-23 曾记为**既有失败**的 `test_dry_run_and_shutdown.py::test_icc_panel_hint_present`
（测试要 `modules.console.setting_meta.ICC_PANEL_HINT`，该常量当时不存在）已于 2026-10-25 修复：
在 `setting_meta.py` 补 `ICC_PANEL_HINT`（面板级 outcome-blind 文案），并在 `console_schema.js`
的 `icc` 组接上 `title` / `hint`（该组此前是全套唯一无 title 无 hint 的组）。未改动测试断言。

项目侧一致性用 `python D:\0Grok\0doc\_code\02-data-processing\_shared\sync\sync_modules.py --check`（22 个项目应全部 OK）。

## 纪律

- 本仓只放代码与测试，**不放**患者数据、`ref\settings.ini`、项目稿。
- `__pycache__` / `*.pyc` 由 `.gitignore` 挡住，不要提交。
- 需要远端时由主人点名后再 `git remote add`（本仓当前无远端）。

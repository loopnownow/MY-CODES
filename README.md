# stats-analysis（算法库主库 / 02 席位脚本半）

2026-10-23 由 `0doc/GitHub/stats-analysis/` 提升为**独立主库**。

## 三仓分工（2026-10-27 站立）

| 仓 | 放什么 | 不放什么 |
|---|---|---|
| **MY-RULES** | 站立运营政策：本机布局、交付落点、新建禁区、网络红线、技能库契约 | 专业技能正文、可运行代码 |
| **MY-SKILLS** | 专业技能（`NN_slug/base/` 能力正文 + `advanced/` 个人层 + `qc/` 门控标准），全部是 prose | 可运行的分析/清洗代码 |
| **stats-analysis**（本仓） | 可运行代码：`modules/` 算法库 + `model-validation/` CLI | 任何 skill 正文、患者数据、课题配置 |

`stats-analysis` 是 **02_data-processing 席位的脚本半**：`MY-SKILLS/02_data-processing/` 说「怎么做、按什么规则」，
本仓是它真正跑的那部分。技能正文引用本仓的路径别名（`{code_stats}` / `{code_modules}` / `{code_model_validation}`），
不 vendor 任何 `.py`。反方向同样成立：本仓不写 skill 规则，规则改动进 MY-SKILLS。

## 远端

`origin` = `https://github.com/loopnownow/stats-analysis.git`（**public**）。
默认分支 `main`；推送 / fetch / 改可见性都属**联网动作**，须当轮点名才做。

- 公网可读：不要把本机绝对路径、未发表课题代号、具体分析变量名写进代码、用例或文档。
- `modules/console/console.override.json` / `console.port.json` 是**本机会话状态**（控制台每次保存重写），已 gitignore；键结构示例见 `modules/console/console.override.example.json`。

## 内容

| 目录 | 角色 |
|---|---|
| `modules/` | 可复用算法库：`config` / `console`（含 `strobe/` 页；控制台已拆为 `schema_ui` / `settings_layer` / `preview` / `dryrun` / `run` / `serve` + `http_impl` 零注入）/ `stats` / `tests` / `utils` |
| `model-validation/` | 建模 / 验证 / 插补 CLI（校准、DCA、NRI、泄漏检查、研究卡审计、`impute_excel.py`） |

测试用 `pyproject.toml` 的 `[tool.pytest.ini_options]`（`testpaths = modules/tests`，`pythonpath = ["."]`），
仓根直接 `pytest` 即可，不另设 `pytest.ini`。

## 与 `_code` 的关系（本仓即唯一真源）

```
<this-repo>/modules                         ← 运行时真源 + git（唯一）
<_code>/02-data-processing/stats-analysis/  ← 已退役，只留指针 README
```

- **本仓是运行时的唯一真源**：各课题 `ref/settings.ini` 的 `MODULES_DIR` 指向本仓 `modules/`。`MODULES_DIR` 不是目录时 console 与 `import modules` 直接报错，不回退。
- 改算法 → 直接改本仓 → `git commit`；需要远端时再 `git push`（须点名）。
- **不再有 `--promote` / 镜像同步**：旧 `sync_modules.py --promote` 已删除，`sync_modules.py --check` 也不再存在。

实验根目录用环境变量 `GROK_ROOT`（未设置时本机 lab 有冻结默认）；算法库根用 `STATS_ROOT` 或 `MODULES_DIR`。

## 校验

```bash
cd <this-repo>
PYTHONDONTWRITEBYTECODE=1 python -B -m pytest -q -p no:cacheprovider
```

2026-10-30 实测基线：**221 passed / 2 skipped / 0 failed**（statsmodels / sklearn 的告警不算失败）。

版本号只有两个位置：`modules/__init__.py` 的 `__version__`（运行时，课题看到的就是它）与
`pyproject.toml` 的 `version`（打包元数据）。两者必须相同，`modules/tests/test_version_consistency.py` 会卡住。
技能正文（MY-SKILLS）不复写版本号以外的细节，只引用同一个数字。

## 纪律

- 本仓只放代码与测试，**不放**患者数据、`ref/settings.ini`、项目稿。
- `__pycache__` / `*.pyc` / `*.egg-info` 由 `.gitignore` 挡住，不要提交。
- 公网仓：示例课题名用 `demo_study`；路径用相对路径或 `GROK_ROOT` / `STATS_ROOT` / `PROJECT_ROOT`。

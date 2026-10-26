# stats-analysis（算法库主库）

2026-10-23 由 `0doc/GitHub/stats-analysis/` 提升为**独立主库**。

## 远端

`origin` = `https://github.com/loopnownow/stats-analysis.git`（**public**）。
本地以 `master` 跟踪 `origin/master`；推送 / fetch / 改可见性都属**联网动作**，须当轮点名才做。

- 公网可读：不要把本机绝对路径、未发表课题代号、具体分析变量名写进代码、用例或文档。
- `modules/console/console.override.json` / `console.port.json` 是**本机会话状态**（控制台每次保存重写），已 gitignore；键结构示例见 `modules/console/console.override.example.json`。

## 内容

| 目录 | 角色 |
|---|---|
| `modules/` | 可复用算法库：`config` / `console`（含 `strobe/` 页）/ `stats` / `tests` / `utils` |
| `model-validation/` | 模型验证（校准 / DCA / NRI 等）流程与脚本 |

## 与 `_code` 的关系（本仓即唯一真源）

```
<this-repo>/modules                         ← 运行时真源 + git（唯一）
<_code>/02-data-processing/stats-analysis/  ← 已退役，只留指针 README
```

- **本仓是运行时的唯一真源**：各课题 `ref/settings.ini` 的 `MODULES_DIR` 指向本仓 `modules/`。`MODULES_DIR` 不是目录时 console 与 `import modules` 直接报错，不回退。
- 改算法 → 直接改本仓 → `git commit`；需要远端时再 `git push`（须点名）。
- **不再有 `--promote` / 镜像同步**：旧 `sync_modules.py --promote` 已删除。

实验根目录用环境变量 `GROK_ROOT`（未设置时本机 lab 有冻结默认）；算法库根用 `STATS_ROOT` 或 `MODULES_DIR`。

## 校验

```powershell
cd <this-repo>
$env:PYTHONDONTWRITEBYTECODE='1'
python -B -m pytest modules\tests -q -p no:cacheprovider
```

2026-10-26 实测基线：**177 passed / 1 skipped / 0 failed**（可有 1 条 statsmodels 警告）。

项目侧一致性用 `_code` 下的 `sync_modules.py --check`（各项目应全部 OK）。

## 纪律

- 本仓只放代码与测试，**不放**患者数据、`ref/settings.ini`、项目稿。
- `__pycache__` / `*.pyc` 由 `.gitignore` 挡住，不要提交。
- 公网仓：示例课题名用 `demo_study`；路径用相对路径或 `GROK_ROOT` / `STATS_ROOT` / `PROJECT_ROOT`。
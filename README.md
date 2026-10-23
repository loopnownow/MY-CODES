# stats-analysis（算法库主库）

2026-10-23 由 `0doc\_code\04-analysis\stats-analysis\` 提升为**独立主库**（用户方案 P1）。
只做**本地 git**，**未配远端**（不联网、不推送）。

## 内容

| 目录 | 角色 |
|---|---|
| `modules\` | 可复用算法库：`config` / `console` / `stats` / `strobe` / `tests` / `utils` |
| `model-validation\` | 模型验证（校准 / DCA / NRI 等）流程与脚本 |

## 两者的关系（单向）

```
D:\0Grok\0doc\_code\04-analysis\stats-analysis\modules     ← 工程参考副本（GOLD）
                    │  sync_modules.py --promote
                    ▼
D:\0Grok\0doc\GitHub\stats-analysis\modules                 ← 本仓（主库快照）
```

- **GOLD 是运行时的唯一真源**：44 个分析项目的 `settings.py` 里写死的 `MODULES_DIR`
  仍指向 `_code\04-analysis\stats-analysis\modules`，**本次迁移未改任何项目**。
- 改算法 → 改 GOLD → 跑 `--promote` 把差异同步到本仓 → 在本仓 `git commit`。
- 反向不自动：本仓手改不会被 GOLD 吸收（要落回 GOLD 再 promote，避免双份分叉）。

## 同步命令

```powershell
# 只看差异（不写）
python D:\0Grok\0doc\_code\_infra\sync\sync_modules.py --promote

# 写入（被替换/多余的文件移到 D:\0del\modules_master_bak\<时间戳>\，不删）
python D:\0Grok\0doc\_code\_infra\sync\sync_modules.py --promote --apply
```

## 校验

```powershell
python -B D:\0Grok\0doc\_code\04-analysis\stats-analysis\modules\tests\smoke_test.py
```

`ALL SMOKE OK` 才算可用。项目侧一致性用 `sync_modules.py --check`（应全部 OK）。

## 纪律

- 本仓只放代码与测试，**不放**患者数据、`ref\settings.ini`、项目稿。
- `__pycache__` / `*.pyc` 由 `.gitignore` 挡住，不要提交。
- 需要远端时由主人点名后再 `git remote add`（本仓当前无远端）。

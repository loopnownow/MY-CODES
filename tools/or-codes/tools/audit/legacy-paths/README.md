# audit_legacy_paths —— 旧路径 / 旧名称扫描器

配套规则：`OR-RULES\01_project-org.md` §8 Q21 与 §14。

## 用途

改名 / 搬盘 / 重排布局后，确认项目内不再残留指向旧布局的路径或旧名称。

## 用法

```powershell
# 1) 只读扫描：给已弃布局写正则，输出命中清单
python -X utf8 audit_legacy_paths.py --root {lab_root}\pre-submit\lxf_LUNG `
    --pattern 'roi[\\/](BB|JS)[\\/]' --pattern 'nnUNet[\\/]inference' `
    --out L:\_Del\_by-function\<批次>\LEGACY-PATH-HITS.tsv

# 2) 数据件批量改值（old->new 两列 tsv），改前备份
python -X utf8 audit_legacy_paths.py --root <项目根> --ext .csv --map old-new.tsv `
    --fix --backup <项目根>\ref\tmp\legacy-path-fix-<YYYYMMDD>
```

## 约定

- 默认跳过 `tmp`、`_pre-reorg-*`、`.git`、`__pycache__`；`--backup` 目录本身不参与扫描。
- 只替换**字面全路径**；把 `old→new` 表做小的替换（如整树换根）时，先用导出工具生成对照表。
- 文档（md）与代码（py）**不自动改**：md 区分活文档 / 史证，py 要人看懂后再改。
- 通过标准：命中清单只剩带 `史证 / 旧布局 / MOVED / 已退役` 标记词的行。

## 已知限制

- 不做语义理解：同一字符串既是历史记录又是活引用时，需人工判。
- 大表（万行级）逐对替换会慢，批量改值请用前缀锚定的单次替换（见 `fix_paths_fast.py` 思路）。

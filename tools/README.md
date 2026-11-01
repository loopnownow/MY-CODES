# MY-CODES\tools — 运维 / 加工脚本（02 席位脚本半的非算法部分）

算法库是 `modules/`；建模/验证 CLI 在 `model-validation/`；本层放**能直接跑的数据加工与写稿辅助脚本**。
2026-11-01 由 `0doc\_code` 迁入（MOVE 台账见 `{DEL}\_by-function\t1521u-mycodes-rename\removed\MOVED-tools.tsv`）。

| 目录 | 作用 | 入口 / 备注 |
|---|---|---|
| `clean-data/` | Excel 清洗主链（tidy 合并/拆分/校验、插补、随访补齐、`_selftest` 夹具） | `setup_cln_only.py`、`tidy_complete.py`；`_selftest/README.md` 说夹具怎么造 |
| `sync/` | 课题侧同步与巡检：`settings.ini` 对齐、多课题批量重跑、smoke | `sync_modules.py`、`batch_rerun_pipelines.py`、`smoke_test.py`、`project-shim/` |
| `preprocess-imaging/` | 影像预处理（`fmri/` MATLAB、`t1vbm/` CAT12 流水线 + 测试） | `t1vbm/run_all.m`；MATLAB 侧按 `t1vbm_config.m` 配 |
| `fill-protocol/` | 伦理/方案模板的 Word 批量填充 | `docx_cell_replace.py`、`word_find_replace.py` |
| `lab-reference/` | 检验参考区间 / 扫描仪参数登记表的抽取与建表 | `*_extract.py` → `*_rows.py` → `*_build.py` |
| `write-paper/` | 稿件侧小工具（docx 抽文本、改稿证据汇总） | `docx_extract.py`、`diff_harvest.py` |
| `revise/` | 回修包打包 | `pack_docx.py` |
| `templates/` | 软编码 + dry-run 模板 | `soft_code_template.py` |
| `orchestrator/` | 00 层 QC 子检查：AI-tell 风格 linter（G-05） | `style_lint.py`；需 `SKILLS_ROOT`（读技能树的 `ban-terms.yaml`） |
| `office/` | Word 修订机制：`unpack` / `validate` / `pack` + `helpers/` + `validators/`（Anthropic，见 `LICENSE.txt`） | `validate.py --original <pre-edit.docx> --author A` 是硬门；契约在 `{SKILLS}\06_review\base\track-changes\MODULE.md` |
| `verify-refs/` | 引用核验与 BibTeX：OpenAlex / PubMed / Scholar 检索、元数据抽取、格式化、核验、撤稿扫描 | `_common.py` + 8 个 CLI；上游 MIT 见 `LICENSE_NOTE.txt`；联网脚本过网络闸（见下节） |
| `lit-fulltext/` | OA 全文批量取回（Unpaywall / PMC / EuropePMC / OpenAlex / Crossref / arXiv） | `fetch.py`；付费墙只写 `manual_needed.txt`；`LICENSE_NOTE.txt` 记上游 |
| `manuscript-qc/` | 稿面 QC 检测器：改写保真（数字/引用不漂移）与句长多样性 | `check_rewrite_fidelity.py`、`check_sentence_variety.py`；离线、stdlib |
| `vocab-harvest/` | 语料措辞计数：重建 IMRAD 段落层、统计句首/句尾短语 | `harvest_sections.py`、`harvest_write_phrases.py`；需 `DOC_BANK_ROOT`（`<0doc>\_bank`）与 `SKILLS_ROOT`（ban 词表），缺则 exit 1 |
| `draw-flowchart/` | STROBE 流程图生成（type A published / type A′ expanded；`--json` + `--out`，n-audit 缺数即非零） | `draw_strobe_flow.py`、`draw_strobe_flow_expanded.py`；项目常量留在课题 `ref/figure1_strobe.json` |
| `visualization/` | 技能树可视化编译器（registry / runtime-flow / gates / ARCHITECTURE → typed IR → HTML） | `gen_visualization.py`；需 `SKILLS_ROOT`/`MY_SKILLS`，输出用 `--out` 或 `MY_SKILLS_VIZ_OUT`；需 `pyyaml` |
| `stats-analysis/` | 统计假设程序化检查（正态性 / 方差 / 离群点） | `assumption_checks.py`；离线、stdlib |
| `response-qc/` | 审稿回复包 QC：包一致性审计 + tone-polish 机械打磨 | `check_package_consistency.py`、`tone_polish_main.py`；MIT（AIPOCH）见 `LICENSE_NOTE.txt`；离线、stdlib |

## 网络闸（`verify-refs` / `lit-fulltext`）

这两个包会打 PubMed / OpenAlex / Crossref / Unpaywall / Retraction Watch，**默认拒绝运行**
（exit 1，stderr `network disabled`）。opt-in 两种：每次加 `--allow-network`，或整壳设
`SKILLS_ALLOW_NETWORK=1`。`format_bibtex.py`、`search_google_scholar.py` 不出网，无需开关。
`modules/tests/test_tools_lit_verify.py` 卡住这条闸（只在本地跑，不联网）。

## 路径与环境（软编码，无绝对盘符）

脚本不写死本机盘符（公开仓）。解析顺序：

| 变量 | 用途 | 未设时的解析 |
|---|---|---|
| `STATS_ROOT` | 本仓根（`modules/` 所在） | `Path(__file__).resolve().parents[2]`（脚本在 `tools/<tool>/`） |
| `GROK_ROOT` | 课题阶段根父目录 | `modules.console.paths.grok_root()` → 冻结 lab 默认（C1 唯一文档例外） |
| `DEL_ROOT` | 临时/删除区 | `D:\0del`（不属于 lab 盘符，可留默认） |
| `ZERAD_ROOT` / `ORAD_ROOT` | 单个阶段根（只扫该阶段） | 未设则扫 `GROK_ROOT` 下所有阶段目录 |

`tools/sync/project-shim/console.html`、`strobe.html` 里的示例路径同样写作 `%GROK_ROOT%\…`。

## 跑法（门）

```bash
python -B tools/sync/smoke_test.py          # ALL SMOKE OK
python -B -m pytest -q -p no:cacheprovider  # 仓测试（含 C1 公开路径策略）
```

## 纪律

- 患者数据、课题 `ref/settings.ini`、稿件一律不进本仓。
- 本层只放**能跑**的东西；怎么用、按什么规则写在 MY-SKILLS（`{SKILLS}\02_data-processing\` 等）。
- 新增/改动 `modules\` 前须先获同意（见 `{RULES}\workspace-layout.md`）。

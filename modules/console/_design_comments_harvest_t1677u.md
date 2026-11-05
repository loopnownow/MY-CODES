# 前端设计注释抽取（t1676u / t1677u）

本文件是**只读抽取产物**：把控制台金版里「界面 / 布局 / UX 意图」的注释逐条列出行号与原文，归类，并对照
`c2b38fa`（起始页开/关分键 + 预览/配置卡合并 + 临床线性拟合默认开）的已合入说明做验收映射。

- **基线**：`6422b4f`（`Merge pull request #53`，即 `c2b38fa` 之后的 #52/#53 已入主线）。行号以此为准。
- **范围**：`modules/console/project.html`、`console.html`、`console.css`、`console_ui.js`、
  `console_schema.js`、`strobe/index.html`（后者仅看页壳 / 预览相关设计注）。
- **不含未提交改动**：抽取当时主工作树有 12 个脏文件（含 `console_ui.js` / `schema.py` / `preview.py`），
  为免把别人的 WIP 当成现状，本清单在独立 worktree 上按 `6422b4f` 的干净内容抽。
  已知差异：脏树快照会比本清单多 5 条（`console_ui.js` 的「展示姓名列默认跟 ID 列同列」「单块时…块表映射」
  「单块：角色就叫组学模块」「先按角色表顺序…」「旧配置只有 RAD_SHEET」等 WIP 新增注释）。
- **抽取规则**：`<!-- -->`、`//`、`/* */` 三种形态；跨行块注只记起始行。
- **排除（另列「保留附录」）**：纯实现细节（正则前缀判定、事件时序 / 防抖、BOM 与编码）、数据契约镜像
  （`CORR_*` ↔ `PARTIAL_*`）、以及浏览器兼容 workaround。这些**不建议剥离**——剥离后同位置极易被改回去。
- **安全 / token / CORS**：上述文件里**没有**以注释形态存在的安全契约句（token 契约实际落在
  `console.html:6` 的 `<meta name="console-token">` 与 `modules/console/registry.py`），故本清单不涉及该类剥离。
- **口径**：`建议剥离` = 该句只是把已进提交说明 / 使用说明的界面契约又在代码里复述一遍；
  `建议保留` = 该句解释了「为什么这么写」且别处查不到（改动此处会踩回去）。

## 1 project.html（6 条）

| 行 | 原文 | 归类 | 建议 |
|---|---|---|---|
| 271 | `<!-- 数据区已移到控制台「数据预览」页；本区只作 pageUrl / protoUrl 的状态来源，页面不显示。 -->` | 起始页 | 剥离 |
| 339 | `// 共享算法库默认路径（写回 settings.ini 的 MODULES_DIR；serve 时注入）` | 其它（注入实现） | 保留 |
| 628 | `// 只带非空值：空白参数会把 ini 里的 sheet 覆盖成空串。` | 其它（传参实现） | 保留 |
| 935 | `// 开服务后自动进控制台「统计配置」页（先探一次 /api/settings，失败就留在本页报原因）。` | 起始页 | 剥离 |
| 1263 | `// single result → status only` | 起始页（扫描状态文案） | 保留 |
| 1340 | `// Fire probe UI in parallel; protocol launch must stay inside the click gesture.` | 其它（事件时序，排除项） | 保留 |

## 2 console.html（0 条）

整页无注释。界面意图全部写在 **`data-tip` / `title` 属性**里（`:25/:26/:27` 三个 tab，
`:30` 保存配置、`:31` 重启服务，`:58` banner，`:115/:121/:157/:164` 四张卡的 `data-tip`）。
→ 不是可剥离对象：剥掉会直接掉 UX 提示，属于活的界面文案。

## 3 console.css（33 条）

| 行 | 原文 | 归类 | 建议 |
|---|---|---|---|
| 46 | `数据预览页：三列（项目信息 / 数据+结果 / 操作）` | CSS 布局 | 剥离 |
| 66 | `工作表角色表` | CSS 布局 | 剥离 |
| 76 | `样本排除名单（多文件，共用第一行的 sheet / 列 / 内容）` | 数据预览 | 剥离 |
| 85 | `三个文件卡（主数据 / 外验 / 排除名单）共用同一套头行：标签靠左、输入框铺满、按钮贴右。` | 数据预览 | 剥离 |
| 170 | `Keep overflow:auto while a <select> is open. overflow:visible on :focus` | CSS 布局（兼容 workaround） | 保留 |
| 270 | `临床筛选：一行多列（与临床预处理同一套排布）` | 统计配置 | 剥离 |
| 359 | `Size by middle pane, not the window. Keep 6 columns until the pane is truly narrow.` | CSS 布局 | 保留 |
| 474 | `display:contents 的祖先会让 Windows 上的 <select> 点不开。这里改成普通网格。` | CSS 布局（兼容 workaround） | 保留 |
| 714 | `roc/advanced 布局见文末权威块；此处不再写 3/8 列` | CSS 布局（防回归） | 保留 |
| 966 | `=== RAD console polish (UX + visual design) ===` | CSS 布局 | 剥离 |
| 982 | `Section cards: clearer hierarchy, calmer spacing` | CSS 布局 | 剥离 |
| 1014 | `Bool switches: multi-column, left-aligned; share zone with dropdowns via display:contents` | CSS 布局 | 剥离 |
| 1033 | `Switch / fig / module zones: equal-width fr columns across the row` | CSS 布局 | 剥离 |
| 1053 | `Selected chips ABOVE add-from-data dropdown (column flex; chip-row first)` | CSS 布局 | 剥离 |
| 1068 | `No hot red asterisks / red bold (keep inert)` | CSS 布局 | 剥离 |
| 1072 | `Cards / controls: calmer hover, visible focus already above` | CSS 布局 | 剥离 |
| 1087 | `Soften heavy inset accent bars slightly (still one accent, no glow stack)` | CSS 布局 | 剥离 |
| 1094 | `===== 一行多列：等宽均分各列（1fr），不用 max-content 挤压 =====` | CSS 布局 | 剥离 |
| 1095 | `说明只悬停（data-tip / title），不常驻标题栏正文` | CSS 布局 | 剥离 |
| 1130 | `roc/advanced：见文末权威块` | CSS 布局（防回归） | 保留 |
| 1175 | `Body hints live in title / sub-title bars now` | CSS 布局 | 剥离 |
| 1191 | `===== A 权威块：ROC / 高级分析（后写唯一生效；勿在上方再叠同选择器） =====` | CSS 布局（防回归） | 保留 |
| 1197 | `正文常驻说明已改为 h3 data-tip 悬停` | CSS 布局 | 剥离 |
| 1240 | `E：checklist / pathlist 占满块宽，不用 8 列碎格` | CSS 布局 | 剥离 |
| 1253 | `B：中栏常 <640px；仅真正很窄时才叠成一列` | CSS 布局 | 剥离 |
| 1266 | `文本编码默认收起，占工作表整行。` | 统计配置 | 剥离 |
| 1275 | `标记侧和最少列数跟在重新识别旁边。` | 统计配置 | 剥离 |
| 1286 | `隐藏页必须盖住 main/display:grid，否则配置下拉会露到数据预览上。` | CSS 布局（防回归） | 保留 |
| 1301 | `组学预处理 / LASSO / Logistic / 图与显示：一行四列，卡片内单列堆叠。` | 统计配置 | 剥离 |
| 1319 | `列线图 / DCA / NRI 不用 display:contents，Windows 上 select 才能点开。` | CSS 布局（兼容 workaround） | 保留 |
| 1353 | `===== 临床两大模块（预处理 / 筛选）：一行多列（后写生效） =====` | 统计配置 | 剥离 |
| 1364 | `一行多列时不整行铺开（整行会让 grid-column:1/-1 撑满整行）` | CSS 布局（防回归） | 保留 |
| 1397 | `组学模块：预处理并入后整卡单列堆叠，预处理字段自己一行多列（2026-11-05）` | 统计配置 | 保留 |

## 4 console_ui.js（39 条）

| 行 | 原文 | 归类 | 建议 |
|---|---|---|---|
| 1 | `console_ui.js — DOM state, render, binders (loads after schema + api)` | 其它（文件职责） | 保留 |
| 74 | `右侧运行台属于统计配置页：进配置就显示，进预览/流程就收起（不再依赖 loadAll 是否成功）。` | 统计配置 | 剥离 |
| 272 | `外验的角色（clin_sheet / rad_sheet / qc_sheet / icc_sheet）只认用户在各卡里选的，` | 数据预览 | 保留 |
| 273 | `不填默认值：空 = 该表不参与，空 rad_id_col = 用全局组学 ID 列。` | 数据预览 | 保留 |
| 587 | `多结局公共页：不要把残留 PAIRWISE_ALL 收成两两比较。` | 其它（数据契约） | 保留 |
| 674 | `与 data_io._column_matches_prefix 同规则。suffix: 才认结尾。` | 其它（正则，排除项） | 保留 |
| 1340 | `结局页没有手填展示名时用分组卡。不要改成结局列名 / non_结局列名。` | 其它（文案口径） | 保留 |
| 1345 | `Shared continuous vars: UI edits CORR_* and mirrors PARTIAL_* (settings.ini keeps both).` | 其它（数据契约，排除项） | 保留 |
| 1356 | `write twin without re-entering mirror` | 其它（实现，排除项） | 保留 |
| 1825 | `数据预览页的字段：这些键只在 renderDataPane 里渲染，不进配置页。` | 数据预览 | 剥离 |
| 1868 | `分数名默认取工作表名（临床行另用 Clinical）；空=不设时仍按此显示。` | 数据预览 | 剥离 |
| 2053 | `首文件的值继续镜像到 EXCLUDE_SHEET/COL/ROW（旧口径与预览仍读这三个键）。` | 数据预览 | 剥离 |
| 2302 | `额外排除 ID：逗号 / 分号 / 空格分隔，写回 EXCLUDE_IDS。` | 数据预览 | 剥离 |
| 2443 | `服务端原生对话框：默认打开子工程根目录，返回相对路径（rel）。` | 数据预览 | 剥离 |
| 2557 | `旧配置只有单张组学表：有块就按第 1 块显示，存回时落成 sheets 映射。` | 数据预览 | 剥离 |
| 2662 | `只有「文件 + 临床 + 组学」齐全的才进 EXTERNAL_TESTS（后端按这两张表对齐）；不齐的留在卡上等补。` | 数据预览 | 剥离 |
| 2873 | `重绘前把各折叠块的开/关状态记到 S.kvFoldOpen，重绘后按它回填（用户开过的不会被"弹回"或"弹开"）。` | 统计配置（UX 行为） | 保留 |
| 2916 | `样本排除名单只在「数据预览」页（#pvExclude）设置，统计配置页不再显示。` | 数据预览 | 剥离 |
| 2951 | `组学 / 预处理 并进 组学模块；相关热图 并进 相关 / 偏相关分析（ini 分节不变）。` | 统计配置 | 剥离 |
| 3009 | `「有外验仍做内拆」放标题行；其余（种子 / 训练比例 / 分组列）留在 body。` | 统计配置 | 剥离 |
| 3250 | `热图列与组别、预测因子与参考变量、偏相关的控制变量与方法都在同一张卡里。` | 统计配置 | 剥离 |
| 3346 | `C：与 ROC 同构 — 布尔一行 + 分块；pathlist / checklist 全宽（E）` | 统计配置 | 剥离 |
| 3697-3703 | `Known image types stay one token: original_firstorder_Mean → original_.` 等 7 行影像特征名前缀切分示例 | 其它（算法，排除项） | 保留 |
| 3711 | `foo__… → foo_` | 其它（算法，排除项） | 保留 |
| 3725 | `TitleCase anatomy (Frontal). All-caps parameters (FA, P2) stay in the prefix.` | 其它（算法，排除项） | 保留 |
| 3759 | `识别阈值只在界面。已保存标记仍按原记号匹配，不受最少列数限制。` | 统计配置 | 剥离 |
| 3760 | `未识别的标记侧不改成「二者」，两侧都不自动列出。` | 统计配置 | 剥离 |
| 3882-3883 | `Checking a box used to rebuild with excludes first, so that row jumped / up under the ep2d block. Keep the order already on screen.` | 其它（事件时序，排除项） | 保留 |
| 5075 | `空的 base 不会把勾选值转成数字。时间点必须保持数字列表。` | 其它（数据契约） | 保留 |
| 5128-5130 | `Replace the form after this click finishes. A sync rebuild detaches / the label while it is still activating, and the row that slides / under the pointer takes the check.` | 其它（事件时序，排除项） | 保留 |

> 表中 3 行是区间（`3697-3703` = 7 条、`3882-3883` = 2 条、`5128-5130` = 3 条），故本节 30 行 = **39 条**。

## 5 console_schema.js（3 条）

| 行 | 原文 | 归类 | 建议 |
|---|---|---|---|
| 1 | `console_schema.js — panel / key / palette data (no DOM)` | 其它（文件职责） | 保留 |
| 20 | `多结局时公共页不画这些，只在各结局页改（空=用 ini 默认）。` | 统计配置 | 剥离 |
| 227 | `这三个 bundle 在「数据预览」页渲染，不进配置页的 io 段。` | 数据预览 | 剥离 |

## 6 strobe/index.html（0 条）

3123 行，无 `<!--`、无 `//`、无 `/*`。页壳 / 预览意图在 `id` + 文案里：
`:826 Patients screened (n = -)`、`:2224「手稿未注明的人数在图上留空为 n = -（不猜数）」`。
→ 无可剥离注释；`n = -` 那两处是**活的行为文案**，属 B 节流程图契约的界面端证据。

## 7 汇总

| 文件 | 注释条数 | 建议剥离 | 建议保留 |
|---|---|---|---|
| `project.html` | 6 | 2 | 4 |
| `console.html` | 0 | 0 | 0 |
| `console.css` | 33 | 23 | 10 |
| `console_ui.js` | 39 | 15 | 24 |
| `console_schema.js` | 3 | 2 | 1 |
| `strobe/index.html` | 0 | 0 | 0 |
| **合计** | **81** | **42** | **39** |

## 8 Round2 验收清单（SSOT = `c2b38fa` 提交说明，逐字，勿增补）

### 8.1 起始页 `project.html`
1. **开启服务 / 关闭服务 拆成两键**（放在端口探测下方）
2. **数据位置折进「高级」**
3. **删除隐藏 `extSplit` 与 `ext_split` 传参**

### 8.2 数据预览
4. **三列宽度 / 断点与统计配置一致**
5. **ID 列 / 非特征列并入文件卡**
6. **外验：去黑框、只选不填**（`/api/pick-file`，`dir=` 子工程根）

### 8.3 角色表
7. **临床分数名默认 `Clinical`；组学默认工作表名**
8. **QC / ICC 增加 ID 列与分数名**

### 8.4 统计配置
9. **「保存 ini」文案 → 「保存配置」；删右栏保存**
10. **内拆开关上移到可重复性标题栏**
11. **组学预处理并入组学模块；相关热图并入相关 / 偏相关**
12. **组学块去掉模型分数名与常驻说明**

### 8.5 流程图
13. **缺数留空（`n = -`）仍出图；audit 只校验已给出的数字**
14. **新增键**（schema / defaults / ini）：`CLIN_SCORE` / `QC_ID_COL` / `QC_SCORE` / `ICC_ID_COL` / `ICC_SCORE` / `CLIN_SCORE_LINEAR`

> 注：c2b38fa 正文把「临床筛选标题栏：线性拟合开关，默认开（OLS → Clinical 分数）」写在统计配置段末尾，
> 本清单把它独立为第 15 项（下表编号 15），属于**同一条提交说明的拆分**，不是新增契约。

## 9 注释 ↔ 契约 map

判定口径：`✓` = 注释句确实钉住了该契约；`契约有·注释无` = 提交说明有、代码里没有对应注释
（多落在界面文案 / `data-tip` / schema 说明串 / docstring 上）；`注释有·契约无` = 注释在说契约之外的事。

| # | 契约 | 命中的注释 | 代码锚点（`6422b4f`） | 判定 |
|---|---|---|---|---|
| 1 | 开/关拆两键（端口探测下方） | — | `project.html:253 #portRow` → `:263 #btnSvcOn` → `:264 #btnSvcOff` | **契约有·注释无**（仅 `:935` 提「开服务后自动进配置」） |
| 2 | 数据位置折进「高级」 | — | `project.html:225 <details class="adv-row">`、`:229 #projDir` | **契约有·注释无** |
| 3 | 删 `extSplit` / `ext_split` | — | 全 `modules/` grep 两串 = 0 命中 | **契约有·注释无** |
| 4 | 预览三列宽度/断点 = 统计配置 | `console.css:46` | `.preview-page`(`:47/113/116/120`) ≡ `main`(`:123/126-127`) | ✓ |
| 5 | ID 列 / 非特征列并入文件卡 | `console_schema.js:227`（弱）、`console_ui.js:1825` | `console.html:126 [data-file-card=main]` 内含 `:133 #pvIds` | ✓（弱） |
| 6 | 外验去黑框、只选不填 | `console_ui.js:2443`、`console.css:85` | `api("/api/pick-file")`；`console.html:129 readonly` + `:130 选择` | ✓ |
| 7 | 临床分数名默认 Clinical；组学默认工作表名 | `console_ui.js:1868` | `console_ui.js:1894` 表头含「分数名」、`:1905 placeholder="Clinical"` | ✓ |
| 8 | QC / ICC 增 ID 列与分数名 | — | `console_ui.js` 中 `QC_ID_COL/QC_SCORE/ICC_ID_COL/ICC_SCORE` 读写；`schema.py:162-165` | **契约有·注释无** |
| 9 | 「保存配置」；删右栏保存 | — | `console.html:30 >保存配置</button>`（在 `:27 tabBtnFlow` 之后） | **契约有·注释无**（文案在 `data-tip` 里） |
| 10 | 内拆开关上移可重复性标题栏 | `console_ui.js:3009` | `console_ui.js:3010 titleKeys = ["USE_INTERNAL_SPLIT_WHEN_EXTERNAL"]` | ✓ |
| 11 | 组学预处理并入组学模块 | `console_ui.js:2951`、`console.css:1397` | `console_ui.js:2960 mergeGroup("radprep", "radblocks")` | ✓ |
| 12 | 相关热图并入相关 / 偏相关 | `console_ui.js:2951`、`:3250` | `console_ui.js:2961 mergeGroup("corr", "pcorr", "相关 / 偏相关")`、`:3246 titleKeys` 含 `DO_HEATMAP` | ✓ |
| 13 | 组学块去模型分数名与常驻说明 | `console.css:1175/1197`（只覆盖「常驻说明→data-tip」） | `console_ui.js:4214 <span class="blk-score mono" title="分数名在「数据预览」页…">`，`data-blk-score` 计数 0 | ✓（半）+ **契约有·注释无**（去分数名那半） |
| 14 | 流程图 `n = -` 仍出图；audit 只校验已给数字 | （非注释：docstring + 文案） | `figure_strobe_flow.py:298 audit_study`、`strobe/index.html:826/2224` | ✓（docstring / 文案） |
| 15 | 临床筛选标题栏线性拟合，默认开（OLS → Clinical） | — | `console_ui.js:3060 titleKeys` 含 `CLIN_SCORE_LINEAR`；`defaults.py:217 True`；`schema.py:52/238/438` | **契约有·注释无** |
| 16 | 六枚新键进 schema / defaults / ini | — | `schema.py:18/161-165`；`defaults.py:29-33/217`；`ini_io.py:49/79`；`console_schema.js:122-126` | **契约有·注释无** |

**反向（注释有·契约无）**：`console.css:170/359/474/714/1130/1191/1286/1319/1364`（浏览器 select 兼容、
隐藏页覆盖、权威块防叠加）、`console_ui.js:2873`（折叠态保持）、`console_ui.js:272-273`（外验角色不填默认）
——都是「为什么这么写」的实现守卫，**不进契约**，按 §1 的保留附录留在原位。

**统计**：✓ 6 条（4/6/7/10/11/12）；✓（弱/半）2 条（5/13）；✓（docstring/文案）1 条（14）；
**契约有·注释无 7 条**（1/2/3/8/9/15/16）；**注释有·契约无** 0 条（上述守卫不计契约）。

## 10 后续（本文件**不做**的事）

1. **剥注释另开 PR**：范围 = 上表 42 条「建议剥离」；剔除 39 条保留附录；token 哨兵（`console.html:6`
   meta 与 JS 哨兵）与安全 / CORS 文本**一律不动**——它们不是注释，剥离动作不涉及。
2. **本 PR 只落清单 + QC 测**（`modules/tests/test_console_ui_round2.py`），不改金版 HTML/JS/CSS。

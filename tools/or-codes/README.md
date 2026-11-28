# tools/or-codes — harvested unique code and docs from OR-CODES (t2312u)

This is the **public MY-CODES copy** of unique lab-shared code and documentation.
Intake inventory, inclusions, and exclusions: `INTAKE-t2312u.md`.
T1 VBM already lives at `tools/preprocess-imaging/t1vbm/` (do not use the older OR snapshot).
Lab-absolute paths in this copy were rewritten for the public-path policy (C1): `{or_codes}` / `{lab_root}` placeholders, and module-level drive defaults via `os.environ.get`.

The original README text follows (paths sanitized).

---
# `_Codes`：本机共享代码树（lab 私有，不进 git）

这里放 **多个课题共用的可运行代码**。原来每个课题 `habitat_pipeline/` 里各存一份完全相同的
8 个 py（实测 8 个工程字节一致，只有 `config.py` 的 3 行路径不同），现在只留这里一份。

## 目录

| 路径 | 作用 |
|---|---|
| `habitat_core\__init__.py` | 包入口 |
| `habitat_core/ini_config.py` | 定位并读写工程 `settings.ini`（值用 JSON，与 MY-CODES `ini_io` 同口径） |
| `habitat_core/ini_sections.py` | 段/键元数据 + 配置页中文标签 |
| `habitat_core/config.py` | 读 ini → 模块级常量（`DATA_TABLE_PATH` / `DIRS` / `PREPROC` / `HABITAT` …） |
| `habitat_core/data_loader.py` | 数据表解析（长表：每行一个患者的一个序列） |
| `habitat_core/preprocessing.py` | 重采样 / N4 / 归一化（按模态） |
| `habitat_core/registration.py` | 序列间配准（ANTsPy SyN） |
| `habitat_core/habitat.py` | 生境聚类（K-means / 共识聚类 / Superpixel） |
| `habitat_core/radiomics.py` | 影像组学特征提取 + 特征筛选 |
| `habitat_core/pipeline.py` | 主流程编排（断点、日志、QC） |
| `habitat_core/test.py` | 冒烟测试（合成数据，不读影像盘） |
| `habitat_core/tools/log_reader.py` | 日志合并解析 → 患者级 QC 表 |
| `habitat_core/tool_ini.py` | 加工脚本读工程 ini 的统一入口（`--project` / `--ini`） |
| `habitat_core/web/server.py` | 控制台服务（只绑 `127.0.0.1`；token + 动作白名单） |
| `habitat_core/web/config.html` | 控制台前端（配置 / 运行 / 日志 三页） |
| `tools\` | 工程加工脚本（按功能归组）；同源脚本已综合为共享实现，变体由 ini/CLI 控制（见 `tools\README.md`） |
| `entry\run_pipeline.py`、`entry\run_smoke_test.py` | 工程无关的共享入口（`--ini` 指工程配置）；工程根只放 `.bat` 壳 |
| `entry\config_server.py` | **唯一的控制台启动器**（含 `--register-protocol` 浏览器协议登记） |
| `matlab\t1vbm\` | T1 VBM 的 MATLAB 套件（16 个 `.m`，配置在 `t1vbm_config.m` 的 `C`） |
| `tools\vlm\skinvl\`、`tools\vlm\medgemma\` | 大模型课题胶水脚本（原 `E:\mlh\*\0script`）：`skinvl` 9 个、`medgemma` 14 个；路径常量指向 `E:\_models`、`G:\SCC\mlh_SIX\data`、`{lab_root}\pre-submit\mlh_SIX` |
| `external\` | **第三方代码依赖（外来件，非自研）**，与 `tools\` 分层；现有 `MM-Skin\`（来源 `github.com/ZwQ803/MM-Skin`，无 `.git`）与 `TimesFM\timesfm-master.zip`（上游 master 归档，Apache-2.0，v2.0.1，**未解压**）；不改内部，升级先 diff；见 `external\README.md` |

## 盘面分工（迁移后）

| 内容 | 位置 |
|---|---|
| 代码（本树） | `{or_codes}` |
| 数据表 `combined.csv`、`ref\`、结果 `results\04–06`、`QC\` | `{lab_root}\<project>\` |
| 处理后中间数据 `results\01–03`、`checkpoints`、模型与运行日志 | 处理后盘 `G:\<project>\results\`；另一处理后盘 `E:\<project>\results\`（等价，由 `settings.ini` 的 `OUTPUT_ROOT` 决定） |
| 大模型权重（safetensors 等大件资产） | 模型盘 `E:\_models\`（如 `SkinVL-MM` / `medgemma-1.5-4b-it`）；不进 git、不留第二份副本 |
| 只读输入图集（如 mlh_SIX 的 4,039 张 jpg） | 处理盘 `G:\SCC\mlh_SIX\data\` |
| 配置与入口壳 | `G:\<project>\settings.ini`、`run_*.bat`、`settings.html` |

`settings.ini` 用两个根表达这件事：`OUTPUT_ROOT`（G:，01–03）与 `ARCHIVE_ROOT`（D:，04–06 与 QC）；
`habitat_core\config.py` 里 `DIRS` 已按此拆分，新跑一次不会再把 04–06 写回 G:。备份目录由
`BACKUP_DIR` 指定（默认 `<ARCHIVE_ROOT>\ref\tmp`），控制台改 ini 时原件存那里。

## 怎么用（工程侧）

工程根只留配置与壳：`settings.ini`、`run_pipeline.bat`、`run_smoke_test.bat`、`settings.html`。
**不再有 `.py`，也不再有 `open_settings.bat`。**

```powershell
cd G:\CC\fyh_CAC
run_smoke_test.bat                # 冒烟测试，12 项（＝ _Codes\entry\run_smoke_test.py --ini %~dp0settings.ini）
run_pipeline.bat                  # 跑流水线（数据取 ini）
```

控制台：双击工程根的 `settings.html`。页面会先探测 `127.0.0.1:8791–8799`；没有服务时点
「启动控制台」→ 走已登记的 `habitat-config:<project>` 协议，由本树 `entry\config_server.py`
拉起服务并自动打开页面。控制台可改配置、跑流水线 / 冒烟测试 / 本工程加工脚本，并实时看日志。

协议登记（一次性，当前用户，可撤销）：

```powershell
python "{or_codes}\entry\config_server.py" --register-protocol
python "{or_codes}\entry\config_server.py" --unregister-protocol
```

安全约束：只监听 `127.0.0.1`；写配置与执行动作都要 `X-Token`（每次启动随机生成，仅存
`%LOCALAPPDATA%\habitat_core\server.json`）；动作是白名单（流水线 / 冒烟 / 本工程 tools 脚本）；
子进程用 argv 列表、`shell=False`；协议命令是固定模板，只接受 `[A-Za-z0-9_]` 的工程名。

## 动作策略与底部输出面板（每工程两个文件）

| 文件 | 作用 |
|---|---|
| `G:\<project>\actions.ini` | 每个动作的 `ENABLED` / `DRY_RUN` / `ARGS`；`match`、`roi` 另有 `OUTPUT_DIR`，`roi` 另有 `LIMIT`；`[confirm]` 控制二次确认与确认词 |
| `G:\<project>\console.ini` | 底部输出面板：`HEIGHT_PX` / `FONT_SIZE` / `POLL_MS` / `MAX_LINES` / `MAX_CHARS` / `AUTOSCROLL` / `SHOW_TIMESTAMP` / `KEEP_LOG_FILES` |

两者在首次打开控制台时按默认值生成，之后只读不改。

**二次确认（真两步）**：点「运行」→ 确认卡显示风险级别、dry-run 状态、**将执行的完整命令行**、写入目录 →
必须原样输入工程名（`[confirm] PHRASE`）才能执行。服务端同样校验：`POST /api/run` 缺 `confirm` 或输错一律 400。

**dry-run**：`cleanup`（不带 `--execute`）、`match`（`--dry-run`）、`roi`（`--dry-run`）默认只算不写；
`pipeline` 没有 dry-run 能力，若把 `[pipeline] DRY_RUN` 设为 true 会被直接拒绝（防误以为在干跑）。

**终止任务**：面板「终止任务」→ `POST /api/run/stop`，先 `terminate`，3 秒后 `kill`；不再出现「以为停了其实还在跑」。

**日志**：面板显示尾部 8 KB；「下载完整日志」走 `/api/run/log?full=1`；每次运行写
`<ARCHIVE_ROOT>\ref\tmp\run-<时间>.log`。


1. **页面有没有检测到服务**：`settings.html` 会探测 `127.0.0.1:8791–8799`。命中就自动跳转
   `http://127.0.0.1:<端口>/?project=<工程>`（`?project=` 让同一服务切到该工程）。
2. **点「启动控制台」后浏览器有没有弹窗**：Chrome/Edge 会问「打开 Habitat Config Protocol?」。
   若曾误点「阻止」，点地址栏左侧的拦截图标允许一次。
3. **协议是否登记**：`reg query HKCU\Software\Classes\habitat-config /s` 应看到
   `... pythonw.exe ... config_server.py --launch-protocol "%1"`；没有就跑 `--register-protocol`。
4. **启动器报错看日志**：`%LOCALAPPDATA%\habitat_core\launcher.log`（`pythonw` 无控制台，异常都写这里）。
5. **已在跑但页面连不上**：直接用 `http://127.0.0.1:8791/`；要换工程加 `?project=<工程名>`。
6. **file:// 页面的跨源限制**：服务端对 `Origin: null` 回 `Access-Control-Allow-Origin: null`；
   若浏览器仍拦（Private Network 策略），页面会显示「直接打开」链接，点它即可。
7. **页脚一直显示「检测中」**：说明页面 JS 没跑起来（旧版页面缺 `</script>`）。`Ctrl+F5` 强刷即可；
   现在的页面出错会把状态改成「脚本错误」并在横幅显示原因，6 秒无结果会变「无响应」。
8. **服务卡住不回包**：早期版本用单线程 `HTTPServer`，浏览器一条预连接就能堵死；现已改
   `ThreadingHTTPServer`。若仍卡，结束占用 8791 的 pythonw 再启动。



不想用工程里的入口时，也可直接：

```powershell
$env:PYTHONPATH = '{or_codes}'
$env:HABITAT_INI = 'G:\CC\fyh_CAC\settings.ini'
python -m habitat_core.pipeline --data G:\CC\fyh_CAC\habitat_pipeline\combined.csv
python -m habitat_core.tools.log_reader
```

## 配置优先级

`HABITAT_INI` 环境变量 → 从当前目录向上找 `settings.ini` / `ref/settings.ini` → 都没有就**报错**（不猜路径）。

## 工具变体约定（re-rule）

同一功能的多个课题脚本（影像组学提取、checkpoints 清理、文件复制/解压等）**先综合为一份**，
差异（路径、关键词、ROI 规则等）通过工程 `settings.ini` 的 `[tools]` 段或 CLI 参数控制，
**不为每个工程复制新脚本**。

- 控制台动作：`server.py` 按 `tools/<组>/<工程>/*.py` 扫描；需保留工程级按钮时，工程夹放
  约 10 行薄壳，实现放 `<组>/_shared/`（如 `cleanup`、`match`）。
- 非控制台工具（如 `radiomics/extract_features.py`）：直接 CLI 传参，见各夹 README。
- 新增变体：优先加参数 / ini 键；只有出现全新形态时才扩展规则函数。

## 新增一个同类工程

1. 建工程夹 `<prefix>_<SUFFIX>`，放 `habitat_pipeline\combined.csv`；
2. 复制任一工程的 `settings.ini`，改 `PROJECT` / `DATA_TABLE_PATH` / `OUTPUT_ROOT` / `QC_DIR`；
3. 复制 `run_pipeline.bat` / `run_smoke_test.bat` / `settings.html`（壳指向 `_Codes\entry\`；工程根不放 `.py`，也没有 `open_settings.bat`）；
4. 跑 `run_smoke_test.bat`，12 项全 PASS 即接入完成。

**不要**在工程里再放一份 `habitat_pipeline\*.py`：改动只改 `_Codes` 这一份。

## 边界

- 原始影像（`L:`，本机卷 `raw`）**只读**：不复制、不改名、不写入。
- 本树**不含** PHI / 患者数据 / 名单；工程侧数据表与结果留在工程目录。
- 本树不进 git、不参与 `syn.bat` 之外的镜像；改进后如需长期固化，按 `skills.md` §5 提案进 MY-CODES。

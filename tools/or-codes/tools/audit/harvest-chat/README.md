# harvest-chat —— 会话级 census 与水位线

配套规则：`OR-RULES\00_local-override.md` §7（chat 源 / 水位线）；判据来源 `02_or-skill.md` §1。

## 作用

1. 扫 `C:\Users\Administrator\.cline\data\sessions\`，出会话级 census（八类判级：`H1-harvested` / `H1-already-promoted` / `H2-no-harvest` / `H3-live-dep` / `H4-project` / `redline-phi` / `harvest-op` / `new`）；
2. 整表**覆写** `{lab_root}\_Docs\harvest\CHAT-LEDGER.tsv`（水位线唯一载体，不追加）；
3. `--since <session_id>` 只列水位线之后启动的会话。

## 用法

```powershell
$env:PYTHONIOENCODING='utf-8'
python -B {or_codes}\tools\audit\harvest-chat\chat_census.py --mark <session_id>
python -B {or_codes}\tools\audit\harvest-chat\chat_census.py --since <session_id>
```

- `--mark`：把该会话标 `watermark=Y`；不给则取最新非 `harvest-op` 会话。
- 输出：`<轮次>\evidence\CHAT-CENSUS.tsv`（全字段）+ `_Docs\harvest\CHAT-LEDGER.tsv`（台账 11 列）。

## 配置（脚本顶部）

| 常量 | 含义 |
|---|---|
| `ROOT` | 会话根 |
| `ROUND` | 本轮轮次名（决定 census 落点） |
| `OUT_CENSUS` / `OUT_LEDGER` | 证据层与水位线台账 |
| `CLASS_OVERRIDE` | 人工判级表（session_id → class / lanes / note）；改判级只改这里 |
| `NEW_AFTER` | 本轮开始时刻；此后的会话自动标 `new`，交下次 |
| `GROUP_RULES` | 按时间的分组判级（早期 H2 / a–e 轮覆盖 H1-already-promoted） |
| `RED_PAT` | 脱敏：私网 IP / 手机 / 身份证 / 长数字 |

## 坑

- 判级以人工表为准；`GROUP_RULES` 只兜底，别把"已 promote"写成"未抽"。
- 脱敏必须在写盘前做：会话里会出现禁用网段字面与手机号，台账外发风险最高。
- `NEW_AFTER` 每轮要跟着轮次更新，否则新会话会被算成本轮。

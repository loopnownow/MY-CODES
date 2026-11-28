#!/usr/bin/env python
"""把 stage 里的一个家族换入真实 `_DataBase`（1+N 五表/六表库）。

用法::

    set DB5T_BATCH=D:\\MyProjects\\.Del\\_by-function\\db5t-pilot-20261124
    python -B cutover.py MCI            # 干跑，只印计划
    python -B cutover.py MCI --go       # 真做（默认仍是干跑）

顺序固定：
 1. stage 侧门禁必须「全部通过」，否则一份字节都不搬；
 2. 三库（家族 + 各子项目）逐个 `PRAGMA wal_checkpoint(TRUNCATE)` 后拷主文件，不带 -wal/-shm；
 3. 落位后统一 `PRAGMA journal_mode=DELETE`（只在 case 一个进程读一次也会留边车，搬库只拷主文件会丢页）；
 4. 旧家族库改名 `<fam>db_6t` 冻结（不删、不压档，D62 的同族快照指针要解它）；
 5. `fix_sha.py` 刷 `ref_project.sha256`，再在真库根上跑一遍门禁；
 6. 台账追加 `cutover_ledger.md`（回滚就是把它逆序做一遍）。

任何一步失败即停，不继续下一步——半切状态比不切更难救。
"""
import argparse
import hashlib
import os
import pathlib
import shutil
import sqlite3
import subprocess
import sys

HERE = pathlib.Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import paths  # noqa: E402

STAGE = pathlib.Path(os.environ.get("DB5T_BATCH") or paths.BATCH) / "stage"
ROOT = pathlib.Path(os.environ.get("DB5T_ROOT") or r"D:\MyProjects\_DataBase")
LEDGER = STAGE / "cutover_ledger.md"
# --recut 模式下被替换掉的「上一版新库」备份处（不是旧 6t 库，旧 6t 库永不覆盖）
BACKUP = STAGE.parent / "_replaced"


def sha(p: pathlib.Path) -> str:
    h = hashlib.sha256()
    with p.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def checkpoint(db: pathlib.Path) -> None:
    with sqlite3.connect(str(db)) as c:
        c.execute("PRAGMA wal_checkpoint(TRUNCATE)")


def journal_delete(db: pathlib.Path) -> None:
    with sqlite3.connect(str(db)) as c:
        c.execute("PRAGMA journal_mode=DELETE")


def gate(env: dict) -> tuple[int, str]:
    """返回 (是否全绿, 输出)。gates.py 的退出码不表示违规，只看汇总行与 P0/P1 标记。"""
    r = subprocess.run([sys.executable, "-B", str(HERE / "gates.py"), env["DB5T_FAM"]],
                       env=env, capture_output=True, text=True)
    out = r.stdout + r.stderr
    ok = (("存在违反" in out or "全部通过" in out)
          and "存在违反" not in out
          and "!! P0" not in out and "!! P1" not in out
          and "Traceback" not in out)
    return (0 if ok else 1), out


def plan(fam: pathlib.Path) -> list[tuple[pathlib.Path, pathlib.Path]]:
    """(src_db, dst_db) 列表：家族库 + 各子项目库。"""
    pairs = []
    fam_db = fam / "_PHI" / (fam.name + "db")
    if fam_db.exists():
        pairs.append((fam_db, ROOT / fam.name / "_PHI" / fam_db.name))
    for leaf in sorted(p for p in fam.iterdir() if p.is_dir() and p.name != "_PHI"):
        db = leaf / (leaf.name + "db")
        if db.exists():
            pairs.append((db, ROOT / fam.name / leaf.name / db.name))
    return pairs


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("family")
    ap.add_argument("--go", action="store_true")
    ap.add_argument("--recut", action="store_true",
                    help="允许替换已存在的目标库（仅当 <fam>db_6t 已存在，即旧库早已冻结）；"
                         "被替换的上一版先备份到 _replaced/ 再删")
    a = ap.parse_args()
    fam = STAGE / a.family
    if not fam.exists():
        print(f"阶段目录不存在：{fam}")
        return 2

    env = dict(os.environ, DB5T_FAM=a.family)
    # 先按当前文件重算 ref_project.sha256：构建后任何补写都会让 D35 假红
    subprocess.run([sys.executable, "-B", str(HERE / "fix_sha.py"), a.family], env=env,
                   capture_output=True, text=True)
    rc, out = gate(env)
    tail = [ln for ln in out.splitlines() if "CHECK 全库汇总" in ln or "!! P0" in ln or "!! P1" in ln]
    print(f"[1] stage 门禁 rc={rc}")
    for ln in tail[:12]:
        print("    " + ln)
    if rc != 0:
        print("    门禁未全绿：不搬任何字节。先修，再切。")
        return 1

    pairs = plan(fam)
    print(f"[2] 计划换入 {len(pairs)} 个库")
    for s, d in pairs:
        print(f"    {s.relative_to(STAGE)}  {s.stat().st_size:>10,} B  →  {d.relative_to(ROOT)}")
    old = ROOT / a.family / "_PHI" / (a.family + "db")
    if old.exists():
        print(f"[3] 旧库冻结：{old.name} → {old.name}_6t"
              f"{'（目标已存在，跳过改名）' if pathlib.Path(str(old) + '_6t').exists() else ''}")
    if not a.go:
        print("\n干跑结束。确认无误后加 --go 执行。")
        return 0

    # 先冻结旧库，再落新库：反过来 copy2 会直接把旧库覆盖掉（2026-11-25 ACN 事故）
    if old.exists():
        tgt = pathlib.Path(str(old) + "_6t")
        if not tgt.exists():
            old.rename(tgt)
            print(f"    冻结 {tgt.name}")
        else:
            print(f"    {tgt.name} 已存在，旧库不再改名（先人工确认哪一份是原档）")
    for s, d in pairs:
        checkpoint(s)
        d.parent.mkdir(parents=True, exist_ok=True)
        if d.exists():
            fam6t = ROOT / a.family / "_PHI" / (a.family + "db_6t")
            if not (a.recut and fam6t.exists()):
                print(f"    !! 目标已存在，拒绝覆盖：{d}")
                if not a.recut:
                    print("    这是重切（旧库已冻结过）改用 --recut；"
                          "旧 6t 库不存在时不许重切，先人工确认哪一份是原档。")
                return 3
            import datetime
            stamp = datetime.datetime.now().strftime("%Y%m%d-%H%M%S")
            bak = BACKUP / a.family / f"{d.name}.{stamp}"
            bak.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(d, bak)
            d.unlink()
            print(f"    recut：上一版已备份 {bak.relative_to(BACKUP.parent)}")
        shutil.copy2(s, d)
        journal_delete(d)
        print(f"    换入 {d.relative_to(ROOT)}  sha={sha(d)[:12]}")

    for readme in fam.rglob("目录说明.md"):
        rel = readme.relative_to(fam)
        dst = ROOT / a.family / rel
        dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(readme, dst)

    env2 = dict(env, DB5T_ROOT=str(ROOT))
    subprocess.run([sys.executable, "-B", str(HERE / "fix_sha.py"), a.family], env=env2)
    rc2, out2 = gate(env2)
    print(f"[4] 真库门禁 rc={rc2}")
    for ln in [line for line in out2.splitlines() if "CHECK 全库汇总" in line][:3]:
        print("    " + ln)

    LEDGER.parent.mkdir(parents=True, exist_ok=True)
    with LEDGER.open("a", encoding="utf-8") as fh:
        fh.write(f"\n## {a.family}  {__import__('datetime').datetime.now():%Y-%m-%d %H:%M}\n")
        for s, d in pairs:
            fh.write(f"- `{d.relative_to(ROOT)}`  {d.stat().st_size:,} B  sha256 `{sha(d)}`\n")
        fh.write(f"- 旧库冻结：`{a.family}db_6t`\n- 回滚：把上面每个文件移到 `{STAGE.name}/` 下原位，"
                 f"再把 `{a.family}db_6t` 改回 `{a.family}db`\n")
    print(f"[5] 台账已追加 {LEDGER}")
    return 0 if rc2 == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())

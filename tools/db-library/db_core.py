# -*- coding: utf-8 -*-
"""db-library 公共核心：DDL / 去重入库 / 解档 / _share 归档 / 台账 / 移动校验。

口径（2026-11-16family 起）：
- 库：<db-root>/<family>/_db/<family>db；_db 内只放库 + 目录说明.md
- 归档：只在 <family>/_share/（层级 ≤2，ethics/thesis 打平为 <slug>__<name>）
- 档内表格：解出入库，doc.rel_path = <FAM>/_share[/<类>]/<档名>/<成员>
- 叶内：只留 目录说明.md（未入库 PHI/模板例外，见 LEAF_EXCEPTIONS）
- 原件：入 {DEL}/_by-function/<batch>/originals/<code>/<family>/<leaf>/
- 台账：kind=table|archive|archive-member，sha256 全记

本模块只提供函数与常量，不写盘（除 open/create 由调用方显式触发）。
"""
from __future__ import annotations

import hashlib
import re
import shutil
import sqlite3
import subprocess
import zipfile
import zlib
from datetime import datetime
from pathlib import Path

from common import ledger_write, norm, read_any, sha256

DEFAULT_DB_ROOT = Path(r"D:\MyProjects\_DataBase")
DEL = Path(r"D:\MyProjects\.Del")
DEL_ROOT = DEL / "harvested"      # 台账/历史：批次搬进终弃档后 verify_db 仍能找到
WORK_ROOT = DEL / "_by-function"  # 新批次写入位（不写终弃档）


def batch_dir(name: str, root: Path | None = None) -> Path:
    """返回批次目录（缺省落 WORK_ROOT，新批次不写终弃档）。

    Args:
        name: 批次名，如 ``2026-11-db-share``。
        root: 可选根目录覆盖，用于回放历史批次。

    Returns:
        批次目录路径，不保证存在。
    """
    return Path(root or WORK_ROOT) / name


def find_batch(name: str) -> Path:
    """按 工作区 → 终弃档 → ``<name>__bulk-*`` 返回第一个存在的批次目录。

    Args:
        name: 批次名。

    Returns:
        存在的批次目录；都不存在时返回 ``WORK_ROOT/name`` 供调用方报错。
    """
    for cand in (WORK_ROOT / name, DEL_ROOT / name):
        if cand.is_dir():
            return cand
    for cand in sorted(p for p in DEL_ROOT.glob(f"{name}__bulk-*") if p.is_dir()):
        return cand
    return WORK_ROOT / name


def ledger_roots() -> list[Path]:
    """返回台账根目录列表（终弃档在前，活动工作区在后）。"""
    return [DEL_ROOT, WORK_ROOT]
TAB = (".xls", ".xlsx", ".csv")
# 长后缀在前，避免 .tar.zst 被当成 .zst 以外的误判
ARC_EXTS = (".tar.zst", ".tar.gz", ".tar.bz2", ".tar.xz", ".tgz", ".tbz2", ".7z",
            ".zip", ".rar", ".tar", ".zst", ".gz", ".bz2", ".xz")
LEAF_README = "目录说明.md"
SHARE_CLASSES = ("ethics", "thesis")
# 未入库例外（PHI 花名册 / 模板）：叶内允许残留，校验时只 warn
LEAF_EXCEPTIONS = {"MHE_PA/lx_MHE_1"}

# 现行口径（meta.rule 逐字；verify 按关键字校验）
RULE_TEXT = ("深度去重：sha→整表(重复/子集)→行级；压缩档不入库（原档留 _share，"
             "档内表解出入库）；叶内只留说明；原件移 _DEL")
RULE_KEYWORDS = ("_share", "叶内只留说明")

NUM = re.compile(r"^[-+]?\d+(?:\.\d+)?(?:[eE][-+]?\d+)?$")
IDCOL = re.compile(
    r"^(医技号|样本编号|样本号|编号|case\s*no\.?|mid|谱图编号|patient(?:_?id)?|id)$", re.I)

DDL = """
CREATE TABLE meta(k TEXT PRIMARY KEY, v TEXT);
CREATE TABLE project(id INTEGER PRIMARY KEY, family TEXT, slug TEXT, leaf TEXT, short_code TEXT);
CREATE TABLE doc(id INTEGER PRIMARY KEY, project_id INTEGER, rel_path TEXT, size INTEGER,
                 sha256 TEXT, mtime TEXT, n_sheets INTEGER, status TEXT, dup_of INTEGER);
CREATE TABLE sheet(id INTEGER PRIMARY KEY, doc_id INTEGER, name TEXT, nrow INTEGER, ncol INTEGER,
                   header TEXT, blob BLOB, is_wide INTEGER, status TEXT, dup_of INTEGER,
                   role TEXT DEFAULT 'raw',          -- raw | curated | derived
                   derived_of INTEGER REFERENCES sheet(id));
CREATE TABLE fact(id INTEGER PRIMARY KEY, sheet_id INTEGER, entity_id INTEGER, row_ix INTEGER,
                  item TEXT, value_num REAL, value_text TEXT);
CREATE TABLE entity(id INTEGER PRIMARY KEY, project_id INTEGER, key_kind TEXT, key_norm TEXT,
                    key_raw TEXT);
CREATE TABLE sheet_dup(id INTEGER PRIMARY KEY, sheet_id INTEGER, dup_sheet_id INTEGER,
                       reason TEXT, kept_bytes INTEGER);
CREATE INDEX ix_fact_sheet ON fact(sheet_id);
CREATE INDEX ix_fact_item ON fact(item, value_num);
CREATE INDEX ix_entity ON entity(project_id, key_norm);
"""

# ---------------------------------------------------------------------------
# 基础判定
# ---------------------------------------------------------------------------

def is_table(name):
    return Path(name).suffix.lower() in TAB


def is_archive(name):
    low = Path(name).name.lower()
    return any(low.endswith(e) for e in ARC_EXTS)


def strip_arc_ext(name):
    low = name.lower()
    for e in ARC_EXTS:
        if low.endswith(e):
            return name[: -len(e)]
    return name


def now_iso():
    return datetime.now().isoformat(timespec="seconds")


def family_dir(db_root, family):
    return Path(db_root) / family


def family_db(db_root, family):
    return Path(db_root) / family / "_db" / f"{family}db"


def family_of_db(db_path):
    """<root>/<family>/_db/<family>db → family。"""
    p = Path(db_path)
    if p.parent.name == "_db":
        return p.parent.parent.name
    return p.parent.name


def leaf_dirs(fam_dir):
    """家族下的叶目录（跳过 _db/_share 等下划线目录）。"""
    fam = Path(fam_dir)
    if not fam.is_dir():
        return []
    return sorted(p for p in fam.iterdir() if p.is_dir() and not p.name.startswith("_"))


def table_files(base):
    base = Path(base)
    return sorted(p for p in base.rglob("*") if p.is_file() and is_table(p.name))


def norm_path(s):
    s = str(s).replace("\\", "/").strip()
    while s.startswith("./"):
        s = s[2:]
    return s


def make_dirs(p):
    p = Path(p)
    p.mkdir(parents=True, exist_ok=True)
    return p


# ---------------------------------------------------------------------------
# 库：建 / 开 / 元数据 / 去重索引
# ---------------------------------------------------------------------------

def create_db(db_path):
    """新建库（已存在则先删），返回连接（已建表）。"""
    db_path = Path(db_path)
    make_dirs(db_path.parent)
    if db_path.exists():
        db_path.unlink()
    con = sqlite3.connect(str(db_path))
    con.executescript(DDL)
    return con


def open_db(db_path):
    con = sqlite3.connect(str(db_path))
    return con


def set_meta(con, **kv):
    for k, v in kv.items():
        con.execute("INSERT OR REPLACE INTO meta(k, v) VALUES(?, ?)", (k, str(v)))


def get_meta(con):
    return dict(con.execute("SELECT k, v FROM meta"))


def refresh_meta_counts(con):
    """把 meta.n_doc/n_sheet/n_fact/n_entity 刷新为当前实际计数。"""
    counts = {}
    for key, tab in (("n_doc", "doc"), ("n_sheet", "sheet"),
                     ("n_fact", "fact"), ("n_entity", "entity")):
        counts[key] = con.execute(f"SELECT count(*) FROM {tab}").fetchone()[0]
    set_meta(con, **counts)
    return counts


def build_sig(con):
    """从已有 sheet blob 反建去重索引：hkey -> [{"id","rows","n"}]。

    增量入库（archive_ingest / sweep_tables / merge --into）前必须调用，
    否则与库内已有表无法做整表去重。
    """
    sig = {}
    for sid, hkey, blob in con.execute(
            "SELECT id, header, blob FROM sheet WHERE blob IS NOT NULL AND status='ingested'"):
        try:
            txt = zlib.decompress(blob).decode("utf-8")
        except (zlib.error, UnicodeDecodeError):
            continue
        lines = txt.split("\n") if txt else []
        rows = lines[1:] if not (hkey or "").startswith("__nohdr__") else lines
        rh = set()
        for r in rows:
            if not r:
                continue
            cells = r.split("\t")
            if any(cells):
                rh.add(hashlib.sha1(("\x1f".join(cells)).encode("utf-8")).hexdigest())
        sig.setdefault(hkey or "", []).append(dict(id=sid, rows=rh, n=len(rh)))
    return sig


def as_num(s):
    t = norm(s).replace(",", "")
    if t and NUM.match(t):
        try:
            return float(t)
        except ValueError:
            return None
    return None


def find_header(rows):
    best, bi = 0, -1
    for i, r in enumerate(rows[:6]):
        c = sum(1 for v in r if v)
        if c > best:
            best, bi = c, i
    return bi if best >= 2 else -1


def header_of(rows, hi):
    hdr, seen = [], {}
    for v in rows[hi]:
        h = v or ""
        if h in seen:
            seen[h] += 1
            h = f"{h}#{seen[h]}"
        else:
            seen[h] = 1
        hdr.append(h)
    return hdr


class Ingestor:
    """增量入库器：与 build_code_db 旧逻辑逐字同源的整表去重 + blob + fact/entity。"""

    def __init__(self, con, code):
        self.con = con
        self.code = code
        self.sig = build_sig(con)
        self.projects = {}
        self._ent_cache = {}
        self.stat = dict(files=0, dup_files=0, sheets=0, dup_sheets=0, subset_sheets=0,
                         facts=0, entities=0, blob_bytes=0)

    # -- 项目缓存 ---------------------------------------------------------
    def ensure_project(self, family, slug, leaf):
        key = (family, slug, leaf)
        pid = self.projects.get(key)
        if pid is None:
            row = self.con.execute(
                "SELECT id FROM project WHERE family=? AND slug=? AND leaf=?",
                (family, slug, leaf)).fetchone()
            if row:
                pid = row[0]
            else:
                pid = self.con.execute(
                    "INSERT INTO project(family,slug,leaf,short_code) VALUES(?,?,?,?)",
                    (family, slug, leaf, self.code)).lastrowid
            self.projects[key] = pid
        return pid

    def sha_seen(self, h):
        row = self.con.execute("SELECT id FROM doc WHERE sha256=?", (h,)).fetchone()
        return row[0] if row else None

    def ingest(self, path, rel, family, slug, leaf, incremental=False):
        """入库一个表格文件。incremental=True 时同 rel / 同 sha 已入库则跳过。

        返回 (doc_id, 新增 sheet 数)；跳过时返回 (已有 doc_id, 0)。
        """
        p = Path(path)
        h = sha256(p)
        rel = norm_path(rel)
        if incremental:
            row = self.con.execute("SELECT id FROM doc WHERE rel_path=?", (rel,)).fetchone()
            if row:
                self.stat["dup_files"] += 1
                return row[0], 0
            did = self.sha_seen(h)
            if did:
                self.stat["dup_files"] += 1
                return did, 0
        pid = self.ensure_project(family, slug, leaf)
        st = p.stat()
        did = self.con.execute(
            "INSERT INTO doc(project_id,rel_path,size,sha256,mtime,n_sheets,status)"
            " VALUES(?,?,?,?,?,0,'ingested')",
            (pid, rel, st.st_size, h,
             datetime.fromtimestamp(st.st_mtime).isoformat(timespec="seconds"))).lastrowid
        self.stat["files"] += 1
        try:
            sheets = read_any(p)
        except Exception as e:  # 读取失败仍留 doc 记录
            print(f"! 读取失败 {rel}: {e.__class__.__name__}: {e}"[:160])
            sheets = []
        self.con.execute("UPDATE doc SET n_sheets=? WHERE id=?", (len(sheets), did))
        added = 0
        for name, rows in sheets:
            self._ingest_sheet(did, pid, name, rows, st.st_size)
            added += 1
        return did, added

    def _ingest_sheet(self, did, pid, name, rows, kept_bytes=0):
        con = self.con
        stat = self.stat
        stat["sheets"] += 1
        hi = find_header(rows)
        hdr = header_of(rows, hi) if hi >= 0 else []
        hkey = "\x1f".join(hdr) if hdr else f"__nohdr__{name}"
        data = rows[hi + 1:] if hi >= 0 else rows
        rh = set()
        for r in data:
            if any(r):
                rh.add(hashlib.sha1(("\x1f".join(r)).encode("utf-8")).hexdigest())
        nrow = sum(1 for r in data if any(r))
        ncol = max((len(r) for r in rows), default=0)
        status, dup_of = "ingested", None
        prev = self.sig.setdefault(hkey, [])
        if hdr:
            for meta in prev:
                if meta["rows"] == rh:
                    status, dup_of = "dup", meta["id"]
                    break
                if rh and rh <= meta["rows"]:
                    status, dup_of = "subset", meta["id"]
                    break
        blob = None
        if status == "ingested":
            txt = "\n".join("\t".join(r) for r in rows)
            blob = zlib.compress(txt.encode("utf-8"), 9)
            stat["blob_bytes"] += len(blob)
        sid = con.execute(
            "INSERT INTO sheet(doc_id,name,nrow,ncol,header,blob,is_wide,status,dup_of)"
            " VALUES(?,?,?,?,?,?,?,?,?)",
            (did, name, nrow, ncol, hkey[:4000], blob, int(ncol > 40), status, dup_of)).lastrowid
        prev.append(dict(id=sid, rows=rh, n=nrow))
        if status != "ingested":
            stat["dup_sheets" if status == "dup" else "subset_sheets"] += 1
            con.execute("INSERT INTO sheet_dup(sheet_id,dup_sheet_id,reason,kept_bytes)"
                        " VALUES(?,?,?,?)",
                        (sid, dup_of, "整表重复" if status == "dup" else "整表子集",
                         kept_bytes))
            return
        # 整洁表 → fact；矩阵（表头多为数字）只留 blob
        hdr_num = sum(1 for h2 in hdr[1:] if h2 and as_num(h2) is not None)
        is_matrix = hdr and (hdr_num / max(1, len(hdr) - 1)) > 0.6
        if hdr and ncol <= 40 and nrow > 0 and not is_matrix:
            idcol = next((i for i, h2 in enumerate(hdr) if IDCOL.match(h2 or "")), None)
            # 个体键去重按 (project, key_norm) 跨 sheet：同一样本分散在多表时应归一个 entity
            ent_cache = self._ent_cache.setdefault(pid, {})
            for rix, r in enumerate(data):
                if not any(r):
                    continue
                eid = None
                if idcol is not None and idcol < len(r) and r[idcol]:
                    kn = re.sub(r"\s+", "", r[idcol]).lower()
                    if kn not in ent_cache:
                        ent_cache[kn] = con.execute(
                            "INSERT INTO entity(project_id,key_kind,key_norm,key_raw)"
                            " VALUES(?,?,?,?)",
                            (pid, hdr[idcol][:60], kn, r[idcol][:200])).lastrowid
                        stat["entities"] += 1
                    eid = ent_cache[kn]
                for ci, v in enumerate(r):
                    if v == "" or ci >= len(hdr) or not hdr[ci]:
                        continue
                    con.execute(
                        "INSERT INTO fact(sheet_id,entity_id,row_ix,item,value_num,value_text)"
                        " VALUES(?,?,?,?,?,?)",
                        (sid, eid, rix, hdr[ci][:120], as_num(v), v[:500]))
                    stat["facts"] += 1


# ---------------------------------------------------------------------------
# 归档：识别 / 枚举 / 解包
# ---------------------------------------------------------------------------

def extract_archive(arc, dest):
    """解包归档到 dest（dest 须为空或不存在），返回成员相对路径（posix，文件）。

    .zip → stdlib（修 cp437→gbk 文件名 + CRC 校验）
    .7z  → py7zr
    其余（rar / tar / tar.zst / gz …）→ bsdtar -xf
    """
    arc = Path(arc)
    dest = Path(dest)
    if dest.exists() and any(dest.iterdir()):
        raise SystemExit(f"解包目标非空，拒绝覆盖: {dest}")
    make_dirs(dest)
    low = arc.name.lower()
    if low.endswith(".zip"):
        members = _zip_extract(arc, dest)
    elif low.endswith(".7z"):
        members = _7z_extract(arc, dest)
    else:
        members = _bsdtar_extract(arc, dest)
    missing = [m for m in members if not (dest / m).is_file()]
    if missing:
        raise RuntimeError(f"解包后缺成员 {len(missing)} 个: {missing[:3]}")
    return members


def _safe_parts(name):
    return [p for p in name.replace("\\", "/").split("/") if p not in ("", ".", "..")]


def _zip_extract(arc, dest):
    members = []
    with zipfile.ZipFile(str(arc)) as zf:
        bad = zf.testzip()
        if bad:
            raise RuntimeError(f"zip CRC 校验失败: {bad}")
        for info in zf.infolist():
            name = info.filename
            if not info.flag_bits & 0x800:
                try:
                    name = name.encode("cp437").decode("gbk")
                except (UnicodeEncodeError, UnicodeDecodeError):
                    pass
            parts = _safe_parts(name)
            if not parts:
                continue
            tgt = dest.joinpath(*parts)
            if info.is_dir():
                make_dirs(tgt)
                continue
            make_dirs(tgt.parent)
            with zf.open(info) as fsrc, open(tgt, "wb") as fdst:
                shutil.copyfileobj(fsrc, fdst)
            members.append("/".join(parts))
    return members


def _7z_extract(arc, dest):
    try:
        import py7zr  # 本机已装 1.1.0
    except ImportError:
        raise RuntimeError("解 .7z 需要 py7zr（python -m pip install py7zr）")
    with py7zr.SevenZipFile(str(arc)) as z:
        names = [i.filename for i in z.list() if not i.is_directory]
        z.extractall(path=str(dest))
    return ["/".join(_safe_parts(n)) for n in names]


def _decode(b):
    """归档内文件名常为 GBK；按 utf-8 → gbk → latin-1 依次尝试。"""
    if isinstance(b, str):
        return b
    for enc in ("utf-8", "gbk", "cp936"):
        try:
            return b.decode(enc)
        except UnicodeDecodeError:
            continue
    return b.decode("latin-1")


def _run_bsdtar(args):
    exe = shutil.which("bsdtar") or shutil.which("tar")
    if not exe:
        raise RuntimeError("未找到 bsdtar/tar，无法处理归档")
    proc = subprocess.run([exe] + list(args), capture_output=True)
    if proc.returncode != 0:
        raise RuntimeError(f"bsdtar 失败 rc={proc.returncode}: "
                           f"{_decode(proc.stderr or b'').strip()[:300]}")
    return _decode(proc.stdout or b"")


def _bsdtar_extract(arc, dest):
    _run_bsdtar(["-xf", str(arc), "-C", str(dest)])
    return sorted(p.relative_to(dest).as_posix() for p in dest.rglob("*") if p.is_file())


def is_raw_pack(name):
    """叶内 raw 打包产物（<code>__<leaf>__<unit>-raw-<YYYY-MM-DD>.tar.zst），不是源归档。"""
    return bool(re.search(r"-raw-\d{4}-\d{2}-\d{2}\.tar\.zst$", Path(name).name, re.I))


def share_archives(fam_dir):
    """<family>/_share 下（层级 ≤2）全部归档文件。"""
    share = Path(fam_dir) / "_share"
    out = []
    if not share.is_dir():
        return out
    for p in sorted(share.rglob("*")):
        if p.is_file() and is_archive(p.name):
            if len(p.relative_to(share).parts) > 2:
                continue
            out.append(p)
    return out


def share_rel_dir(fam_dir, arc):
    """归档相对家族目录的目录部分，含 `_share`，如 `_share/thesis`。"""
    rel = Path(arc).relative_to(Path(fam_dir))
    return rel.parent.as_posix()


def share_arc_leaf(fam_dir, arc):
    """由 _share 归档反推 (leaf, project_slug)。

    - `_share/<leaf>/<arc>`          → (leaf, leaf)
    - `_share/thesis|ethics/<arc>`   → 打平名 `<slug>__<文件>` 的 slug
    - `_share/<arc>`（家族根档）      → 家族名
    """
    fam = Path(fam_dir)
    rel = Path(arc).relative_to(fam / "_share")
    parent = rel.parts[0] if len(rel.parts) > 1 else fam.name
    base = strip_arc_ext(Path(arc).name)
    prefix = base.split("__", 1)[0] if "__" in base else ""
    leaves = {p.name for p in leaf_dirs(fam)}
    if parent in leaves:
        return parent, parent
    if parent in SHARE_CLASSES:
        slug = prefix or base
        return slug, slug
    return parent, parent


def rel_for_archive_member(fam_dir, arc, member):
    """档内成员的 doc.rel_path = <FAM>/_share[/<类>]/<档名>/<成员>。"""
    fam = Path(fam_dir).name
    return f"{fam}/{share_rel_dir(fam_dir, arc)}/{Path(arc).name}/{member}".replace("\\", "/")


# ---------------------------------------------------------------------------
# 台账
# ---------------------------------------------------------------------------

_REL_NAMES = ("rel_path", "rel", "src_rel", "original_rel_path", "path_rel", "relpath")
_SRC_NAMES = ("src", "source", "from", "original", "src_path")
_DST_NAMES = ("dst", "dest", "dest_rel", "now_at", "to", "dst_path")
_SHA_NAMES = ("sha256", "sha256_12", "sha", "hash")
_SIZE_NAMES = ("size", "size_B", "bytes", "size_bytes", "nbytes")
_KIND_NAMES = ("kind", "action", "type")
_ARC_NAMES = ("archive", "arc", "archive_name")
_MEMBER_NAMES = ("member", "member_path", "inner")


def _pick(hdr, names):
    for n in names:
        if n in hdr:
            return hdr.index(n)
    return None


def ledger_read(path):
    """读任意 MOVED 台账 → list[dict(kind, rel, src, dst, sha, size, archive, member, path)]。

    支持列名别名；无表头台账按位置 (kind, rel, dst, size, sha) 解析。
    """
    path = Path(path)
    try:
        lines = [ln for ln in path.read_text(encoding="utf-8-sig", errors="replace").splitlines()
                 if ln.strip()]
    except OSError:
        return []
    if not lines:
        return []
    hdr = [h.strip().lstrip("\ufeff").lower() for h in lines[0].split("\t")]
    i_rel, i_src = _pick(hdr, _REL_NAMES), _pick(hdr, _SRC_NAMES)
    i_dst = _pick(hdr, _DST_NAMES)
    i_sha, i_size = _pick(hdr, _SHA_NAMES), _pick(hdr, _SIZE_NAMES)
    i_kind = _pick(hdr, _KIND_NAMES)
    i_arc, i_member = _pick(hdr, _ARC_NAMES), _pick(hdr, _MEMBER_NAMES)
    rows = []
    if i_rel is None and i_src is None and i_dst is None:
        # 无表头：位置口径 (kind, rel, dst, size, sha)
        for ln in lines:
            f = ln.split("\t")
            if len(f) < 3:
                continue
            rows.append(dict(kind=f[0].strip(), rel=f[1].strip(), src=f[1].strip(),
                             dst=f[2].strip(), size=f[3].strip() if len(f) > 3 else "",
                             sha=(f[4].strip().lower() if len(f) > 4 else ""),
                             archive="", member="", path=str(path)))
        return rows

    def cell(f, i):
        return f[i].strip() if i is not None and i < len(f) else ""

    for ln in lines[1:]:
        f = ln.split("\t")
        rows.append(dict(
            kind=cell(f, i_kind), rel=cell(f, i_rel) or cell(f, i_src),
            src=cell(f, i_src), dst=cell(f, i_dst),
            sha=cell(f, i_sha).lower(), size=cell(f, i_size),
            archive=cell(f, i_arc), member=cell(f, i_member), path=str(path)))
    return rows


def rel_from_path(s, code=None):
    """台账里的绝对/相对路径 → _DataBase 内相对路径（posix）。"""
    s = norm_path(s)
    low = s.lower()
    for marker in ("/_database/", "data_base/", "_database/"):
        i = low.rfind(marker)
        if i >= 0:
            return s[i + len(marker):]
    rest = None
    i = low.find("/originals/")
    if i >= 0:
        rest = s[i + len("/originals/"):]
    elif low.startswith("originals/"):
        rest = s[len("originals/"):]
    if rest is not None:
        parts = rest.split("/")
        if code and parts and parts[0].lower() == code.lower():
            parts = parts[1:]
        return "/".join(parts)
    return s


# ---------------------------------------------------------------------------
# 移动 + 校验
# ---------------------------------------------------------------------------

def move_verified(src, dst, expect_sha=None):
    """移动文件到 dst，移动前后重算 sha256/size 校验。返回 (sha256, size)。"""
    src, dst = Path(src), Path(dst)
    if not src.is_file():
        raise RuntimeError(f"源文件不存在: {src}")
    h = sha256(src)
    size = src.stat().st_size
    if expect_sha:
        e = expect_sha.lower()
        if not (h.startswith(e) or e.startswith(h)):
            raise RuntimeError(f"移动前 sha256 与台账不符: {src} 实际 {h[:12]} 台账 {e[:12]}")
    if dst.exists():
        raise RuntimeError(f"移动目标已存在，拒绝覆盖: {dst}")
    make_dirs(dst.parent)
    shutil.move(str(src), str(dst))
    if not dst.is_file():
        raise RuntimeError(f"移动后目标缺失: {dst}")
    h2 = sha256(dst)
    if h2 != h or dst.stat().st_size != size:
        raise RuntimeError(f"移动后校验失败: {dst} sha {h2[:12]} size {dst.stat().st_size}")
    return h, size


def write_ledger(path, header, rows):
    """薄封装：统一 UTF-8 + \n。"""
    ledger_write(Path(path), tuple(header), rows)
    return Path(path)


# ---------------------------------------------------------------------------
# 便捷函数（CLI 共用）
# ---------------------------------------------------------------------------

def read_code(db_root, family):
    """读库 meta.short_code；库不存在返回 None。"""
    db = family_db(db_root, family)
    if not Path(db).is_file():
        return None
    con = open_db(db)
    try:
        return get_meta(con).get("short_code")
    finally:
        con.close()


def is_within(path, root):
    """path 是否在 root 之下（解析后比较）。"""
    try:
        Path(path).resolve().relative_to(Path(root).resolve())
        return True
    except (ValueError, OSError):
        return False


def readme_path(d):
    return Path(d) / LEAF_README


def append_readme_note(d, note):
    """向 目录说明.md 追加一行说明（不存在则创建）。"""
    p = readme_path(d)
    if p.is_file():
        txt = p.read_text(encoding="utf-8")
        if note in txt:
            return p
        if not txt.endswith("\n"):
            txt += "\n"
        p.write_text(txt + note + "\n", encoding="utf-8")
    else:
        p.write_text("# 说明\n\n" + note + "\n", encoding="utf-8")
    return p


def pack_tar_zst(entries, leaf_dir, out_path):
    """bsdtar -c --zstd 打包 entries（相对 leaf_dir 的路径）到 out_path。"""
    out_path = Path(out_path)
    make_dirs(out_path.parent)
    exe = shutil.which("bsdtar")
    if not exe:
        raise RuntimeError("未找到 bsdtar（leaf_raw_pack 需要 bsdtar --zstd）")
    if out_path.exists():
        raise RuntimeError(f"压缩档已存在，拒绝覆盖: {out_path}")
    cmd = [exe, "-c", "--zstd", "-f", str(out_path), "-C", str(leaf_dir)] + list(entries)
    proc = subprocess.run(cmd, capture_output=True, text=True)
    if proc.returncode != 0:
        raise RuntimeError(f"bsdtar 打包失败 rc={proc.returncode}: {(proc.stderr or '').strip()[:300]}")
    if not out_path.is_file() or out_path.stat().st_size == 0:
        raise RuntimeError(f"打包产物缺失或为空: {out_path}")
    return out_path


def tar_list(arc):
    """bsdtar -tf 列出归档成员（posix 相对路径，目录项剔除）。"""
    out = _run_bsdtar(["-tf", str(arc)])
    return [ln.strip().replace("\\", "/") for ln in out.splitlines()
            if ln.strip() and not ln.strip().endswith("/")]


def archive_members(arc):
    """列出归档成员（不解包）：.zip/.7z 用库，其余 bsdtar -tf。"""
    arc = Path(arc)
    low = arc.name.lower()
    if low.endswith(".zip"):
        with zipfile.ZipFile(str(arc)) as zf:
            names = zf.namelist()
        return [n.replace("\\", "/") for n in names if n and not n.endswith("/")]
    if low.endswith(".7z"):
        try:
            import py7zr
        except ImportError:
            raise RuntimeError("列 .7z 成员需要 py7zr")
        with py7zr.SevenZipFile(str(arc)) as z:
            return ["/".join(_safe_parts(i.filename)) for i in z.list() if not i.is_directory]
    return tar_list(arc)

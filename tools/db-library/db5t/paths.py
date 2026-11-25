# -*- coding: utf-8 -*-
"""db5t 工具的路径解析（唯一一处）。

三个可覆盖项，按优先级从环境变量读，读不到才用默认值：

  DB5T_BATCH   批次根（默认 pilot 批次目录；含 SCHEMA-v1.md 与 stage/）
  DB5T_ROOT    库根（默认 <DB5T_BATCH>/stage；切库后指 D:/MyProjects/_DataBase）
  DB5T_SCHEMA  SCHEMA 文件（默认 <DB5T_BATCH>/SCHEMA-v1.md；再退到本目录 schema-v1.md）

为什么不在代码里写死路径：门禁要能对 stage 产物跑，也要能对切库后的真库跑，
两种情况共用同一份判据。写死等于每切一家就改一次代码。

自检：python -m db5t.paths 会打印当前解析结果与 SCHEMA 的 sha256，
换批次/换库根后先跑这一条，比看门禁输出更快发现指错地方。
"""
import hashlib
import os
import pathlib
import sys

HERE = pathlib.Path(__file__).resolve().parent

_DEF_BATCH = pathlib.Path(r'D:\MyProjects\.Del\_by-function\db5t-pilot-20261124')

BATCH = pathlib.Path(os.environ.get('DB5T_BATCH') or _DEF_BATCH)
STAGE = pathlib.Path(os.environ.get('DB5T_ROOT') or (BATCH / 'stage'))


def _schema() -> pathlib.Path:
    env = os.environ.get('DB5T_SCHEMA')
    if env:
        return pathlib.Path(env)
    for cand in (BATCH / 'SCHEMA-v1.md', HERE / 'schema-v1.md'):
        if cand.exists():
            return cand
    return HERE / 'schema-v1.md'


SCHEMA = _schema()
SCHEMA_MD = SCHEMA          # 老脚本按 SCHEMA_MD 引用
ROOT = STAGE                # audit_families 按 ROOT 算相对路径


def sha256(path) -> str:
    h = hashlib.sha256()
    with open(path, 'rb') as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b''):
            h.update(chunk)
    return h.hexdigest()


def describe() -> str:
    ok = 'exists' if SCHEMA.exists() else 'MISSING'
    digest = sha256(SCHEMA) if SCHEMA.exists() else '-'
    return (
        f'BATCH  = {BATCH}\n'
        f'STAGE  = {STAGE}  (DB5T_ROOT={"set" if os.environ.get("DB5T_ROOT") else "default"})\n'
        f'SCHEMA = {SCHEMA} [{ok}] sha256={digest}\n'
    )


if __name__ == '__main__':
    sys.stdout.write(describe())

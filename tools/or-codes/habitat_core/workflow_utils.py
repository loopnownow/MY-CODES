# -*- coding: utf-8 -*-
"""expand_roi_5mm 的日志与断点工具（原为 workflow_utils.pyc，此为实现等价的可读版本）。

行为与旧 pyc 一致，便于复用既有日志/断点：
  setup_logger(log_dir, name)  -> <log_dir>/<name>_<%Y%m%d_%H%M%S>.log，同时输出到控制台
  CheckpointManager(dir)       -> is_done / mark_done / get_checkpoint_path / clear
  断点文件：<dir>/<safe_id>.done（safe_id 只保留字母数字与 -_，其余换成 _）
"""
from __future__ import annotations

import logging
import os
from datetime import datetime


def setup_logger(log_dir, name="process"):
    """建目录、写日志文件、返回带文件与控制台双 handler 的 logger。"""
    os.makedirs(log_dir, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    log_path = os.path.join(log_dir, f"{name}_{stamp}.log")

    logger = logging.getLogger(name)
    logger.setLevel(logging.INFO)
    for handler in list(logger.handlers):
        logger.removeHandler(handler)
        handler.close()

    fmt = logging.Formatter("%(asctime)s  %(levelname)-8s  %(message)s",
                            datefmt="%Y-%m-%d %H:%M:%S")
    stream = logging.StreamHandler()
    stream.setFormatter(fmt)
    logger.addHandler(stream)

    file_handler = logging.FileHandler(log_path, encoding="utf-8")
    file_handler.setFormatter(fmt)
    logger.addHandler(file_handler)

    logger.info(f"日志文件: {log_path}")
    return logger


class CheckpointManager:
    """按任务 id 记录已完成项（存在 .done 文件即视为完成）。"""

    def __init__(self, checkpoint_dir):
        self.checkpoint_dir = checkpoint_dir
        os.makedirs(checkpoint_dir, exist_ok=True)

    def get_checkpoint_path(self, task_id):
        safe_id = "".join(c if (c.isalnum() or c in ("-", "_")) else "_" for c in task_id)
        return os.path.join(self.checkpoint_dir, f"{safe_id}.done")

    def is_done(self, task_id):
        return os.path.exists(self.get_checkpoint_path(task_id))

    def mark_done(self, task_id):
        path = self.get_checkpoint_path(task_id)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w", encoding="utf-8") as fh:
            fh.write(datetime.now().strftime("%Y-%m-%d %H:%M:%S"))

    def clear(self, task_id=None):
        """task_id 为空则清空全部断点。"""
        if task_id is None:
            for name in os.listdir(self.checkpoint_dir):
                if name.endswith(".done"):
                    os.remove(os.path.join(self.checkpoint_dir, name))
            return
        path = self.get_checkpoint_path(task_id)
        if os.path.exists(path):
            os.remove(path)

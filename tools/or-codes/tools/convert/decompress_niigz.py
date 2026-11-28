#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
NIFTI .nii.gz 批量解压脚本
递归扫描目录，解压所有.nii.gz文件，保留原始压缩文件
"""
import os
import gzip
import shutil
import sys
from pathlib import Path


def decompress_nii_gz(root_dir: str, dry_run: bool = False):
    """
    递归扫描目录并解压.nii.gz文件
    
    Args:
        root_dir: 根目录路径
        dry_run: False=仅测试扫描，True=实际解压
    """
    root_path = Path(root_dir).resolve()
    
    if not root_path.exists():
        print(f"❌ 错误: 目录不存在 {root_path}")
        return

    print(f"{'='*70}")
    print(f"扫描根目录: {root_path}")
    print(f"运行模式: {'🔍 测试模式 (仅扫描) ' if not dry_run else '⚡ 正式模式 (执行解压)'}")
    print(f"{'='*70}")
    
    # 统计变量
    total_found = 0
    total_skipped = 0
    total_decompressed = 0
    total_errors = 0
    
    # 递归遍历所有文件
    for nii_gz_path in root_path.rglob("*.nii.gz"):
        total_found += 1
        
        # 目标.nii文件路径
        nii_path = nii_gz_path.with_suffix('').with_suffix('.nii')
        
        print(f"\n[{total_found:3d}] 发现: {nii_gz_path.relative_to(root_path)}")
        
        # 检查是否已经解压
        if nii_path.exists():
            print("      ⏭️  跳过，已存在解压文件")
            total_skipped += 1
            continue
        
        if not dry_run:
            print(f"      ℹ️  测试模式，将解压为: {nii_path.name}")
            total_decompressed += 1
            continue
        
        # 执行实际解压
        try:
            print("      📦 正在解压...")
            
            # 流式解压，内存友好，适合大文件
            with gzip.open(nii_gz_path, 'rb') as f_in:
                with open(nii_path, 'wb') as f_out:
                    shutil.copyfileobj(f_in, f_out)
            
            # 复制文件修改时间
            stat = nii_gz_path.stat()
            os.utime(nii_path, (stat.st_atime, stat.st_mtime))
            
            print("      ✅ 解压完成")
            total_decompressed += 1
            
        except Exception as e:
            print(f"      ❌ 解压失败: {str(e)}")
            total_errors += 1
            
            # 清理可能的不完整文件
            if nii_path.exists():
                nii_path.unlink()

    # 输出最终报告
    print(f"\n{'='*70}")
    print("📊 处理报告:")
    print(f"{'='*70}")
    print(f"  总计找到 .nii.gz 文件: {total_found}")
    print(f"  跳过 (已解压): {total_skipped}")
    print(f"  待解压 / 已解压: {total_decompressed}")
    print(f"  错误数量: {total_errors}")
    print(f"{'='*70}")
    
    if not dry_run and total_found > 0:
        print("\n💡 提示: 测试扫描完成，确认文件列表正确后")
        print(f"   执行正式解压命令: python {sys.argv[0]} {root_dir} True")


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print(f"用法: python {sys.argv[0]} <根目录> [mode]")
        print(f"示例:")
        print(f"  测试扫描: python {sys.argv[0]} ./ False")
        print(f"  正式解压: python {sys.argv[0]} ./ True")
        sys.exit(1)
    
    root_dir_param = sys.argv[1]
    # 参数逻辑: False=测试扫描, True=正式解压
    dry_run_param = False if len(sys.argv) < 3 else sys.argv[2].lower() == 'true'
    
    decompress_nii_gz(root_dir_param, dry_run_param)
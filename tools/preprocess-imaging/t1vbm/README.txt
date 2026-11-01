T1 VBM V2

This package is a stabilized version of the supplied T1_vbm MATLAB pipeline.
Keep all .m files in the same directory and add that directory to MATLAB path.

Recommended first run:
  addpath('G:\SSD_dyf\T1_vbm')
  C = t1vbm_config();
  t1vbm_env('checkspm', C)
  t1vbm_step1_unzip(C)
  t1vbm_calibrate(C)
  run_all('from','step2','nproc',<calibration result>)

See t1vbm-user-guide-v2.html and CHANGELOG_V2.md.


V2.1 后台运行说明
==================
Windows 下 step2 默认 `C.cat.backgroundHidden=true`。运行：

  C=t1vbm_config('nproc',12);
  run_all('from','step2','until','step2','nproc',12);

流程会使用 PowerShell 隐藏启动独立 MATLAB worker。主 MATLAB Command Window 只显示监督进度，不会弹出 12 个 CAT12 worker Command Window。
每个 worker 使用 nproc=0，因此不会再由 CAT12 二次创建 MATLAB 子进程。

状态文件位于：
  log\cat12_jobs\batch_XXX\
其中 `.running`、`.done`、`.fail` 分别表示运行中、成功、失败。

如需恢复 CAT12 原生并行方式：
  C=t1vbm_config('nproc',12);
  C.cat.backgroundHidden=false;
  t1vbm_step2_cat12(C);

建议优先使用默认隐藏 worker 模式。

V2.5 可选 TIV 协变量（默认仍为 Age/Sex/EducationYears）。XML 提取：python cat12_xml_extract.py --input <目录>

function LST_T2WI(root_dir)
% LST_T2WI.m — 批量遍历 root_dir 各子文件夹，查找 .nii/.nii.gz 并运行 SPM LST-LPA。
%
% 原为 brain_nii_ZJL / HE_nii_ly 两份同源脚本（后者多断点续传 + .nii.gz 解压），
% 合并为此一份函数；root_dir 参数化。
%
% 用法：
%   LST_T2WI(<根目录>)               % 指定根目录（原 tirm 已撤，须显式传参）
%   LST_T2WI()                         % 用下方默认根目录
%
% 断点续传：子文件夹已存在 report_LST_lpa*.html 则跳过。
% 依赖：MATLAB + SPM（含 LST 工具箱，spm.tools.LST.lpa）。

if nargin < 1 || isempty(root_dir)
    root_dir = getenv('SSD_TIRM_DIR');   % 原 L:\SSD\SSD_dyf\tirm 已撤
    if isempty(root_dir)
        error('SSD_TIRM_DIR 未设置：L:\SSD\SSD_dyf\tirm 已不存在，请调用时传入根目录');
    end
end

fprintf('=== LST T2WI Batch Builder ===\n');
fprintf('根目录: %s\n\n', root_dir);

if ~isfolder(root_dir)
    error('根目录不存在: %s', root_dir);
end

matlabbatch = {};
nii_files = {};

% ========== 遍历子文件夹 ==========
subdirs = dir(root_dir);
subdirs = subdirs([subdirs.isdir]);
subdirs = subdirs(~ismember({subdirs.name}, {'.', '..'}));

for i = 1:numel(subdirs)
    sub_path = fullfile(root_dir, subdirs(i).name);

    % 断点续传：已存在 LST-LPA 报告则跳过
    if ~isempty(dir(fullfile(sub_path, 'report_LST_lpa*.html')))
        fprintf('[跳过] %s\\ — 已存在报告文件\n', subdirs(i).name);
        continue;
    end

    % 查找 .nii 或 .nii.gz
    fnames = [dir(fullfile(sub_path, '*.nii')); dir(fullfile(sub_path, '*.nii.gz'))];
    fnames = fnames(~[fnames.isdir]);

    if isempty(fnames)
        fprintf('[跳过] %s\\ — 未找到 .nii/.nii.gz 文件\n', subdirs(i).name);
        continue;
    end

    fname = fnames(1).name;
    full_fname = fullfile(sub_path, fname);

    % .nii.gz 需解压（SPM 不能直接读压缩格式）
    if endsWith(fname, '.gz')
        fprintf('[解压] %s\\ — %s\n', subdirs(i).name, fname);
        unzipped = gunzip(full_fname, sub_path);
        full_fname = unzipped{1};
    else
        fprintf('[添加] %s\\ — %s\n', subdirs(i).name, fname);
    end

    if numel(fnames) > 1
        fprintf('[提示] %s\\ — 发现 %d 个 NIfTI 文件，处理第一个\n', ...
            subdirs(i).name, numel(fnames));
    end

    nii_files{end+1,1} = [full_fname ',1']; %#ok<AGROW>
end

if isempty(nii_files)
    error('未在任何子文件夹中找到 .nii/.nii.gz 文件，终止执行。');
end
fprintf('\n共找到 %d 个有效 NIfTI 文件\n\n', numel(nii_files));

% ========== 检查 SPM 并初始化 ==========
if ~exist('spm', 'file')
    error('SPM 未在 MATLAB 路径中，请先添加 SPM');
end
spm('defaults', 'FMRI');
spm_jobman('initcfg');

% ========== 构建并运行 LST LPA batch ==========
matlabbatch{1}.spm.tools.LST.lpa.data_F2 = nii_files;
matlabbatch{1}.spm.tools.LST.lpa.data_coreg = {''};
matlabbatch{1}.spm.tools.LST.lpa.html_report = 1;

fprintf('\n--- 开始执行 LST LPA 计算 ---\n');
spm_jobman('run', matlabbatch);
fprintf('\n=== LST LPA 计算完成 ===\n');
end

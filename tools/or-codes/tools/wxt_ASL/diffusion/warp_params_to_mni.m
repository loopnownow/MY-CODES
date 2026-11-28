% warp_params_to_mni - 用 ExploreASL 的 y_T1 把参数图归一化到 MNI 1.5mm
% =========================================================================
% 前置: 先运行  python warp_prep_finish.py --stage prep   (解压 .nii)
% 输入: derivatives/Diffusion_Params/sub-XXX/t1_<mod>_<PARAM>.nii (未压缩)
%       derivatives/ExploreASL/sub-XXX_1/y_T1.nii                (未压缩)
% 输出: 同目录 mni_<mod>_<PARAM>.nii (未压缩), 之后由 finish 阶段 gzip
% 用法: matlab -batch "cd('L:/00used/wxt_ASL'); warp_params_to_mni"
%   或: warp_params_to_mni('L:\SSD\wxt_SSD')
% =========================================================================
function warp_params_to_mni(root)
if nargin < 1 || isempty(root), root = 'L:\SSD\wxt_SSD'; end
PARS = fullfile(root, 'derivatives', 'Diffusion_Params');
EA   = fullfile(root, 'derivatives', 'ExploreASL');
addpath('D:\0share\0matelab_scripts\spm12');
spm('Defaults','fMRI'); spm_jobman('initcfg');

VOX = [1.5 1.5 1.5];
BB  = [-90 -126 -72; 90 90 108];
INTERP = 1;

subs = dir(fullfile(PARS, 'sub-*'));
nOk = 0; nSkip = 0;
logf = fopen(fullfile(PARS, 'warp_log.tsv'), 'w');
fprintf(logf, 'subject\tn_images\tstatus\n');

for i = 1:numel(subs)
    sub  = subs(i).name;
    ytmp = fullfile(EA, [sub '_1'], 'y_T1.nii');
    pdir = fullfile(PARS, sub);
    if ~exist(ytmp, 'file')
        fprintf(logf, '%s\t0\tno_y_T1_nii\n', sub); continue;
    end
    fl = dir(fullfile(pdir, 't1_*.nii'));
    imgs = {};
    for k = 1:numel(fl)
        nm = fl(k).name;
        if contains(nm, '_b0') || contains(nm, 'fitresid'), continue; end
        imgs{end+1} = fullfile(pdir, nm); %#ok<AGROW>
    end
    if isempty(imgs)
        fprintf(logf, '%s\t0\tno_t1_params\n', sub); continue;
    end
    imgs = imgs(:);
    nDone = 0;
    for k = 1:numel(imgs)
        [~, base] = fileparts(imgs{k});
        if exist(fullfile(pdir, ['mni_' base(4:end) '.nii']), 'file')
            nDone = nDone + 1;
        end
    end
    if nDone == numel(imgs)
        nSkip = nSkip + 1;
        fprintf(logf, '%s\t%d\tskip_all_done\n', sub, numel(imgs)); continue;
    end

    clear matlabbatch
    matlabbatch{1}.spm.util.defs.comp{1}.def = {ytmp};
    matlabbatch{1}.spm.util.defs.comp{1}.idbbvox.vox = VOX;
    matlabbatch{1}.spm.util.defs.comp{1}.idbbvox.bb  = BB;
    matlabbatch{1}.spm.util.defs.comp{1}.interp = INTERP;
    matlabbatch{1}.spm.util.defs.out{1}.pull.fnames = imgs;
    matlabbatch{1}.spm.util.defs.out{1}.pull.savedir.saveusr = {pdir};
    matlabbatch{1}.spm.util.defs.out{1}.pull.interp = INTERP;
    matlabbatch{1}.spm.util.defs.out{1}.pull.mask   = 0;
    matlabbatch{1}.spm.util.defs.out{1}.pull.fwhm   = [0 0 0];
    try
        spm_jobman('run', matlabbatch);
        % SPM pull 输出为 w<name>.nii -> 重命名为 mni_<name>.nii
        for k = 1:numel(imgs)
            [pd, base] = fileparts(imgs{k});          % base = t1_dti_FA
            wsrc = fullfile(pd, ['w' base '.nii']);
            mnin = fullfile(pd, ['mni_' base(4:end) '.nii']);
            if exist(wsrc, 'file')
                movefile(wsrc, mnin, 'f');
            elseif exist(imgs{k}, 'file')
                movefile(imgs{k}, mnin, 'f');          % 回退: 原地覆盖情形
            end
        end
        nOk = nOk + 1;
        fprintf(logf, '%s\t%d\tok\n', sub, numel(imgs));
        fprintf('  %s: %d images warped\n', sub, numel(imgs));
    catch ME
        fprintf(logf, '%s\t%d\tFAIL:%s\n', sub, numel(imgs), ME.message);
        fprintf(2, '  [FAIL] %s: %s\n', sub, ME.message);
    end
end
fclose(logf);
fprintf('done: ok=%d skip=%d\n', nOk, nSkip);
end


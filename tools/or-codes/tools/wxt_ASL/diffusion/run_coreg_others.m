function run_coreg_others(tag, subs, force)
% run_coreg_others - 运行"其他受试者"弥散->T1 配准 (供 matlab -batch 调用)
%   run_coreg_others            % 全部
%   run_coreg_others('w1','A,B')% 仅指定受试者 (逗号分隔), 日志后缀 w1
% diary 输出到 logs/coreg_others_<tag>.log; 结束打印 ALL_DONE_OK / ALL_DONE_ERR
    addpath('D:/0share/0matelab_scripts/spm12');
    addpath('L:/00used/wxt_ASL');
    cd('L:/00used/wxt_ASL');

    if nargin < 1 || isempty(tag), tag = 'all'; end
    logDir = 'L:/00used/wxt_ASL/logs';
    if ~exist(logDir, 'dir'), mkdir(logDir); end
    logf = fullfile(logDir, sprintf('coreg_others_%s.log', tag));

    diary(logf); diary on;
    fprintf('=== run_coreg_others(%s) start %s ===\n', tag, datestr(now));
    opt = struct( ...
        'manifest', 'L:/00used/wxt_ASL/derivatives/.tmp_coreg_others_nii/manifest_others.tsv', ...
        'outRoot',  'L:/00used/wxt_ASL/derivatives/Diffusion_Coreg');
    if nargin >= 2 && ~isempty(subs), opt.subs = subs; end
    if nargin >= 3 && ~isempty(force), opt.force = force; end
    try
        coreg_diffusion_to_t1(opt);
        fprintf('ALL_DONE_OK %s\n', datestr(now));
    catch ME
        fprintf('ALL_DONE_ERR\n%s\n', getReport(ME));
    end
    diary off;
end


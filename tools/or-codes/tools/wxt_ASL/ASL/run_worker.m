function run_worker(k, phaseName, doDry)
%RUN_WORKER 启动一个 ExploreASL 并行 worker（供 6 个 MATLAB GUI 窗口使用）。
%
% 用法（在每个 MATLAB 窗口中各运行一行）：
%   前置（脚本已归入 scripts/ASL/）：
%       addpath('L:/00used/wxt_ASL/scripts/ASL'); addpath('L:/00used/wxt_ASL')
%   然后：
%   run_worker(1)                 % 结构阶段 worker 1（phase='structural', nWorkers=6）
%   run_worker(2)  ... run_worker(6)
%   run_worker(1, 'asl')          % ASL 阶段 worker 1
%   run_worker(1, 'population')   % 群组阶段（单窗口，nWorkers 自动=1）
%   run_worker(1, 'structural', true)  % 仅预检（dryRun），不处理
%
% 说明：本机 24 物理核 / 128GB 内存，推荐 6 窗口并行。
%   - structural / asl 阶段：nWorkers=6, iWorker=1..6
%   - population 阶段：nWorkers=1, iWorker=1（单窗口）
%
% 断点续传：任何窗口中断后，用相同 iWorker 重跑本命令即可继续。

    if nargin < 1 || isempty(k)
        error('run_worker: 需要 worker 编号 k (1..6)');
    end
    if nargin < 2 || isempty(phaseName)
        phaseName = 'structural';
    end

    % 环境与路径 (脚本位于 scripts/ASL/)
    THIS = fileparts(mfilename('fullpath'));       % = .../scripts/ASL
    ROOT = fileparts(fileparts(THIS));             % = study root
    addpath('D:/0share/0matelab_scripts/spm12');
    addpath(THIS);                                 % run_ExploreASL / config
    addpath(fullfile(ROOT,'scripts','diffusion')); % 弥散脚本 (按需)
    addpath(ROOT);
    cd(ROOT);

    % 并行参数：population 必须单窗口，其余按 6 窗口
    if strcmpi(phaseName, 'population')
        nWorkers = 1;
        iWorker  = 1;
    else
        nWorkers = 6;
        iWorker  = k;
    end

    % 可选 dryRun
    if nargin >= 3 && ~isempty(doDry)
        dryRun = logical(doDry); %#ok<NASGU>
    end

    fprintf('\n=== run_worker: phase=%s, nWorkers=%d, iWorker=%d ===\n', ...
        phaseName, nWorkers, iWorker);

    phase = phaseName;   %#ok<NASGU>  % run_ExploreASL 脚本从本工作区读取
    run_ExploreASL
end
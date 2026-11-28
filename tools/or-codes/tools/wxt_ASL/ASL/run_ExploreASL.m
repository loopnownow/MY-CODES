%% =========================================================================
% run_ExploreASL.m -- ExploreASL 1.11.0 reprocessing main controller
%
% Configuration:
%   config.m is the default configuration.
%   Workspace variables, when explicitly defined BEFORE calling this script,
%   override config.m:
%       phase, dryRun, nWorkers, iWorker
%
% Examples:
%   run_ExploreASL
%   phase = 'structural'; run_ExploreASL
%   nWorkers = 6; iWorker = 1; run_ExploreASL
%   dryRun = true; run_ExploreASL
%
% Safety principles:
%   1) phase=structural/asl/population NEVER archives an existing output.
%   2) archiveOld is only honored by import/all.
%   3) module-level skipping is based on subject-level 999_ready.status.
%   4) ExploreASL itself remains responsible for subject distribution in
%      multi-window mode (iWorker/nWorkers).
% =========================================================================

%% 0. Load defaults, then apply explicit workspace overrides
cfg = config();

if exist('phase','var') && ~isempty(phase)
    cfg.phase = lower(char(phase));
else
    cfg.phase = 'all';
end

if exist('dryRun','var') && ~isempty(dryRun)
    cfg.dryRun = toLogicalScalar(dryRun, 'dryRun');
end

if exist('nWorkers','var') && ~isempty(nWorkers)
    cfg.nWorkers = nWorkers;
end

if exist('iWorker','var') && ~isempty(iWorker)
    cfg.iWorker = iWorker;
end

if strcmp(cfg.phase, 'dryrun')
    cfg.phase = 'all';
    cfg.dryRun = true;
end

cfg = validateConfig(cfg);

fprintf('\n==========================================================\n');
fprintf('ExploreASL 重新处理 | 阶段=%s | worker=%d/%d | dryRun=%d\n', ...
    cfg.phase, cfg.iWorker, cfg.nWorkers, cfg.dryRun);
fprintf('==========================================================\n');

%% 1. Logging
if exist(cfg.logDir,'dir') ~= 7
    mkdir(cfg.logDir);
end

logFile = fullfile(cfg.logDir, sprintf( ...
    'run_%s_w%d_%s.log', cfg.phase, cfg.iWorker, datestr(now,'yyyymmdd_HHMMSS')));

origDir = pwd;
diaryCleanup = onCleanup(@() cleanupRun(origDir)); %#ok<NASGU>

diary(logFile);
diary on;

%% 2. Main execution
try
    env = checkEnv(cfg);
    cfg.nASLFiles = env.nASLFiles;

    if env.fatal
        error('run_ExploreASL:EnvironmentCheckFailed', ...
            '环境检查未通过。请根据上方错误信息修正后重试。');
    end

    printPlan(cfg, env);

    if cfg.dryRun
        fprintf('[DRYRUN] 仅打印计划，未执行任何 ExploreASL 处理。\n');
        return;
    end

    switch cfg.phase
        case 'import'
            doImport(cfg, env);

        case {'structural','asl','population'}
            doProcess(cfg, env);

        case 'all'
            doImport(cfg, env);
            doProcess(cfg, env);
    end

    printSummary(cfg, env);

catch ME
    fprintf(2, '\n==========================================================\n');
    fprintf(2, 'RUN FAILED: %s\n', ME.identifier);
    fprintf(2, '%s\n', ME.message);
    fprintf(2, '%s\n', getReport(ME,'extended','hyperlinks','off'));
    fprintf(2, '==========================================================\n');
    rethrow(ME);
end

%% =========================================================================
%% Local functions
%% =========================================================================

function cfg = validateConfig(cfg)
    validPhase = {'all','import','structural','asl','population'};
    if ~ismember(cfg.phase, validPhase)
        error('run_ExploreASL:InvalidPhase', ...
            'phase 必须是: all / import / structural / asl / population');
    end

    if ~isscalar(cfg.nWorkers) || ~isnumeric(cfg.nWorkers) || ...
            ~isfinite(cfg.nWorkers) || cfg.nWorkers < 1 || cfg.nWorkers ~= round(cfg.nWorkers)
        error('run_ExploreASL:InvalidWorkers', 'nWorkers 必须为 >=1 的整数。');
    end

    if ~isscalar(cfg.iWorker) || ~isnumeric(cfg.iWorker) || ...
            ~isfinite(cfg.iWorker) || cfg.iWorker < 1 || ...
            cfg.iWorker > cfg.nWorkers || cfg.iWorker ~= round(cfg.iWorker)
        error('run_ExploreASL:InvalidWorkerIndex', ...
            'iWorker 必须为 1..nWorkers 范围内的整数。');
    end

    cfg.doImport     = toLogicalScalar(cfg.doImport,     'doImport');
    cfg.doStructural = toLogicalScalar(cfg.doStructural, 'doStructural');
    cfg.doASL        = toLogicalScalar(cfg.doASL,        'doASL');
    cfg.doPopulation = toLogicalScalar(cfg.doPopulation, 'doPopulation');
    cfg.archiveOld   = toLogicalScalar(cfg.archiveOld,   'archiveOld');
    cfg.forceReimport= toLogicalScalar(cfg.forceReimport,'forceReimport');
    cfg.cleanStart   = toLogicalScalar(cfg.cleanStart,   'cleanStart');
    cfg.dryRun       = toLogicalScalar(cfg.dryRun,       'dryRun');

    if ~isscalar(cfg.Quality) || ~ismember(cfg.Quality,[0 1])
        error('run_ExploreASL:InvalidQuality','Quality 必须为 0 或 1。');
    end
    if ~isscalar(cfg.DELETETEMP) || ~ismember(cfg.DELETETEMP,[0 1])
        error('run_ExploreASL:InvalidDeleteTemp','DELETETEMP 必须为 0 或 1。');
    end

    if ~isfinite(cfg.stopAfterErrors) && ~isinf(cfg.stopAfterErrors)
        error('run_ExploreASL:InvalidStopAfterErrors', ...
            'stopAfterErrors 必须为有限非负整数或 Inf。');
    end
    if isfinite(cfg.stopAfterErrors) && ...
            (cfg.stopAfterErrors < 0 || cfg.stopAfterErrors ~= round(cfg.stopAfterErrors))
        error('run_ExploreASL:InvalidStopAfterErrors', ...
            'stopAfterErrors 必须为非负整数或 Inf。');
    end

    if cfg.cleanStart && strcmp(cfg.phase,'import')
        error('run_ExploreASL:InvalidCleanStart', ...
            'cleanStart 不应与 import 阶段组合。');
    end

    if cfg.nWorkers > 1 && ismember(cfg.phase,{'all','import'})
        error('run_ExploreASL:ParallelImportForbidden', ...
            ['nWorkers>1 时禁止 phase=all/import，避免多个 MATLAB 实例同时执行 BIDS2LEGACY。 ' ...
             '请先单实例完成 import，再用 structural/asl/population 做多窗口并行。']);
    end

    if cfg.cleanStart && cfg.nWorkers > 1
        error('run_ExploreASL:ParallelCleanStartForbidden', ...
            'nWorkers>1 时禁止 cleanStart=true；请先单实例清理，再启动并行处理。');
    end
end

function b = toLogicalScalar(x, name)
    if islogical(x) && isscalar(x)
        b = x;
        return;
    end
    if isnumeric(x) && isscalar(x) && isfinite(x) && ismember(x,[0 1])
        b = logical(x);
        return;
    end
    if ischar(x) || (isstring(x) && isscalar(x))
        sx = lower(strtrim(char(x)));
        if any(strcmp(sx,{'true','1','yes','on'}));  b = true;  return; end
        if any(strcmp(sx,{'false','0','no','off'})); b = false; return; end
    end
    error('run_ExploreASL:InvalidLogical', '%s 必须是 true/false 或 0/1。', name);
end

function env = checkEnv(cfg)
    env = struct( ...
        'fatal',false, ...
        'nSubjects',0, ...
        'nT1Files',0, ...
        'nFLAIRFiles',0, ...
        'nASLFiles',0, ...
        'nM0Files',0, ...
        'oldExists',false, ...
        'hasLegacy',false, ...
        'hasMarker',false, ...
        'exploreASLVersion','unknown');

    % ExploreASL
    if exist(cfg.ExploreASLpath,'dir') ~= 7
        fprintf(2,'错误: ExploreASL 目录不存在: %s\n',cfg.ExploreASLpath);
        env.fatal = true; return;
    end

    masterFile = fullfile(cfg.ExploreASLpath,'ExploreASL.m');
    if exist(masterFile,'file') ~= 2
        fprintf(2,'错误: 缺少 ExploreASL.m (master): %s\n',masterFile);
        env.fatal = true; return;
    end

    vList = dir(fullfile(cfg.ExploreASLpath,'VERSION*'));
    if ~isempty(vList)
        env.exploreASLVersion = vList(1).name;
        fprintf('ExploreASL: %s (%s)\n',cfg.ExploreASLpath,vList(1).name);
        if ~strcmpi(vList(1).name,'VERSION_1.11.0')
            fprintf(2,'警告: 当前目录未检测到 VERSION_1.11.0，请确认实际版本。\n');
        end
    else
        fprintf(2,'警告: 未找到 VERSION* 文件，无法自动核对 ExploreASL 版本。\n');
    end

    % Dataset
    if exist(cfg.StudyRoot,'dir') ~= 7
        fprintf(2,'错误: 数据集根目录不存在: %s\n',cfg.StudyRoot);
        env.fatal = true; return;
    end

    if exist(cfg.RawData,'dir') ~= 7
        fprintf(2,'错误: rawdata 目录不存在: %s\n',cfg.RawData);
        env.fatal = true; return;
    end

    if exist(fullfile(cfg.RawData,'dataset_description.json'),'file') ~= 2
        fprintf(2,'警告: rawdata 缺少 dataset_description.json。\n');
    end

    % Subject scan: only directories matching the configured regexp
    dSub = dir(fullfile(cfg.RawData,'sub-*'));
    for i = 1:numel(dSub)
        if ~dSub(i).isdir || strcmp(dSub(i).name,'.') || strcmp(dSub(i).name,'..')
            continue;
        end

        if isempty(regexp(dSub(i).name,cfg.subjectRegexp,'once'))
            continue;
        end

        env.nSubjects = env.nSubjects + 1;
        anatDir = fullfile(cfg.RawData,dSub(i).name,'anat');

        if exist(anatDir,'dir') ~= 7
            continue;
        end

        fList = dir(fullfile(anatDir,'*.nii*'));
        for j = 1:numel(fList)
            nm = lower(fList(j).name);
            if contains(nm,'t1w')
                env.nT1Files = env.nT1Files + 1;
            end
            if contains(nm,'flair')
                env.nFLAIRFiles = env.nFLAIRFiles + 1;
            end
        end

        % ASL/M0 may be in perf or other BIDS modality directories.
        perfDir = fullfile(cfg.RawData,dSub(i).name,'perf');
        if exist(perfDir,'dir') == 7
            pList = dir(fullfile(perfDir,'*.nii*'));
            for j = 1:numel(pList)
                nm = lower(pList(j).name);
                if contains(nm,'asl')
                    env.nASLFiles = env.nASLFiles + 1;
                end
                if contains(nm,'m0')
                    env.nM0Files = env.nM0Files + 1;
                end
            end
        end
    end

    fprintf('rawdata: %d 位受试者, T1w文件=%d, FLAIR文件=%d, ASL文件=%d, M0文件=%d\n', ...
        env.nSubjects,env.nT1Files,env.nFLAIRFiles,env.nASLFiles,env.nM0Files);

    if env.nSubjects == 0
        fprintf(2,'错误: rawdata 中未找到符合 subjectRegexp 的受试者。\n');
        env.fatal = true; return;
    end

    if cfg.doStructural && env.nT1Files == 0
        fprintf(2,'错误: structural 已启用，但未检测到 T1w NIfTI。\n');
        env.fatal = true; return;
    end

    if cfg.doASL && env.nASLFiles == 0
        fprintf('提示: 未检测到 ASL 数据；本次将自动移除 ASL 模块，不调用 ASL processing。\n');
    end

    env.oldExists = exist(cfg.outRoot,'dir') == 7;
    env.hasLegacy = countLegacySubjects(cfg) > 0;
    env.hasMarker = exist(cfg.markerDir,'dir') == 7;

    if env.oldExists
        fprintf('输出目录已存在: %s (受试者=%d, 本脚本标记=%d)\n', ...
            cfg.outRoot,env.hasLegacy,env.hasMarker);
    end
end

function n = countLegacySubjects(cfg)
    n = 0;
    if exist(cfg.outRoot,'dir') ~= 7; return; end
    d = dir(fullfile(cfg.outRoot,'sub-*'));
    for i = 1:numel(d)
        if d(i).isdir && ~strcmp(d(i).name,'.') && ~strcmp(d(i).name,'..')
            n = n + 1;
        end
    end
end

function names = getLegacySubjects(cfg)
    names = {};
    if exist(cfg.outRoot,'dir') ~= 7; return; end
    d = dir(fullfile(cfg.outRoot,'sub-*'));
    for i = 1:numel(d)
        if d(i).isdir && ~strcmp(d(i).name,'.') && ~strcmp(d(i).name,'..')
            names{end+1} = d(i).name; %#ok<AGROW>
        end
    end
end

function nReady = countReadySubjects(cfg,moduleName)
    subjects = getLegacySubjects(cfg);
    nReady = 0;

    lockBase = fullfile(cfg.outRoot,'lock',['xASL_module_' moduleName]);
    if exist(lockBase,'dir') ~= 7; return; end

    for i = 1:numel(subjects)
        subj = subjects{i};
        statusFile = fullfile(lockBase,subj,['xASL_module_' moduleName], ...
            '999_ready.status');
        if exist(statusFile,'file') == 2
            nReady = nReady + 1;
        end
    end
end

function printPlan(cfg,env)
    fprintf('\n------------------------ 执行计划预览 ------------------------\n');
    fprintf('  数据集根目录 : %s\n',cfg.StudyRoot);
    fprintf('  ExploreASL   : %s\n',cfg.ExploreASLpath);
    fprintf('  rawdata      : %d 位受试者 | T1=%d | FLAIR=%d | ASL=%d | M0=%d\n', ...
        env.nSubjects,env.nT1Files,env.nFLAIRFiles,env.nASLFiles,env.nM0Files);
    fprintf('  输出目录     : %s\n',cfg.outRoot);

    if env.oldExists
        if ismember(cfg.phase,{'all','import'}) && cfg.archiveOld && ~env.hasMarker
            fprintf('  旧输出目录   : 存在且无 .myrun；import/all 时将归档。\n');
        elseif env.hasMarker
            fprintf('  旧输出目录   : 有 .myrun；默认按本脚本管理的结果续传。\n');
        else
            fprintf('  旧输出目录   : 存在；当前处理阶段不会自动归档。\n');
        end
    else
        fprintf('  旧输出目录   : 不存在。\n');
    end

    lst = {};
    planPhase = cfg.phase;

    if cfg.doImport && ismember(planPhase,{'all','import'})
        lst{end+1} = 'Import(BIDS2LEGACY)'; %#ok<AGROW>
    end
    if cfg.doStructural && ismember(planPhase,{'all','structural'})
        lst{end+1} = 'Structural'; %#ok<AGROW>
    end
    if cfg.doASL && env.nASLFiles > 0 && ismember(planPhase,{'all','asl'})
        lst{end+1} = 'ASL'; %#ok<AGROW>
    elseif cfg.doASL && env.nASLFiles == 0 && ismember(planPhase,{'all','asl'})
        lst{end+1} = 'ASL[跳过:无ASL数据]'; %#ok<AGROW>
    end
    if cfg.doPopulation && ismember(planPhase,{'all','population'})
        lst{end+1} = 'Population'; %#ok<AGROW>
    end

    fprintf('  将执行阶段   : ');
    if isempty(lst)
        fprintf('(本次无模块执行)\n');
    else
        fprintf('%s\n',strjoin(lst,'  ->  '));
    end

    fprintf('  并行模式     : %d 个 MATLAB/ExploreASL 实例 | worker=%d\n', ...
        cfg.nWorkers,cfg.iWorker);

    fprintf('  续传机制     : ExploreASL 原生 999_ready.status + 本脚本模块级检查\n');

    fprintf('  调用预览     :\n');
    if cfg.doImport && ismember(planPhase,{'all','import'})
        fprintf('    (import 阶段在本版本为空操作；BIDS2LEGACY 在 Process 内自动执行)\n');
    end

    modVec = [cfg.doStructural, ...
              cfg.doASL && env.nASLFiles > 0, ...
              cfg.doPopulation];

    if ismember(planPhase,{'all','structural','asl','population'})
        switch planPhase
            case 'structural'; modVec = [1 0 0];
            case 'asl';        modVec = [0 env.nASLFiles > 0 0];
            case 'population'; modVec = [0 0 1];
        end

        if any(modVec)
            fprintf('    ExploreASL(''%s'',[0 0 0],[%d %d %d],false,%d,%d)\n', ...
                cfg.StudyRoot,modVec(1),modVec(2),modVec(3), ...
                cfg.iWorker,cfg.nWorkers);
        end
    end

    fprintf('----------------------------------------------------------------\n');
end

function doImport(cfg,env)
    %#ok<INUSD>
    % IMPORTANT: automatic archiving is allowed only here.
    if env.oldExists && ~env.hasMarker && cfg.archiveOld
        if isDirEmpty(cfg.outRoot)
            rmdir(cfg.outRoot,'s');
            fprintf('[归档] 检测到空输出目录，已清理。\n');
        else
            backupDir = sprintf('%s_backup_%s',cfg.outRoot,datestr(now,'yyyymmdd_HHMMSS'));
            fprintf('[归档] %s -> %s\n',cfg.outRoot,backupDir);

            [ok,msg] = movefile(cfg.outRoot,backupDir);
            if ~ok
                error('run_ExploreASL:ArchiveFailed', ...
                    '无法归档旧输出目录: %s',msg);
            end
            fprintf('[归档] 完成: %s\n',backupDir);
        end
    end

    % Re-evaluate output state after possible archive.
    nLegacy = countLegacySubjects(cfg);
    if nLegacy > 0 && ~cfg.forceReimport
        fprintf('[导入] 输出目录已有 %d 个受试者，跳过 BIDS2LEGACY。\n',nLegacy);
        return;
    end

    addpath(cfg.ExploreASLpath);

    fprintf('\n------------------- 阶段导入: BIDS2LEGACY -------------------\n');
    % NOTE (ExploreASL 1.11.0 snapshot): BIDS2LEGACY is NOT an import
    % sub-module in this version - it runs as the FIRST step of the PROCESS
    % phase. There is no standalone BIDS2LEGACY. Therefore phase='import' is a
    % no-op here; run phase='all' (or a specific module) which will execute
    % BIDS2LEGACY automatically before processing.
    fprintf(['[导入] 本版本 BIDS2LEGACY 在 Process 阶段自动执行；' ...
             'phase=''import'' 为空操作。请使用 phase=''all''。\n']);

    ensureMarker(cfg);
end

function doProcess(cfg,env)
    %#ok<INUSD>
    % In this ExploreASL version BIDS2LEGACY runs as the FIRST step of the
    % PROCESS call, so legacy subjects and dataPar.json are created HERE.
    nSubj = countLegacySubjects(cfg);
    if nSubj > 0
        fprintf('[处理] 现有 legacy 受试者: %d\n', nSubj);
    else
        fprintf('[处理] 尚无 legacy 数据；本次调用将先执行 BIDS2LEGACY。\n');
    end

    % cleanStart is explicit and destructive (only meaningful with legacy data).
    if cfg.cleanStart && nSubj > 0
        fprintf('[处理] cleanStart=true：删除输出目录中的处理产物，保留 dataPar.json 与 .myrun。\n');
        cleanOutputForRestart(cfg);
    end

    addpath(cfg.ExploreASLpath);

    modVec = [cfg.doStructural, ...
              cfg.doASL && env.nASLFiles > 0, ...
              cfg.doPopulation];

    switch cfg.phase
        case 'structural'; modVec = [1 0 0];
        case 'asl';        modVec = [0 env.nASLFiles > 0 0];
        case 'population'; modVec = [0 0 1];
    end

    modNames = {'Structural','ASL','Population'};

    for i = 1:3
        if ~modVec(i); continue; end

        nReady = countReadySubjects(cfg,modNames{i});

        % Only skip automatically in single-worker mode, and only when there
        % are legacy subjects already (nSubj>0); otherwise modVec would be
        % zeroed out on a fresh run before BIDS2LEGACY has created them.
        if cfg.nWorkers == 1 && nSubj > 0 && nReady >= nSubj
            fprintf('[续传] %s 已完成 %d/%d，跳过。\n', ...
                modNames{i},nReady,nSubj);
            modVec(i) = 0;
        else
            fprintf('[续传] %s：ready=%d/%d；交给 ExploreASL 继续处理。\n', ...
                modNames{i},nReady,nSubj);
        end
    end

    if ~any(modVec)
        fprintf('[处理] 所选模块均无需继续运行。\n');
        return;
    end

    fprintf('\n------------------- 阶段处理 -------------------\n');
    fprintf('  modules = [%d %d %d], worker=%d/%d\n', ...
        modVec(1),modVec(2),modVec(3),cfg.iWorker,cfg.nWorkers);

    callExploreASLMaster(cfg,[0 0 0],modVec);

    % ExploreASL creates dataPar.json during BIDS2LEGACY; apply user settings
    % for subsequent runs (guarded - do not fail if absent yet).
    if exist(fullfile(cfg.outRoot,'dataPar.json'),'file') == 2
        try
            applyDataParSettings(cfg);
        catch ME
            fprintf(2,'[处理] applyDataParSettings 跳过: %s\n',ME.message);
        end
    end

    fprintf('[处理] ExploreASL processing 返回。\n');
end

function callExploreASLMaster(cfg,importVec,procVec)
    % ExploreASL 1.11.0 (this snapshot) requires its own installation folder
    % as the working directory, otherwise xASL_init_GetMyPath cannot locate
    % ExploreASL.m (the folder name 'ExploreASL-main' does not match the
    % 'ExploreASL$' regexp) and ExploreASL falls back to an interactive
    % input() prompt, which crashes under MATLAB -batch.
    % Use the documented 'ExploreASL' entry (consistent bImport/bProcess
    % convention); BIDS2LEGACY runs inside ExploreASL_Process for this version.
    origDir = pwd;
    cleaner = onCleanup(@() cd(origDir)); %#ok<NASGU>
    cd(cfg.ExploreASLpath);
    ExploreASL(cfg.StudyRoot,importVec,procVec,false, ...
        cfg.iWorker,cfg.nWorkers);
end

function cleanOutputForRestart(cfg)
    dRoot = dir(cfg.outRoot);

    for i = 1:numel(dRoot)
        nm = dRoot(i).name;
        if strcmp(nm,'.') || strcmp(nm,'..'); continue; end
        if strcmpi(nm,'dataPar.json') || strcmp(nm,'.myrun'); continue; end

        p = fullfile(cfg.outRoot,nm);

        if dRoot(i).isdir
            [ok,msg] = rmdir(p,'s');
        else
            try
                delete(p);
                ok = exist(p,'file') ~= 2;
                msg = '';
            catch ME
                ok = false;
                msg = ME.message;
            end
        end

        if ~ok
            error('run_ExploreASL:CleanStartFailed', ...
                '无法删除 %s: %s',p,msg);
        end
    end
end

function applyDataParSettings(cfg)
% Update only settings controlled by this wrapper. Preserve all other fields
% generated by ExploreASL (e.g. sequence parameters and study-specific data).
    fjson = fullfile(cfg.outRoot,'dataPar.json');
    if exist(fjson,'file') ~= 2
        error('run_ExploreASL:MissingDataPar', ...
            'dataPar.json 不存在: %s',fjson);
    end

    if exist('jsondecode','builtin') ~= 5 && exist('jsondecode','file') ~= 2
        error('run_ExploreASL:JsonUnsupported', ...
            '当前 MATLAB 不支持 jsondecode；本流程要求 MATLAB R2016b+。');
    end

    txt = fileread(fjson);
    try
        x = jsondecode(txt);
    catch ME
        error('run_ExploreASL:InvalidDataPar', ...
            '无法解析 dataPar.json: %s\n%s',fjson,ME.message);
    end

    if ~isfield(x,'x') || ~isstruct(x.x)
        x.x = struct();
    end
    if ~isfield(x.x,'dataset') || ~isstruct(x.x.dataset)
        x.x.dataset = struct();
    end
    if ~isfield(x.x,'settings') || ~isstruct(x.x.settings)
        x.x.settings = struct();
    end

    x.x.dataset.subjectRegexp = cfg.subjectRegexp;
    x.x.settings.Quality = cfg.Quality;
    x.x.settings.DELETETEMP = cfg.DELETETEMP;

    % JSON has no Inf literal. Use a very large finite value for the
    % "effectively unlimited" setting.
    if isinf(cfg.stopAfterErrors)
        x.x.settings.stopAfterErrors = 999999;
    else
        x.x.settings.stopAfterErrors = cfg.stopAfterErrors;
    end

    % When ASL is absent, explicitly tell ExploreASL to skip ASL/M0-dependent
    % subjects. This is safer than entering the ASL module with empty input.
    if isfield(cfg,'_nASLFiles') && cfg.nASLFiles == 0
        x.x.settings.SkipIfNoASL = 1;
        x.x.settings.SkipIfNoM0 = 1;
    end

    % jsonencode exists in R2016b+ but pretty printing is not essential.
    out = jsonencode(x);

    fid = fopen(fjson,'w');
    if fid < 0
        error('run_ExploreASL:DataParWriteFailed', ...
            '无法写入 dataPar.json: %s',fjson);
    end
    cleaner = onCleanup(@() fclose(fid)); %#ok<NASGU>
    fprintf(fid,'%s\n',out);
end

function ensureMarker(cfg)
    if exist(cfg.outRoot,'dir') ~= 7
        mkdir(cfg.outRoot);
    end
    if exist(cfg.markerDir,'dir') ~= 7
        mkdir(cfg.markerDir);
    end

    fmark = fullfile(cfg.markerDir,'initialized.txt');
    if exist(fmark,'file') ~= 2
        fh = fopen(fmark,'w');
        if fh < 0
            warning('run_ExploreASL:MarkerWriteFailed', ...
                '无法写入标记文件: %s',fmark);
            return;
        end
        fprintf(fh,'created by run_ExploreASL at %s\n',datestr(now));
        fprintf(fh,'ExploreASL path: %s\n',cfg.ExploreASLpath);
        fclose(fh);
    end
end

function printSummary(cfg,env)
    fprintf('\n======================== 运行汇总 ========================\n');

    nSubj = countLegacySubjects(cfg);
    fprintf('  Legacy受试者数 : %d\n',nSubj);

    nT1 = 0;
    nWMH = 0;
    nErr = 0;

    dSub = dir(fullfile(cfg.outRoot,'sub-*'));
    for i = 1:numel(dSub)
        if ~dSub(i).isdir; continue; end

        sdir = fullfile(cfg.outRoot,dSub(i).name);

        if exist(fullfile(sdir,'T1.nii'),'file') == 2
            nT1 = nT1 + 1;
        elseif exist(fullfile(sdir,'T1.nii.gz'),'file') == 2
            nT1 = nT1 + 1;
        end

        if exist(fullfile(sdir,'WMH_SEGM.nii'),'file') == 2 || ...
                exist(fullfile(sdir,'WMH_SEGM.nii.gz'),'file') == 2
            nWMH = nWMH + 1;
        end

        if exist(fullfile(sdir,'err'),'dir') == 7
            nErr = nErr + 1;
        end
    end

    fprintf('  T1结果         : %d\n',nT1);
    fprintf('  WMH_SEGM结果   : %d\n',nWMH);
    fprintf('  err目录        : %d\n',nErr);

    tDir = fullfile(cfg.outRoot,'Population','TissueVolume');
    if exist(tDir,'dir') == 7
        fprintf('  TissueVolume TSV: %d\n', ...
            numel(dir(fullfile(tDir,'TissueVolume_sub-*.tsv'))));
        fprintf('  WMH_LST_LPA TSV : %d\n', ...
            numel(dir(fullfile(tDir,'WMH_LST_LPA_sub-*.tsv'))));
    end

    chk = {'T1Check','FLAIRCheck','ASLCheck','M0Check','MotionASL'};
    for i = 1:numel(chk)
        p = fullfile(cfg.outRoot,'Population',chk{i});
        if exist(p,'dir') == 7
            jpgs = dir(fullfile(p,'*.jpg'));
            pngs = dir(fullfile(p,'*.png'));
            fprintf('  QC %-12s: jpg=%d png=%d\n', ...
                chk{i},numel(jpgs),numel(pngs));
        end
    end

    fprintf('  rawdata受试者   : %d\n',env.nSubjects);
    fprintf('========================================================\n');
    fprintf('下一步：优先检查 Population/QC 图及 subject-level QC；确认配准、分割和WMH质量后，再导出统计表。\n');
end

function bEmpty = isDirEmpty(dirPath)
    bEmpty = false;
    if exist(dirPath,'dir') ~= 7; return; end

    d = dir(dirPath);
    for i = 1:numel(d)
        if strcmp(d(i).name,'.') || strcmp(d(i).name,'..'); continue; end
        bEmpty = false;
        return;
    end
    bEmpty = true;
end

function cleanupRun(origDir)
    try
        diary off;
    catch
    end

    try
        if exist(origDir,'dir') == 7
            cd(origDir);
        end
    catch
    end
end

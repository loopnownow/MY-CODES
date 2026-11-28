function cfg = config()
% =========================================================================
% config.m -- ExploreASL 1.11.0 reprocessing configuration
%
% This is the only file normally edited by the user.
% =========================================================================

    % ---------------- 1. Paths ----------------
    cfg.ExploreASLpath = 'D:\0share\0matelab_scripts\ExploreASL-main';
    cfg.StudyRoot      = 'L:\SSD\wxt_SSD';
    cfg.OutDirName     = 'ExploreASL';

    % ---------------- 2. Existing-result policy ----------------
    % IMPORTANT:
    %   archiveOld is honored only by phase=import/all.
    %   structural/asl/population never auto-archive an existing result.
    cfg.archiveOld     = true;
    cfg.forceReimport  = false;
    cfg.cleanStart     = false;

    % ---------------- 3. Modules ----------------
    cfg.doImport       = true;
    cfg.doStructural   = true;
    cfg.doASL          = true;     % Automatically skipped when no ASL NIfTI exists
    cfg.doPopulation   = true;

    % ---------------- 4. ExploreASL processing parameters ----------------
    cfg.subjectRegexp   = '^sub-.*$';
    cfg.Quality         = 1;
    cfg.DELETETEMP      = 1;
    cfg.stopAfterErrors = Inf;

    % ---------------- 5. External multi-window parallelization ----------------
    % One MATLAB window by default.
    %
    % For 6-window parallel processing:
    %   Window 1: nWorkers=6; iWorker=1; run_ExploreASL
    %   Window 2: nWorkers=6; iWorker=2; run_ExploreASL
    %   ...
    %
    % These can be supplied in the MATLAB workspace and override these defaults.
    cfg.nWorkers = 1;
    cfg.iWorker  = 1;

    % ---------------- 6. Dry run ----------------
    cfg.dryRun = false;

    % ---------------- 7. Derived paths ----------------
    % NOTE: 本文件位于 scripts/ASL/ 下, 但日志统一写到 study root 的 logs/
    cfg.thisDir   = fileparts(mfilename('fullpath'));
    cfg.logDir    = fullfile(cfg.StudyRoot,'logs');

    cfg.DerivRoot = fullfile(cfg.StudyRoot,'derivatives');
    cfg.outRoot   = fullfile(cfg.DerivRoot,cfg.OutDirName);
    cfg.RawData   = fullfile(cfg.StudyRoot,'rawdata');

    cfg.markerDir = fullfile(cfg.outRoot,'.myrun');
end

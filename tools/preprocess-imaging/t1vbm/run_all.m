function run_all(varargin)
%RUN_ALL  T1 VBM 主入口：状态驱动、断点续跑、失败节点停止。
validStep={'step1','step2','step3','step4'};from='step1';until='step4';cfgArgs={};i=1;
while i<=numel(varargin)
    v=varargin{i};
    if (ischar(v)||isstring(v))&&strcmpi(v,'from'),assert(i<numel(varargin),'from 缺少取值');from=char(varargin{i+1});i=i+2;
    elseif (ischar(v)||isstring(v))&&strcmpi(v,'until'),assert(i<numel(varargin),'until 缺少取值');until=char(varargin{i+1});i=i+2;
    else,assert(i<numel(varargin),'参数 "%s" 缺少配对值',string(v));cfgArgs(end+1:end+2)={v,varargin{i+1}};i=i+2;end
end
fromI=find(strcmpi(validStep,from),1);untilI=find(strcmpi(validStep,until),1);assert(~isempty(fromI)&&~isempty(untilI),'未知 from/until');assert(untilI>=fromI,'until 早于 from');
myDir=fileparts(which('run_all'));addpath(myDir);C=t1vbm_config(cfgArgs{:});t1vbm_env('mkdirs',C);
fprintf('\n########## T1 VBM %s ##########\n',datetime('now','Format','yyyy-MM-dd HH:mm:ss'));fprintf('模式=%s | nproc=%d | %s -> %s\n',ternary(C.dryrun,'DRYRUN','实跑'),C.nproc,from,until);

steps={@()t1vbm_step1_unzip(C),@()t1vbm_step2_cat12(C),@()t1vbm_step3_stats(C),@()t1vbm_step4_report(C)};
nm={'step1_unzip','step2_cat12','step3_stats','step4_report'};
state=loadState(C);t0=tic;
for k=fromI:untilI
    fprintf('\n========== [%d/4] %s ==========\n',k,nm{k});
    state=stateSet(state,nm{k},'running');saveState(C,state);
    try
        R=steps{k}(); %#ok<NASGU>
        state=stateSet(state,nm{k},'complete');state.(nm{k}).finished=char(datetime('now'));saveState(C,state);
    catch ME
        state=stateSet(state,nm{k},'failed');state.(nm{k}).error=ME.message;saveState(C,state);
        t1vbm_log(C,'run_all_err',sprintf('%s: %s',nm{k},ME.message));
        fprintf('\n!! %s 失败: %s\n',nm{k},ME.message);
        fprintf('状态已写入 %s；修复后运行 run_all(''from'',''%s'')。\n',fullfile(C.logDir,'pipeline_state.mat'),validStep{k});
        if C.stopOnStepFailure||~C.dryrun,rethrow(ME);end
    end
end
fprintf('\n########## 流程结束 %s~%s，用时 %.1f min ##########\n',from,until,toc(t0)/60);
end

function s=loadState(C)
f=fullfile(C.logDir,'pipeline_state.mat');
if exist(f,'file')==2,z=load(f,'state');s=z.state;else,s=struct();end
end
function s=stateSet(s,name,status),if ~isfield(s,name),s.(name)=struct();end;s.(name).status=status;s.(name).updated=char(datetime('now'));end
function saveState(C,s),if C.dryrun,return;end;if ~exist(C.logDir,'dir'),mkdir(C.logDir);end;state=s;save(fullfile(C.logDir,'pipeline_state.mat'),'state');end
function y=ternary(c,a,b),if c,y=a;else,y=b;end,end

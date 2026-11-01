function S=t1vbm_step2_cat12(C)
%T1VBM_STEP2_CAT12  CAT12 分割+归一化；以 completion marker + 多输出 QC 作为完成判定。
if nargin<1||isempty(C),C=t1vbm_config();end
t1vbm_env('mkdirs',C); t1vbm_env('banner',C,'step2  CAT12 分割 + 归一化'); t1vbm_env('checkspm',C);
M=t1vbm_manifest('load',C);
mriDir=fullfile(C.niiDir,'mri'); if ~C.dryrun && ~exist(mriDir,'dir'),mkdir(mriDir);end

todo={}; done=0; fail=0;
for i=1:height(M)
    p=pathsFromManifest(C,M(i,:)); marker=markerPath(C,M.Key(i));
    [ok,reason]=catOutputQC(p,C);
    if exist(marker,'file')==2 && ok
        done=done+1; M.CAT12Status(i)="complete"; M.Status(i)="cat12_complete";
        M=fillTiv(C,M,i,p.nii);
    elseif ok
        % 历史已有完整结果：补建 marker，允许安全续跑。
        if ~C.dryrun, writeMarker(marker,M.Subject(i)); end
        done=done+1; M.CAT12Status(i)="complete"; M.Status(i)="cat12_complete";
        M=fillTiv(C,M,i,p.nii);
    elseif exist(p.nii,'file')==2
        todo{end+1}=p.nii; %#ok<AGROW>
        M.CAT12Status(i)="pending"; M.Status(i)="cat12_pending";
        if reason~="pending", t1vbm_log(C,'step2_qc',sprintf('%s: %s',M.Subject(i),reason)); end
    else
        fail=fail+1; M.CAT12Status(i)="missing_input"; M.Status(i)="missing_input";
    end
end
if ~C.dryrun, M.Updated(:)=string(datetime('now','Format','yyyy-MM-dd HH:mm:ss')); saveManifestLocal(C,M); end
fprintf('CAT12 已完成 %d | 待处理 %d | 缺输入 %d | nproc=%d | batch=%d\n',done,numel(todo),fail,C.nproc,C.batchN);

if fail>0, error('step2: %d 个被试缺少解压后的 NIfTI，请先修复 step1。',fail); end
if isempty(todo)
    S=struct('n',height(M),'done',done,'todo',0,'batchRuns',0,'ok',done,'fail',0); return
end
effectiveBatchN=min(max(1,C.batchN),max(1,C.nproc));
if ~C.dryrun && C.nproc<1, error('step2: nproc 必须 >=1。建议先运行 t1vbm_calibrate(C)，再使用 suggestNproc。'); end
nB=ceil(numel(todo)/effectiveBatchN);
if C.dryrun
    for b=1:nB
        lo=(b-1)*effectiveBatchN+1;hi=min(b*effectiveBatchN,numel(todo));
        fprintf('[DRYRUN] 批次 %d/%d: %d-%d (%d例), nproc=%d\n',b,nB,lo,hi,hi-lo+1,C.nproc);
    end
    S=struct('n',height(M),'done',done,'todo',numel(todo),'batchRuns',nB,'ok',0,'fail',0);return
end
oldpwd=pwd; cleanup=onCleanup(@()cd(oldpwd)); %#ok<NASGU>
cd(C.root); okN=0; failN=0; failRows=strings(0,1);
for b=1:nB
    lo=(b-1)*effectiveBatchN+1;hi=min(b*effectiveBatchN,numel(todo));batch=todo(lo:hi);
    fprintf('\n[批次 %d/%d] nproc=%d  %d-%d (%d例)\n',b,nB,C.nproc,lo,hi,numel(batch));
    try
        if isfield(C.cat,'backgroundHidden') && C.cat.backgroundHidden
            runbatchHidden(C,batch,b);
        else
            runbatch(C,batch);
        end
        for j=1:numel(batch)
            % 关键：batch 中保存的是 manifest.NiiFile 的完整路径。
            % 不再从 NIfTI basename 重新生成 namekey；否则
            % `HUANG GAO LI_t1_mprage...` 会变成 `HUANGGAOLITMPRAGE...`，
            % 无法匹配 manifest 中的 `HUANGGAOLI`。
            idx=find(string(M.NiiFile)==string(batch{j}),1);
            if isempty(idx)
                failN=failN+1;
                failRows(end+1)=sprintf('UNMAPPED_BATCH_INPUT,manifest_row_not_found,%s',batch{j}); %#ok<AGROW>
                t1vbm_log(C,'step2_err',sprintf('batch %d: 无法将 CAT12 输入映射回 manifest: %s',b,batch{j}));
                continue
            end
            p=pathsFromManifest(C,M(idx,:)); marker=markerPath(C,M.Key(idx));
            [ok,reason]=catOutputQC(p,C);
            if ok
                writeMarker(marker,M.Subject(idx)); M.CAT12Status(idx)="complete"; M.Status(idx)="cat12_complete"; okN=okN+1;
                M=fillTiv(C,M,idx,p.nii);
            else
                M.CAT12Status(idx)="failed"; M.Status(idx)="cat12_failed"; failN=failN+1;
                failRows(end+1)=sprintf('%s,%s,%s',M.Subject(idx),reason,p.nii); %#ok<AGROW>
            end
        end
    catch ME
        failN=failN+numel(batch); failRows(end+1)=sprintf('BATCH_%d,%s,%s',b,ME.identifier,ME.message); %#ok<AGROW>
        t1vbm_log(C,'step2_err',sprintf('batch %d: %s',b,ME.message));
        % 单批失败后继续检查/进入下一批；最终以 manifest fail 状态阻止 step3。
        for j=1:numel(batch)
            idx=find(string(M.NiiFile)==string(batch{j}),1);
            if ~isempty(idx), M.CAT12Status(idx)="failed"; M.Status(idx)="cat12_failed"; end
        end
    end
    saveManifestLocal(C,M);
end
if ~isempty(failRows)
    fid=fopen(fullfile(C.logDir,'cat12_fail.csv'),'w','n','UTF-8');
    fprintf(fid,'SubjectOrBatch,Reason,Input\n%s\n',strjoin(failRows,newline));fclose(fid);
end
fprintf('\nCAT12 本次成功 %d | 失败 %d | 历史已完成 %d\n',okN,failN,done);
if failN>0, error('step2: CAT12 有 %d 例未通过输出 QC；详见 manifest 与 log/cat12_fail.csv。',failN);end
S=struct('n',height(M),'done',done,'todo',numel(todo),'batchRuns',nB,'ok',okN,'fail',failN);
end

function runbatch(C,files)
c=cat_get_defaults(); c.data=files(:); c.nproc=C.nproc;
c.output.surface=C.cat.surface;c.output.GM.mod=C.cat.GMmod;c.output.WM.mod=C.cat.WMmod;c.output.CSF.mod=C.cat.CSFmod;c.output.label.native=C.cat.labelNative;c.output.warps=C.cat.warps;
if isfield(C.cat,'redspmres')&&isfield(c.opts,'redspmres'),c.opts.redspmres=C.cat.redspmres;end
cat_run(c);
waitForSubjects(C,files);
end

function runbatchHidden(C,files,batchNo)
%RUNBATCHHIDDEN  Windows 隐藏 MATLAB worker：每个 worker 只处理 1 个 subject。
% 目的：避免 CAT12 内部 `start matlab` 打开可见 Command Window，同时保留并行。
statusDir=fullfile(C.logDir,'cat12_jobs',sprintf('batch_%03d',batchNo));
if ~exist(statusDir,'dir'),mkdir(statusDir);end
codeDir=fileparts(which('t1vbm_step2_cat12'));
spmDir=spm('dir'); catDir=fileparts(which('cat_get_defaults'));
ids=cell(numel(files),1);
for i=1:numel(files)
    [~,bn]=fileparts(files{i}); key=t1vbm_files('namekey',bn); ids{i}=char(key);
    % jobId 仅用于 worker 状态文件；manifest 回写必须使用 NiiFile 精确匹配。
    J=struct('jobId',ids{i},'subject',bn,'input',files{i},'statusDir',statusDir, ...
        'codeDir',codeDir,'spmDir',spmDir,'catDir',catDir,'C',C);
    jf=fullfile(statusDir,[ids{i} '.mat']); save(jf,'J');
    launchHiddenWorker(C,jf);
end
waitForHiddenWorkers(C,files,statusDir,ids);
end

function launchHiddenWorker(C,jobFile)
matlabExe=fullfile(matlabroot,'bin','matlab.exe');
assert(exist(matlabExe,'file')==2,'step2: 找不到 MATLAB executable: %s',matlabExe);
codeDir=fileparts(which('t1vbm_step2_cat12'));
jobFile=char(jobFile); codeDir=char(codeDir);
% CAT12 官方 Windows 并行实现使用 `start matlab`，因此会产生可见窗口。
% 这里改为 PowerShell Start-Process -WindowStyle Hidden，并让 worker 自己以 nproc=0 运行 CAT12。
matlabArg=sprintf("addpath(''%s'');t1vbm_cat_worker(''%s'')",escapePSSingle(codeDir),escapePSSingle(jobFile));
ps=sprintf("Start-Process -FilePath '%s' -ArgumentList @('-batch','%s') -WindowStyle Hidden", ...
    escapePSSingle(matlabExe),matlabArg);
cmd=sprintf('powershell.exe -NoProfile -ExecutionPolicy Bypass -WindowStyle Hidden -Command "%s"',ps);
[status,out]=system(cmd);
if status~=0
    error('step2:hidden_worker_launch_failed: %s\n%s',cmd,out);
end
end

function waitForHiddenWorkers(C,files,statusDir,ids)
n=numel(files); started=tic; lastDone=-1; maxWait=max(C.catTimeoutMin,ceil(n/max(1,C.nproc))*C.catTimeoutMin+C.catTimeoutExtraMin);
while true
    done=false(1,n); failed=false(1,n); running=false(1,n);
    for i=1:n
        done(i)=exist(fullfile(statusDir,[ids{i} '.done']),'file')==2;
        failed(i)=exist(fullfile(statusDir,[ids{i} '.fail']),'file')==2;
        running(i)=exist(fullfile(statusDir,[ids{i} '.running']),'file')==2;
    end
    nd=sum(done); nf=sum(failed); nr=sum(running); elapsed=toc(started)/60;
    if nd+nf>=n
        fprintf('后台 CAT12 批次结束: %d/%d 完成, %d 失败, %d 运行中。\n',nd,n,nf,nr);
        if nf>0
            bad=ids(failed); error('step2: %d 个隐藏 CAT12 worker 失败: %s',nf,strjoin(bad,', '));
        end
        return
    end
    if elapsed>=maxWait
        error('step2: 隐藏 CAT12 批次超时(%d min): %d 完成, %d 失败, %d 运行中。',maxWait,nd,nf,nr);
    end
    if nd~=lastDone
        fprintf('后台监督: %d/%d 完成 | %d 运行中 | %d 失败 | %.1f min / %d min\n',nd,n,nr,nf,elapsed,maxWait); lastDone=nd;
    end
    pause(C.catPollSec);
end
end

function s=escapePSSingle(s)
s=strrep(char(s),'''','''''');
end

function waitForSubjects(C,files)
target=cell(numel(files),1);
for i=1:numel(files),[~,b]=fileparts(files{i});target{i}=fullfile(C.niiDir,'mri',['mwp1' b]);end
slots=max(1,C.nproc); rounds=ceil(numel(target)/slots);
maxWait=max(C.catTimeoutMin,rounds*C.catTimeoutMin+C.catTimeoutExtraMin); waited=0; lastDone=-1;
while true
    done=sum(cellfun(@(f)exist(f,'file')==2,target));
    if done>=numel(target),fprintf('批次监督: %d/%d 完成。\n',done,numel(target));return;end
    if waited>=maxWait
        miss=target(~cellfun(@(f)exist(f,'file')==2,target));
        error('step2: CAT12 批次超时(%d min)，%d 例未生成 mwp1:\n%s',maxWait,numel(miss),strjoin(miss,newline));
    end
    if done~=lastDone || mod(floor(waited*60/C.catPollSec),4)==0
        fprintf('批次监督: %d/%d 完成，已 %.1f min / 预算 %d min\n',done,numel(target),waited,maxWait);lastDone=done;
    end
    pause(C.catPollSec);waited=waited+C.catPollSec/60;
end
end

function [ok,reason]=catOutputQC(p,C)
req={p.mwp1,p.mwp2,p.warp}; ok=true;reason="complete";
for i=1:numel(req)
    d=dir(req{i}); if isempty(d)||d.bytes<=0,ok=false;reason="missing_or_empty_output";return;end
end
end

function p=pathsFromManifest(C,row)
p.nii=char(row.NiiFile);p.mwp1=char(row.Mwp1File);p.mwp2=char(row.Mwp2File);p.warp=char(row.WarpFile);
end
function f=markerPath(C,key),f=fullfile(C.manifestDir,'cat12_done',[char(key) '.done']);end
function writeMarker(f,subject)
d=fileparts(f);if ~exist(d,'dir'),mkdir(d);end
fid=fopen(f,'w','n','UTF-8');if fid>0,fprintf(fid,'Subject: %s\nCompleted: %s\n',char(subject),char(datetime('now')));fclose(fid);end
end
function saveManifestLocal(C,M)
if C.dryrun,return;end
save(fullfile(C.manifestDir,'subject_manifest.mat'),'M');writetable(M,fullfile(C.manifestDir,'subject_manifest.csv'));
end
function M=fillTiv(C,M,idx,niiFile)
if ~ismember('TIV',M.Properties.VariableNames)
    M.TIV=nan(height(M),1);
end
tiv=t1vbm_files('tiv',niiFile);
if isfinite(tiv)
    M.TIV(idx)=tiv;
elseif ~isfinite(M.TIV(idx))
    t1vbm_log(C,'step2_tiv',sprintf('%s: 未读到 TIV（cat_*.mat / cat_*.xml）',M.Subject(idx)));
end
end

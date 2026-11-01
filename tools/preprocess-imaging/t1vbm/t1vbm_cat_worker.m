function t1vbm_cat_worker(jobFile)
%T1VBM_CAT_WORKER  单被试 CAT12 worker；由 PowerShell 隐藏启动。
% 每个 worker 只处理 1 个 subject，因此 CAT12 不再创建二级并行 MATLAB。
assert(exist(jobFile,'file')==2,'t1vbm_cat_worker: 找不到 job 文件: %s',jobFile);
J=load(jobFile,'J'); J=J.J;
statusDir=J.statusDir;
if ~exist(statusDir,'dir'),mkdir(statusDir);end
runFile=fullfile(statusDir,[J.jobId '.running']); doneFile=fullfile(statusDir,[J.jobId '.done']); failFile=fullfile(statusDir,[J.jobId '.fail']);
writeStatus(runFile,J,'running','');
try
    addpath(J.codeDir);
    if ~isempty(J.spmDir), addpath(J.spmDir); addpath(fullfile(J.spmDir,'toolbox','OldNorm')); addpath(fullfile(J.spmDir,'toolbox','DARTEL')); end
    if ~isempty(J.catDir), addpath(J.catDir); end
    spm12def=spm_get_defaults; cat12def=cat_get_defaults;
    global defaults cat; defaults=spm12def; cat=cat12def;
    c=cat_get_defaults();
    c.data={J.input};
    c.nproc=0;
    c.output.surface=J.C.cat.surface;
    c.output.GM.mod=J.C.cat.GMmod;
    c.output.WM.mod=J.C.cat.WMmod;
    c.output.CSF.mod=J.C.cat.CSFmod;
    c.output.label.native=J.C.cat.labelNative;
    c.output.warps=J.C.cat.warps;
    if isfield(J.C.cat,'redspmres') && isfield(c.opts,'redspmres'), c.opts.redspmres=J.C.cat.redspmres; end
    cat_run(c);
    writeStatus(doneFile,J,'done','');
    if exist(runFile,'file'),delete(runFile);end
catch ME
    writeStatus(failFile,J,'failed',getReport(ME,'extended','hyperlinks','off'));
    if exist(runFile,'file'),delete(runFile);end
    try
        fid=fopen(fullfile(J.C.logDir,['cat12_worker_' J.jobId '.log']),'a','n','UTF-8');
        if fid>0, fprintf(fid,'%s\n',getReport(ME,'extended','hyperlinks','off')); fclose(fid); end
    catch, end
end
end

function writeStatus(f,J,status,msg)
fid=fopen(f,'w','n','UTF-8');
if fid<0, return; end
fprintf(fid,'Subject: %s\nStatus: %s\nTime: %s\nInput: %s\n',J.subject,status,char(datetime('now','Format','yyyy-MM-dd HH:mm:ss')),J.input);
if ~isempty(msg), fprintf(fid,'Message:\n%s\n',msg); end
fclose(fid);
end

function S=t1vbm_calibrate(C)
%T1VBM_CALIBRATE  单例实测峰值内存/耗时，并给出保守 nproc 建议；结果持久化。
if nargin<1||isempty(C),C=t1vbm_config();end
t1vbm_env('mkdirs',C);t1vbm_env('banner',C,'calibrate  单例 CAT12 校准');t1vbm_env('checkspm',C);
subs=t1vbm_files('scan',C);assert(~isempty(subs),'calibrate: 无可用被试，请先跑 step1');
k=min(max(1,C.calibN),numel(subs));[~,b]=fileparts(char(subs(k).file));img=fullfile(C.niiDir,b);assert(exist(img,'file')==2,'calibrate: 未找到 %s',img);
try,mm=memory;totGB=mm.PhysicalMemory.Total/2^30;catch,totGB=NaN;end
try,nCore=feature('numcores');catch,nCore=1;end
fprintf('本机: %d 物理核, %.1f GB RAM | 校准被试: %s\n',nCore,totGB,subs(k).Patient_ID);
if C.dryrun,S=struct('peakGB',NaN,'minutes',NaN,'suggestNproc',NaN,'img',img);return;end
c=cat_get_defaults();c.data={img};c.nproc=0;c.output.surface=C.cat.surface;c.output.GM.mod=C.cat.GMmod;c.output.WM.mod=C.cat.WMmod;c.output.CSF.mod=C.cat.CSFmod;c.output.label.native=C.cat.labelNative;c.output.warps=C.cat.warps;
if isfield(C.cat,'redspmres')&&isfield(c.opts,'redspmres'),c.opts.redspmres=C.cat.redspmres;end
oldpwd=pwd;cleanup=onCleanup(@()cd(oldpwd)); %#ok<NASGU>
cd(C.root);t0=tic;cat_run(c);mins=toc(t0)/60;peakGB=getpeakGB();
budgetGB=0.75*totGB;byMem=floor(budgetGB/max(peakGB,1.5));suggest=max(1,min([byMem,nCore,C.catMaxNproc]));
if ~isfinite(byMem),suggest=max(1,min(nCore,C.catMaxNproc));end
S=struct('peakGB',peakGB,'minutes',mins,'suggestNproc',suggest,'nCore',nCore,'totGB',totGB,'img',img,'timestamp',char(datetime('now')));
if ~C.dryrun,save(fullfile(C.logDir,'calibration.mat'),'S');end
fprintf('\n校准: peak=%.2f GB | %.1f min/例 | 建议 nproc=%d\n',peakGB,mins,suggest);
end
function gb=getpeakGB()
gb=NaN;try,p=System.Diagnostics.Process.GetCurrentProcess();gb=double(p.PeakWorkingSet64)/2^30;return;catch,end
try,[~,o]=system('tasklist /fi "imagename eq MATLAB.exe" /fo csv /nh');tok=regexp(o,'"MATLAB[^,]*","[^"]*","[^"]*","([0-9,]+) K"','tokens','once');if ~isempty(tok),gb=str2double(strrep(tok{1},',',''))/1024^2;end,catch,end
end

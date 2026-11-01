function S=t1vbm_step3_stats(C)
%T1VBM_STEP3_STATS  SPM 二阶双样本 t。协变量来自 C.covVars（默认 Age/Sex/EducationYears，可加 TIV）。
if nargin<1||isempty(C),C=t1vbm_config();end
t1vbm_env('mkdirs',C);t1vbm_env('banner',C,'step3  SPM 二阶统计 + Design QC');t1vbm_env('checkspm',C);
M=t1vbm_manifest('load',C);
assert(all(M.Group==C.gA|M.Group==C.gB),'step3: manifest 存在非目标组别。');

smDir=fullfile(C.statDir,'sm');if ~C.dryrun&&~exist(smDir,'dir'),mkdir(smDir);end
n=height(M); files=cell(n,1);
for i=1:n
    assert(M.CAT12Status(i)=="complete",'step3: %s CAT12 状态不是 complete。',M.Subject(i));
    src=char(M.Mwp1File(i));assert(C.dryrun||exist(src,'file')==2,'step3: 缺 %s',src);
    [~,b]=fileparts(src);dst=fullfile(smDir,['s' num2str(C.smoothFWHM) '_' b '.nii']);
    % SPM 文件名标准写法：FWHM=8 -> smwp1；兼容已有输出。
    if C.smoothFWHM==8, dst=fullfile(smDir,['s' b '.nii']); end
    if C.smoothFWHM>0
        if ~C.dryrun&&exist(dst,'file')~=2,spm_smooth(src,dst,C.smoothFWHM);end
        files{i}=dst;
    else, files{i}=src;end
end

g=string(M.Group);iA=find(g==C.gA);iB=find(g==C.gB);
fprintf('组别: %s=%d, %s=%d | 平滑 FWHM=%g mm\n',C.gA,numel(iA),C.gB,numel(iB),C.smoothFWHM);
assert(~isempty(iA)&&~isempty(iB),'step3: 某一组为空。');

% 协变量按 C.covVars 生成。连续变量中心化；Sex 保持 0/1。缺失值报错，不把未知性别编为 0。
[covVals,covName]=covariatesFromConfig(C,M);

if C.dryrun
    fprintf('[DRYRUN] nscan=%d；设计=%s vs %s + %s\n',n,C.gA,C.gB,strjoin(covName,', '));
    S=struct('outDir',fullfile(C.statDir,'SSDvsHC'),'n',n,'nA',numel(iA),'nB',numel(iB));return
end

% 显式 GM mask：全样本平均 GM probability > maskTh。
maskFile=fullfile(C.statDir,'GMmask.nii');
maskMeta=fullfile(C.statDir,'GMmask_meta.mat');
needMask=true;
if exist(maskFile,'file')==2&&exist(maskMeta,'file')==2
    z=load(maskMeta);needMask=~isfield(z,'meta')||z.meta.n~=n||z.meta.th~=C.maskTh||z.meta.fwhm~=C.smoothFWHM;
end
if needMask
    acc=[];
    for i=1:n
        V=spm_vol(files{i});Y=spm_read_vols(V);assert(~isempty(Y),'step3: 无法读取 %s',files{i});
        if isempty(acc),acc=zeros(V.dim);end
        assert(isequal(size(Y),size(acc)),'step3: 输入图像维度不一致。');
        acc=acc+Y;
    end
    acc=acc/n;V0=spm_vol(files{1});Vm=V0;Vm.fname=maskFile;Vm.dt=[spm_type('uint8') 0];Vm.pinfo=[1;0;0];
    spm_write_vol(Vm,uint8(acc>C.maskTh));
    meta=struct('n',n,'th',C.maskTh,'fwhm',C.smoothFWHM,'voxels',nnz(acc>C.maskTh),'created',char(datetime('now'))); %#ok<NASGU>
    save(maskMeta,'meta');
else
    fprintf('复用已有 GM mask: %s\n',maskFile);
end
maskV=spm_vol(maskFile);maskY=spm_read_vols(maskV);maskCoverage=nnz(maskY>0)/numel(maskY);

% 设计矩阵先验 QC。
predictors=[double(g==C.gA),covVals];
qc=designQC(predictors,[{'Group'},covName]);
qcFile=fullfile(C.repDir,'Design_QC.csv');writetable(qc, qcFile);
if any(qc.VIF>10),warning('t1vbm:highVIF','Design QC: 至少一个预测变量 VIF > 10。');end
fprintf('Design QC: rank=%d/%d, condition number=%.2f, mask coverage=%.1f%%\n',rank(predictors),size(predictors,2),cond([ones(n,1) predictors]),100*maskCoverage);

outDir=fullfile(C.statDir,'SSDvsHC');if ~exist(outDir,'dir'),mkdir(outDir);end
if exist(fullfile(outDir,'SPM.mat'),'file')==2
    delete(fullfile(outDir,'SPM.mat'));
end
clear matlabbatch
matlabbatch{1}.spm.stats.factorial_design=mkFD(outDir,files,iA,iB,covVals,covName,maskFile);
matlabbatch{2}.spm.stats.fmri_est.spmmat={fullfile(outDir,'SPM.mat')};matlabbatch{2}.spm.stats.fmri_est.write_residuals=0;matlabbatch{2}.spm.stats.fmri_est.method.Classical=1;
matlabbatch{3}.spm.stats.con=conJob(outDir,C.gA,C.gB,numel(covName));
spm_jobman('initcfg');spm_jobman('run',matlabbatch);

Sp=load(fullfile(outDir,'SPM.mat'));X=Sp.SPM.xX.X;
assert(Sp.SPM.nscan==n,'step3: nscan=%d 应为 %d',Sp.SPM.nscan,n);
assert(size(X,2)==2+numel(covName),'step3: 设计列数=%d，应为 %d',size(X,2),2+numel(covName));
assert(rank(X)==size(X,2),'step3: 设计矩阵秩亏 rank=%d/%d。',rank(X),size(X,2));
assert(all(isfinite(X(:))),'step3: 设计矩阵存在 NaN/Inf。');
qc2=table(size(X,1),size(X,2),rank(X),cond(X),Sp.SPM.xX.erdf,maskCoverage,...
    'VariableNames',{'Nscan','Nregressors','Rank','ConditionNumber','ResidualDF','MaskCoverage'});
writetable(qc2,fullfile(C.repDir,'SPM_design_summary.csv'));
fprintf('设计校验通过: nscan=%d, regressors=%d, rank=%d, df=%.1f\n',size(X,1),size(X,2),rank(X),Sp.SPM.xX.erdf);
t1vbm_log(C,'step3',sprintf('nscan=%d regressors=%d rank=%d df=%.1f condition=%.2f',size(X,1),size(X,2),rank(X),Sp.SPM.xX.erdf,cond(X)));

S=struct('outDir',outDir,'n',n,'nA',numel(iA),'nB',numel(iB),'mask',maskFile,'covNames',{covName},'designQC',qcFile);
if C.altModel
    try
        [altVals,altNames,hasAlt]=pickCov(covVals,covName,{'Age','Sex'});
        if ~hasAlt
            warning('t1vbm:altModel','备用模型需要 Age 和 Sex，已跳过。');
        else
        altDir=fullfile(C.statDir,'alt_AgeSex','SSDvsHC');if ~exist(altDir,'dir'),mkdir(altDir);end
        mb={};mb{1}.spm.stats.factorial_design=mkFD(altDir,files,iA,iB,altVals,altNames,maskFile);
        mb{2}.spm.stats.fmri_est.spmmat={fullfile(altDir,'SPM.mat')};mb{2}.spm.stats.fmri_est.write_residuals=0;mb{2}.spm.stats.fmri_est.method.Classical=1;
        mb{3}.spm.stats.con=conJob(altDir,C.gA,C.gB,numel(altNames));spm_jobman('initcfg');spm_jobman('run',mb);
        fprintf('已生成仅 Age+Sex 模型: %s\n',altDir);
        end
    catch ME
        warning('t1vbm:altModel','备用模型失败（不影响主模型）: %s',ME.message);
    end
end
end

function qc=designQC(Z,names)
p=size(Z,2);v=nan(p,1);r2=nan(p,1);
for j=1:p
    y=Z(:,j);others=Z(:,setdiff(1:p,j));
    if isempty(others)||std(y)==0, v(j)=Inf;continue;end
    X=[ones(size(others,1),1),others];b=X\y;res=y-X*b;sst=sum((y-mean(y)).^2);r2(j)=1-sum(res.^2)/sst;
    v(j)=1/max(1e-12,1-r2(j));
end
qc=table(string(names(:)),v,r2,'VariableNames',{'Predictor','VIF','R2_from_others'});
end

function fd=mkFD(outDir,files,iA,iB,covVals,covName,maskFile)
fd=struct();fd.dir={outDir};fd.des.t2.scans1=files(iA);fd.des.t2.scans2=files(iB);fd.des.t2.dept=0;fd.des.t2.variance=1;fd.des.t2.gmsca=0;fd.des.t2.ancova=0;
cov=struct('c',{},'cname',{},'iCFI',{},'iCC',{});
for j=1:numel(covName),cov(j).c=covVals(:,j);cov(j).cname=covName{j};cov(j).iCFI=1;cov(j).iCC=1;end
fd.cov=cov;fd.multi_cov=struct('files',{},'iCFI',{},'iCC',{});fd.masking.tm.tm_none=1;fd.masking.im=0;fd.masking.em={maskFile};fd.globalc.g_omit=1;fd.globalm.gmsca.gmsca_no=1;fd.globalm.glonorm=1;
end
function [covVals,covName]=covariatesFromConfig(C,M)
covName=reshape(cellstr(string(C.covVars)),1,[]);
n=height(M); covVals=nan(n,numel(covName));
for j=1:numel(covName)
    name=covName{j}; key=lower(strtrim(name));
    switch key
        case 'age'
            v=double(M.Age); rejectBadCov(C,M,name,~isfinite(v)); v=centerIf(C,v);
        case 'sex'
            v=double(M.SexNum); rejectBadCov(C,M,name,~isfinite(v)|~ismember(v,[0 1]));
        case 'educationyears'
            v=double(M.Education); rejectBadCov(C,M,name,~isfinite(v)); v=centerIf(C,v);
        case 'tiv'
            if ~ismember('TIV',M.Properties.VariableNames)
                error('step3: covVars 含 TIV，但 manifest 没有 TIV 列。请先运行 step2。');
            end
            v=double(M.TIV); rejectBadCov(C,M,name,~isfinite(v)); v=centerIf(C,v);
        otherwise
            error('step3: 未知协变量 "%s"。允许 Age、Sex、EducationYears、TIV。',name);
    end
    covVals(:,j)=v;
end
end
function v=centerIf(C,v)
if C.centerContinuous, v=v-mean(v); end
end
function rejectBadCov(C,M,name,bad)
if ~any(bad), return; end
ids=strjoin(cellstr(string(M.Subject(bad))),', ');
t1vbm_log(C,'step3',sprintf('协变量 %s 缺失或非法（不把未知性别编为 0）: %s',name,ids));
error('step3: 协变量 %s 含缺失或非法值: %s',name,ids);
end
function [vals,names,ok]=pickCov(covVals,covName,want)
idx=zeros(1,numel(want)); names=cell(1,numel(want));
for i=1:numel(want)
    j=find(strcmpi(covName,want{i}),1);
    if isempty(j), vals=[]; names={}; ok=false; return; end
    idx(i)=j; names{i}=covName{j};
end
vals=covVals(:,idx); ok=true;
end
function con=conJob(outDir,gA,gB,nCov)
lim=[1 -1 zeros(1,nCov)];con=struct();con.spmmat={fullfile(outDir,'SPM.mat')};con.consess{1}.tcon.name=sprintf('%s > %s',gA,gB);con.consess{1}.tcon.weights=lim;con.consess{1}.tcon.sessrep='none';con.consess{2}.tcon.name=sprintf('%s > %s',gB,gA);con.consess{2}.tcon.weights=-lim;con.consess{2}.tcon.sessrep='none';con.delete=0;
end

function S=t1vbm_step4_report(C)
%T1VBM_STEP4_REPORT  主分析 FWE + exploratory uncorrected 结果、cluster 表和 Table 1。
if nargin<1||isempty(C),C=t1vbm_config();end
t1vbm_env('mkdirs',C);t1vbm_env('banner',C,'step4  FWE/探索性结果 + Table 1');t1vbm_env('checkspm',C);
outDir=fullfile(C.statDir,'SSDvsHC');SpFile=fullfile(outDir,'SPM.mat');
if C.dryrun
    fprintf('[DRYRUN] 将输出 FWE 主分析、p<%g 探索性分析(k>=%d)及 Table1。\n',C.pUnc,C.kMin);
    S=struct('clustersFWE','','clustersUnc','','table1','','uUnc',NaN,'uFWE',NaN,'uMethod','','df',NaN);return
end
assert(exist(SpFile,'file')==2,'step4: 缺 SPM.mat，请先跑 step3');
Sp=load(SpFile);df=Sp.SPM.xX.erdf;

% FWE voxel threshold：优先 RFT，失败时给出 Bonferroni fallback。
rpv=fullfile(outDir,'RPV.nii');uFWE=NaN;uMethod='unavailable';R=NaN;
if exist(rpv,'file')==2
    try
        R=spm_resels_vol(spm_vol(rpv),[1 1 1]);
        if isfinite(R(1)) && R(1)>0
            uFWE=spm_uc(C.pFWE,[1 df],'T',R(1),1);uMethod='RFT(RPV)';
        end
    catch ME1
        t1vbm_log(C,'step4_qc',sprintf('RFT threshold unavailable: %s',ME1.message));
    end
end
if ~isfinite(uFWE)
    try
        gm=fullfile(C.statDir,'GMmask.nii');nV=nnz(spm_read_vols(spm_vol(gm))>0);
        uFWE=-spm_invTcdf(C.pFWE/nV,df);uMethod=sprintf('Bonferroni(n=%d)',nV);
    catch ME2
        t1vbm_log(C,'step4_qc',sprintf('FWE threshold unavailable: %s',ME2.message));
    end
end
uUnc=-spm_invTcdf(C.pUnc,df);
fprintf('df=%.1f | uncorrected p<%g => T=%.3f | FWE p<%g (%s) => T=%.3f\n',df,C.pUnc,uUnc,C.pFWE,uMethod,uFWE);

names={sprintf('%s_gt_%s',C.gA,C.gB),sprintf('%s_gt_%s',C.gB,C.gA)};
rowsFWE=cell(0,11);rowsUNC=cell(0,11);
for c=1:2
    fT=fullfile(outDir,sprintf('spmT_%04d.nii',c));if exist(fT,'file')~=2,continue;end
    V=spm_vol(fT);Tv=spm_read_vols(V);
    if isfinite(uFWE)
        rowsFWE=extractClusters(V,Tv,uFWE,C.kMin,names{c},df,rowsFWE,'voxel_FWE');
    end
    if C.exploratoryUncorrected
        rowsUNC=extractClusters(V,Tv,uUnc,C.kMin,names{c},df,rowsUNC,'uncorrected');
    end
end

csvFWE=writeClusterTable(rowsFWE,C.repDir,'Clusters_FWE.csv');
csvUNC=writeClusterTable(rowsUNC,C.repDir,'Clusters_uncorrected.csv');

M=t1vbm_manifest('load',C);
T1=t1vbm_table('table1',M,C);csv1=fullfile(C.repDir,'Table1_人口学.csv');writetable(T1,csv1);
meta=struct('df',df,'pUnc',C.pUnc,'uUnc',uUnc,'pFWE',C.pFWE,'uFWE',uFWE,'uMethod',uMethod,'kMin',C.kMin,'created',char(datetime('now')));save(fullfile(C.repDir,'analysis_thresholds.mat'),'meta');
summary=table(string({'Primary voxel-FWE';'Exploratory uncorrected'}),[C.pFWE;C.pUnc],[uFWE;uUnc],string({uMethod;'T threshold'}),[height(rowsToTable(rowsFWE));height(rowsToTable(rowsUNC))], ...
    'VariableNames',{'Analysis','P_threshold','T_threshold','Method','Clusters'});writetable(summary,fullfile(C.repDir,'Analysis_threshold_summary.csv'));

t1vbm_log(C,'step4',sprintf('FWE=%g (%s), T=%.3f; uncorrected p=%g T=%.3f; FWE clusters=%d; exploratory=%d',C.pFWE,uMethod,uFWE,C.pUnc,uUnc,size(rowsFWE,1),size(rowsUNC,1)));
qcHtml=t1vbm_qc_report(C); methodsTxt=t1vbm_methods_summary(C);
S=struct('clustersFWE',csvFWE,'clustersUnc',csvUNC,'table1',csv1,'uUnc',uUnc,'uFWE',uFWE,'uMethod',uMethod,'df',df,'qcHtml',qcHtml,'methodsSummary',methodsTxt);
end

function rows=extractClusters(V,Tv,u,kMin,contrast,df,rows,thresholdType)
idx=find(Tv>u);if isempty(idx),return;end
[ii,jj,kk]=ind2sub(V.dim,idx);cl=spm_clusters([ii';jj';kk']);ids=unique(cl);
voxelVol=abs(det(V.mat(1:3,1:3)))/1000; % mm^3 -> mL
for q=1:numel(ids)
    m=cl==ids(q);vx=idx(m);nVox=numel(vx);if nVox<kMin,continue;end
    [pk,p]=max(Tv(vx));[a,b,c]=ind2sub(V.dim,vx(p));mni=V.mat*[a;b;c;1];
    rows(end+1,:)={contrast,thresholdType,ids(q),nVox,nVox*voxelVol,pk,1-spm_Tcdf(pk,df),mni(1),mni(2),mni(3),u}; %#ok<AGROW>
end
end

function f=writeClusterTable(rows,repDir,name)
if isempty(rows),f='';fprintf('%s: 0 clusters\n',name);return;end
T=rowsToTable(rows);f=fullfile(repDir,name);writetable(T,f);fprintf('%s: %d clusters -> %s\n',name,height(T),f);
end

function T=rowsToTable(rows)
if isempty(rows),T=table();return;end
T=cell2table(rows,'VariableNames',{'Contrast','ThresholdType','ClusterID','Voxels','ClusterVolume_mL','PeakT','p_unc','MNI_x','MNI_y','MNI_z','ThresholdT'});
T=sortrows(T,{'Contrast','PeakT'},{'ascend','descend'});
end

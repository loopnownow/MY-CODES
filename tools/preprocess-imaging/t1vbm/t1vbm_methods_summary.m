function out=t1vbm_methods_summary(C)
%T1VBM_METHODS_SUMMARY  从配置和已生成结果生成可粘贴到 Methods 的参数摘要。
if nargin<1||isempty(C),C=t1vbm_config();end
if C.dryrun,out='';return;end
out=fullfile(C.repDir,'Methods_analysis_summary.txt');fid=fopen(out,'w','n','UTF-8');assert(fid>0);cleanup=onCleanup(@()fclose(fid)); %#ok<NASGU>
M=t1vbm_manifest('load',C);fprintf(fid,'T1 VBM analysis parameter summary\nGenerated: %s\n\n',char(datetime('now')));
fprintf(fid,'Participants: %d (%s=%d; %s=%d).\n',height(M),C.gA,sum(M.Group==C.gA),C.gB,sum(M.Group==C.gB));
fprintf(fid,'Preprocessing: CAT12; modulated GM (mwp1); surface reconstruction=%d; redspmres=%g mm.\n',C.cat.surface,C.cat.redspmres);
fprintf(fid,'Spatial smoothing: Gaussian kernel FWHM %g mm.\n',C.smoothFWHM);
fprintf(fid,'Second-level model: two-sample t-test, unequal group variance, covariates age, sex and education years. Continuous covariates were %s before model construction. Sex was coded 0/1.\n',ternary(C.centerContinuous,'centered','not centered'));
fprintf(fid,'Mask: explicit mean-GM-probability mask threshold > %.2f.\n',C.maskTh);
fprintf(fid,'Primary inference: voxel-level FWE p < %.3g. Exploratory inference: voxel-level uncorrected p < %.3g with cluster extent >= %d voxels.\n',C.pFWE,C.pUnc,C.kMin);
fprintf(fid,'Two-sided group contrasts were generated: %s > %s and %s > %s.\n',C.gA,C.gB,C.gB,C.gA);
end
function y=ternary(c,a,b),if c,y=a;else,y=b;end,end

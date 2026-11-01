function out=t1vbm_qc_report(C)
%T1VBM_QC_REPORT  自动生成最终 HTML QC 报告。
if nargin<1||isempty(C),C=t1vbm_config();end
if C.dryrun,out='';return;end
rep=C.repDir;if ~exist(rep,'dir'),mkdir(rep);end
M=t1vbm_manifest('load',C);fwe=fullfile(rep,'Clusters_FWE.csv');unc=fullfile(rep,'Clusters_uncorrected.csv');dqc=fullfile(rep,'Design_QC.csv');sumf=fullfile(rep,'SPM_design_summary.csv');
cf=0;cu=0;if exist(fwe,'file')==2,cf=height(readtable(fwe));end;if exist(unc,'file')==2,cu=height(readtable(unc));end
if exist(sumf,'file')==2,D=readtable(sumf);else,D=table();end
if exist(dqc,'file')==2,V=readtable(dqc);else,V=table();end
out=fullfile(rep,'T1_VBM_QC_Report.html');fid=fopen(out,'w','n','UTF-8');assert(fid>0);cleanup=onCleanup(@()fclose(fid)); %#ok<NASGU>
html=@(x)char(string(x));
fprintf(fid,'<!doctype html><html lang="zh-CN"><head><meta charset="utf-8"><title>T1 VBM QC Report</title><style>body{font-family:Arial,"Microsoft YaHei",sans-serif;max-width:1100px;margin:30px auto;line-height:1.6;color:#20252b}h1,h2{color:#174a6e}.card{border:1px solid #ddd;border-radius:8px;padding:16px;margin:14px 0}table{border-collapse:collapse;width:100%%}th,td{border:1px solid #ddd;padding:6px 8px}th{background:#eef3f7}.ok{color:#176b3a}.warn{color:#a15c00}</style></head><body>');
fprintf(fid,'<h1>T1 VBM Pipeline QC Report</h1><p>Generated: %s</p>',html(datetime('now')));
fprintf(fid,'<div class="card"><h2>Pipeline status</h2><table><tr><th>Metric</th><th>Value</th></tr><tr><td>Subjects</td><td>%d</td></tr><tr><td>%s</td><td>%d</td></tr><tr><td>%s</td><td>%d</td></tr><tr><td>CAT12 complete</td><td>%d</td></tr></table></div>',height(M),C.gA,sum(M.Group==C.gA),C.gB,sum(M.Group==C.gB),sum(M.CAT12Status=="complete"));
fprintf(fid,'<div class="card"><h2>Statistical QC</h2>');
if ~isempty(D),fprintf(fid,'<table><tr><th>Nscan</th><th>Regressors</th><th>Rank</th><th>Condition number</th><th>Residual DF</th><th>Mask coverage</th></tr><tr><td>%d</td><td>%d</td><td>%d</td><td>%.2f</td><td>%.1f</td><td>%.1f%%</td></tr></table>',D.Nscan(1),D.Nregressors(1),D.Rank(1),D.ConditionNumber(1),D.ResidualDF(1),100*D.MaskCoverage(1));end
if ~isempty(V),fprintf(fid,'<h3>Predictor VIF</h3><table><tr><th>Predictor</th><th>VIF</th><th>R² from others</th></tr>');for i=1:height(V),cls='ok';if V.VIF(i)>10,cls='warn';end;fprintf(fid,'<tr class="%s"><td>%s</td><td>%.3f</td><td>%.3f</td></tr>',cls,html(V.Predictor(i)),V.VIF(i),V.R2_from_others(i));end;fprintf(fid,'</table>');end
fprintf(fid,'</div><div class="card"><h2>Inference</h2><p>Primary: voxel-level FWE p &lt; %.3g. Exploratory: voxel-level uncorrected p &lt; %.3g with cluster size ≥ %d voxels.</p><p>FWE clusters: %d; exploratory clusters: %d.</p></div>',C.pFWE,C.pUnc,C.kMin,cf,cu);
fprintf(fid,'<div class="card"><h2>Reproducibility</h2><p>Input, CAT12 status, statistical design and thresholds are stored under <code>manifest/</code>, <code>03_stats/</code>, <code>04_report/</code> and <code>log/</code>.</p></div></body></html>');
end

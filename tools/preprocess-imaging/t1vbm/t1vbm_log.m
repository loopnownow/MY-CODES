function t1vbm_log(C, tag, msg)
%T1VBM_LOG  统一日志接口。
if C.dryrun, return; end
if ~exist(C.logDir,'dir'), mkdir(C.logDir); end
fid = fopen(fullfile(C.logDir, [tag '.log']), 'a', 'n', 'UTF-8');
if fid < 0, return; end
cleanup = onCleanup(@() fclose(fid)); %#ok<NASGU>
fprintf(fid, '[%s] %s\n', datetime('now','Format','yyyy-MM-dd HH:mm:ss'), char(msg));
if C.verbose >= 1, fprintf('%s\n', char(msg)); end
end

function S=t1vbm_step1_unzip(C)
%T1VBM_STEP1_UNZIP  解压输入、核对分组表、建立统一 subject manifest。
if nargin<1||isempty(C), C=t1vbm_config(); end
t1vbm_env('mkdirs',C); t1vbm_env('banner',C,'step1  输入 QC + 解压 + Manifest');
subs=t1vbm_files('scan',C);
assert(~isempty(subs),'step1: 未匹配到任何被试');
S=struct('C',C,'subjects',subs,'n',numel(subs),'unzipped',0,'skipped',0,'failed',0,'checklist','');
lines=strings(0,1);
nFiles=numel(dir(fullfile(C.srcDir,C.srcPat)));
lines(end+1)=sprintf('原始图像文件: %d | 分组表匹配: %d',nFiles,S.n);
for i=1:numel(subs)
    srcF=fullfile(C.srcDir,subs(i).file); [~,b]=fileparts(char(subs(i).file)); dstF=fullfile(C.niiDir,b);
    if ~isempty(subs(i).excluded)
        lines(end+1)=sprintf('[重名排除] %s -> %s',subs(i).Patient_ID,strjoin(subs(i).excluded,', ')); %#ok<AGROW>
    end
    if C.dryrun
        lines(end+1)=sprintf('[DRYRUN] %s -> %s',subs(i).file,b); %#ok<AGROW>
        continue
    end
    need=true;
    if exist(dstF,'file')==2, need=(dir(dstF).bytes~=gzisize(srcF)); end
    if ~need, S.skipped=S.skipped+1; continue; end
    try
        gunzip(srcF,C.niiDir); S.unzipped=S.unzipped+1;
    catch ME
        S.failed=S.failed+1; lines(end+1)=sprintf('[失败] %s : %s',subs(i).file,ME.message); %#ok<AGROW>
    end
end
fprintf('解压 %d | 跳过 %d | 失败 %d\n',S.unzipped,S.skipped,S.failed);
if C.dryrun
    fprintf('[DRYRUN] %s\n',strjoin(lines,newline)); S.manifest=''; return
end
assert(S.failed==0,'step1: 有 %d 个输入解压失败，停止进入 CAT12。',S.failed);
% 解压后再次确认每个 manifest input 存在，避免 step2 才暴露问题。
for i=1:numel(subs)
    [~,b]=fileparts(char(subs(i).file)); assert(exist(fullfile(C.niiDir,b),'file')==2,'step1: 缺失解压文件 %s',b);
end
M=t1vbm_manifest('create',C,subs);
S.manifest=fullfile(C.manifestDir,'subject_manifest.csv'); %#ok<NASGU>
S.checklist=fullfile(C.repDir,'核对清单_step1.txt');
fid=fopen(S.checklist,'w','n','UTF-8'); assert(fid>0);
cleanup=onCleanup(@()fclose(fid)); %#ok<NASGU>
fprintf(fid,'%s\n',strjoin(lines,newline)); fprintf(fid,'\n解压 %d | 跳过 %d | 失败 %d | Manifest %d 例\n',S.unzipped,S.skipped,S.failed,height(M));
t1vbm_log(C,'step1',sprintf('manifest=%s n=%d unzip=%d skip=%d fail=%d',S.manifest,S.n,S.unzipped,S.skipped,S.failed));
end

function n=gzisize(f)
fh=fopen(f,'r'); if fh<0,n=-1;return;end
cleanup=onCleanup(@()fclose(fh)); %#ok<NASGU>
fseek(fh,-4,'eof'); n=double(fread(fh,1,'uint32=>uint32'));
end

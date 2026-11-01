function varargout = t1vbm_manifest(action, varargin)
%T1VBM_MANIFEST  全流程 subject manifest：统一管理输入、CAT12状态和统计输入。
switch lower(action)
    case 'create'
        varargout{1}=createManifest(varargin{:});
    case 'load'
        varargout{1}=loadManifest(varargin{:});
    case 'update'
        varargout{1}=updateManifest(varargin{:});
    case 'paths'
        varargout{1}=subjectPaths(varargin{:});
    otherwise
        error('t1vbm_manifest:未知动作 "%s"',action);
end
end

function M=createManifest(C, subs)
if nargin<2 || isempty(subs), subs=t1vbm_files('scan',C); end
n=numel(subs);
M=table(strings(n,1),strings(n,1),strings(n,1),zeros(n,1),strings(n,1), ...
    nan(n,1),nan(n,1),nan(n,1),nan(n,1),strings(n,1),strings(n,1),strings(n,1), ...
    strings(n,1),strings(n,1),strings(n,1),strings(n,1), ...
    'VariableNames',{'Subject','Key','Group','Age','Sex','SexNum','Education','TIV', ...
    'SourceFile','NiiFile','Mwp1File','Mwp2File','WarpFile','Status','CAT12Status','Updated'});
for i=1:n
    s=subs(i); p=subjectPaths(C,s);
    M.Subject(i)=string(s.Patient_ID); M.Key(i)=string(s.key); M.Group(i)=string(s.Group);
    M.Age(i)=s.Age; M.Sex(i)=string(s.Sex); M.SexNum(i)=s.SexNum; M.Education(i)=s.Edu;
    M.SourceFile(i)=string(s.file); M.NiiFile(i)=string(p.nii); M.Mwp1File(i)=string(p.mwp1);
    M.Mwp2File(i)=string(p.mwp2); M.WarpFile(i)=string(p.warp); M.Status(i)="input_matched";
    M.CAT12Status(i)="pending"; M.Updated(i)=string(datetime('now','Format','yyyy-MM-dd HH:mm:ss'));
end
saveManifest(C,M);
end

function M=loadManifest(C)
mat=fullfile(C.manifestDir,'subject_manifest.mat'); csv=fullfile(C.manifestDir,'subject_manifest.csv');
if exist(mat,'file')==2
    z=load(mat,'M'); M=ensureTivColumn(z.M); return;
end
assert(exist(csv,'file')==2,'t1vbm_manifest: 找不到 manifest，请先运行 step1');
M=ensureTivColumn(readtable(csv,'TextType','string'));
end

function M=ensureTivColumn(M)
% 旧 subject_manifest 没有 TIV 列时补 NaN，不要求重跑 step1。
if ismember('TIV', M.Properties.VariableNames)
    return
end
tiv = nan(height(M), 1);
if ismember('Education', M.Properties.VariableNames)
    M = addvars(M, tiv, 'After', 'Education', 'NewVariableNames', 'TIV');
else
    M.TIV = tiv;
end
end

function M=updateManifest(C,M,varargin)
% name-value: Key/Status/CAT12Status 等列更新不超过当前表结构。
for i=1:2:numel(varargin)
    key=varargin{i}; val=varargin{i+1};
    if ~ismember(key,M.Properties.VariableNames), error('manifest:未知列 %s',key); end
    if ischar(val) || isstring(val)
        M.(key)=string(val);
    else
        M.(key)=val;
    end
end
M.Updated(:)=string(datetime('now','Format','yyyy-MM-dd HH:mm:ss'));
saveManifest(C,M);
end

function p=subjectPaths(C,s)
[~,b]=fileparts(char(s.file));
p.nii=fullfile(C.niiDir,b);
p.mriDir=fullfile(C.niiDir,'mri');
p.mwp1=fullfile(p.mriDir,['mwp1' b]);
p.mwp2=fullfile(p.mriDir,['mwp2' b]);
p.warp=fullfile(p.mriDir,['y_' b]);
end

function saveManifest(C,M)
if C.dryrun, return; end
if ~exist(C.manifestDir,'dir'), mkdir(C.manifestDir); end
mat=fullfile(C.manifestDir,'subject_manifest.mat'); csv=fullfile(C.manifestDir,'subject_manifest.csv');
save(mat,'M'); writetable(M,csv);
end

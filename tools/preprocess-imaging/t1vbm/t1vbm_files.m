function varargout = t1vbm_files(action, varargin)
%T1VBM_FILES  文件扫描、被试匹配与文件名工具。
switch lower(action)
    case 'scan'
        varargout{1} = scanfiles(varargin{:});
    case 'namekey'
        varargout{1} = namekey(varargin{:});
    case 'samefile'
        varargout{1} = samefile(varargin{:});
    case 'fkey'
        varargout{1} = fkey(varargin{:});
    case 'tiv'
        varargout{1} = readtiv(varargin{:});
    otherwise
        error('t1vbm_files:未知动作 "%s"', action);
end
end

function k = namekey(s)
k = string(upper(regexprep(char(string(s)), '[^A-Za-z]', '')));
end

function k = fkey(fname)
stem = regexprep(fname, '(_\d+)?_t1_mprage.*$', '');
k = namekey(stem);
end

function b = samefile(a, b)
da = dir(a); db = dir(b);
b = ~isempty(da) && ~isempty(db) && da.bytes == db.bytes;
end

function S = scanfiles(C)
d = dir(fullfile(C.srcDir, C.srcPat));
[T, M] = t1vbm_table('read', C);
fk = arrayfun(@(x) fkey(x), string({d.name}));
S = struct('Patient_ID',{},'key',{},'file',{},'excluded',{}, ...
    'Group',{},'Age',{},'Sex',{},'SexNum',{},'Edu',{});
for i = 1:height(T)
    pid = string(T.(M.PID)(i));
    k = namekey(pid);
    hit = find(fk == k);
    if isempty(hit)
        warning('t1vbm:noImage', '分组表 %s 无对应图像，已跳过', pid);
        continue
    end
    files = string({d(hit).name});
    excl = strings(0,1);
    if numel(files) > 1
        pref = find(contains(files, C.dupRule), 1, 'first');
        if isempty(pref), keep = 1; else, keep = pref; end
        excl = files; excl(keep) = []; files = files(keep);
    end
    e.Patient_ID = pid;
    e.key = k;
    e.file = char(files(1));
    e.excluded = cellstr(excl);
    e.Group = string(T.(M.Group)(i));
    e.Age = double(T.(M.Age)(i));
    e.Sex = string(T.(M.Sex)(i));
    e.SexNum = sex2num(e.Sex, C.sexMap);
    e.Edu = double(T.(M.Edu)(i));
    S(end+1,1) = e; %#ok<AGROW>
end
end

function n = sex2num(sv, map)
n = NaN;
s = strtrim(char(sv));
for i = 1:size(map,1)
    if strcmpi(s, char(map{i,1}))
        n = double(map{i,2}); return;
    end
end
end

function tiv = readtiv(niiFile)
%READTIV  CAT12 TIV。先 cat_*.mat，没有再读 cat_*.xml 的 <vol_TIV>。
% 本流程的 NIfTI 共用一个目录，因此只匹配该被试的 basename，不取目录里第一个 cat_*.mat。
tiv = NaN;
[pth, nam, ext] = fileparts(char(niiFile));
if strcmpi(ext, '.gz')
    [~, nam] = fileparts(nam);
end
matCandidates = { ...
    fullfile(pth, ['cat_' nam '.mat']); ...
    fullfile(pth, 'report', ['cat_' nam '.mat'])};
for i = 1:numel(matCandidates)
    if exist(matCandidates{i}, 'file') ~= 2, continue; end
    tiv = tivFromMat(matCandidates{i});
    if isfinite(tiv), return; end
end
xmlCandidates = { ...
    fullfile(pth, ['cat_' nam '.xml']); ...
    fullfile(pth, 'report', ['cat_' nam '.xml'])};
for i = 1:numel(xmlCandidates)
    if exist(xmlCandidates{i}, 'file') ~= 2, continue; end
    tiv = tivFromXml(xmlCandidates{i});
    if isfinite(tiv), return; end
end
end

function tiv = tivFromMat(matFile)
tiv = NaN;
try
    mat_data = load(matFile);
catch
    return
end
raw = [];
if isfield(mat_data, 'S') && isfield(mat_data.S, 'subjectmeasures') && isfield(mat_data.S.subjectmeasures, 'vol_TIV')
    raw = mat_data.S.subjectmeasures.vol_TIV;
elseif isfield(mat_data, 'S') && isfield(mat_data.S, 'vol') && isfield(mat_data.S.vol, 'TIV')
    raw = mat_data.S.vol.TIV;
elseif isfield(mat_data, 'S') && isfield(mat_data.S, 'vol_TIV')
    raw = mat_data.S.vol_TIV;
end
if isempty(raw), return; end
raw = double(raw);
if isscalar(raw) && isfinite(raw)
    tiv = raw;
end
end

function tiv = tivFromXml(xmlFile)
tiv = NaN;
txt = fileread(xmlFile);
val = regexp(txt, '<vol_TIV>([\d\.]+)</vol_TIV>', 'tokens');
if ~isempty(val)
    tiv = str2double(val{1}{1});
end
end

function varargout = t1vbm_env(action, varargin)
%T1VBM_ENV  环境检查与目录初始化。
switch lower(action)
    case 'mkdirs'
        mkdirs(varargin{:});
    case 'checkspm'
        checkspm(varargin{:});
    case 'banner'
        banner(varargin{:});
    otherwise
        error('t1vbm_env:未知动作 "%s"', action);
end
end

function mkdirs(C)
if C.dryrun, return; end
for p = {C.root,C.niiDir,C.statDir,C.repDir,C.logDir,C.manifestDir}
    if ~exist(p{1},'dir'), mkdir(p{1}); end
end
end

function checkspm(~)
if isempty(which('spm'))
    error(['t1vbm_env:找不到 SPM。请先启动 SPM，再运行本流程。']);
end
if isempty(which('cat_get_defaults'))
    error(['t1vbm_env:找不到 CAT12。请确认 SPM 目录下 toolbox\\cat12 在路径中。']);
end
end

function banner(C,name)
fprintf('\n%s\n',repmat('=',1,72));
if C.dryrun, md='DRYRUN 演练'; else, md='实跑'; end
fprintf('  %s   [%s]\n',name,md);
fprintf('  工作根: %s\n',C.root);
fprintf('%s\n',repmat('=',1,72));
end

function varargout = t1vbm_util(action, varargin)
%T1VBM_UTIL  兼容层。核心功能已拆分到 t1vbm_* 模块。
% 旧调用仍可用，避免历史脚本失效。
switch lower(action)
    case 'mkdirs',   t1vbm_env('mkdirs',varargin{:});
    case 'namekey',  varargout{1}=t1vbm_files('namekey',varargin{:});
    case 'log',      t1vbm_log(varargin{:});
    case 'table',    [varargout{1},varargout{2}]=t1vbm_table('read',varargin{:});
    case 'files',    varargout{1}=t1vbm_files('scan',varargin{:});
    case 'samefile', varargout{1}=t1vbm_files('samefile',varargin{:});
    case 'banner',   t1vbm_env('banner',varargin{:});
    case 'checkspm', t1vbm_env('checkspm',varargin{:});
    case 'manifest', varargout{1}=t1vbm_manifest(varargin{:});
    otherwise, error('t1vbm_util:未知动作 "%s"',action);
end
end

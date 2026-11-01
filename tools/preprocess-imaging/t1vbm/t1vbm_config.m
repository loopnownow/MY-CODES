function C = t1vbm_config(varargin)
%T1VBM_CONFIG  T1 VBM 全流程唯一配置入口。
% 例：C=t1vbm_config('nproc',8); run_all('from','step2','nproc',8)

%% 1. 路径
C.root      = 'G:\SSD_dyf\T1_vbm';
C.srcDir    = 'L:\00used\SSD_dyf\T1';
C.srcPat    = '*_t1_mprage_tra_p2_iso.nii.gz';
C.xlsx      = fullfile(C.srcDir,'分组.xlsx');
C.sheet     = 'Sheet1';
C.niiDir    = fullfile(C.root,'01_nii');
C.statDir   = fullfile(C.root,'03_stats');
C.repDir    = fullfile(C.root,'04_report');
C.logDir    = fullfile(C.root,'log');
C.manifestDir = fullfile(C.root,'manifest');

%% 2. 输入/预处理
C.unzip = true;
C.smoothFWHM = 8;
C.dupRule = '_12';

%% 3. 并行与监督
C.nproc = 0;
C.batchN = 12;
C.calibN = 1;
C.catTimeoutMin = 60;        % 每个并行槽的基础分钟预算
C.catTimeoutExtraMin = 30;   % 批次额外余量
C.catPollSec = 30;
C.catMaxNproc = 12;

%% 4. CAT12
C.cat = struct(...
    'surface',0,'GMmod',1,'WMmod',1,'CSFmod',1,'labelNative',1, ...
    'warps',[1 0],'redspmres',1.0,'backgroundHidden',true);

%% 5. 统计
C.gA='SSD'; C.gB='HC';
% 默认与 V2.4 相同。加入 TIV：C.covVars={'Age','Sex','EducationYears','TIV'};
% TIV 由 step2 写入 manifest（先 cat_*.mat，没有再读 cat_*.xml 的 vol_TIV）。
C.covVars={'Age','Sex','EducationYears'};
C.sexMap={'female',0;'male',1};
C.maskTh=0.2;
C.maskMinCoverage=0.80;        % 记录 mask 覆盖率 QC，不作为硬筛选
C.pUnc=0.001;
C.pFWE=0.05;
C.kMin=10;
C.altModel=true;
C.centerContinuous=true;
C.computeVIF=true;
C.fwePrimary=true;
C.exploratoryUncorrected=true;

%% 6. 行为
C.dryrun=false;
C.verbose=2;
C.stopOnStepFailure=true;

%% 7. Name-value 覆盖
assert(mod(numel(varargin),2)==0,'t1vbm_config: name-value 参数必须成对');
for i=1:2:numel(varargin)
    f=varargin{i};
    assert((ischar(f)||isstring(f)) && isfield(C,char(f)), ...
        't1vbm_config: 未知字段 "%s"',string(f));
    C.(char(f))=varargin{i+1};
end

if ~C.dryrun
    assert(isempty(strfind(C.root,' ')), '工作目录含空格，CAT12 并行可能失败: %s',C.root); %#ok<STREMP>
end
assert(C.kMin>=1 && C.pUnc>0 && C.pUnc<1 && C.pFWE>0 && C.pFWE<1);
end

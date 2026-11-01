function varargout = t1vbm_table(action, varargin)
%T1VBM_TABLE  分组表读取与人口学 QC。
switch lower(action)
    case 'read'
        [varargout{1}, varargout{2}] = readtab(varargin{:});
    case 'table1'
        varargout{1} = makeTable1(varargin{:});
    otherwise
        error('t1vbm_table:未知动作 "%s"', action);
end
end

function [T, M] = readtab(C)
T = readtable(C.xlsx, 'VariableNamingRule','preserve', 'Sheet', C.sheet);
vn = T.Properties.VariableNames;
norm = @(v) upper(regexprep(v, '[^A-Za-z0-9]', ''));
wants = {'Patient_ID','Group','Age','Sex','Education years'};
flds = {'PID','Group','Age','Sex','Edu'};
M = struct();
for i = 1:numel(wants)
    idx = find(cellfun(@(v) strcmp(norm(v), norm(wants{i})), vn));
    assert(isscalar(idx), ...
        't1vbm_table: 分组表列 "%s" 缺失或重名（现有: %s）', ...
        wants{i}, strjoin(vn, ', '));
    M.(flds{i}) = vn{idx};
end
end

function T1 = makeTable1(subs, C)
if istable(subs)
    g=string(subs.Group); age=double(subs.Age); sex=double(subs.SexNum); edu=double(subs.Education);
else
    g=string({subs.Group})'; age=[subs.Age]'; sex=[subs.SexNum]'; edu=[subs.Edu]';
end
iA = g == C.gA; iB = g == C.gB;
assert(all(isfinite(age(iA))) && all(isfinite(age(iB))), 'Table1: Age 含缺失/非有限值');
assert(all(isfinite(edu(iA))) && all(isfinite(edu(iB))), 'Table1: Education 含缺失/非有限值');
assert(all(ismember(sex(iA | iB), [0 1])), 'Table1: Sex 必须编码为 0/1');

nA = sum(iA); nB = sum(iB);
[~,pAge,ciAge,stAge] = ttest2(age(iA), age(iB), 'Vartype','unequal');
[~,pEdu,ciEdu,stEdu] = ttest2(edu(iA), edu(iB), 'Vartype','unequal');
[~,pSex,~,stSex] = ttest2(double(sex(iA)), double(sex(iB)), 'Vartype','unequal'); %#ok<ASGLU>
% 性别主报告采用 Fisher/chi-square；小样本时优先 Fisher。
tab = [sum(sex(iA)==1), sum(sex(iA)==0); sum(sex(iB)==1), sum(sex(iB)==0)];
pSex = NaN; sexMethod = 'not tested';
try
    if all(tab(:) >= 5)
        [~,pSex] = chi2test2x2(tab);
        sexMethod = 'chi-square';
    else
        pSex = fisherExact2x2(tab);
        sexMethod = 'Fisher exact';
    end
catch
    % 无统计工具箱时保留描述性结果，不伪造 p 值。
end

labels = {'Age, mean (SD)'; 'Male, n (%)'; 'Education, mean (SD)'};
aMean = [mean(age(iA)); 100*mean(sex(iA)==1); mean(edu(iA))];
aSD = [std(age(iA)); NaN; std(edu(iA))];
bMean = [mean(age(iB)); 100*mean(sex(iB)==1); mean(edu(iB))];
bSD = [std(age(iB)); NaN; std(edu(iB))];
p = [pAge; pSex; pEdu];
T1 = table(labels, aMean, aSD, bMean, bSD, p, ...
    'VariableNames', {'Variable',[C.gA '_value'],[C.gA '_SD'],[C.gB '_value'],[C.gB '_SD'],'P'});
T1.Properties.UserData = struct('ageCI',ciAge,'ageT',stAge.tstat,'eduT',stEdu.tstat, ...
    'sexMethod',sexMethod,'sexTable',tab,'nA',nA,'nB',nB);
end

function [h,p] = chi2test2x2(obs)
row = sum(obs,2); col = sum(obs,1); N = sum(row);
expv = row*col/N;
chi2 = sum((obs-expv).^2 ./ expv, 'all');
p = 1-chi2cdf(chi2,1); h = p < 0.05;
end

function p = fisherExact2x2(obs)
a=obs(1,1); b=obs(1,2); c=obs(2,1); d=obs(2,2); n=sum(obs,'all');
p0 = exp(gammaln(a+b+1)+gammaln(c+d+1)+gammaln(a+c+1)+gammaln(b+d+1)- ...
    gammaln(a+1)-gammaln(b+1)-gammaln(c+1)-gammaln(d+1)-gammaln(n+1));
lo=max(0,a-d); hi=min(a+b,a+c); ps=0;
for x=lo:hi
    y=a+b-x; z=a+c-x; w=n-a-b-c+x;
    px=exp(gammaln(a+b+1)+gammaln(c+d+1)+gammaln(a+c+1)+gammaln(b+d+1)- ...
        gammaln(x+1)-gammaln(y+1)-gammaln(z+1)-gammaln(w+1)-gammaln(n+1));
    if px <= p0*(1+1e-12), ps=ps+px; end
end
p=min(1,ps);
end

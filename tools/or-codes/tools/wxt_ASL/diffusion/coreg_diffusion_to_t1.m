function coreg_diffusion_to_t1(opt)
% coreg_diffusion_to_t1 - SPM12-based b0->T1 coregistration for DTI/DKI/IVIM
%
% Reads manifest.tsv produced by prep_coreg_files.py, then for each
% (subject, modality):
%   * spm_coreg(T1, b0) -> rigid transform
%   * apply transform to b0 and all derived parameter maps
%   * save coreg_matrix_<subj>_<mod>.mat and QC png to
%     derivatives/Diffusion_Coreg/<subj>/
%
% Usage:
%   coreg_diffusion_to_t1
%   coreg_diffusion_to_t1(struct('subs', {'sub-007'}, 'mods', {'dki'}))
if nargin < 1
    opt = struct();
end

studyRoot = getOpt(opt, 'studyRoot', 'L:\SSD\wxt_SSD');
manifest  = getOpt(opt, 'manifest', fullfile(studyRoot, 'derivatives', '.tmp_coreg_nii', 'manifest.tsv'));
outRoot   = getOpt(opt, 'outRoot', fullfile(studyRoot, 'derivatives', 'Diffusion_Coreg'));
force     = getOpt(opt, 'force', false);
subsFilt  = getOpt(opt, 'subs', []);
modsFilt  = getOpt(opt, 'mods', []);

if ~exist(manifest, 'file')
    error('manifest not found: %s', manifest);
end

fh = fopen(manifest, 'r');
hdr = fgetl(fh);
entries = struct('subject', {}, 'mod', {}, 't1', {}, 'b0', {}, 'params', {});
while ~feof(fh)
    line = fgetl(fh);
    if isempty(line) || strcmp(line, char([13]))
        continue;
    end
    flds = splitTsvLine(line, 5);
    if numel(flds) < 5
        continue;
    end
    entries(end+1) = struct('subject', flds{1}, 'mod', flds{2}, 't1', flds{3}, 'b0', flds{4}, 'params', flds{5});
end
fclose(fh);
fprintf('Manifest entries: %d\n', numel(entries));

if ischar(subsFilt) && ~isempty(subsFilt)
    % support reading subject list from a text file: subs = 'file:path'
    if startsWith(subsFilt, 'file:')
        listFile = subsFilt(6:end);
        fh = fopen(listFile, 'r');
        subsFilt = {};
        while ~feof(fh)
            l = strtrim(fgetl(fh));
            if ~isempty(l)
                subsFilt{end+1} = l;
            end
        end
        fclose(fh);
    else
        subsFilt = strsplit(subsFilt, ',');
        subsFilt = cellfun(@strtrim, subsFilt, 'UniformOutput', false);
    end
end
if ischar(modsFilt) && ~isempty(modsFilt)
    modsFilt = strsplit(modsFilt, ',');
end
if ~isempty(modsFilt)
    modsFilt = cellfun(@strtrim, modsFilt, 'UniformOutput', false);
end

nOK = 0;
nErr = 0;
tStart = tic;
for i = 1:numel(entries)
    e = entries(i);
    if ~isempty(subsFilt) && ~any(strcmp(subsFilt, e.subject))
        continue;
    end
    if ~isempty(modsFilt) && ~any(strcmp(modsFilt, e.mod))
        continue;
    end
    if ~exist(e.t1, 'file') || ~exist(e.b0, 'file')
        fprintf(2, '[MISS] %s %s\n', e.subject, e.mod);
        nErr = nErr + 1;
        continue;
    end

    outDir = fullfile(outRoot, e.subject);
    marker = fullfile(outDir, sprintf('.done_%s', e.mod));
    if exist(marker, 'file') && ~force
        nOK = nOK + 1;
        continue;
    end

    try
        doOne(e, outDir);
        fh2 = fopen(marker, 'w'); fclose(fh2);
        nOK = nOK + 1;
    catch ME
        fprintf(2, '[ERR] %s %s: %s\n', e.subject, e.mod, ME.message);
        nErr = nErr + 1;
    end
end
fprintf('Done. OK=%d ERR=%d elapsed=%.1fs\n', nOK, nErr, toc(tStart));
end
%% ---------------------------------------------------------------------
function doOne(e, outDir)
    mkdir(outDir);

    % 1) coregistration: b0 -> T1 via spm_coreg (returns 6 rigid params)
    VG = spm_vol(e.t1);   % reference
    VF = spm_vol(e.b0);   % source (moving)
    flags = struct('sep',[4 2],'params',[0 0 0 0 0 0],'fwhm',[7 7],'cost_fun','nmi','tol',[0.02 0.02 0.02 0.001 0.001 0.001],'graphics',0);
    x = spm_coreg(VG, VF, flags);

    % mapping voxel G -> voxel F (SPM coreg convention):
    %   Fvox = inv(VF.mat) * spm_matrix(x) * VG.mat * Gvox
    aff = inv(VF.mat) * spm_matrix(x) * VG.mat;   % 4x4 G-voxel -> F-voxel
    save(fullfile(outDir, sprintf('coreg_matrix_%s_%s.mat', e.subject, e.mod)), 'aff');

    % 3) resample b0 + derived param maps onto T1 grid
    resliceToT1(e.b0, e.t1, aff, fullfile(outDir, sprintf('coreg_%s_b0_to_T1.nii.gz', e.mod)));
    if ~isempty(e.params)
        mkdir(fullfile(outDir, 'params'));
        pairs = parseParams(e.params);
        for j = 1:numel(pairs)
            [pp, pname] = fileparts(pairs{j});
            resliceToT1(pairs{j}, e.t1, aff, fullfile(outDir, 'params', sprintf('coreg_%s_%s.nii.gz', e.mod, pname)));
        end
    end

    % 4) QC png (best-effort, GUI-free)
    try
        makeQC(e, outDir);
    catch
    end
end
%% ---------------------------------------------------------------------
function pairs = parseParams(s)
    items = strsplit(s, ',');
    pairs = {};
    for i = 1:numel(items)
        kv = strsplit(items{i}, '=');
        if numel(kv) == 2 && exist(kv{2}, 'file')
            pairs{end+1} = kv{2};
        end
    end
end

function resliceToT1(srcNii, refNii, aff, dstGz)
    % Reslice srcNii onto refNii grid using SPM coreg convention:
    %   aff: 4x4 maps G-voxel -> F-voxel, i.e. Fvox = aff * Gvox.
    VF = spm_vol(srcNii);
    VG = spm_vol(refNii);
    nX = VG.dim(1); nY = VG.dim(2); nZ = VG.dim(3);
    [Xg, Yg, Zg] = ndgrid(1:nX, 1:nY, 1:nZ);
    Gvox = [Xg(:)'; Yg(:)'; Zg(:)'; ones(1, nX*nY*nZ)];
    Fvox = aff * Gvox;
    data = spm_sample_vol(VF, Fvox(1, :), Fvox(2, :), Fvox(3, :), 1);  % trilinear
    data = reshape(data, [nX, nY, nZ]);
    % write to plain nii then gzip to dstGz (near-atomically)
    tmpPlain = [dstGz(1:end-7) '_tmp.nii'];
    Vout = struct('fname', tmpPlain, 'dim', VG.dim, 'mat', VG.mat, 'dt', [spm_type('float32'), 0], 'descrip', 'resliced b0->T1');
    spm_write_vol(Vout, data);
    gzip(tmpPlain);
    gzTmp = [tmpPlain '.gz'];
    if exist(gzTmp, 'file')
        if exist(dstGz, 'file'), delete(dstGz); end
        movefile(gzTmp, dstGz, 'f');
    end
    delete(tmpPlain);
end

function copyAndGzip(src, dstGz)
    dstPlain = dstGz(1:end-3);
    copyfile(src, dstPlain, 'f');
    gzip(dstPlain);
    gzTmp = [dstPlain '.gz'];
    if exist(gzTmp, 'file')
        if exist(dstGz, 'file'), delete(dstGz); end
        movefile(gzTmp, dstGz, 'f');
    end
end

function makeQC(e, outDir)
    Vb = spm_vol(e.b0);
    Vt = spm_vol(e.t1);
    Ib = spm_read_vols(Vb);
    It = spm_read_vols(Vt);
    bn = double(Ib); bn = (bn - min(bn(:))) / max(eps, max(bn(:)) - min(bn(:)));
    tn = double(It); tn = (tn - min(tn(:))) / max(eps, max(tn(:)) - min(tn(:)));
    sh = min(size(tn), size(bn));
    c = floor(sh / 2);
    fig = figure('visible', 'off');
    subplot(2, 2, 1); imshow(rot90(tn(:, :, c(3))), [], 'colormap', gray(256));
    title('T1');
    subplot(2, 2, 2); imshow(rot90(bn(:, :, c(3))), [], 'colormap', gray(256));
    title('b0 native');
    subplot(2, 2, 3); imshow(rot90(tn(:, :, c(3))), [], 'colormap', gray(256));
    title('T1 (same)');
    subplot(2, 2, 4); imshow(rot90(bn(:, :, c(3))), [], 'colormap', gray(256));
    title('b0 (native ref)');
    pngPath = fullfile(outDir, sprintf('qc_coreg_%s_%s.png', e.subject, e.mod));
    print(fig, pngPath, '-dpng', '-r80');
    close(fig);
end

function v = getOpt(opt, key, default)
    if isfield(opt, key) && ~isempty(opt.(key))
        v = opt.(key);
    else
        v = default;
    end
end

function flds = splitTsvLine(line, n)
    parts = strsplit(line, sprintf('\t'));
    flds = cell(1, n);
    for i = 1:n
        if i <= numel(parts)
            flds{i} = strtrim(parts{i});
        else
            flds{i} = '';
        end
    end
end
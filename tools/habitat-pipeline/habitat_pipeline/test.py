"""
test.py
=======
多参数生境影像组学流程 —— 本地冒烟测试脚本

背景
----
本脚本用于在不依赖真实医学影像数据的情况下，验证流程各模块的正确性，
并针对用户提供的真实数据表 combined.csv 校验 data_loader.py 的解析
鲁棒性（该表存在列名别名 img_path、is_reference 含非标准取值 'fuzzy'、
每位患者仅单序列等真实世界数据的常见"脏数据"特征）。

覆盖范围
--------
    1. 数据解析
       - test_data_loader_real_csv      针对 combined.csv 的真实解析
       - test_data_loader_edge_cases    合成 CSV 覆盖多序列/异常标记等场景
    2. 预处理（preprocessing.py，依赖 SimpleITK）
       - test_preprocessing_ct_minmax   CT 分支：重采样 + minmax 归一化
       - test_preprocessing_mri_zscore  MRI 分支：N4 + zscore 归一化
    3. 生境划分（habitat.py，依赖 SimpleITK / scikit-learn / scipy）
       - test_habitat_voxel_features        多序列融合体素特征提取
       - test_habitat_parametric_map_rule    参数图派生规则
       - test_habitat_local_clustering       单患者本地聚类模式
       - test_habitat_superpixel              Superpixel 预分割方案（依赖 scikit-image）
       - test_cohort_habitat_model            全队列一致建模（拟合 + 预测）
    4. 可选重依赖模块可用性探测（不视为失败，仅标记 SKIP）
       - test_registration_module_available   依赖 ANTsPy
       - test_radiomics_module_available      依赖 Pictologics
    5. pipeline.py 整体可导入性（若上述重依赖缺失则标记 SKIP 而非 FAIL）

运行方式
--------
    python test.py                        # 使用脚本同目录下的 combined.csv
    python test.py --csv /path/to/xx.csv  # 指定其他数据表
    python test.py -v                     # 输出各模块内部 INFO 日志

退出码：0 = 无 FAIL（PASS/SKIP 均视为正常），1 = 存在 FAIL
"""

import os
import sys
import shutil
import tempfile
import logging
import argparse
import traceback
from contextlib import contextmanager

import numpy as np

logger = logging.getLogger("test")


class SkipTest(Exception):
    """用于标记"因缺少可选依赖而跳过"的测试，与真正的失败区分开。"""
    pass


# ═══════════════════════════════════════════════════════════════════
#  测试框架（极简版，不引入 pytest 依赖）
# ═══════════════════════════════════════════════════════════════════

RESULTS = {"PASS": [], "FAIL": [], "SKIP": []}


def run_test(name: str, fn):
    """执行单个测试函数，捕获 SkipTest / 其他异常并记录结果。"""
    print(f"\n{'─'*70}\n▶ {name}")
    try:
        fn()
        RESULTS["PASS"].append(name)
        print(f"  [PASS] {name}")
    except SkipTest as e:
        RESULTS["SKIP"].append((name, str(e)))
        print(f"  [SKIP] {name} — {e}")
    except Exception as e:
        RESULTS["FAIL"].append((name, e))
        print(f"  [FAIL] {name} — {e}")
        traceback.print_exc()


@contextmanager
def temp_dict_values(d: dict, **kwargs):
    """临时修改字典 d 中的若干键值（原地修改，测试结束后自动恢复）。

    利用 config.py 中各配置项均为模块级可变对象（dict/list）这一点，
    此处对同一对象原地赋值，其余模块通过 `from config import XXX`
    绑定的同名引用会同步看到修改（无需重新 import），测试结束后恢复
    原值，不污染其他测试用例。
    """
    original = {k: d[k] for k in kwargs if k in d}
    d.update(kwargs)
    try:
        yield
    finally:
        for k in kwargs:
            if k in original:
                d[k] = original[k]
            else:
                d.pop(k, None)


# ═══════════════════════════════════════════════════════════════════
#  合成数据构造（不依赖真实医学影像）
# ═══════════════════════════════════════════════════════════════════

def _make_synthetic_volume(shape=(20, 24, 24), spacing=(1.0, 1.0, 2.0),
                            seed=0, add_bias_field=False):
    """
    构造一个带"病灶"的合成 3D 体数据 + 对应的球形 ROI mask。

    设计目的：病灶区域强度显著高于背景，且背景内随机加入两个子区域
    （用不同均值模拟"生境异质性"），便于后续聚类测试有明确的可分结构。

    Parameters
    ----------
    shape           : tuple — (Z, Y, X) 体素尺寸（刻意取小以加速测试）
    spacing         : tuple — (x, y, z) 体素间距（mm）
    seed            : int   — 随机种子
    add_bias_field  : bool  — 是否叠加低频乘性偏置场（用于测试 N4 校正）

    Returns
    -------
    (sitk.Image, sitk.Image) — (图像, 二值 ROI mask)
    """
    import SimpleITK as sitk

    rng = np.random.RandomState(seed)
    base = rng.normal(100, 10, size=shape).astype(np.float32)

    zz, yy, xx = np.indices(shape)
    center = np.array(shape) / 2.0
    dist = np.sqrt((zz - center[0])**2 + (yy - center[1])**2 + (xx - center[2])**2)
    radius = min(shape) / 3.0
    roi = (dist <= radius)

    # 在 ROI 内部构造两个强度不同的子区域，模拟生境异质性
    sub_a = roi & (xx <= center[2])
    sub_b = roi & (xx > center[2])
    base[sub_a] += rng.normal(40, 8, size=int(sub_a.sum()))
    base[sub_b] += rng.normal(90, 8, size=int(sub_b.sum()))

    if add_bias_field:
        bias_curve = 1.0 + 0.35 * np.sin(np.linspace(0, np.pi, shape[0]))
        base = base * bias_curve[:, None, None]

    img = sitk.GetImageFromArray(base.astype(np.float32))
    img.SetSpacing(spacing)
    mask = sitk.GetImageFromArray(roi.astype(np.uint8))
    mask.CopyInformation(img)
    return img, mask


def _write_nii(img, path):
    import SimpleITK as sitk
    sitk.WriteImage(img, path)
    return path


# ═══════════════════════════════════════════════════════════════════
#  1. 数据解析测试
# ═══════════════════════════════════════════════════════════════════

def test_data_loader_real_csv(csv_path: str):
    """
    针对用户提供的真实数据表 combined.csv 校验解析结果。

    该表的真实世界特征：
        - 列名为 img_path（而非 image_path）
        - is_reference 列部分取值为 'fuzzy'（非标准 0/1）
        - 每位患者仅有 1 行（单序列 CT_plain）
        - 额外携带 label 列（DDL / WDL 两类标签）
    验证：data_loader 应能全部正确解析（自动推断参考序列），不因
    列名或 is_reference 取值差异而错误跳过患者。
    """
    from data_loader import load_patient_data

    if not os.path.exists(csv_path):
        raise SkipTest(f"未找到 {csv_path}，跳过真实数据表测试")

    patients = load_patient_data(csv_path, validate_paths=False, default_modality="CT")

    assert len(patients) > 0, "应至少解析出 1 位患者"

    # 结构完整性检查
    for p in patients[:5]:
        assert 'patient_id' in p and 'reference_sequence' in p and 'roi_path' in p
        assert p['reference_sequence'] in p['sequences']
        assert p['sequences'][p['reference_sequence']]['is_reference'] is True

    n_with_label = sum(1 for p in patients if p.get('label'))
    print(f"    解析患者数：{len(patients)}，携带 label 的患者数：{n_with_label}")

    labels = [p['label'] for p in patients if p.get('label')]
    if labels:
        from collections import Counter
        label_counts = Counter(labels)
        print(f"    label 分布：{dict(label_counts)}")
        # 不对具体取值（如 DDL/WDL、Negative/Positive 等）做硬编码假设 ——
        # 不同数据集的标签体系不同，这里只做通用性检查：非空字符串即可。
        assert all(isinstance(v, str) and v.strip() for v in labels), \
            "label 应为非空字符串"
    else:
        print("    该数据表未提供 label 列，或所有患者 label 为空（跳过标签相关检查）。")

    # 结构性检查：每位患者的序列数、参考序列分布，帮助快速判断数据是否符合预期
    seq_counts = [len(p['sequences']) for p in patients]
    print(f"    每位患者序列数：min={min(seq_counts)}, max={max(seq_counts)}, "
          f"mean={sum(seq_counts)/len(seq_counts):.1f}")


def test_data_loader_edge_cases():
    """
    合成 CSV 覆盖以下场景，验证 data_loader 的分组/推断/跳过逻辑：
        A. 正常多序列 + 显式 is_reference=1 → 应成功解析
        B. 多序列但无任何 is_reference 标记、且 >1 序列携带 roi_path
           → 无法唯一确定参考序列，应跳过
        C. 单序列、is_reference 缺失/为空 → 自动推断为参考序列
        D. 两条 is_reference=1（同一患者）→ 因标记冲突应跳过
    """
    from data_loader import load_patient_data

    tmp_dir = tempfile.mkdtemp(prefix="dl_edge_")
    try:
        # 创建空文件占位，validate_paths=False 时不检查其内容
        def touch(name):
            p = os.path.join(tmp_dir, name)
            open(p, 'a').close()
            return p

        img_a1, roi_a = touch("A_plain.nii"), touch("A_roi.nii")
        img_a2 = touch("A_arterial.nii")
        img_b1, roi_b1 = touch("B_plain.nii"), touch("B_roi1.nii")
        img_b2, roi_b2 = touch("B_arterial.nii"), touch("B_roi2.nii")
        img_c, roi_c = touch("C_plain.nii"), touch("C_roi.nii")
        img_d1, roi_d1 = touch("D_plain.nii"), touch("D_roi1.nii")
        img_d2, roi_d2 = touch("D_arterial.nii"), touch("D_roi2.nii")

        csv_content = (
            "patient_id,sequence,modality,image_path,roi_path,is_reference\n"
            f"A,CT_plain,CT,{img_a1},{roi_a},1\n"
            f"A,CT_arterial,CT,{img_a2},,0\n"
            f"B,CT_plain,CT,{img_b1},{roi_b1},\n"          # 场景 B：无标记但两条都有 roi
            f"B,CT_arterial,CT,{img_b2},{roi_b2},\n"
            f"C,CT_plain,CT,{img_c},{roi_c},\n"             # 场景 C：单序列，自动推断
            f"D,CT_plain,CT,{img_d1},{roi_d1},1\n"          # 场景 D：两条显式标记冲突
            f"D,CT_arterial,CT,{img_d2},{roi_d2},1\n"
        )
        csv_path = os.path.join(tmp_dir, "edge.csv")
        with open(csv_path, 'w', encoding='utf-8') as f:
            f.write(csv_content)

        patients = load_patient_data(csv_path, validate_paths=False, default_modality="CT")
        ids = {p['patient_id'] for p in patients}

        assert "A" in ids, "场景 A（显式标记）应成功解析"
        assert "C" in ids, "场景 C（单序列自动推断）应成功解析"
        assert "B" not in ids, "场景 B（标记缺失且歧义）应被跳过"
        assert "D" not in ids, "场景 D（标记冲突）应被跳过"

        rec_a = next(p for p in patients if p['patient_id'] == "A")
        assert rec_a['reference_sequence'] == "CT_plain"
        assert len(rec_a['sequences']) == 2

        print("    通过全部 4 个边界场景（A 保留，B/D 跳过，C 自动推断）")
    finally:
        shutil.rmtree(tmp_dir, ignore_errors=True)


# ═══════════════════════════════════════════════════════════════════
#  2. 预处理测试
# ═══════════════════════════════════════════════════════════════════

def test_preprocessing_ct_minmax():
    """CT 分支：验证重采样至目标体素间距 + minmax 归一化至 [0,1]。"""
    import SimpleITK as sitk
    from preprocessing import preprocess_sequence
    from config import PREPROC

    tmp_dir = tempfile.mkdtemp(prefix="preproc_ct_")
    try:
        img, mask = _make_synthetic_volume(spacing=(0.7, 0.7, 3.0))
        img_path = _write_nii(img, os.path.join(tmp_dir, "ct.nii.gz"))
        roi_path = _write_nii(mask, os.path.join(tmp_dir, "roi.nii.gz"))

        out_dir = os.path.join(tmp_dir, "out")
        processed_img, processed_mask = preprocess_sequence(
            image_path=img_path, output_dir=out_dir, patient_id="synthCT",
            sequence="CT_plain", modality="CT", roi_path=roi_path,
        )

        target_spacing = PREPROC["target_spacing"]
        got_spacing = tuple(round(s, 3) for s in processed_img.GetSpacing())
        assert got_spacing == tuple(round(s, 3) for s in target_spacing), (
            f"重采样后间距 {got_spacing} 应等于目标 {target_spacing}"
        )

        arr = sitk.GetArrayFromImage(processed_img)
        assert arr.min() >= -1e-3 and arr.max() <= 1.0 + 1e-3, (
            f"minmax 归一化后取值应落在 [0,1]，实际范围 [{arr.min():.4f}, {arr.max():.4f}]"
        )

        n_vox = int(sitk.GetArrayFromImage(processed_mask).sum())
        assert n_vox > 0, "ROI mask 体素数应 > 0"
        print(f"    重采样间距={got_spacing}，归一化范围=[{arr.min():.3f}, {arr.max():.3f}]，ROI体素={n_vox}")
    finally:
        shutil.rmtree(tmp_dir, ignore_errors=True)


def test_preprocessing_mri_zscore():
    """MRI 分支：验证 N4 偏置场校正可正常执行 + zscore 归一化（均值≈0，标准差≈1）。"""
    import SimpleITK as sitk
    from preprocessing import preprocess_sequence
    import config

    tmp_dir = tempfile.mkdtemp(prefix="preproc_mri_")
    try:
        # 缩短 N4 迭代次数以加速测试（原地修改共享 dict，测试结束后恢复）
        with temp_dict_values(
            config.PREPROC["modality_settings"]["MRI"],
            n4_iterations=[5, 5],
        ):
            img, mask = _make_synthetic_volume(spacing=(1.0, 1.0, 1.0), add_bias_field=True)
            img_path = _write_nii(img, os.path.join(tmp_dir, "mri.nii.gz"))
            roi_path = _write_nii(mask, os.path.join(tmp_dir, "roi.nii.gz"))

            out_dir = os.path.join(tmp_dir, "out")
            processed_img, processed_mask = preprocess_sequence(
                image_path=img_path, output_dir=out_dir, patient_id="synthMRI",
                sequence="T2WI", modality="MRI", roi_path=roi_path,
            )

        arr = sitk.GetArrayFromImage(processed_img)
        # 注意：preprocess_sequence 中 normalize_intensity 在归一化时尚未加载
        # mask（mask 是随后才对齐/返回的），因此 zscore 统计范围是整幅图像，
        # 而非仅 ROI 区域——这与 preprocessing.py 的既定设计一致，测试按
        # 全图统计量校验，而非 ROI 内统计量。
        print(f"    zscore 后全图均值={arr.mean():.4f}，标准差={arr.std():.4f}")
        assert abs(arr.mean()) < 0.1, "zscore 归一化后全图均值应接近 0"
        assert abs(arr.std() - 1.0) < 0.1, "zscore 归一化后全图标准差应接近 1"
    finally:
        shutil.rmtree(tmp_dir, ignore_errors=True)


# ═══════════════════════════════════════════════════════════════════
#  3. 生境划分测试
# ═══════════════════════════════════════════════════════════════════

def test_habitat_voxel_features():
    """验证多序列融合体素特征提取：通道数与 feature_names 一致，无 NaN/Inf。"""
    from habitat import compute_voxel_feature_maps

    img_a, mask = _make_synthetic_volume(seed=1)
    img_b, _    = _make_synthetic_volume(seed=2)   # 模拟第二个已配准对齐的序列

    sequence_images = {"CT_plain": img_a, "CT_arterial": img_b}
    voxel_feats, mask_arr, feature_names = compute_voxel_feature_maps(sequence_images, mask)

    n_roi_voxels = int(mask_arr.sum())
    assert voxel_feats.shape[0] == n_roi_voxels
    assert voxel_feats.shape[1] == len(feature_names), "特征矩阵列数应与 feature_names 长度一致"
    assert np.isfinite(voxel_feats).all(), "特征矩阵不应包含 NaN/Inf"
    print(f"    ROI体素数={n_roi_voxels}，融合特征通道数={voxel_feats.shape[1]}：{feature_names}")


def test_habitat_parametric_map_rule():
    """验证参数图派生规则（relative_change）能正确计算并进入融合特征。"""
    import config
    from habitat import compute_voxel_feature_maps

    img_a, mask = _make_synthetic_volume(seed=3)
    img_b, _    = _make_synthetic_volume(seed=4)
    sequence_images = {"CT_plain": img_a, "CT_arterial": img_b}

    rule = {"name": "test_enhancement", "type": "relative_change",
            "seq_a": "CT_plain", "seq_b": "CT_arterial"}
    config.PARAMETRIC_MAPS.append(rule)   # 原地修改共享 list
    try:
        voxel_feats, mask_arr, feature_names = compute_voxel_feature_maps(sequence_images, mask)
        assert "param_test_enhancement" in feature_names, "派生参数图应出现在特征名列表中"

        # 手工验证数值正确性（取 ROI 内首个体素核对）
        import SimpleITK as sitk
        arr_a = sitk.GetArrayFromImage(img_a).astype(np.float32)
        arr_b = sitk.GetArrayFromImage(img_b).astype(np.float32)
        expected = (arr_b - arr_a) / (np.abs(arr_a) + config.PARAMETRIC_MAP_EPSILON)
        expected_roi = expected[mask_arr]

        col_idx = feature_names.index("param_test_enhancement")
        np.testing.assert_allclose(
            voxel_feats[:, col_idx], expected_roi, rtol=1e-4, atol=1e-4
        )
        print(f"    参数图 'test_enhancement' 数值校验通过（{len(expected_roi)} 体素）")
    finally:
        config.PARAMETRIC_MAPS.remove(rule)   # 恢复现场，避免影响其他测试


def test_habitat_local_clustering():
    """单患者本地模式：build_habitats(global_model=None) 应返回 K∈k_range 个 Habitat Mask。"""
    import config
    from habitat import build_habitats
    import SimpleITK as sitk

    tmp_dir = tempfile.mkdtemp(prefix="habitat_local_")
    try:
        with temp_dict_values(config.HABITAT,
                               k_range=range(2, 4), consensus_n_iter=5,
                               use_superpixel=False):
            img_a, mask = _make_synthetic_volume(seed=5)
            img_b, _    = _make_synthetic_volume(seed=6)
            sequence_images = {"CT_plain": img_a, "CT_arterial": img_b}

            masks, k, detail = build_habitats(
                sequence_images=sequence_images, mask=mask,
                output_dir=tmp_dir, patient_id="synthLocal", global_model=None,
            )

            assert 2 <= k <= 3, f"K={k} 应落在测试设定的 k_range=[2,3] 内"
            assert len(masks) == k
            total_vox = sum(int(sitk.GetArrayFromImage(m).sum()) for m in masks)
            roi_vox   = int(sitk.GetArrayFromImage(mask).sum())
            assert total_vox == roi_vox, "各 Habitat 体素数之和应等于 ROI 总体素数（分区完整且不重叠）"
            print(f"    本地聚类完成：K={k}，各 Habitat 体素数之和={total_vox}（=ROI 体素数 {roi_vox}）")
    finally:
        shutil.rmtree(tmp_dir, ignore_errors=True)


def test_habitat_superpixel():
    """Superpixel 预分割方案：验证分割 + 按超体素聚合 + 完整 build_habitats 流程。"""
    try:
        import skimage  # noqa: F401
    except ImportError:
        raise SkipTest("scikit-image 未安装，跳过 Superpixel 测试")

    import config
    from habitat import (compute_superpixel_segmentation, aggregate_by_superpixel,
                          compute_voxel_feature_maps, build_habitats)

    tmp_dir = tempfile.mkdtemp(prefix="habitat_sp_")
    try:
        with temp_dict_values(config.HABITAT,
                               k_range=range(2, 4), consensus_n_iter=5,
                               use_superpixel=True):
            config.HABITAT["superpixel"]["n_segments"] = 30   # 小体积用少量超体素即可

            img_a, mask = _make_synthetic_volume(seed=7)
            sequence_images = {"CT_plain": img_a}

            sp_labels = compute_superpixel_segmentation(sequence_images, mask)
            n_sp = len(np.unique(sp_labels[sp_labels >= 0]))
            assert n_sp > 1, "应生成多个超体素"

            voxel_feats, mask_arr, _ = compute_voxel_feature_maps(sequence_images, mask)
            sp_feats, sp_ids, voxel_sp_id = aggregate_by_superpixel(voxel_feats, mask_arr, sp_labels)
            assert sp_feats.shape[0] == n_sp
            assert sp_feats.shape[0] == len(sp_ids)

            masks, k, detail = build_habitats(
                sequence_images=sequence_images, mask=mask,
                output_dir=tmp_dir, patient_id="synthSP", global_model=None,
            )
            assert detail['use_superpixel'] is True
            assert len(masks) == k
            print(f"    Superpixel 数={n_sp}，聚合特征矩阵={sp_feats.shape}，最终 K={k}")
    finally:
        shutil.rmtree(tmp_dir, ignore_errors=True)


def test_cohort_habitat_model():
    """全队列一致建模：两名合成患者池化拟合全局模型，再分别 predict，标签应落在 [0,K)。"""
    import config
    from habitat import compute_voxel_feature_maps, CohortHabitatModel

    tmp_dir = tempfile.mkdtemp(prefix="cohort_model_")
    try:
        with temp_dict_values(config.HABITAT,
                               k_range=range(2, 4), consensus_n_iter=5):
            img1, mask1 = _make_synthetic_volume(seed=11)
            img2, mask2 = _make_synthetic_volume(seed=12)

            feats1, _, _ = compute_voxel_feature_maps({"CT_plain": img1}, mask1)
            feats2, _, _ = compute_voxel_feature_maps({"CT_plain": img2}, mask2)

            pooled = np.concatenate([feats1, feats2], axis=0).astype(np.float32)

            model = CohortHabitatModel()
            model.fit(pooled, tmp_dir)

            labels1 = model.predict(feats1)
            labels2 = model.predict(feats2)

            assert labels1.min() >= 0 and labels1.max() < model.optimal_k
            assert labels2.min() >= 0 and labels2.max() < model.optimal_k
            print(f"    全局模型 K={model.optimal_k}，患者1/2 标签范围均在 [0,{model.optimal_k})")

            # save/load 往返一致性
            model_path = os.path.join(tmp_dir, "model.joblib")
            model.save(model_path)
            reloaded = CohortHabitatModel.load(model_path)
            np.testing.assert_array_equal(reloaded.predict(feats1), labels1)
            print("    模型 save()/load() 往返预测结果一致")
    finally:
        shutil.rmtree(tmp_dir, ignore_errors=True)


# ═══════════════════════════════════════════════════════════════════
#  4. 可选重依赖模块可用性探测
# ═══════════════════════════════════════════════════════════════════

def test_registration_module_available():
    """registration.py 依赖 ANTsPy；未安装时标记 SKIP，不视为失败。"""
    try:
        import ants  # noqa: F401
    except ImportError:
        raise SkipTest("ANTsPy 未安装（pip install antspyx），跳过配准模块测试")

    import registration  # noqa: F401
    assert hasattr(registration, "register_sequence")
    print("    ANTsPy 已安装，registration.py 可正常导入")


def test_radiomics_module_available():
    """radiomics.py 依赖 Pictologics；未安装时标记 SKIP，不视为失败。"""
    try:
        import pictologics  # noqa: F401
    except ImportError:
        raise SkipTest("Pictologics 未安装，跳过放射组学模块测试")

    import radiomics  # noqa: F401
    assert hasattr(radiomics, "extract_features_all_habitats")
    print("    Pictologics 已安装，radiomics.py 可正常导入")


def test_pipeline_importable():
    """
    pipeline.py 在顶层 import 了 registration（依赖 ants）和 radiomics
    （依赖 pictologics），任一缺失都会在 import 阶段抛出 ImportError。
    这是预期行为：完整编排流程需要完整环境，此测试仅确认"要么能正常
    导入，要么因缺少可选重依赖而清晰失败"，不掩盖真正的语法/逻辑错误。
    """
    try:
        import pipeline  # noqa: F401
    except ImportError as e:
        missing = str(e)
        if "ants" in missing.lower() or "pictologics" in missing.lower():
            raise SkipTest(f"pipeline.py 需要完整依赖（ants/pictologics）：{e}")
        raise
    assert hasattr(pipeline, "run_pipeline")
    print("    pipeline.py 导入成功（完整依赖环境）")


# ═══════════════════════════════════════════════════════════════════
#  主入口
# ═══════════════════════════════════════════════════════════════════

def main():
    parser = argparse.ArgumentParser(description="多参数生境影像组学流程 —— 冒烟测试")
    parser.add_argument('--csv', default=os.path.join(os.path.dirname(os.path.abspath(__file__)), "combined.csv"),
                        help="真实数据表路径（默认：脚本同目录下的 combined.csv）")
    parser.add_argument('-v', '--verbose', action='store_true', help="输出各模块内部 INFO 日志")
    args = parser.parse_args()

    logging.basicConfig(
        level=logging.INFO if args.verbose else logging.WARNING,
        format='%(levelname)s  %(name)s  %(message)s',
    )

    tests = [
        ("数据解析 · 真实 combined.csv",       lambda: test_data_loader_real_csv(args.csv)),
        ("数据解析 · 边界场景",                 test_data_loader_edge_cases),
        ("预处理 · CT minmax 归一化",           test_preprocessing_ct_minmax),
        ("预处理 · MRI N4 + zscore 归一化",     test_preprocessing_mri_zscore),
        ("生境划分 · 多序列融合体素特征",        test_habitat_voxel_features),
        ("生境划分 · 参数图派生规则",            test_habitat_parametric_map_rule),
        ("生境划分 · 单患者本地聚类",            test_habitat_local_clustering),
        ("生境划分 · Superpixel 预分割",         test_habitat_superpixel),
        ("生境划分 · 全队列一致建模",            test_cohort_habitat_model),
        ("可用性 · registration.py（ANTsPy）",  test_registration_module_available),
        ("可用性 · radiomics.py（Pictologics）", test_radiomics_module_available),
        ("整体 · pipeline.py 可导入性",          test_pipeline_importable),
    ]

    for name, fn in tests:
        run_test(name, fn)

    print(f"\n{'='*70}")
    print(f"测试汇总：PASS={len(RESULTS['PASS'])}  "
          f"FAIL={len(RESULTS['FAIL'])}  SKIP={len(RESULTS['SKIP'])}")
    if RESULTS["SKIP"]:
        print("\n跳过的测试（通常因缺少可选依赖，非代码问题）：")
        for name, reason in RESULTS["SKIP"]:
            print(f"  - {name}: {reason}")
    if RESULTS["FAIL"]:
        print("\n失败的测试：")
        for name, err in RESULTS["FAIL"]:
            print(f"  - {name}: {err}")
        sys.exit(1)
    else:
        print("\n全部测试通过（含 SKIP）。")
        sys.exit(0)


if __name__ == "__main__":
    main()

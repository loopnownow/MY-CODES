import os
from os.path import dirname, join
import shutil
import pydicom
from pydicom.filereader import read_dicomdir
import re

path = r'./3/DICOMDIR'
dicom_dir = read_dicomdir(path)
base_dir = dirname(path)

for _idx, patient_record in enumerate(dicom_dir.patient_records, 1):  # got through each patient
    # 用 PatientID 建目录，不用 PatientName（避免患者姓名/PHI 进目录名）
    pid = str(getattr(patient_record, "PatientID", "") or "").strip()
    if not pid:
        pid = f"UNKNOWN_{_idx}"
    path1 = os.path.join(base_dir, pid)  # 拼接目录
    try: os.mkdir(path1) # 创建目录
    except FileExistsError: pass

    studies = patient_record.children  # got through each study
    for study in studies:
        path2 = os.path.join(path1, study.StudyID)
        try: os.mkdir(path2)  # 创建目录
        except FileExistsError: pass

        all_series = study.children  # go through each serie
        for series in all_series:
            series.SeriesDescription = re.sub(r'\W', "", series.SeriesDescription)#偶尔存在冒号和空格，去掉
            path3 = os.path.join(path2, series.SeriesDescription)
            try: os.mkdir(path3)  # 创建目录
            except FileExistsError: pass

            image_records = series.children  # go through each image
            image_filenames = [join(base_dir, *image_rec.ReferencedFileID) for image_rec in image_records]
            for image_filename in image_filenames:
                try: shutil.move(image_filename, path3)#移动文件
                except shutil.Error: pass
                except FileNotFoundError: pass
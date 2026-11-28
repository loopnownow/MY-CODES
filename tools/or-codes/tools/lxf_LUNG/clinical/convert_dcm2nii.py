r"""Batch conversion of DICOM directories to NIfTI.

The script walks through DICOM directories, finds each patient folder that 
contains a ``DICOMOBJ`` subfolder, extracts basic patient metadata, 
writes a ``patient_info.txt`` file and converts the DICOM series to 
compressed ``.nii.gz`` files using SimpleITK.

The output layout mirrors the input layout.
"""

import os
import re
import csv
import pydicom
import SimpleITK as sitk

# --- Configuration ---
CONFIG = {
    "ROOT_DIR": os.environ.get("CONFIG", ""),
    "OUTPUT_ROOT": os.environ.get("CONFIG_2", ""),
    "DRY_RUN": False,         # Set to False for real execution
    "RESUME": True,          # Breakpoint resume: skip already converted files
    "LIMIT_SAMPLES": None,   # Process only N samples for debugging. Set to None for all.
    "SPECIFIC_GROUPS": [],   # List of groups to process. Empty for all. 
                             # 用法解释：SPECIFIC_GROUPS 用于指定只处理 ROOT_DIR 下的某些子文件夹。
                             # 例如：["GroupA", "GroupB"] 将只处理 G:\SynapseMediaSets\GroupA 和 G:\SynapseMediaSets\GroupB。
                             # 留空则处理 ROOT_DIR 下的所有文件夹。
    "VERBOSE": True,         # Print detailed progress
}

def log(msg: str):
    if CONFIG.get("VERBOSE"):
        prefix = "[DRY-RUN] " if CONFIG.get("DRY_RUN") else ""
        print(f"{prefix}{msg}")

def sanitize_filename(name: str) -> str:
    """Remove or replace characters that are unsafe for filenames, allowing Chinese characters."""
    # Replace common separators with underscores
    name = name.replace('^', '_').replace(' ', '_').replace('=', '_')
    # Keep alphanumeric, underscores, dashes and CJK characters
    # CJK range: \u4e00-\u9fff
    clean_name = "".join(c if c.isalnum() or c in "_-" or '\u4e00' <= c <= '\u9fff' else "_" for c in name)
    # Remove multiple underscores
    clean_name = re.sub(r'_+', '_', clean_name)
    return clean_name.strip("_")

def extract_patient_info(dicom_path: str) -> dict:
    """Read the first DICOM file in *dicom_path* and return a dict of fields."""
    all_files = os.listdir(dicom_path)
    if not all_files:
        raise FileNotFoundError(f"No files found in {dicom_path}")
    
    ds = None
    for fname in all_files:
        try:
            ds = pydicom.dcmread(os.path.join(dicom_path, fname), stop_before_pixels=True)
            break
        except Exception:
            continue
    if ds is None:
        raise FileNotFoundError(f"No readable DICOM files in {dicom_path}")
    
    raw_name = str(ds.get('PatientName', ''))
    parts = raw_name.split('=')
    eng_name = parts[0].strip() if parts else ''
    chi_name = parts[1].strip().rstrip(',') if len(parts) > 1 else ''
    
    return {
        'PatientName': eng_name,
        'PatientNameChinese': chi_name,
        'PatientID': ds.get('PatientID', ''),
        'PatientSex': ds.get('PatientSex', ''),
        'PatientAge': ds.get('PatientAge', ''),
        'AccessionNumber': ds.get('AccessionNumber', ''),
        'StudyID': ds.get('StudyID', ''),
        'StudyDate': ds.get('StudyDate', ''),
        'StudyDescription': ds.get('StudyDescription', ''),
    }

def write_patient_info(info: dict, out_dir: str):
    txt_path = os.path.join(out_dir, "patient_info.txt")
    log(f"Writing patient info to {txt_path}")
    if CONFIG.get("DRY_RUN"):
        return
    with open(txt_path, "w", encoding="utf-8") as f:
        f.write(f"姓名: {info['PatientName']} {info['PatientNameChinese']}\n")
        f.write(f"性别: {info['PatientSex']}\n")
        f.write(f"年龄: {info['PatientAge']}\n")
        f.write(f"门诊号 (PatientID): {info['PatientID']}\n")
        f.write(f"影像号 (AccessionNumber): {info['AccessionNumber']}\n")
        f.write(f"检查ID (StudyID): {info['StudyID']}\n")
        f.write(f"检查日期: {info['StudyDate']}\n")
        f.write(f"检查描述: {info['StudyDescription']}\n")

def convert_series(dicom_dir: str, out_dir: str) -> list:
    """Convert all DICOM series in *dicom_dir* to NIfTI using SimpleITK."""
    reader = sitk.ImageSeriesReader()
    series_IDs = reader.GetGDCMSeriesIDs(dicom_dir)
    if not series_IDs:
        raise RuntimeError(f"No DICOM series found in {dicom_dir}")
    
    converted_paths = []
    for i, s_id in enumerate(series_IDs):
        try:
            series_file_names = reader.GetGDCMSeriesFileNames(dicom_dir, s_id)
            if not series_file_names:
                continue
            
            ds = pydicom.dcmread(series_file_names[0], stop_before_pixels=True)
            series_desc = ds.get('SeriesDescription') or ds.get('StudyDescription') or f"series_{i}"
            series_num = ds.get('SeriesNumber', i)
            
            clean_desc = "".join(c if c.isalnum() or c in "_-" else "_" for c in str(series_desc))
            filename = f"{series_num}_{clean_desc}.nii.gz"
            out_path = os.path.join(out_dir, filename)
            
            if CONFIG.get("RESUME") and os.path.exists(out_path):
                log(f"  Series {filename} already exists, skipping conversion.")
                converted_paths.append(out_path)
                continue

            log(f"Detected series {s_id} ({len(series_file_names)} files) -> {filename}")
            
            if not CONFIG.get("DRY_RUN"):
                log(f"  Reading and writing image...")
                reader.SetFileNames(series_file_names)
                image = reader.Execute()
                sitk.WriteImage(image, out_path)
            
            converted_paths.append(out_path)
        except Exception as e:
            log(f"  [WARN] Failed to convert series {s_id}: {e}")
            
    return converted_paths

def main():
    root_dir = CONFIG["ROOT_DIR"]
    output_root = CONFIG["OUTPUT_ROOT"]
    
    if not CONFIG.get("DRY_RUN"):
        os.makedirs(output_root, exist_ok=True)
    
    log(f"Starting conversion in {root_dir}")
    log(f"Output directory: {output_root}")
    
    processed_count = 0
    limit = CONFIG.get("LIMIT_SAMPLES")
    specific_groups = CONFIG.get("SPECIFIC_GROUPS")
    
    # Identify groups
    groups = sorted([g for g in os.listdir(root_dir) if os.path.isdir(os.path.join(root_dir, g)) and g != "nii"])
    
    stop_outer = False
    for group in groups:
        if specific_groups and group not in specific_groups:
            continue
            
        group_path = os.path.join(root_dir, group)
        print(f"\n--- Processing group: {group} ---")
        
        # Decide if this folder is one patient or contains many sub-patient folders
        if os.path.isdir(os.path.join(group_path, "DICOMOBJ")):
            target_patients = [(group, "")]
        else:
            sub_dirs = sorted([p for p in os.listdir(group_path) if os.path.isdir(os.path.join(group_path, p))])
            target_patients = [(p, p) for p in sub_dirs]

        for p_folder, p_sub in target_patients:
            patient_label = p_folder if not p_sub else p_sub
            patient_path = os.path.join(group_path, p_sub) if p_sub else group_path
            
            # Find DICOMOBJ
            dicom_dir = os.path.join(patient_path, "DICOMOBJ")
            if not os.path.isdir(dicom_dir):
                found = False
                for sub in os.listdir(patient_path):
                    if sub.upper() == "DICOMOBJ":
                        dicom_dir = os.path.join(patient_path, sub)
                        found = True
                        break
                if not found:
                    continue
            
            # Output path
            if not p_sub:
                dest_dir = os.path.join(output_root, p_folder)
            else:
                dest_dir = os.path.join(output_root, group, p_folder)
            
            if not CONFIG.get("DRY_RUN"):
                os.makedirs(dest_dir, exist_ok=True)
            
            try:
                log(f"Processing patient: {patient_label}")
                
                # Metadata breakpoint check
                info_txt = os.path.join(dest_dir, "patient_info.txt")
                if CONFIG.get("RESUME") and os.path.exists(info_txt):
                    log(f"  Metadata already exists. Skipping extraction.")
                else:
                    info = extract_patient_info(dicom_dir)
                    log(f"  Metadata: {info['PatientName']} ({info['PatientID']}), {info['PatientSex']}, {info['PatientAge']}")
                    write_patient_info(info, dest_dir)
                
                # Series conversion (it has its own resume logic)
                nii_paths = convert_series(dicom_dir, dest_dir)
                
                if nii_paths:
                    processed_count += 1
                
                if limit is not None and processed_count >= limit:
                    stop_outer = True
                    break
                    
            except Exception as e:
                print(f"[ERROR] Failed to process {patient_label} in {group}: {e}")
        
        if stop_outer:
            log(f"Limit reached ({limit} samples). Stopping.")
            break
                
    if CONFIG.get("DRY_RUN") and limit is not None:
        log("Dry run finished for limit. Skipping final CSV scan.")
        return

    print("\n--- Generating final CSV list ---")
    csv_rows = []
    if not os.path.exists(output_root):
        log(f"Output root {output_root} does not exist. Skipping CSV generation.")
        return

    # Recursive scan of output_root to find converted patients
    for g in sorted(os.listdir(output_root)):
        g_path = os.path.join(output_root, g)
        if not os.path.isdir(g_path) or g == "file_list.csv":
            continue
            
        # Check if g_path is a patient folder (direct) or a group folder
        # If g_path contains .nii.gz directly, it might be a patient folder from root
        has_nii = any(f.endswith('.nii.gz') for f in os.listdir(g_path))
        
        folders_to_check = []
        if has_nii or os.path.exists(os.path.join(g_path, "patient_info.txt")):
            folders_to_check.append(("", g)) # (parent, folder_name)
        else:
            for p in sorted(os.listdir(g_path)):
                p_path = os.path.join(g_path, p)
                if os.path.isdir(p_path):
                    folders_to_check.append((g, p))

        for group_name, p_name in folders_to_check:
            p_path = os.path.join(output_root, group_name, p_name) if group_name else os.path.join(output_root, p_name)
            nii_files = [f for f in os.listdir(p_path) if f.endswith('.nii.gz')]
            if nii_files:
                p_info = {'PatientName': p_name, 'PatientID': '', 'PatientSex': '', 'PatientAge': '', 'AccessionNumber': ''}
                txt_path = os.path.join(p_path, "patient_info.txt")
                if os.path.exists(txt_path):
                    try:
                        with open(txt_path, "r", encoding="utf-8") as tf:
                            for line in tf:
                                if ":" in line:
                                    k, v = line.split(":", 1)
                                    if "姓名" in k: p_info['PatientName'] = v.strip()
                                    elif "门诊号" in k: p_info['PatientID'] = v.strip()
                                    elif "性别" in k: p_info['PatientSex'] = v.strip()
                                    elif "年龄" in k: p_info['PatientAge'] = v.strip()
                                    elif "影像号" in k: p_info['AccessionNumber'] = v.strip()
                    except:
                        pass
                
                for nf in nii_files:
                    csv_rows.append({
                        'Group': group_name if group_name else "Root",
                        'PatientFolder': p_name,
                        'NIfTIPath': os.path.abspath(os.path.join(p_path, nf)),
                        '姓名': p_info['PatientName'],
                        '门诊号': p_info['PatientID'],
                        '性别': p_info['PatientSex'],
                        '年龄': p_info['PatientAge'],
                        '影像号': p_info['AccessionNumber'],
                    })

    csv_path = os.path.join(output_root, "file_list.csv")
    if csv_rows:
        fieldnames = ['Group', 'PatientFolder', 'NIfTIPath', '姓名', '门诊号', '性别', '年龄', '影像号']
        with open(csv_path, "w", newline='', encoding='utf-8-sig') as csvfile:
            writer = csv.DictWriter(csvfile, fieldnames=fieldnames)
            writer.writeheader()
            writer.writerows(csv_rows)
        print(f"CSV summary written to {csv_path}")

if __name__ == "__main__":
    main()

r"""Batch conversion of DICOM directories to NIfTI.

The script walks through ``HE_DCM_ROOT`` (excluding any existing ``nii``
directory), finds each patient folder that contains a ``DICOMOBJ`` subfolder,
extracts basic patient metadata, writes a ``patient_info.txt`` file and
converts the DICOM series to a compressed ``.nii.gz`` file using SimpleITK.

Note: the historical root ``L:\HE_dcm_ly`` is retired; set ``HE_DCM_ROOT`` to
the current DICOM root (the script fails loud when it is unset).

The output layout mirrors the input layout:

```
<HE_DCM_ROOT>\nii\<group>\<patient>\
    patient_info.txt
    <SeriesDescription>.nii.gz
```

If the same patient name appears in multiple groups, the group name is kept
as part of the output path to avoid name clashes.

After conversion a CSV file ``file_list.csv`` is generated in the ``nii``
directory, containing one row per converted patient.
"""

import os
import csv
import pydicom
import SimpleITK as sitk

ROOT_DIR = os.environ.get("HE_DCM_ROOT", "")
if not ROOT_DIR:
    raise SystemExit("HE_DCM_ROOT 未设置：旧根 L:\\HE_dcm_ly 已撤销，请指向当前 DICOM 根")
OUTPUT_ROOT = os.path.join(ROOT_DIR, "nii")

def extract_patient_info(dicom_path: str) -> dict:
    """Read the first DICOM file in *dicom_path* and return a dict of fields.

    The function is tolerant of missing tags – empty strings are used as
    fall‑backs.  ``SpecificCharacterSet`` is respected by pydicom, so the
    returned strings are correctly decoded.
    """
    # Some DICOM files may have no extension (e.g., "SYN00001").
    # List all files and pick the first one that pydicom can read.
    all_files = os.listdir(dicom_path)
    if not all_files:
        raise FileNotFoundError(f"No files found in {dicom_path}")
    # Find a readable DICOM file
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
    # Split "English=Chinese," format if present
    parts = raw_name.split('=')
    eng_name = parts[0].strip() if parts else ''
    chi_name = parts[1].strip().rstrip(',') if len(parts) > 1 else ''
    return {
        'PatientName': eng_name,
        'PatientNameChinese': chi_name,
        'PatientID': ds.get('PatientID', ''),
        'OtherPatientIDs': ds.get('OtherPatientIDs', ''),
        'PatientSex': ds.get('PatientSex', ''),
        'PatientAge': ds.get('PatientAge', ''),
        'AccessionNumber': ds.get('AccessionNumber', ''),
        'StudyID': ds.get('StudyID', ''),
        'StudyDate': ds.get('StudyDate', ''),
        'StudyDescription': ds.get('StudyDescription', ''),
    }

def write_patient_info(info: dict, out_dir: str):
    txt_path = os.path.join(out_dir, "patient_info.txt")
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
    """Convert all DICOM series in *dicom_dir* to NIfTI using SimpleITK.

    Returns a list of generated NIfTI file paths.
    """
    reader = sitk.ImageSeriesReader()
    series_IDs = reader.GetGDCMSeriesIDs(dicom_dir)
    if not series_IDs:
        # Fallback: check if the directory itself has DICOM files not recognized as series
        # or if there's only one file.
        raise RuntimeError(f"No DICOM series found in {dicom_dir}")
    
    converted_paths = []
    for i, s_id in enumerate(series_IDs):
        try:
            series_file_names = reader.GetGDCMSeriesFileNames(dicom_dir, s_id)
            if not series_file_names:
                continue
            
            reader.SetFileNames(series_file_names)
            image = reader.Execute()
            
            # Use the first file to get a descriptive name
            ds = pydicom.dcmread(series_file_names[0], stop_before_pixels=True)
            series_desc = ds.get('SeriesDescription') or ds.get('StudyDescription') or f"series_{i}"
            series_num = ds.get('SeriesNumber', i)
            
            # Clean filename
            clean_desc = "".join(c if c.isalnum() or c in "_-" else "_" for c in str(series_desc))
            filename = f"{series_num}_{clean_desc}.nii.gz"
            out_path = os.path.join(out_dir, filename)
            
            sitk.WriteImage(image, out_path)
            converted_paths.append(out_path)
        except Exception as e:
            print(f"  [WARN] Failed to convert series {s_id}: {e}")
            
    return converted_paths

def main():
    os.makedirs(OUTPUT_ROOT, exist_ok=True)
    csv_rows = []
    
    # Get sorted list of groups to process
    groups = sorted([g for g in os.listdir(ROOT_DIR) if os.path.isdir(os.path.join(ROOT_DIR, g)) and g != "nii"])
    
    for group in groups:
        group_path = os.path.join(ROOT_DIR, group)
        print(f"\n--- Processing group: {group} ---")
        
        patients = sorted([p for p in os.listdir(group_path) if os.path.isdir(os.path.join(group_path, p))])
        
        for patient in patients:
            patient_path = os.path.join(group_path, patient)
            dicom_dir = os.path.join(patient_path, "DICOMOBJ")
            
            if not os.path.isdir(dicom_dir):
                # Try finding DICOMOBJ case-insensitively or just use the patient folder if no DICOMOBJ
                found = False
                for sub in os.listdir(patient_path):
                    if sub.upper() == "DICOMOBJ":
                        dicom_dir = os.path.join(patient_path, sub)
                        found = True
                        break
                if not found:
                    # If no DICOMOBJ, maybe DICOMs are directly in the patient folder?
                    # But per instructions, they are under DICOMOBJ. 
                    # We'll skip if not found.
                    continue
            
            dest_dir = os.path.join(OUTPUT_ROOT, group, patient)
            
            # Check if already processed (skip if folder exists and contains .nii.gz)
            if os.path.exists(dest_dir) and any(f.endswith('.nii.gz') for f in os.listdir(dest_dir)):
                continue

            os.makedirs(dest_dir, exist_ok=True)
            
            try:
                # 1. Metadata
                info = extract_patient_info(dicom_dir)
                write_patient_info(info, dest_dir)
                
                # 2. Conversion
                nii_paths = convert_series(dicom_dir, dest_dir)
                
                if not nii_paths:
                    # Clean up empty dir if failed
                    if not os.listdir(dest_dir):
                        os.rmdir(dest_dir)
                    continue
                
                # 3. CSV record (one row per series or one row per patient?)
                # Requirement: "a master CSV file file_list.csv listing all converted files"
                # We'll add one row per patient but list all paths, or multiple rows?
                # Let's do one row per series for clarity.
                for p in nii_paths:
                    csv_rows.append({
                        'Group': group,
                        'PatientFolder': patient,
                        'DICOMPath': dicom_dir,
                        'NIfTIPath': p,
                        'PatientName': info['PatientName'],
                        'PatientID': info['PatientID'],
                        'Sex': info['PatientSex'],
                        'Age': info['PatientAge'],
                        'AccessionNumber': info['AccessionNumber'],
                    })
                
                # No periodic print unless error
                pass
                
            except Exception as e:
                print(f"[ERROR] Failed to process {patient} in {group}: {e}")
                
    # Finalize CSV
    print("\n--- Generating final CSV list ---")
    # Re-scan for all converted files to ensure CSV is complete regardless of when files were created
    csv_rows = []
    for g in sorted(os.listdir(OUTPUT_ROOT)):
        g_path = os.path.join(OUTPUT_ROOT, g)
        if not os.path.isdir(g_path) or g == "file_list.csv": continue
        for p in sorted(os.listdir(g_path)):
            p_path = os.path.join(g_path, p)
            if not os.path.isdir(p_path): continue
            
            nii_files = [f for f in os.listdir(p_path) if f.endswith('.nii.gz')]
            if nii_files:
                # Try to read info from patient_info.txt if it exists
                p_info = {'PatientName': p, 'PatientID': '', 'PatientSex': '', 'PatientAge': '', 'AccessionNumber': ''}
                txt_path = os.path.join(p_path, "patient_info.txt")
                if os.path.exists(txt_path):
                    try:
                        with open(txt_path, "r", encoding="utf-8") as tf:
                            for line in tf:
                                if ":" in line:
                                    k, v = line.split(":", 1)
                                    k = k.strip()
                                    v = v.strip()
                                    if "姓名" in k: p_info['PatientName'] = v
                                    elif "门诊号" in k: p_info['PatientID'] = v
                                    elif "性别" in k: p_info['PatientSex'] = v
                                    elif "年龄" in k: p_info['PatientAge'] = v
                                    elif "影像号" in k: p_info['AccessionNumber'] = v
                    except: pass
                
                for nf in nii_files:
                    csv_rows.append({
                        'Group': g,
                        'PatientFolder': p,
                        'DICOMPath': 'N/A', # Original path not stored in output
                        'NIfTIPath': os.path.join(p_path, nf),
                        'PatientName': p_info['PatientName'],
                        'PatientID': p_info['PatientID'],
                        'Sex': p_info['PatientSex'],
                        'Age': p_info['PatientAge'],
                        'AccessionNumber': p_info['AccessionNumber'],
                    })

    csv_path = os.path.join(OUTPUT_ROOT, "file_list.csv")
    fieldnames = ['Group', 'PatientFolder', 'DICOMPath', 'NIfTIPath',
                  '姓名', '门诊号', '性别', '年龄', '影像号']
    with open(csv_path, "w", newline='', encoding='utf-8-sig') as csvfile:
        # Using utf-8-sig for Excel compatibility with Chinese characters
        writer = csv.DictWriter(csvfile, fieldnames=fieldnames)
        
        # Mapping internal keys to user requested headers
        writer.writeheader()
        for row in csv_rows:
            # Map keys to Chinese headers if necessary
            mapped_row = {
                'Group': row['Group'],
                'PatientFolder': row['PatientFolder'],
                'DICOMPath': row['DICOMPath'],
                'NIfTIPath': row['NIfTIPath'],
                '姓名': row['PatientName'],
                '门诊号': row['PatientID'],
                '性别': row['PatientSex'],
                '年龄': row['Age'],
                '影像号': row['AccessionNumber']
            }
            writer.writerow(mapped_row)
        # writer.writeheader() # Handled above in updated logic
    print(f"CSV summary written to {csv_path}")

if __name__ == "__main__":
    main()
# BraTS_nnUNet_MultiModal

## 기본 정보
- **진료과:** Neurology
- **프로젝트:** BraTS_nnUNet_MultiModal
- **task_type:** segmentation
- **required_data:** [nii, nii.gz, directory]
- **result_type:** [segmentation_overlay, 3d_overlay, segmentation_mask_volume, volume_measurements]
- **provides:** [brain_tumour_segmentation]
- **requires:** []

## 설명
Brain tumor sub-region segmentation from four MRI sequences, trained on BraTS 2020. Input: brain MRI (T1, T1ce, T2 and FLAIR volumes); file formats nii, nii.gz, directory. Assesses: brain tumor, glioma, whole tumor, tumor core, enhancing tumor. Outputs: segmentation overlay, tumor volumes.

## 임상 해석 패턴

## 관련 개념
brain tumor, glioma, whole tumor, tumor core, enhancing tumor

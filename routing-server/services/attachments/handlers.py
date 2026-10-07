"""
모달리티별 첨부 핸들러 구현.

핸들러 5종: DICOM / NIfTI / 이미지(PNG·JPG) / CSV / PDF.
향후 확장: AudioHandler(ASR), VideoHandler(키프레임+ASR).

CPU 바운드 작업(pydicom/nibabel/PIL/pandas)은 asyncio.to_thread로
이벤트 루프 밖에서 실행한다.
"""
from __future__ import annotations

import asyncio
import base64
import hashlib
import logging
import os
import re
import tempfile
from io import BytesIO

import numpy as np
from PIL import Image

from .base import AttachmentHandler, NormalizedAttachment

logger = logging.getLogger("maple.attachments")

MAX_IMAGE_DIM = 1024   # 렌더 이미지 최대 변 길이 (다운스케일)
CSV_ROW_CAP   = 1000   # tabular로 넘기는 최대 행 수
PDF_TEXT_CAP  = 20000  # PDF 추출 텍스트 최대 문자 수


# ── 공통 이미지 유틸 ──────────────────────────────────────────────────────────

def _norm_uint8(arr2d) -> np.ndarray:
    """2D 배열을 0~255 uint8로 정규화."""
    arr = np.asarray(arr2d, dtype="float32")
    amin, amax = float(np.nanmin(arr)), float(np.nanmax(arr))
    if amax > amin:
        arr = (arr - amin) / (amax - amin) * 255.0
    else:
        arr = np.zeros_like(arr)
    return arr.astype("uint8")


def _arr_to_data_uri(arr_uint8: np.ndarray) -> str:
    """uint8 2D 배열 → PNG data URI (필요시 다운스케일)."""
    pil = Image.fromarray(arr_uint8)
    if pil.mode != "RGB":
        pil = pil.convert("RGB")
    if max(pil.size) > MAX_IMAGE_DIM:
        pil.thumbnail((MAX_IMAGE_DIM, MAX_IMAGE_DIM))
    buf = BytesIO()
    pil.save(buf, format="PNG")
    return "data:image/png;base64," + base64.b64encode(buf.getvalue()).decode()


def _orient(arr2d: np.ndarray) -> np.ndarray:
    """재구성 슬라이스를 통상적인 방향으로 회전 (표시용, cosmetic)."""
    try:
        return np.rot90(arr2d)
    except Exception:
        return arr2d


def _first_num(v) -> float | None:
    """DSfloat / MultiValue / 단일값에서 첫 숫자를 float로."""
    if v is None:
        return None
    try:
        if hasattr(v, "__iter__") and not isinstance(v, (str, bytes)):
            v = list(v)[0]
        return float(v)
    except Exception:
        return None


def _drop_empty(md: dict) -> dict:
    return {k: v for k, v in md.items() if v not in (None, "", [], {})}


# ── DICOM ────────────────────────────────────────────────────────────────────

def _dicom_windowed_volume(ds) -> np.ndarray:
    """Modality LUT + VOI LUT 적용, MONOCHROME1 반전한 float 볼륨."""
    from pydicom.pixels import apply_modality_lut, apply_voi_lut

    arr = ds.pixel_array
    try:
        arr = apply_modality_lut(arr, ds)
    except Exception:
        pass
    try:
        arr = apply_voi_lut(arr, ds)
    except Exception:
        pass
    arr = np.asarray(arr, dtype="float32")
    if str(getattr(ds, "PhotometricInterpretation", "")).strip() == "MONOCHROME1":
        arr = float(np.nanmax(arr)) - arr
    return arr


def _planes_from_dicom(vol: np.ndarray) -> list[tuple[str, np.ndarray]]:
    """DICOM 볼륨(2D 또는 (frames,H,W))에서 대표 슬라이스. axial 우선."""
    v = np.squeeze(vol)
    while v.ndim > 3:
        v = v[0]
    if v.ndim == 2:
        return [("axial", v)]
    if v.ndim == 3:
        f, h, w = v.shape[0] // 2, v.shape[1] // 2, v.shape[2] // 2
        return [
            ("axial",    v[f, :, :]),
            ("coronal",  _orient(v[:, h, :])),
            ("sagittal", _orient(v[:, :, w])),
        ]
    return [("axial", np.atleast_2d(v))]


def _deid_age(ds) -> str | None:
    """비식별 나이. PatientAge 우선, 없으면 DOB+StudyDate 환산. 90+ 비닝."""
    years: int | None = None
    age = getattr(ds, "PatientAge", None)
    if age:
        m = re.match(r"(\d+)\s*([YMWD])?", str(age).strip())
        if m:
            val = int(m.group(1))
            unit = (m.group(2) or "Y").upper()
            if unit == "Y":
                years = val
            else:
                return str(age).strip()  # 영유아 등 월/주/일 단위는 그대로 (식별력 낮음)
    if years is None:
        dob = str(getattr(ds, "PatientBirthDate", "") or "")
        std = str(getattr(ds, "StudyDate", "") or getattr(ds, "SeriesDate", "") or "")
        if len(dob) == 8 and len(std) == 8:
            try:
                y = int(std[:4]) - int(dob[:4])
                if (int(std[4:6]), int(std[6:8])) < (int(dob[4:6]), int(dob[6:8])):
                    y -= 1
                years = y
            except Exception:
                pass
    if years is None:
        return None
    if years >= 90:
        return "90+"
    return f"{years:03d}Y"


def _dicom_metadata(ds) -> dict:
    """비식별화 화이트리스트 추출. 이름/DOB원본/원본ID는 배제, ID는 해시 참조로."""
    def s(attr: str) -> str | None:
        v = getattr(ds, attr, None)
        return str(v).strip() if v not in (None, "") else None

    md: dict = {
        "modality":            s("Modality"),
        "body_part":           s("BodyPartExamined"),
        "study_description":   s("StudyDescription"),
        "series_description":  s("SeriesDescription"),
        "protocol_name":       s("ProtocolName"),
    }

    rows, cols = getattr(ds, "Rows", None), getattr(ds, "Columns", None)
    if rows and cols:
        md["shape"] = [int(rows), int(cols)]

    ps = getattr(ds, "PixelSpacing", None)
    if ps is not None:
        try:
            md["pixel_spacing_mm"] = [round(float(x), 4) for x in ps]
        except Exception:
            pass

    st = _first_num(getattr(ds, "SliceThickness", None))
    if st is not None:
        md["slice_thickness_mm"] = st

    wc = _first_num(getattr(ds, "WindowCenter", None))
    ww = _first_num(getattr(ds, "WindowWidth", None))
    if wc is not None:
        md["window_center"] = wc
    if ww is not None:
        md["window_width"] = ww

    mr = {}
    tr = _first_num(getattr(ds, "RepetitionTime", None))
    te = _first_num(getattr(ds, "EchoTime", None))
    if tr is not None:
        mr["TR"] = tr
    if te is not None:
        mr["TE"] = te
    if mr:
        md["mr_params"] = mr

    # 인구학 정보 (비식별) — 임상 유용성 유지
    age = _deid_age(ds)
    if age:
        md["age"] = age
    md["sex"] = s("PatientSex")
    wt = _first_num(getattr(ds, "PatientWeight", None))
    if wt is not None:
        md["weight_kg"] = wt
    sz = _first_num(getattr(ds, "PatientSize", None))
    if sz is not None:
        md["height_m"] = sz

    # 직접 식별자: 이름/DOB원본/원본ID 미추출. ID는 추적용 해시 참조로만.
    pid = getattr(ds, "PatientID", None)
    if pid:
        md["patient_ref"] = hashlib.sha256(str(pid).encode()).hexdigest()[:12]

    return _drop_empty(md)


class DicomHandler(AttachmentHandler):
    type = "dicom"

    def can_handle(self, filename: str, content_type: str = "") -> bool:
        n = (filename or "").lower()
        return n.endswith((".dcm", ".dicom")) or "dicom" in (content_type or "").lower()

    async def parse(self, filename: str, content: bytes) -> NormalizedAttachment:
        return await asyncio.to_thread(self._parse_sync, filename, content)

    def _parse_sync(self, filename: str, content: bytes) -> NormalizedAttachment:
        import pydicom

        ds = pydicom.dcmread(BytesIO(content), force=True)
        images: list[str] = []
        try:
            vol = _dicom_windowed_volume(ds)
            for _label, sl in _planes_from_dicom(vol):
                images.append(_arr_to_data_uri(_norm_uint8(sl)))
        except Exception as e:
            logger.warning("DICOM 렌더 실패 (%s): %s", filename, e)
        return NormalizedAttachment(
            type=self.type, filename=filename or "", images=images, metadata=_dicom_metadata(ds)
        )

    async def extract_metadata(self, filename: str, content: bytes) -> dict:
        return await asyncio.to_thread(self._meta_sync, content)

    def _meta_sync(self, content: bytes) -> dict:
        import pydicom

        ds = pydicom.dcmread(BytesIO(content), stop_before_pixels=True, force=True)
        return _dicom_metadata(ds)

    async def extract_metadata_path(self, filename: str, path: str) -> dict:
        return await asyncio.to_thread(self._meta_path_sync, path)

    def _meta_path_sync(self, path: str) -> dict:
        import pydicom

        ds = pydicom.dcmread(path, stop_before_pixels=True, force=True)
        return _dicom_metadata(ds)


# ── NIfTI ────────────────────────────────────────────────────────────────────

def _write_temp(content: bytes, filename: str) -> str:
    suffix = ".nii.gz" if (filename or "").lower().endswith(".gz") else ".nii"
    fd, path = tempfile.mkstemp(suffix=suffix)
    with os.fdopen(fd, "wb") as f:
        f.write(content)
    return path


def _safe_unlink(path: str) -> None:
    try:
        os.unlink(path)
    except Exception as e:
        logger.debug("temp 삭제 실패 %s: %s", path, e)


def _planes_from_nifti(vol: np.ndarray) -> list[tuple[str, np.ndarray]]:
    """NIfTI 볼륨(canonical RAS 가정: x=L/R, y=P/A, z=I/S)에서 3면. axial 우선."""
    v = np.squeeze(vol)
    while v.ndim > 3:
        v = v[..., 0]
    if v.ndim == 2:
        return [("axial", _orient(v))]
    x, y, z = v.shape[0] // 2, v.shape[1] // 2, v.shape[2] // 2
    return [
        ("axial",    _orient(v[:, :, z])),
        ("coronal",  _orient(v[:, y, :])),
        ("sagittal", _orient(v[x, :, :])),
    ]


def _nifti_metadata(img) -> dict:
    import nibabel as nib

    hdr = img.header
    md: dict = {"shape": [int(x) for x in img.shape], "ndim": len(img.shape)}
    try:
        md["voxel_spacing_mm"] = [round(float(z), 4) for z in hdr.get_zooms()[:3]]
    except Exception:
        pass
    try:
        md["orientation"] = "".join(nib.aff2axcodes(img.affine))
    except Exception:
        pass
    try:
        md["dtype"] = str(hdr.get_data_dtype())
    except Exception:
        pass
    return _drop_empty(md)


class NiftiHandler(AttachmentHandler):
    type = "nifti"

    def can_handle(self, filename: str, content_type: str = "") -> bool:
        n = (filename or "").lower()
        return n.endswith(".nii") or n.endswith(".nii.gz")

    async def parse(self, filename: str, content: bytes) -> NormalizedAttachment:
        return await asyncio.to_thread(self._parse_sync, filename, content)

    def _parse_sync(self, filename: str, content: bytes) -> NormalizedAttachment:
        import nibabel as nib

        images: list[str] = []
        md: dict = {}
        tmp_path = _write_temp(content, filename)
        try:
            img = nib.load(tmp_path)
            md = _nifti_metadata(img)  # 메타는 원본 방향(orientation) 기준
            # 렌더는 canonical RAS로 정렬 후 슬라이스 → axial/coronal/sagittal 라벨 정확화
            canon = nib.as_closest_canonical(img)
            data = np.asanyarray(canon.dataobj, dtype="float32")
            for _label, sl in _planes_from_nifti(data):
                images.append(_arr_to_data_uri(_norm_uint8(sl)))
        except Exception as e:
            logger.warning("NIfTI 렌더 실패 (%s): %s", filename, e)
        finally:
            _safe_unlink(tmp_path)
        return NormalizedAttachment(type=self.type, filename=filename or "", images=images, metadata=md)

    async def extract_metadata(self, filename: str, content: bytes) -> dict:
        return await asyncio.to_thread(self._meta_sync, filename, content)

    def _meta_sync(self, filename: str, content: bytes) -> dict:
        import nibabel as nib

        tmp_path = _write_temp(content, filename)
        try:
            return _nifti_metadata(nib.load(tmp_path))
        except Exception as e:
            logger.warning("NIfTI 메타 추출 실패 (%s): %s", filename, e)
            return {}
        finally:
            _safe_unlink(tmp_path)


# ── 이미지 (PNG/JPG/…) ────────────────────────────────────────────────────────

class ImageHandler(AttachmentHandler):
    type = "image"

    def can_handle(self, filename: str, content_type: str = "") -> bool:
        n = (filename or "").lower()
        return (
            n.endswith((".png", ".jpg", ".jpeg", ".bmp", ".webp", ".tif", ".tiff"))
            or (content_type or "").lower().startswith("image/")
        )

    async def parse(self, filename: str, content: bytes) -> NormalizedAttachment:
        return await asyncio.to_thread(self._parse_sync, filename, content)

    def _parse_sync(self, filename: str, content: bytes) -> NormalizedAttachment:
        images: list[str] = []
        md: dict = {}
        try:
            pil = Image.open(BytesIO(content))
            md = {"width": pil.width, "height": pil.height, "mode": pil.mode}
            if pil.format:
                md["format"] = pil.format.upper()
            rgb = pil.convert("RGB")
            if max(rgb.size) > MAX_IMAGE_DIM:
                rgb.thumbnail((MAX_IMAGE_DIM, MAX_IMAGE_DIM))
            buf = BytesIO()
            rgb.save(buf, format="PNG")
            images.append("data:image/png;base64," + base64.b64encode(buf.getvalue()).decode())
        except Exception as e:
            logger.warning("이미지 처리 실패 (%s): %s", filename, e)
        return NormalizedAttachment(type=self.type, filename=filename or "", images=images, metadata=md)

    async def extract_metadata(self, filename: str, content: bytes) -> dict:
        def _m() -> dict:
            try:
                pil = Image.open(BytesIO(content))
                md = {"width": pil.width, "height": pil.height, "mode": pil.mode}
                if pil.format:
                    md["format"] = pil.format.upper()
                return md
            except Exception:
                return {}
        return await asyncio.to_thread(_m)


# ── CSV ──────────────────────────────────────────────────────────────────────

class CsvHandler(AttachmentHandler):
    type = "csv"

    def can_handle(self, filename: str, content_type: str = "") -> bool:
        return (filename or "").lower().endswith(".csv") or "csv" in (content_type or "").lower()

    async def parse(self, filename: str, content: bytes) -> NormalizedAttachment:
        return await asyncio.to_thread(self._parse_sync, filename, content)

    def _parse_sync(self, filename: str, content: bytes) -> NormalizedAttachment:
        import pandas as pd

        md: dict = {}
        tabular: list[dict] | None = None
        text = ""
        try:
            df = pd.read_csv(BytesIO(content))
            n_rows, n_cols = df.shape
            md = {
                "rows":    int(n_rows),
                "columns": int(n_cols),
                "schema":  {str(c): str(t) for c, t in df.dtypes.astype(str).items()},
            }
            capped = df.head(CSV_ROW_CAP)
            tabular = capped.to_dict(orient="records")
            if n_rows > CSV_ROW_CAP:
                md["rows_truncated_to"] = CSV_ROW_CAP
            try:
                text = capped.describe(include="all").to_string()
            except Exception:
                text = f"{n_rows} rows x {n_cols} cols; columns: {list(df.columns)}"
        except Exception as e:
            logger.warning("CSV 파싱 실패 (%s): %s", filename, e)
        return NormalizedAttachment(
            type=self.type, filename=filename or "", images=[], text=text, metadata=md, tabular=tabular
        )

    async def extract_metadata(self, filename: str, content: bytes) -> dict:
        def _m() -> dict:
            import pandas as pd
            try:
                df = pd.read_csv(BytesIO(content))
                return {
                    "rows":    int(df.shape[0]),
                    "columns": int(df.shape[1]),
                    "schema":  {str(c): str(t) for c, t in df.dtypes.astype(str).items()},
                }
            except Exception:
                return {}
        return await asyncio.to_thread(_m)


# ── PDF ──────────────────────────────────────────────────────────────────────

class PdfHandler(AttachmentHandler):
    type = "pdf"

    def can_handle(self, filename: str, content_type: str = "") -> bool:
        return (filename or "").lower().endswith(".pdf") or "pdf" in (content_type or "").lower()

    async def parse(self, filename: str, content: bytes) -> NormalizedAttachment:
        return await asyncio.to_thread(self._parse_sync, filename, content)

    def _parse_sync(self, filename: str, content: bytes) -> NormalizedAttachment:
        text = ""
        md: dict = {}
        try:
            from pypdf import PdfReader

            reader = PdfReader(BytesIO(content))
            md["pages"] = len(reader.pages)
            parts: list[str] = []
            for page in reader.pages:
                try:
                    parts.append(page.extract_text() or "")
                except Exception:
                    pass
            text = "\n".join(parts).strip()
            if len(text) > PDF_TEXT_CAP:
                text = text[:PDF_TEXT_CAP]
                md["text_truncated_to"] = PDF_TEXT_CAP
        except Exception as e:
            logger.warning("PDF 파싱 실패 (%s): %s", filename, e)
        return NormalizedAttachment(type=self.type, filename=filename or "", images=[], text=text, metadata=md)

    async def extract_metadata(self, filename: str, content: bytes) -> dict:
        def _m() -> dict:
            try:
                from pypdf import PdfReader
                return {"pages": len(PdfReader(BytesIO(content)).pages)}
            except Exception:
                return {}
        return await asyncio.to_thread(_m)

"""PDF -> DoclingDocument JSON. The only module (besides verify) that needs docling installed."""

from __future__ import annotations

import hashlib
import logging
import time
from dataclasses import dataclass, field
from pathlib import Path

log = logging.getLogger("pdfk.convert")


@dataclass
class ConvertStats:
    pages: int = 0
    seconds: float = 0.0
    docling_version: str = ""
    mean_grade: str = ""
    low_grade: str = ""
    page_grades: dict[int, str] = field(default_factory=dict)
    low_pages: list[int] = field(default_factory=list)


def sha256_of(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def _require_docling():
    try:
        import docling  # noqa: F401
    except ImportError as e:  # pragma: no cover
        raise SystemExit(
            "docling is not importable in this Python. Install it in a venv "
            "(pip install docling) and run `pdfk build` with that interpreter, "
            "e.g. set PDFK_PYTHON=<venv>/Scripts/python.exe"
        ) from e


def convert_pdf(
    pdf: Path,
    out_json: Path,
    *,
    page_range: tuple[int, int] | None = None,
    ocr: bool = False,
    figures: bool = False,
    threads: int = 8,
    device: str = "auto",
    artifacts_path: str | None = None,
    table_mode: str = "accurate",
) -> ConvertStats:
    _require_docling()
    from docling import __version__ as docling_version
    from docling.datamodel.accelerator_options import AcceleratorDevice, AcceleratorOptions
    from docling.datamodel.base_models import InputFormat
    from docling.datamodel.pipeline_options import (
        HeadingHierarchyOptions,
        PdfPipelineOptions,
        TableFormerMode,
    )
    from docling.document_converter import DocumentConverter, PdfFormatOption
    from docling_core.types.doc import ImageRefMode

    opts = PdfPipelineOptions()
    opts.do_ocr = ocr
    opts.do_table_structure = True
    opts.table_structure_options.mode = (
        TableFormerMode.ACCURATE if table_mode == "accurate" else TableFormerMode.FAST
    )
    opts.table_structure_options.do_cell_matching = True
    # Heading levels from PDF bookmarks / numbering. Style signal needs parsed pages (memory heavy),
    # so we skip it: vendor manuals have complete outlines.
    opts.heading_hierarchy_options = HeadingHierarchyOptions(enabled=True, use_style=False)
    opts.generate_parsed_pages = False
    opts.generate_picture_images = figures
    opts.images_scale = 2.0
    opts.accelerator_options = AcceleratorOptions(
        num_threads=threads, device=AcceleratorDevice(device)
    )
    if artifacts_path:
        opts.artifacts_path = artifacts_path

    converter = DocumentConverter(
        format_options={InputFormat.PDF: PdfFormatOption(pipeline_options=opts)}
    )

    t0 = time.perf_counter()
    kwargs = {}
    if page_range:
        kwargs["page_range"] = page_range
    result = converter.convert(pdf, raises_on_error=True, **kwargs)
    seconds = time.perf_counter() - t0

    doc = result.document
    out_json.parent.mkdir(parents=True, exist_ok=True)
    if figures:
        artifacts_dir = out_json.parent / "docling_artifacts"
        doc.save_as_json(out_json, artifacts_dir=artifacts_dir, image_mode=ImageRefMode.REFERENCED)
    else:
        doc.save_as_json(out_json, image_mode=ImageRefMode.PLACEHOLDER)

    stats = ConvertStats(
        pages=len(doc.pages),
        seconds=round(seconds, 1),
        docling_version=str(docling_version),
    )
    conf = result.confidence
    try:
        stats.mean_grade = conf.mean_grade.value
        stats.low_grade = conf.low_grade.value
        for pno, pc in conf.pages.items():
            g = pc.low_grade.value
            stats.page_grades[int(pno)] = g
            if g in ("poor", "fair"):
                stats.low_pages.append(int(pno))
        stats.low_pages.sort()
    except Exception as e:  # confidence is informational; never fail the build on it
        log.warning("confidence report unavailable: %s", e)
    return stats

"""PDF -> DoclingDocument (gzip JSON) + figure PNGs. The only module (besides verify) that needs docling."""

from __future__ import annotations

import gzip
import hashlib
import json
import logging
import os
import sys
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path

log = logging.getLogger("pdfk.convert")

MIN_FIG_W, MIN_FIG_H = 120, 80  # px at images_scale=2.0; smaller pictures are logos / icons


@dataclass
class ConvertStats:
    pages: int = 0
    seconds: float = 0.0
    docling_version: str = ""
    mean_grade: str = ""
    low_grade: str = ""
    page_grades: dict[int, str] = field(default_factory=dict)
    low_pages: list[int] = field(default_factory=list)
    figures: int = 0


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
            "docling is not importable in this Python. Install it (pip install docling) or set "
            "PDFK_PYTHON to an interpreter that has it, then run `pdfk build` again."
        ) from e


def _quiet_third_party_logs() -> None:
    """A 600-page manual emits ~20k 'Orphan pdf_cell' warnings and HF cache notices; none are actionable."""
    os.environ.setdefault("HF_HUB_DISABLE_SYMLINKS_WARNING", "1")
    for name in ("docling", "docling_ibm_models", "docling_parse", "huggingface_hub", "transformers", "RapidOCR"):
        logging.getLogger(name).setLevel(logging.ERROR)
    import warnings

    warnings.filterwarnings("ignore", module=r"huggingface_hub.*")
    os.environ.setdefault("HF_HUB_DISABLE_PROGRESS_BARS", "1")
    try:  # TableFormer builds its own stdout loggers per class ("MatchingPostProcessor") at WARNING
        import docling_ibm_models.tableformer.settings as tf_settings

        orig = tf_settings.get_custom_logger
        if not getattr(orig, "_pdfk_quiet", False):
            def quiet(logger_name, level, stream=sys.stdout):
                return orig(logger_name, logging.ERROR, stream)

            quiet._pdfk_quiet = True  # type: ignore[attr-defined]
            tf_settings.get_custom_logger = quiet
    except ImportError:
        pass
    try:
        from transformers.utils import logging as hf_logging

        hf_logging.disable_progress_bar()
    except ImportError:
        pass


class Progress:
    """Throttled progress lines: `rp2040: 120/642 pages (19%) 1.7 s/page, ETA 15 min`."""

    def __init__(self, label: str, total: int, every_s: float = 20.0, stream=None):
        self.label, self.total, self.every_s = label, max(total, 1), every_s
        self.stream = stream or sys.stdout
        self.done = 0
        self.t0 = time.perf_counter()
        self.t_first: float | None = None
        self.t_last_print = 0.0
        self._lock = threading.Lock()

    def page_done(self, _page_no: int | None = None) -> None:
        with self._lock:
            now = time.perf_counter()
            self.done += 1
            if self.t_first is None:
                self.t_first = now
                self._emit(now, note=f"models loaded, first page after {now - self.t0:.0f}s")
                return
            if now - self.t_last_print >= self.every_s or self.done == self.total:
                self._emit(now)

    def _emit(self, now: float, note: str = "") -> None:
        self.t_last_print = now
        pct = 100 * self.done // self.total
        rate = eta = ""
        if self.t_first is not None and self.done > 1:
            spp = (now - self.t_first) / (self.done - 1)
            left = (self.total - self.done) * spp
            rate = f" {spp:.1f} s/page,"
            eta = f" ETA {left / 60:.0f} min" if left >= 90 else f" ETA {left:.0f}s"
        tail = f" ({note})" if note else ""
        if self.done >= self.total:
            eta, tail = "", " — assembling document …"
        print(f"{self.label}: {self.done}/{self.total} pages ({pct}%){rate}{eta}{tail}", file=self.stream, flush=True)


def _progress_pipeline_cls(progress: Progress):
    """StandardPdfPipeline subclass that reports each finished page. Uses a private hook
    (`_release_page_resources`, the last stage's postprocess); if docling renames it, the build
    still works, only without per-page progress."""
    from docling.pipeline.standard_pdf_pipeline import StandardPdfPipeline

    if not hasattr(StandardPdfPipeline, "_release_page_resources"):  # narrow compat probe, see docstring
        return StandardPdfPipeline

    class ProgressPdfPipeline(StandardPdfPipeline):
        def _release_page_resources(self, item):  # type: ignore[override]
            super()._release_page_resources(item)
            try:
                progress.page_done(getattr(item, "page_no", None))
            except Exception:  # progress must never break a conversion
                pass

    return ProgressPdfPipeline


def count_pages(pdf: Path, page_range: tuple[int, int] | None) -> int:
    import pypdfium2 as pdfium

    doc = pdfium.PdfDocument(str(pdf))
    n = len(doc)
    doc.close()
    if page_range:
        lo, hi = page_range
        return max(0, min(hi, n) - max(lo, 1) + 1)
    return n


def export_figures(doc, fig_dir: Path) -> int:
    """Write body pictures as figures/fNNNN.png (NNNN = index in doc.pictures) and drop the
    embedded bitmaps from the document so the stored JSON stays small."""
    fig_dir.mkdir(parents=True, exist_ok=True)
    for old in fig_dir.glob("f*.png"):
        old.unlink()
    n = 0
    for i, pic in enumerate(doc.pictures):
        img = None
        try:
            img = pic.get_image(doc)
        except Exception as e:
            log.warning("figure %d: %s", i, e)
        if img is not None and img.width >= MIN_FIG_W and img.height >= MIN_FIG_H:
            img.save(fig_dir / f"f{i:04d}.png", optimize=True)
            n += 1
        pic.image = None
    return n


def save_doc_gz(doc, path: Path) -> None:
    data = doc.export_to_dict()
    with gzip.open(path, "wt", encoding="utf-8", compresslevel=6) as f:
        json.dump(data, f, ensure_ascii=False, separators=(",", ":"))


def load_doc(path: Path):
    from docling_core.types.doc import DoclingDocument

    if path.suffix == ".gz":
        with gzip.open(path, "rt", encoding="utf-8") as f:
            return DoclingDocument.model_validate(json.load(f))
    return DoclingDocument.load_from_json(path)


def convert_pdf(
    pdf: Path,
    out_gz: Path,
    *,
    label: str = "",
    page_range: tuple[int, int] | None = None,
    ocr: bool = False,
    figures: bool = False,
    threads: int = 8,
    device: str = "auto",
    artifacts_path: str | None = None,
    table_mode: str = "accurate",
    progress_every_s: float = 20.0,
) -> ConvertStats:
    _require_docling()
    _quiet_third_party_logs()
    from docling import __version__ as docling_version
    from docling.datamodel.accelerator_options import AcceleratorDevice, AcceleratorOptions
    from docling.datamodel.base_models import InputFormat
    from docling.datamodel.pipeline_options import (
        HeadingHierarchyOptions,
        PdfPipelineOptions,
        TableFormerMode,
    )
    from docling.document_converter import DocumentConverter, PdfFormatOption

    opts = PdfPipelineOptions()
    opts.do_ocr = ocr
    opts.do_table_structure = True
    opts.table_structure_options.mode = (
        TableFormerMode.ACCURATE if table_mode == "accurate" else TableFormerMode.FAST
    )
    opts.table_structure_options.do_cell_matching = True
    # Heading levels from PDF bookmarks / numbering (pdfk re-normalizes from numbering afterwards).
    opts.heading_hierarchy_options = HeadingHierarchyOptions(enabled=True, use_style=False)
    opts.generate_parsed_pages = False
    opts.generate_picture_images = figures
    opts.images_scale = 2.0
    opts.accelerator_options = AcceleratorOptions(num_threads=threads, device=AcceleratorDevice(device))
    if artifacts_path:
        opts.artifacts_path = artifacts_path

    total = count_pages(pdf, page_range)
    progress = Progress(label or pdf.stem, total, every_s=progress_every_s)
    converter = DocumentConverter(
        format_options={
            InputFormat.PDF: PdfFormatOption(pipeline_options=opts, pipeline_cls=_progress_pipeline_cls(progress))
        }
    )

    t0 = time.perf_counter()
    kwargs = {"page_range": page_range} if page_range else {}
    result = converter.convert(pdf, raises_on_error=True, **kwargs)
    seconds = time.perf_counter() - t0

    doc = result.document
    out_gz.parent.mkdir(parents=True, exist_ok=True)
    n_fig = export_figures(doc, out_gz.parent / "figures") if figures else 0
    save_doc_gz(doc, out_gz)
    legacy = out_gz.parent / "docling.json"
    if legacy.is_file():
        legacy.unlink()

    stats = ConvertStats(pages=len(doc.pages), seconds=round(seconds, 1), docling_version=str(docling_version), figures=n_fig)
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

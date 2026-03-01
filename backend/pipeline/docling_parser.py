from __future__ import annotations
from dataclasses import dataclass, field
from pathlib import Path

import pandas as pd
from docling.document_converter import DocumentConverter, PdfFormatOption
from docling.datamodel.pipeline_options import (
    PdfPipelineOptions,
    TableFormerMode,
    TableStructureOptions,
)


@dataclass
class ParsedDocument:
    markdown: str
    tables: list[pd.DataFrame]
    title: str
    source: str


def parse_document(source: str | Path) -> ParsedDocument:
    """
    Parse a document (PDF, DOCX, image, ...) using Docling.
    Returns structured markdown + all tables as DataFrames.
    """
    pipeline_options = PdfPipelineOptions()
    pipeline_options.do_ocr = False
    pipeline_options.images_scale = 2.0
    pipeline_options.table_structure_options = TableStructureOptions(
        do_cell_matching=True,
        mode=TableFormerMode.ACCURATE,
    )

    converter = DocumentConverter(
        format_options={
            "pdf": PdfFormatOption(pipeline_options=pipeline_options)
        }
    )

    result = converter.convert(str(source))
    doc = result.document

    markdown = doc.export_to_markdown()

    tables: list[pd.DataFrame] = []
    for item, _ in doc.iterate_items():
        if hasattr(item, "export_to_dataframe"):
            try:
                df = item.export_to_dataframe(doc=doc)
                if not df.empty:
                    tables.append(df)
            except Exception:
                pass

    title = _extract_title(markdown, source)

    return ParsedDocument(
        markdown=markdown,
        tables=tables,
        title=title,
        source=str(source),
    )


def _extract_title(markdown: str, source: str | Path) -> str:
    for line in markdown.splitlines():
        stripped = line.strip()
        if stripped.startswith("# "):
            return stripped[2:].strip()
    return Path(source).stem

"""
app/infrastructure/document/langchain_extractor.py — LangChain document loader adapter

Infrastructure adapter implementing TextExtractor port.
Uses langchain-community loaders (PyPDFLoader, Docx2txtLoader, TextLoader)
with graceful fallback to robust Python parsers when LangChain not installed.

Clean Architecture: only this file imports langchain — domain/application stay pure.
"""

from __future__ import annotations

import io
import logging
import mimetypes
import re
import tempfile
from pathlib import Path

logger = logging.getLogger("voice-doc-assistant")


class LangChainExtractor:
    """LangChain-backed extractor — handles PDF, DOCX, XLSX, TXT, MD, CSV, JSON."""

    def extract(self, data: bytes, content_type: str, filename: str) -> str:
        ext = Path(filename).suffix.lower().lstrip(".")
        # Normalize content_type for some browsers that send generic types
        ct = (content_type or "").lower()

        # Try LangChain loaders first (best quality for PDF/DOCX)
        text = self._try_langchain(data, ext, ct, filename)
        if text and len(text.strip()) > 50:
            return text

        # Fallback: direct library parsing (no langchain)
        text = self._try_direct(data, ext, ct, filename)
        if text and len(text.strip()) > 50:
            return text

        # Final fallback: robust text decode
        return self._fallback_decode(data, ext, filename, ct)

    # ------------------------------------------------------------------ #
    # LangChain path — uses loaders that internally use pypdf/docx2txt
    # ------------------------------------------------------------------ #
    def _try_langchain(self, data: bytes, ext: str, ct: str, filename: str) -> str | None:
        try:
            # Import lazily — allows app to boot without langchain
            if ext == "pdf":
                return self._langchain_pdf(data)
            if ext in ("docx", "doc"):
                return self._langchain_docx(data)
            if ext in ("xlsx", "xls"):
                return self._langchain_excel(data, ext)
            if ext in ("txt", "md", "csv", "json"):
                return self._langchain_text(data, ext)
        except Exception as exc:
            logger.debug(f"LangChain extractor failed for {filename} ({ext}): {exc}")
        return None

    def _langchain_pdf(self, data: bytes) -> str | None:
        try:
            from langchain_community.document_loaders import PyPDFLoader  # type: ignore

            with tempfile.NamedTemporaryFile(suffix=".pdf", delete=False) as tmp:
                tmp.write(data)
                tmp.flush()
                loader = PyPDFLoader(tmp.name)
                docs = loader.load()
                text = "\n\n".join(d.page_content for d in docs if d.page_content)
                Path(tmp.name).unlink(missing_ok=True)
                if text.strip():
                    logger.info(f"LangChain PyPDFLoader extracted {len(text)} chars")
                    return text
        except Exception as exc:
            logger.debug(f"PyPDFLoader failed: {exc}")
        return None

    def _langchain_docx(self, data: bytes) -> str | None:
        try:
            from langchain_community.document_loaders import Docx2txtLoader  # type: ignore

            with tempfile.NamedTemporaryFile(suffix=".docx", delete=False) as tmp:
                tmp.write(data)
                tmp.flush()
                loader = Docx2txtLoader(tmp.name)
                docs = loader.load()
                text = "\n\n".join(d.page_content for d in docs if d.page_content)
                Path(tmp.name).unlink(missing_ok=True)
                if text.strip():
                    logger.info(f"LangChain Docx2txtLoader extracted {len(text)} chars")
                    return text
        except Exception as exc:
            logger.debug(f"Docx2txtLoader failed: {exc}")
        return None

    def _langchain_excel(self, data: bytes, ext: str) -> str | None:
        try:
            from langchain_community.document_loaders import UnstructuredExcelLoader  # type: ignore

            suffix = f".{ext}"
            with tempfile.NamedTemporaryFile(suffix=suffix, delete=False) as tmp:
                tmp.write(data)
                tmp.flush()
                loader = UnstructuredExcelLoader(tmp.name)
                docs = loader.load()
                text = "\n\n".join(d.page_content for d in docs if d.page_content)
                Path(tmp.name).unlink(missing_ok=True)
                if text.strip():
                    logger.info(f"LangChain ExcelLoader extracted {len(text)} chars")
                    return text
        except Exception as exc:
            logger.debug(f"UnstructuredExcelLoader failed: {exc}")
        return None

    def _langchain_text(self, data: bytes, ext: str) -> str | None:
        try:
            # For txt/md/csv we can just decode, but use LangChain for consistency
            text = data.decode("utf-8")
            if text.strip():
                return text
        except Exception:
            pass
        return None

    # ------------------------------------------------------------------ #
    # Direct library path — no LangChain, pure python deps (pypdf, python-docx, openpyxl)
    # ------------------------------------------------------------------ #
    def _try_direct(self, data: bytes, ext: str, ct: str, filename: str) -> str | None:
        if ext == "pdf":
            return self._direct_pdf(data)
        if ext in ("docx", "doc"):
            return self._direct_docx(data)
        if ext in ("xlsx", "xls"):
            return self._direct_excel(data, ext)
        return None

    def _direct_pdf(self, data: bytes) -> str | None:
        try:
            from pypdf import PdfReader  # type: ignore

            reader = PdfReader(io.BytesIO(data))
            parts = []
            for page in reader.pages:
                try:
                    t = page.extract_text() or ""
                    if t.strip():
                        parts.append(t)
                except Exception:
                    continue
            text = "\n\n".join(parts)
            if text.strip() and len(text.strip()) > 50:
                logger.info(f"Direct pypdf extracted {len(text)} chars, {len(reader.pages)} pages")
                return text
        except Exception as exc:
            logger.debug(f"Direct pypdf failed: {exc}")
        return None

    def _direct_docx(self, data: bytes) -> str | None:
        try:
            import docx  # type: ignore

            doc = docx.Document(io.BytesIO(data))
            text = "\n".join(p.text for p in doc.paragraphs if p.text.strip())
            # Also extract tables
            for table in doc.tables:
                for row in table.rows:
                    text += "\n" + "\t".join(cell.text for cell in row.cells)
            if text.strip() and len(text.strip()) > 30:
                logger.info(f"Direct python-docx extracted {len(text)} chars")
                return text
        except Exception as exc:
            logger.debug(f"Direct docx failed: {exc}")
        return None

    def _direct_excel(self, data: bytes, ext: str) -> str | None:
        try:
            import openpyxl  # type: ignore

            wb = openpyxl.load_workbook(io.BytesIO(data), data_only=True, read_only=True)
            parts = []
            for ws in wb.worksheets:
                parts.append(f"Sheet: {ws.title}")
                for row in ws.iter_rows(values_only=True):
                    vals = [str(v).strip() for v in row if v is not None and str(v).strip()]
                    if vals:
                        parts.append("\t".join(vals))
            text = "\n".join(parts)
            if text.strip() and len(text.strip()) > 30:
                logger.info(f"Direct openpyxl extracted {len(text)} chars, {len(wb.worksheets)} sheets")
                return text
        except Exception as exc:
            logger.debug(f"Direct openpyxl failed: {exc}")
        return None

    # ------------------------------------------------------------------ #
    # Final fallback — robust text decode (handles txt/md/csv/json + garbage)
    # ------------------------------------------------------------------ #
    def _fallback_decode(self, data: bytes, ext: str, filename: str, ct: str) -> str:
        # Try utf-8
        try:
            text = data.decode("utf-8")
            if text.strip() and sum(c.isprintable() or c.isspace() for c in text) / max(len(text), 1) > 0.6:
                return text
        except Exception:
            pass
        # Try latin-1 with cleanup
        try:
            text = data.decode("latin-1", errors="ignore")
            # Keep unicode ranges for multilingual
            text = re.sub(r"[^\x20-\x7E\n\r\t\u00A0-\u024F\u0400-\u04FF\u4E00-\u9FFF\u0600-\u06FF]+", " ", text)
            text = re.sub(r"\s+", " ", text).strip()
            if len(text) > 50:
                logger.warning(f"Fallback latin-1 decode for {filename} ({ext}, {ct}) — {len(text)} chars")
                return text
        except Exception:
            pass
        # Last resort — return metadata so user knows upload succeeded but extraction failed
        return (
            f"[Document {filename} uploaded ({len(data)} bytes, type={ct or ext or 'unknown'}) — "
            f"text extraction found no readable content. "
            f"For scanned PDFs, try OCR. For this demo, try a text-based PDF/DOCX.]\n"
        )

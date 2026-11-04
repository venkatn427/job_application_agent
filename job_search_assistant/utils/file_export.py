import io
import os
from datetime import datetime

import pandas as pd


# ── DOCX ─────────────────────────────────────────────────────────────────────

def text_to_docx(text: str, title: str = "Document") -> bytes:
    from docx import Document
    from docx.shared import Pt, Inches
    from docx.enum.text import WD_ALIGN_PARAGRAPH

    doc = Document()
    doc.core_properties.title = title

    style = doc.styles["Normal"]
    style.font.name = "Calibri"
    style.font.size = Pt(11)

    for line in text.split("\n"):
        p = doc.add_paragraph(line)

    buf = io.BytesIO()
    doc.save(buf)
    return buf.getvalue()


# ── PDF ──────────────────────────────────────────────────────────────────────

def text_to_pdf(text: str, title: str = "Document") -> bytes:
    from fpdf import FPDF

    pdf = FPDF()
    pdf.set_auto_page_break(auto=True, margin=15)
    pdf.add_page()
    pdf.set_font("Helvetica", size=11)
    pdf.set_title(title)

    for line in text.split("\n"):
        safe_line = line.encode("latin-1", errors="replace").decode("latin-1")
        pdf.multi_cell(0, 6, safe_line)

    return pdf.output()


# ── TXT ──────────────────────────────────────────────────────────────────────

def text_to_txt(text: str) -> bytes:
    return text.encode("utf-8")


# ── CSV ──────────────────────────────────────────────────────────────────────

def applications_to_csv(applications: list) -> bytes:
    df = pd.DataFrame(applications)
    buf = io.StringIO()
    df.to_csv(buf, index=False)
    return buf.getvalue().encode("utf-8")


# ── Resume Parsing ────────────────────────────────────────────────────────────

def parse_resume(uploaded_file) -> str:
    """Extract text from PDF, DOCX, or TXT upload."""
    name = uploaded_file.name.lower()
    data = uploaded_file.read()

    if name.endswith(".pdf"):
        return _parse_pdf(data)
    elif name.endswith(".docx"):
        return _parse_docx(data)
    else:
        return data.decode("utf-8", errors="replace")


def _parse_pdf(data: bytes) -> str:
    try:
        import pdfplumber
        with pdfplumber.open(io.BytesIO(data)) as pdf:
            return "\n".join(page.extract_text() or "" for page in pdf.pages)
    except Exception:
        import PyPDF2
        reader = PyPDF2.PdfReader(io.BytesIO(data))
        return "\n".join(
            page.extract_text() or "" for page in reader.pages
        )


def _parse_docx(data: bytes) -> str:
    from docx import Document
    doc = Document(io.BytesIO(data))
    return "\n".join(p.text for p in doc.paragraphs)

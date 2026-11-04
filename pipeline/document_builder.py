"""
Document Builder
-----------------
Converts a TailoredResume into:
  • ATS-friendly PDF  (via reportlab — no JavaScript, machine-parseable)
  • Formatted DOCX    (via python-docx)
  • Cover letter DOCX

Both formats use clean, ATS-safe fonts (Calibri/Helvetica) and no tables/columns
that confuse parsers.
"""

import logging
from pathlib import Path
from typing import Optional

from .resume_generator import TailoredResume

logger = logging.getLogger(__name__)


def _xml(text: str) -> str:
    """Escape XML/HTML special characters for reportlab Paragraph tags."""
    return (
        str(text)
        .replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
        .replace('"', "&quot;")
        .replace("'", "&#39;")
    )


def _register_unicode_font() -> str:
    """Try to register a Unicode-capable TTF font; fall back to Helvetica."""
    try:
        from reportlab.pdfbase import pdfmetrics
        from reportlab.pdfbase.ttfonts import TTFont

        candidates = [
            # macOS
            "/Library/Fonts/Arial Unicode.ttf",
            "/System/Library/Fonts/Helvetica.ttc",
            # Linux (common locations)
            "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
            "/usr/share/fonts/truetype/liberation/LiberationSans-Regular.ttf",
        ]
        for path in candidates:
            if Path(path).exists():
                try:
                    pdfmetrics.registerFont(TTFont("UnicodeSans", path))
                    logger.debug("Registered Unicode font from %s", path)
                    return "UnicodeSans"
                except Exception:
                    continue
    except Exception:
        pass
    return "Helvetica"


_BODY_FONT = _register_unicode_font()


# ─────────────────────────────────────────────────────────────────────────────
# PDF builder (reportlab)
# ─────────────────────────────────────────────────────────────────────────────

def build_pdf(resume: TailoredResume, output_path: Path) -> Path:
    """Render a TailoredResume to a clean ATS-optimized PDF."""
    try:
        from reportlab.lib.pagesizes import LETTER
        from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
        from reportlab.lib.units import inch
        from reportlab.lib import colors
        from reportlab.platypus import (
            SimpleDocTemplate, Paragraph, Spacer, HRFlowable, ListFlowable, ListItem
        )
        from reportlab.lib.enums import TA_LEFT, TA_CENTER
    except ImportError:
        logger.error("reportlab not installed — cannot build PDF. Run: pip install reportlab")
        raise

    output_path.parent.mkdir(parents=True, exist_ok=True)
    doc = SimpleDocTemplate(
        str(output_path),
        pagesize=LETTER,
        rightMargin=0.75 * inch,
        leftMargin=0.75 * inch,
        topMargin=0.75 * inch,
        bottomMargin=0.75 * inch,
    )

    styles = getSampleStyleSheet()
    bold_font = _BODY_FONT + "-Bold" if _BODY_FONT == "Helvetica" else _BODY_FONT
    name_style = ParagraphStyle(
        "Name", parent=styles["Normal"],
        fontName=bold_font, fontSize=16,
        spaceAfter=2, alignment=TA_CENTER,
    )
    contact_style = ParagraphStyle(
        "Contact", parent=styles["Normal"],
        fontName=_BODY_FONT, fontSize=9,
        spaceAfter=6, alignment=TA_CENTER, textColor=colors.HexColor("#444444"),
    )
    section_header_style = ParagraphStyle(
        "SectionHeader", parent=styles["Normal"],
        fontName=bold_font, fontSize=11,
        spaceBefore=10, spaceAfter=3,
        textColor=colors.HexColor("#1a1a2e"),
    )
    body_style = ParagraphStyle(
        "Body", parent=styles["Normal"],
        fontName=_BODY_FONT, fontSize=10,
        leading=14, spaceAfter=3,
    )
    bullet_style = ParagraphStyle(
        "Bullet", parent=styles["Normal"],
        fontName=_BODY_FONT, fontSize=10,
        leading=13, leftIndent=16, spaceAfter=2,
    )
    exp_header_style = ParagraphStyle(
        "ExpHeader", parent=styles["Normal"],
        fontName=bold_font, fontSize=10,
        spaceBefore=8, spaceAfter=2,
    )

    story = []

    # ── Header ───────────────────────────────────────────────────────────────
    story.append(Paragraph(_xml(resume.candidate_name or "Candidate Name"), name_style))
    contact_parts = list(filter(None, [
        resume.candidate_email,
        resume.candidate_phone,
        resume.candidate_location,
        resume.linkedin_url,
        resume.github_url,
    ]))
    if contact_parts:
        story.append(Paragraph(_xml("  |  ".join(contact_parts)), contact_style))
    story.append(HRFlowable(width="100%", thickness=1.5, color=colors.HexColor("#1a1a2e")))

    # ── Summary ──────────────────────────────────────────────────────────────
    if resume.professional_summary:
        story.append(Paragraph("PROFESSIONAL SUMMARY", section_header_style))
        story.append(HRFlowable(width="100%", thickness=0.5, color=colors.grey))
        story.append(Spacer(1, 3))
        story.append(Paragraph(_xml(resume.professional_summary), body_style))

    # ── Skills ───────────────────────────────────────────────────────────────
    if resume.skills_section:
        story.append(Paragraph("SKILLS", section_header_style))
        story.append(HRFlowable(width="100%", thickness=0.5, color=colors.grey))
        story.append(Spacer(1, 3))
        story.append(Paragraph(_xml(resume.skills_section), body_style))

    # ── Experience ───────────────────────────────────────────────────────────
    if resume.experience_bullets:
        story.append(Paragraph("EXPERIENCE", section_header_style))
        story.append(HRFlowable(width="100%", thickness=0.5, color=colors.grey))
        for header, bullets in resume.experience_bullets.items():
            story.append(Paragraph(_xml(header), exp_header_style))
            for b in bullets:
                story.append(Paragraph(f"• {_xml(b)}", bullet_style))

    # ── Education ────────────────────────────────────────────────────────────
    if resume.education_section:
        story.append(Paragraph("EDUCATION", section_header_style))
        story.append(HRFlowable(width="100%", thickness=0.5, color=colors.grey))
        story.append(Spacer(1, 3))
        story.append(Paragraph(_xml(resume.education_section), body_style))

    # ── Certifications ───────────────────────────────────────────────────────
    if resume.certifications_section:
        story.append(Paragraph("CERTIFICATIONS", section_header_style))
        story.append(HRFlowable(width="100%", thickness=0.5, color=colors.grey))
        story.append(Spacer(1, 3))
        story.append(Paragraph(_xml(resume.certifications_section), body_style))

    # ── Additional sections ──────────────────────────────────────────────────
    for section_name, content in resume.additional_sections.items():
        story.append(Paragraph(_xml(section_name.upper()), section_header_style))
        story.append(HRFlowable(width="100%", thickness=0.5, color=colors.grey))
        story.append(Spacer(1, 3))
        story.append(Paragraph(_xml(content), body_style))

    doc.build(story)
    logger.info("PDF saved to %s", output_path)
    return output_path


# ─────────────────────────────────────────────────────────────────────────────
# DOCX builder (python-docx)
# ─────────────────────────────────────────────────────────────────────────────

def build_docx(resume: TailoredResume, output_path: Path) -> Path:
    """Render a TailoredResume to a clean ATS-friendly DOCX."""
    try:
        from docx import Document
        from docx.shared import Pt, RGBColor, Inches
        from docx.enum.text import WD_ALIGN_PARAGRAPH
        from docx.oxml.ns import qn
        from docx.oxml import OxmlElement
    except ImportError:
        logger.error("python-docx not installed — cannot build DOCX. Run: pip install python-docx")
        raise

    output_path.parent.mkdir(parents=True, exist_ok=True)
    doc = Document()

    # Margins
    section = doc.sections[0]
    section.left_margin = Inches(0.75)
    section.right_margin = Inches(0.75)
    section.top_margin = Inches(0.75)
    section.bottom_margin = Inches(0.75)

    def set_font(run, size_pt: int, bold: bool = False, color: Optional[tuple] = None):
        run.font.name = "Calibri"
        run.font.size = Pt(size_pt)
        run.bold = bold
        if color:
            run.font.color.rgb = RGBColor(*color)

    def add_hr(doc):
        p = doc.add_paragraph()
        p.paragraph_format.space_before = Pt(2)
        p.paragraph_format.space_after = Pt(2)
        pPr = p._p.get_or_add_pPr()
        pBdr = OxmlElement("w:pBdr")
        bottom = OxmlElement("w:bottom")
        bottom.set(qn("w:val"), "single")
        bottom.set(qn("w:sz"), "6")
        bottom.set(qn("w:space"), "1")
        bottom.set(qn("w:color"), "1a1a2e")
        pBdr.append(bottom)
        pPr.append(pBdr)

    def add_section_header(doc, title: str):
        p = doc.add_paragraph()
        p.paragraph_format.space_before = Pt(10)
        p.paragraph_format.space_after = Pt(2)
        run = p.add_run(title)
        set_font(run, 11, bold=True, color=(26, 26, 46))
        add_hr(doc)

    # ── Name ─────────────────────────────────────────────────────────────────
    name_p = doc.add_paragraph()
    name_p.alignment = WD_ALIGN_PARAGRAPH.CENTER
    name_run = name_p.add_run(resume.candidate_name or "Candidate Name")
    set_font(name_run, 16, bold=True)

    # ── Contact ───────────────────────────────────────────────────────────────
    contact_parts = list(filter(None, [
        resume.candidate_email, resume.candidate_phone,
        resume.candidate_location, resume.linkedin_url, resume.github_url,
    ]))
    if contact_parts:
        contact_p = doc.add_paragraph()
        contact_p.alignment = WD_ALIGN_PARAGRAPH.CENTER
        contact_run = contact_p.add_run("  |  ".join(contact_parts))
        set_font(contact_run, 9, color=(68, 68, 68))

    add_hr(doc)

    # ── Summary ───────────────────────────────────────────────────────────────
    if resume.professional_summary:
        add_section_header(doc, "PROFESSIONAL SUMMARY")
        p = doc.add_paragraph(resume.professional_summary)
        p.runs[0].font.name = "Calibri"
        p.runs[0].font.size = Pt(10)

    # ── Skills ────────────────────────────────────────────────────────────────
    if resume.skills_section:
        add_section_header(doc, "SKILLS")
        p = doc.add_paragraph(resume.skills_section)
        p.runs[0].font.name = "Calibri"
        p.runs[0].font.size = Pt(10)

    # ── Experience ────────────────────────────────────────────────────────────
    if resume.experience_bullets:
        add_section_header(doc, "EXPERIENCE")
        for header, bullets in resume.experience_bullets.items():
            p = doc.add_paragraph()
            p.paragraph_format.space_before = Pt(8)
            run = p.add_run(header)
            set_font(run, 10, bold=True)
            for b in bullets:
                bp = doc.add_paragraph(style="List Bullet")
                bp.paragraph_format.left_indent = Inches(0.2)
                bp.paragraph_format.space_after = Pt(2)
                brun = bp.add_run(b)
                set_font(brun, 10)

    # ── Education ─────────────────────────────────────────────────────────────
    if resume.education_section:
        add_section_header(doc, "EDUCATION")
        p = doc.add_paragraph(resume.education_section)
        p.runs[0].font.name = "Calibri"
        p.runs[0].font.size = Pt(10)

    # ── Certifications ────────────────────────────────────────────────────────
    if resume.certifications_section:
        add_section_header(doc, "CERTIFICATIONS")
        p = doc.add_paragraph(resume.certifications_section)
        p.runs[0].font.name = "Calibri"
        p.runs[0].font.size = Pt(10)

    doc.save(str(output_path))
    logger.info("DOCX saved to %s", output_path)
    return output_path


def build_cover_letter_docx(
    cover_letter_text: str,
    company: str,
    role: str,
    candidate_name: str,
    output_path: Path,
) -> Path:
    """Save a cover letter as a formatted DOCX."""
    try:
        from docx import Document
        from docx.shared import Pt, Inches
    except ImportError:
        logger.error("python-docx not installed")
        raise

    output_path.parent.mkdir(parents=True, exist_ok=True)
    doc = Document()
    section = doc.sections[0]
    section.left_margin = Inches(1)
    section.right_margin = Inches(1)
    section.top_margin = Inches(1)
    section.bottom_margin = Inches(1)

    for line in cover_letter_text.split("\n"):
        p = doc.add_paragraph(line)
        p.paragraph_format.space_after = Pt(6)
        if p.runs:
            p.runs[0].font.name = "Calibri"
            p.runs[0].font.size = Pt(11)

    doc.save(str(output_path))
    logger.info("Cover letter DOCX saved to %s", output_path)
    return output_path

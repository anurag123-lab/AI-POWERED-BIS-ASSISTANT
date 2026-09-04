"""
PDF generation for a product Case - the one consolidated BIS report.

`generate_case_pdf(case, views, language, sources)` renders the full journey
(standards, related, certification, scheme, testing, licensing, documents/labs)
plus a source/reference table. Supports English, Hindi (Devanagari) and Telugu
by registering bundled Noto TTF fonts; Indian Standard numbers and citations
are always left in Latin script.

`generate_compliance_pdf(case_data)` is kept as a thin back-compat wrapper.
"""

import io
import os
import re
import html

from reportlab.lib.pagesizes import A4
from reportlab.lib import colors
from reportlab.lib.units import mm
from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle, HRFlowable
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont

_FONT_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "static", "fonts")
_FONTS_READY = None

# language -> (regular font name, bold font name)
_LANG_FONT = {
    "en": ("NotoSans", "NotoSans-Bold"),
    "hi": ("NotoSansDevanagari", "NotoSansDevanagari"),
    "te": ("NotoSansTelugu", "NotoSansTelugu"),
}


def _register_fonts():
    global _FONTS_READY
    if _FONTS_READY is not None:
        return _FONTS_READY
    faces = {
        "NotoSans": "NotoSans-Regular.ttf",
        "NotoSans-Bold": "NotoSans-Bold.ttf",
        "NotoSansDevanagari": "NotoSansDevanagari-Regular.ttf",
        "NotoSansTelugu": "NotoSansTelugu-Regular.ttf",
    }
    ok = True
    for name, fn in faces.items():
        path = os.path.join(_FONT_DIR, fn)
        if os.path.exists(path):
            try:
                pdfmetrics.registerFont(TTFont(name, path))
            except Exception as exc:  # noqa: BLE001
                print(f"[pdf] font {name} failed: {exc}")
                ok = False
        else:
            ok = False
    _FONTS_READY = ok
    return ok


def _fonts_for(language):
    if _register_fonts() and language in _LANG_FONT:
        return _LANG_FONT[language]
    return ("Helvetica", "Helvetica-Bold")  # Latin-only fallback


_MD_LINK = re.compile(r'\[([^\]]+)\]\((https?://[^)\s]+)\)')


def _md_to_rl(text):
    """Convert the KB markdown body to reportlab mini-HTML paragraphs."""
    if not text:
        return []
    paras = []
    for block in re.split(r'\n{2,}', str(text).strip()):
        lines = [l for l in block.split('\n') if l.strip()]
        is_list = lines and all(re.match(r'\s*[-*]\s+', l) for l in lines)
        if is_list:
            for l in lines:
                paras.append(("li", _inline(re.sub(r'^\s*[-*]\s+', '', l))))
        else:
            paras.append(("p", "<br/>".join(_inline(l) for l in lines)))
    return paras


def _inline(s):
    s = html.escape(str(s), quote=False)
    s = _MD_LINK.sub(r'\1 (\2)', s)
    s = re.sub(r'\*\*([^*]+)\*\*', r'<b>\1</b>', s)
    s = re.sub(r'(?<!\*)\*([^*]+)\*(?!\*)', r'<i>\1</i>', s)
    s = re.sub(r'`([^`]+)`', r'\1', s)
    s = s.replace('#', '')
    return s


def generate_case_pdf(case, views, language="en", sources=None, report_title="BIS Compliance Report"):
    reg, bold = _fonts_for(language)
    buf = io.BytesIO()
    doc = SimpleDocTemplate(buf, pagesize=A4, rightMargin=18 * mm, leftMargin=18 * mm,
                            topMargin=16 * mm, bottomMargin=16 * mm)
    base = getSampleStyleSheet()

    st_title = ParagraphStyle('t', parent=base['Heading1'], fontName=bold, fontSize=19,
                              leading=23, textColor=colors.HexColor('#0f172a'))
    st_sub = ParagraphStyle('s', parent=base['Normal'], fontName=reg, fontSize=9,
                            leading=12, textColor=colors.HexColor('#475569'))
    st_h2 = ParagraphStyle('h2', parent=base['Heading2'], fontName=bold, fontSize=12.5,
                           leading=16, textColor=colors.HexColor('#c2410c'),
                           spaceBefore=14, spaceAfter=5)
    st_body = ParagraphStyle('b', parent=base['Normal'], fontName=reg, fontSize=9.5,
                             leading=14, textColor=colors.HexColor('#1f2937'), spaceAfter=5)
    st_li = ParagraphStyle('li', parent=st_body, leftIndent=12, bulletIndent=2, spaceAfter=2)
    st_src = ParagraphStyle('src', parent=base['Normal'], fontName='Helvetica', fontSize=7.6,
                            leading=10, textColor=colors.HexColor('#475569'))

    story = []
    prod = case.get('product_name') or 'Product'
    story.append(Paragraph(html.escape(report_title), st_title))
    story.append(Paragraph(
        f"{html.escape(prod)} &nbsp;|&nbsp; {html.escape(case.get('user_type') or '')} "
        f"&nbsp;|&nbsp; {html.escape((case.get('city') or ''))}, {html.escape(case.get('state') or '')} "
        f"&nbsp;|&nbsp; {html.escape(case.get('is_number') or '')}", st_sub))
    story.append(Spacer(1, 6))
    story.append(HRFlowable(width="100%", thickness=1.4, color=colors.HexColor('#ea580c'), spaceAfter=10))

    all_sources = list(sources or [])
    for v in (views or []):
        story.append(Paragraph(html.escape(v.get('title') or v.get('area', '')), st_h2))
        for kind, frag in _md_to_rl(v.get('body_md') or ''):
            if kind == 'li':
                story.append(Paragraph(frag, st_li, bulletText='•'))
            else:
                story.append(Paragraph(frag, st_body))
        for s in v.get('sources') or []:
            all_sources.append(s)

    # consolidated source / reference table
    seen = set()
    rows = [[Paragraph("<b>#</b>", st_src), Paragraph("<b>BIS document</b>", st_src),
             Paragraph("<b>Page / clause</b>", st_src), Paragraph("<b>URL</b>", st_src)]]
    n = 0
    for s in all_sources:
        key = (s.get('doc'), s.get('url'))
        if key in seen:
            continue
        seen.add(key)
        n += 1
        loc = " ".join(filter(None, [
            f"p.{s['page']}" if s.get('page') else "",
            f"cl.{s['clause']}" if s.get('clause') else "",
        ])) or "-"
        rows.append([
            Paragraph(str(n), st_src),
            Paragraph(html.escape(s.get('doc') or ''), st_src),
            Paragraph(loc, st_src),
            Paragraph(html.escape(s.get('url') or ''), st_src),
        ])
    if n:
        story.append(Paragraph("Source / reference table", st_h2))
        tbl = Table(rows, colWidths=[12 * mm, 70 * mm, 22 * mm, 70 * mm])
        tbl.setStyle(TableStyle([
            ('BACKGROUND', (0, 0), (-1, 0), colors.HexColor('#fff7ed')),
            ('GRID', (0, 0), (-1, -1), 0.4, colors.HexColor('#e2e8f0')),
            ('VALIGN', (0, 0), (-1, -1), 'TOP'),
            ('TOPPADDING', (0, 0), (-1, -1), 3),
            ('BOTTOMPADDING', (0, 0), (-1, -1), 3),
        ]))
        story.append(tbl)

    story.append(Spacer(1, 14))
    story.append(Paragraph(
        "This report is generated from your BIS Assistant Case. It summarises curated guidance "
        "sourced from bis.gov.in and Government of India notifications. Indian Standard numbers are "
        "kept in their published form so you can verify them. Confirm current notifications on "
        "www.bis.gov.in before commercial production. Not officially affiliated with BIS.", st_src))

    doc.build(story)
    buf.seek(0)
    return buf.getvalue()


def generate_compliance_pdf(case_data):
    """Back-compat wrapper: builds a minimal 'views' list from a legacy case dict."""
    views = [{
        "title": "Compliance summary",
        "area": "summary",
        "body_md": (
            f"**Product:** {case_data.get('product_name', '-')}\n\n"
            f"**Indian Standard:** {case_data.get('is_number', '-')}\n\n"
            f"**QCO status:** {case_data.get('qco_status', '-')}\n\n"
            f"**Scheme:** {case_data.get('scheme', '-')}"
        ),
        "sources": [],
    }]
    checklist = case_data.get('checklist') or []
    if checklist:
        views.append({
            "title": "Mandatory testing & quality control",
            "area": "testing",
            "body_md": "\n".join(f"- {c}" for c in checklist),
            "sources": [],
        })
    return generate_case_pdf(case_data, views, language="en")

"""Render resumes and cover letters to ATS-friendly one-column PDFs in several themes (real text, no images/tables)."""
import re
from fpdf import FPDF

HEADS = ("SUMMARY|PROFILE|SKILLS|CORE SKILLS|EXPERIENCE|WORK EXPERIENCE|PROFESSIONAL EXPERIENCE|EDUCATION|PROJECTS|CERTIFICATIONS|"
         "LANGUAGES|TARGET|MOST RELEVANT SKILLS|NAME/CONTACT|CONTACT|AWARDS|PUBLICATIONS|"
         "RESUMEN|HABILIDADES|EXPERIENCIA|EDUCACION|EDUCACIÓN|PROFIL|KENNTNISSE|BERUFSERFAHRUNG|AUSBILDUNG|"
         "RÉSUMÉ|COMPÉTENCES|EXPÉRIENCE|FORMATION|SPRACHEN|IDIOMAS|LANGUES")
HEAD = re.compile(rf"^({HEADS})\b", re.I)

# name: (font, base size, accent RGB, heading style, name align, line gap)
THEMES = {
    "classic":   ("Helvetica", 10, (30, 64, 175), "rule", "L", 5.0),
    "modern":    ("Helvetica", 10, (217, 4, 41), "band", "L", 5.0),
    "minimal":   ("Helvetica", 10, (60, 60, 60), "plain", "L", 5.0),
    "compact":   ("Helvetica", 9, (30, 30, 30), "rule", "L", 4.3),
    "executive": ("Times", 11, (20, 20, 20), "rule", "C", 5.4),
    "serif":     ("Times", 11, (110, 30, 30), "plain", "L", 5.4),
    "slate":     ("Helvetica", 10, (51, 65, 85), "band", "L", 5.0),
    "emerald":   ("Helvetica", 10, (4, 120, 87), "rule", "L", 5.0),
    "centered":  ("Helvetica", 10, (40, 40, 40), "rule", "C", 5.0),
    "mono":      ("Courier", 9, (0, 0, 0), "plain", "L", 4.6),
}
THEME_LABELS = {"classic": "Classic (blue rule)", "modern": "Modern (crimson band)", "minimal": "Minimal",
                "compact": "Compact (fits more)", "executive": "Executive (serif, centered)", "serif": "Serif",
                "slate": "Slate band", "emerald": "Emerald", "centered": "Centered", "mono": "Monospace"}


def _s(t):
    return (t.replace("\u2022", "-").replace("\u2013", "-").replace("\u2014", "-").replace("\u2019", "'")
             .replace("\u201c", '"').replace("\u201d", '"').encode("latin-1", "replace").decode("latin-1"))


def _pdf():
    pdf = FPDF(format="Letter"); pdf.set_margins(18, 16, 18); pdf.set_auto_page_break(True, 16); pdf.add_page()
    return pdf


def _header(pdf, th, name, contact):
    font, size, acc, style, align, gap = th
    pdf.set_font(font, "B", size + 8); pdf.set_text_color(*acc if style == "band" else (0, 0, 0))
    pdf.multi_cell(0, 9, name, align=align, new_x="LMARGIN", new_y="NEXT"); pdf.set_text_color(0, 0, 0)
    if contact:
        pdf.set_font(font, "", size - 0.5); pdf.set_text_color(80, 80, 80)
        pdf.multi_cell(0, gap, contact, align=align, new_x="LMARGIN", new_y="NEXT"); pdf.set_text_color(0, 0, 0)
    pdf.ln(1)


def _heading(pdf, th, text):
    font, size, acc, style, align, gap = th
    pdf.ln(2)
    if style == "band":
        pdf.set_fill_color(*acc); pdf.set_text_color(255, 255, 255); pdf.set_font(font, "B", size + 1)
        pdf.cell(0, 6.5, " " + text.upper(), fill=True, new_x="LMARGIN", new_y="NEXT"); pdf.set_text_color(0, 0, 0); pdf.ln(1)
        return
    pdf.set_font(font, "B", size + 1); pdf.set_text_color(*acc)
    pdf.multi_cell(0, 6, text.upper(), align="C" if align == "C" and style == "rule" else "L", new_x="LMARGIN", new_y="NEXT")
    pdf.set_text_color(0, 0, 0)
    if style == "rule":
        pdf.set_draw_color(*acc); pdf.line(pdf.l_margin, pdf.get_y(), pdf.w - pdf.r_margin, pdf.get_y())
    pdf.ln(1)


def text_to_pdf(text: str, path: str, theme: str = "classic"):
    th = THEMES.get(theme or "classic", THEMES["classic"])
    font, size, acc, style, align, gap = th
    pdf = _pdf()
    lines = [_s(l.rstrip()) for l in (text or "").splitlines()]
    while lines and not lines[0].strip():
        lines.pop(0)
    name = lines.pop(0).strip() if lines else ""
    contact = ""
    if lines and not HEAD.match(lines[0].strip()) and ("@" in lines[0] or "|" in lines[0]):
        contact = lines.pop(0).strip()
    _header(pdf, th, name, contact)
    for l in lines:
        t = l.strip()
        if HEAD.match(t) and len(t) < 40:
            _heading(pdf, th, t.rstrip(":")); continue
        if not t:
            pdf.ln(1.5); continue
        if t[:2] in ("- ", "* ", "• "):
            pdf.set_font(font, "", size); x = pdf.l_margin
            pdf.set_x(x + 3); pdf.multi_cell(0, gap, "- " + t[2:], new_x="LMARGIN", new_y="NEXT"); continue
        if (" - " in t or " | " in t or " at " in t) and len(t) < 120 and not t.endswith("."):
            pdf.set_font(font, "B", size); pdf.multi_cell(0, gap, t, new_x="LMARGIN", new_y="NEXT"); continue
        pdf.set_font(font, "", size); pdf.multi_cell(0, gap, t, new_x="LMARGIN", new_y="NEXT")
    pdf.output(path)
    return path


def letter_to_pdf(text: str, path: str, name: str = "", contact: str = "", theme: str = "classic"):
    th = THEMES.get(theme or "classic", THEMES["classic"])
    pdf = _pdf()
    if name:
        _header(pdf, th, _s(name), _s(contact)); pdf.ln(4)
    pdf.set_font(th[0], "", th[1] + 1)
    for l in _s(text or "").splitlines():
        pdf.multi_cell(0, th[5] + 0.6, l or " ", new_x="LMARGIN", new_y="NEXT")
    pdf.output(path)
    return path

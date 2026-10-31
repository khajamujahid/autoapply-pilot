"""Render tailored resume text to a clean one-column PDF."""
import re
from fpdf import FPDF

HEAD = re.compile(r"^(SUMMARY|SKILLS|EXPERIENCE|EDUCATION|PROJECTS|CERTIFICATIONS|TARGET|MOST RELEVANT SKILLS|NAME/CONTACT|CONTACT)\b", re.I)


def _s(t):
    return (t.replace("\u2022", "-").replace("\u2013", "-").replace("\u2014", "-").replace("\u2019", "'")
             .encode("latin-1", "replace").decode("latin-1"))


def text_to_pdf(text: str, path: str):
    pdf = FPDF(format="Letter"); pdf.set_margins(18, 16, 18); pdf.set_auto_page_break(True, 16); pdf.add_page()
    lines = (text or "").splitlines() or [""]
    first = True
    for line in lines:
        l = _s(line.rstrip())
        if first and l.strip():
            pdf.set_font("Helvetica", "B", 16); pdf.multi_cell(0, 8, l.strip(), new_x="LMARGIN", new_y="NEXT"); first = False; continue
        if HEAD.match(l.strip()) and len(l) < 60:
            pdf.ln(2); pdf.set_font("Helvetica", "B", 11); pdf.set_text_color(30, 64, 175)
            pdf.multi_cell(0, 6, l.strip().upper(), new_x="LMARGIN", new_y="NEXT"); pdf.set_text_color(0, 0, 0)
            pdf.set_draw_color(200, 200, 200); pdf.line(pdf.l_margin, pdf.get_y(), pdf.w - pdf.r_margin, pdf.get_y()); pdf.ln(1)
            continue
        pdf.set_font("Helvetica", size=10)
        pdf.multi_cell(0, 5, l or " ", new_x="LMARGIN", new_y="NEXT")
    pdf.output(path)
    return path

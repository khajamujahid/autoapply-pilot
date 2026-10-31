from fpdf import FPDF


def text_to_pdf(text: str, path: str):
    pdf = FPDF(); pdf.set_auto_page_break(True, 15); pdf.add_page(); pdf.set_font("Helvetica", size=10)
    safe = text.encode("latin-1", "replace").decode("latin-1")
    for line in safe.splitlines() or [""]:
        pdf.multi_cell(0, 5, line or " ", new_x="LMARGIN", new_y="NEXT")
    pdf.output(path)
    return path

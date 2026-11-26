"""Build the synthetic sample manuscript used by the acceptance tests.

Everything in the generated DOCX is SYNTHETIC FIXTURE CONTENT. The authors,
institutions, data and references are invented for testing the typesetting
pipeline and must never be published or cited.

The manuscript carries what the acceptance criteria ask for: three authors,
multiple affiliations, a wide table, a figure with alt text, a display
equation (real Word OMML), in-text citations and a numbered reference list,
declarations, and a supplementary file link.

    python -m studio.fixtures.make_sample out.docx [--tracked-change]
"""
from __future__ import annotations

import argparse
import io
import struct
import zlib
from pathlib import Path

from docx import Document
from docx.enum.text import WD_ALIGN_PARAGRAPH
from docx.oxml import parse_xml
from docx.oxml.ns import nsdecls
from docx.shared import Pt, Inches

FIXTURE_NOTE = "SYNTHETIC FIXTURE CONTENT: invented for software testing; not real research."


def _png_chart(width: int = 640, height: int = 360) -> bytes:
    """A small bar chart drawn pixel by pixel, so the fixture needs no Pillow."""
    bg = (255, 255, 255)
    axis = (40, 40, 40)
    bars = [(0.42, (31, 78, 121)), (0.68, (31, 78, 121)), (0.55, (196, 89, 17)), (0.81, (196, 89, 17))]
    px = [[bg for _ in range(width)] for _ in range(height)]
    x0, y0 = 60, height - 40
    for x in range(x0, width - 20):
        px[y0][x] = axis
    for y in range(20, y0 + 1):
        px[y][x0] = axis
    bw = 80
    for i, (frac, col) in enumerate(bars):
        left = x0 + 40 + i * (bw + 50)
        top = int(y0 - frac * (y0 - 30))
        for y in range(top, y0):
            for x in range(left, left + bw):
                px[y][x] = col
    raw = b"".join(b"\x00" + bytes(c for p in row for c in p) for row in px)

    def chunk(tag: bytes, data: bytes) -> bytes:
        return struct.pack(">I", len(data)) + tag + data + struct.pack(">I", zlib.crc32(tag + data) & 0xFFFFFFFF)

    ihdr = struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0)
    return b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", ihdr) + chunk(b"IDAT", zlib.compress(raw, 9)) + chunk(b"IEND", b"")


# A display equation in Office Math Markup:  BMI = m / h^2
OMML_EQUATION = (
    '<m:oMathPara %s><m:oMath>'
    '<m:r><m:t>BMI</m:t></m:r><m:r><m:t>=</m:t></m:r>'
    '<m:f><m:num><m:r><m:t>m</m:t></m:r></m:num>'
    '<m:den><m:sSup><m:e><m:r><m:t>h</m:t></m:r></m:e><m:sup><m:r><m:t>2</m:t></m:r></m:sup></m:sSup></m:den></m:f>'
    '</m:oMath></m:oMathPara>' % nsdecls("m")
)


def _sup(par, text):
    run = par.add_run(text)
    run.font.superscript = True
    return run


def _bold_lead(doc, lead, rest):
    p = doc.add_paragraph()
    p.add_run(lead).bold = True
    p.add_run(rest)
    return p


def _hyperlink(par, url, text):
    part = par.part
    r_id = part.relate_to(url, "http://schemas.openxmlformats.org/officeDocument/2006/relationships/hyperlink", is_external=True)
    xml = (
        f'<w:hyperlink {nsdecls("w", "r")} r:id="{r_id}"><w:r><w:rPr><w:rStyle w:val="Hyperlink"/></w:rPr>'
        f"<w:t>{text}</w:t></w:r></w:hyperlink>"
    )
    par._p.append(parse_xml(xml))


def build(path: Path, tracked_change: bool = False) -> Path:
    doc = Document()
    style = doc.styles["Normal"]
    style.font.name = "Times New Roman"
    style.font.size = Pt(11)

    note = doc.add_paragraph(FIXTURE_NOTE)
    note.runs[0].italic = True

    doc.add_paragraph(
        "Effect of a Pharmacist-Led Medication Review on Blood Pressure Control: "
        "A Synthetic Randomised Trial",
        style="Title",
    )

    p = doc.add_paragraph()
    p.add_run("Asha R. Menon")
    _sup(p, "1,2*")
    p.add_run(", Daniel K. Osei")
    _sup(p, "2")
    p.add_run(", María José Núñez-Ibáñez")
    _sup(p, "3")

    for label, text in [
        ("1", "Department of Clinical Pharmacy, Synthetic Institute of Health Sciences, Kochi, India"),
        ("2", "School of Pharmacy, Example University of Medicine, Accra, Ghana"),
        ("3", "Unidad de Farmacología Clínica, Hospital Universitario Ficticio, Sevilla, Spain"),
    ]:
        ap = doc.add_paragraph()
        _sup(ap, label)
        ap.add_run(" " + text)

    doc.add_paragraph("*Corresponding author: Asha R. Menon, asha.menon@example.org")
    doc.add_paragraph(
        # 0000-0002-1825-0097 is ORCID's own published example iD for a fictitious researcher.
        "ORCID: Asha R. Menon https://orcid.org/0000-0002-1825-0097"
    )
    doc.add_paragraph("Received: 2026-03-02; Revised: 2026-05-14; Accepted: 2026-06-20")

    doc.add_heading("Abstract", level=1)
    _bold_lead(doc, "Background: ", "Uncontrolled hypertension remains common in primary care. "
               "We tested whether a structured pharmacist-led medication review lowers systolic blood pressure.")
    _bold_lead(doc, "Methods: ", "In this synthetic two-arm trial, 248 adults were allocated to usual care (n = 124) "
               "or a medication review (n = 124) and followed for 24 weeks.")
    _bold_lead(doc, "Results: ", "Mean systolic blood pressure fell by 12.4 mmHg (95% CI 10.1 to 14.7) with the review "
               "and by 5.1 mmHg (95% CI 3.2 to 7.0) with usual care (p < 0.001).")
    _bold_lead(doc, "Conclusions: ", "A pharmacist-led review produced a larger reduction in systolic blood pressure "
               "than usual care in this synthetic cohort.")
    _bold_lead(doc, "Keywords: ", "hypertension; clinical pharmacy; medication review; primary care; randomised trial")

    doc.add_heading("1. Introduction", level=1)
    doc.add_paragraph(
        "Hypertension affects an estimated 1.28 billion adults worldwide [1]. Medication reviews led by "
        "pharmacists have been associated with better adherence [2,3], although effect sizes vary between "
        "settings [4]. The body mass index (BMI) was recorded as a covariate using Equation 1."
    )

    doc.add_heading("2. Methods", level=1)
    doc.add_heading("2.1 Participants", level=2)
    para = doc.add_paragraph("Adults aged 30 to 79 years with a clinic systolic blood pressure of 140 mmHg or higher were eligible. ")
    if tracked_change:
        ins = parse_xml(
            f'<w:ins {nsdecls("w")} w:id="901" w:author="Reviewer 2" w:date="2026-06-01T10:00:00Z">'
            "<w:r><w:t>Pregnant participants were excluded. </w:t></w:r></w:ins>"
        )
        para._p.append(ins)
    para.add_run("Body mass index was calculated as shown in Equation 1, where m is mass in kilograms and h is height in metres.")

    eq_par = doc.add_paragraph()
    eq_par.alignment = WD_ALIGN_PARAGRAPH.CENTER
    eq_par._p.append(parse_xml(OMML_EQUATION))

    doc.add_heading("2.2 Outcomes", level=2)
    doc.add_paragraph(
        "The primary outcome was the change in mean systolic blood pressure at 24 weeks. Baseline characteristics "
        "are summarised in Table 1 and the primary outcome is shown in Figure 1."
    )

    doc.add_heading("3. Results", level=1)
    doc.add_paragraph("Table 1. Baseline characteristics of participants by allocation group (synthetic data).")
    headers = ["Characteristic", "Usual care (n = 124)", "Review (n = 124)", "Total (n = 248)",
               "Difference", "95% CI", "p value", "Missing (n)"]
    rows = [
        ["Age, years, mean (SD)", "58.2 (9.6)", "57.9 (10.1)", "58.1 (9.8)", "0.3", "−2.2 to 2.8", "0.81", "0"],
        ["Women, n (%)", "61 (49.2)", "64 (51.6)", "125 (50.4)", "−2.4", "−14.8 to 10.0", "0.70", "0"],
        ["Systolic BP, mmHg, mean (SD)", "152.3 (8.7)", "151.8 (9.2)", "152.1 (8.9)", "0.5", "−1.7 to 2.7", "0.66", "2"],
        ["Diastolic BP, mmHg, mean (SD)", "91.4 (6.3)", "90.9 (6.8)", "91.2 (6.5)", "0.5", "−1.1 to 2.1", "0.55", "2"],
        ["BMI, kg/m², mean (SD)", "28.7 (4.1)", "29.0 (4.4)", "28.9 (4.3)", "−0.3", "−1.4 to 0.8", "0.58", "1"],
        ["Antihypertensives, median (IQR)", "2 (1–3)", "2 (1–3)", "2 (1–3)", "0", "—", "0.92", "0"],
    ]
    table = doc.add_table(rows=1 + len(rows), cols=len(headers))
    table.style = "Table Grid"
    for i, h in enumerate(headers):
        table.rows[0].cells[i].text = h
    for r, row in enumerate(rows, start=1):
        for c, val in enumerate(row):
            table.rows[r].cells[c].text = val
    doc.add_paragraph("SD, standard deviation; CI, confidence interval; IQR, interquartile range.")

    doc.add_paragraph(
        "At 24 weeks, the reduction in systolic blood pressure was 12.4 mmHg in the review group and 5.1 mmHg with "
        "usual care, a between-group difference of 7.3 mmHg (95% CI 4.6 to 10.0; p < 0.001), as shown in Figure 1."
    )

    pic_par = doc.add_paragraph()
    pic_par.add_run().add_picture(io.BytesIO(_png_chart()), width=Inches(5.0))
    # Alt text lives on the drawing's docPr element.
    for docpr in pic_par._p.iter("{http://schemas.openxmlformats.org/drawingml/2006/wordprocessingDrawing}docPr"):
        docpr.set("descr", "Bar chart of mean systolic blood pressure reduction at 12 and 24 weeks: "
                           "usual care 3.0 and 5.1 mmHg, review 7.9 and 12.4 mmHg.")
    doc.add_paragraph("Figure 1. Mean reduction in systolic blood pressure by group at 12 and 24 weeks (synthetic data).")

    doc.add_heading("4. Discussion", level=1)
    doc.add_paragraph(
        "The difference of 7.3 mmHg is consistent with earlier reports [2,4] and larger than the effect reported "
        "for telephone follow-up alone [5]. The synthetic design limits generalisability."
    )

    doc.add_heading("5. Conclusions", level=1)
    sp = doc.add_paragraph("A structured pharmacist-led review lowered systolic blood pressure. Supplementary Table S1 is available at ")
    _hyperlink(sp, "https://example.org/supplementary/S1.xlsx", "https://example.org/supplementary/S1.xlsx")
    sp.add_run(".")

    doc.add_heading("Funding", level=2)
    doc.add_paragraph("This synthetic study received no external funding.")
    doc.add_heading("Conflicts of Interest", level=2)
    doc.add_paragraph("The authors declare no conflicts of interest.")
    doc.add_heading("Author Contributions", level=2)
    doc.add_paragraph("ARM conceived the study; DKO analysed the data; MJNI drafted the manuscript. All authors approved the final version.")
    doc.add_heading("Data Availability", level=2)
    doc.add_paragraph("The synthetic dataset is available from the corresponding author on request.")
    doc.add_heading("Ethics Approval", level=2)
    doc.add_paragraph("Not applicable: synthetic fixture content involving no human participants.")
    doc.add_heading("Acknowledgements", level=2)
    doc.add_paragraph("The authors thank the synthetic pharmacy staff.")

    doc.add_heading("References", level=1)
    refs = [
        "Zhou B, Carrillo-Larco RM, Danaei G, Riley LM, Paciorek CJ, Stevens GA, et al. Worldwide trends in "
        "hypertension prevalence and progress in treatment and control from 1990 to 2019. Lancet. "
        "2021;398(10304):957-980. doi:10.1016/S0140-6736(21)01330-1",
        "Example A, Sample B. Pharmacist interventions for adherence in hypertension: a synthetic review. "
        "J Synth Pharm. 2020;12(3):45-52.",
        "Placeholder C, Fixture D, Test E. Medication review and blood pressure outcomes. Synth Clin Res. "
        "2019;8(2):101-109.",
        "Imaginary F. Variation in community pharmacy services across settings. Fict Health Serv J. 2018;5:22-30.",
        "Telephone follow-up for hypertension, report of a working group, unpublished notes circulated 2017",
    ]
    for i, r in enumerate(refs, start=1):
        doc.add_paragraph(f"{i}. {r}")

    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    doc.save(path)
    return path


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("out", type=Path)
    ap.add_argument("--tracked-change", action="store_true", help="include an unresolved insertion")
    args = ap.parse_args(argv)
    print(build(args.out, args.tracked_change))


if __name__ == "__main__":
    main()

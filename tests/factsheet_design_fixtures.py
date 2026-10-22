"""Synthetic factsheet templates and completed examples for the design agents' tests.

Drawn with reportlab, so the tests never need a client's template: a title, a
date, a few labelled figures and a holdings table with a header band and rows
at a regular pitch, the way the real account factsheets are laid out.
"""
from __future__ import annotations

from pathlib import Path

from reportlab.pdfgen import canvas

HOLDINGS = (("Alpha Holdings Inc", "Technology", "25.10%"), ("Beta Biotech Corp", "Health Care", "18.40%"),
            ("Gamma Retail Ltd", "Consumer", "12.05%"), ("Delta Energy plc", "Energy", "9.75%"),
            ("Epsilon Bank AG", "Financials", "7.20%"))
TEMPLATE_HOLDINGS = tuple((f"Holding {i}", f"Sector {i}", "0.00%") for i in range(1, 6))


def draw(path: Path, *, date: str, figures: tuple[str, str, str], holdings, disclaimer: str = "",
         figure_size: float = 12) -> Path:
    c = canvas.Canvas(str(path), pagesize=(595.5, 842.25))
    c.setFont("Helvetica-Bold", 18)
    c.drawString(40, 790, "Example Growth Fund")
    c.setFont("Helvetica", 9)
    c.drawString(40, 772, date)
    labels = ("Net asset value", "Year to date", "Since inception")
    for i, (label, value) in enumerate(zip(labels, figures)):
        c.setFont("Helvetica", 8)
        c.drawString(40 + 170 * i, 730, label)
        c.setFont("Helvetica-Bold", figure_size)
        c.drawString(40 + 170 * i, 714, value)
    c.setFont("Helvetica-Bold", 9)
    c.drawString(40, 672, "Top 5 Holdings")
    c.setFillColorRGB(0.12, 0.25, 0.45)
    c.rect(40, 650, 400, 14, stroke=0, fill=1)
    c.setFillColorRGB(1, 1, 1)
    c.setFont("Helvetica-Bold", 8)
    for x, header in ((44, "Holding"), (224, "Sector"), (436, "Weight")):
        (c.drawRightString if header == "Weight" else c.drawString)(x, 654, header)
    c.setFillColorRGB(0, 0, 0)
    c.setFont("Helvetica", 8)
    for row, (name, sector, weight) in enumerate(holdings):
        y = 641.2 - 12 * row  # centred in a 12pt row, as the renderers draw it
        c.drawString(44, y, name)
        c.drawString(224, y, sector)
        c.drawRightString(436, y, weight)
    c.setStrokeColorRGB(0.6, 0.6, 0.6)
    c.rect(40, 650 - 12 * len(holdings) - 2, 400, 12 * len(holdings) + 2, stroke=1, fill=0)
    c.setFont("Helvetica", 6)
    c.drawString(40, 60, disclaimer or "Past performance is not a guide to future returns.")
    c.showPage()
    c.save()
    return path


def template(folder: Path) -> Path:
    return draw(folder / "template.pdf", date="[Date]", figures=("[NAV]", "[YTD]", "[SI]"),
                holdings=TEMPLATE_HOLDINGS)


def example(folder: Path, name: str = "example.pdf", *, date: str = "30 September 2026",
            figures: tuple[str, str, str] = ("125,430.55", "8.42%", "21.07%"), figure_size: float = 12) -> Path:
    return draw(folder / name, date=date, figures=figures, holdings=HOLDINGS, figure_size=figure_size)

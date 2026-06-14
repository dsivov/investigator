"""Build a fictionalised crime-investigation report from the anonymised
Havering police report by substituting a consistent fake named cast.

Preserves the full procedural detail of the 29-page original (THRIVE+,
safeguarding triage, forensic submissions, investigation updates, solvability
assessment, etc.) so the resulting test document is realistically large and
entity-rich, but every person is invented.
"""
from __future__ import annotations

import re
from pathlib import Path

import fitz  # PyMuPDF

SRC = Path("news_investigations/test_fixtures/police_rep_D_original.txt")
OUT_TXT = Path("news_investigations/test_fixtures/fake_police_rep_D_full.txt")
OUT_PDF = Path("news_investigations/test_fixtures/fake_police_rep_D_full.pdf")

# Real officer surname -> fake surname (badge numbers are kept as-is).
OFFICERS = {
    "Stockman": "Pearce", "Chaudry": "Wells", "Bhogal": "Hayes",
    "Ekrem": "Pryce", "Barnes": "Okafor", "Mira": "Nadel",
    "Adekanmbi": "Okonkwo", "Allix": "Mercer", "Chapman": "Whitfield",
    "Clubb": "Dawson", "Hays": "Cross", "Sehmi": "Bannerman",
    "Stein": "Holloway", "Tunge": "Ashworth", "Yasmeen": "Farrow",
    # Surnames that appear only upper-cased in the narrative.
    "Ladbrooke": "Reilly", "Pavely": "Doyle",
}

# The blanked CIRCUMSTANCES narrative -> a fully-named version that preserves
# every fact (timeline, vehicle, knife, bottle, manager's office, three
# suspects by clothing, fingerprint/blood on the door).
NAMED_NARRATIVE = (
    "THE PALMS HOTEL has a car park outside the front of the building. The "
    "building has a main entrance, with the front door leading onto an air-lock "
    "area, which then leads onto the foyer where the reception desk is. Behind "
    "the desk, on the left-hand side, is a door that leads into the manager's "
    "office, a room with a desk and computers inside. There is a seating area in "
    "the foyer and a restaurant area next to it with a bar and tables. The front "
    "entrance was cordoned off, as well as the manager's office.\n\n"
    "On Thursday 12th June 2025, at just before 01:00 hours, Declan Ferris "
    "arrived at THE PALMS HOTEL in a white Ford Transit van, VRM GK19 ZRT, to "
    "collect his wife, Niamh Ferris, from a wedding reception and drive her back "
    "to their home in LUTON. Declan Ferris got out of the vehicle, where an "
    "argument broke out in the car park in front of the entrance. This led to a "
    "group fight that moved towards the foyer. It is believed that Declan Ferris "
    "had a knife in his possession. Declan Ferris and his brother-in-law Sean "
    "Gallagher were punched and kicked by a group of men. Kyle Marsh struck "
    "Declan Ferris with a knife, causing stab wounds to his upper body. Dean "
    "Foster struck Sean Gallagher with a glass bottle.\n\n"
    "Declan Ferris ran to the manager's office and tried to close the door. Kyle "
    "Marsh kept the door open while holding a bottle. Declan Ferris took the "
    "bottle from Kyle Marsh and managed to close the door. The hotel manager, "
    "Raj Anand, entered the office; a bottle was thrown into the office and Kyle "
    "Marsh was removed from the doorway by Raj Anand. Based on CCTV it was "
    "difficult to identify exactly how Declan Ferris became injured, but it is "
    "suspected that there are three main suspects. SUSPECT 1, Kyle Marsh, was "
    "wearing a PINK TOP. SUSPECT 2, Dean Foster, was wearing a BURGUNDY TOP. "
    "SUSPECT 3, Tyler Quinn, was wearing a GREEN TOP. Kyle Marsh, Dean Foster "
    "and Tyler Quinn are known associates and arrived together. Declan Ferris "
    "did not know the suspects and they have not been identified by any witnesses."
)


def transform(text: str) -> str:
    # 1) Officer surname substitution (both Title-case and UPPER-case forms).
    for real, fake in OFFICERS.items():
        text = re.sub(rf"\b{real}\b", fake, text)
        text = re.sub(rf"\b{real.upper()}\b", fake.upper(), text)

    # 2) Replace the blanked CIRCUMSTANCES narrative block.
    text = re.sub(
        r"THE PALMS HOTEL has a car park.*?identified by any witnesses\.",
        NAMED_NARRATIVE,
        text,
        count=1,
        flags=re.DOTALL,
    )

    # 3) Name the redacted victim block.
    text = re.sub(r"VICTIM\s*\n\*{3,}",
                  "VICTIM\nNamed victims: Declan Ferris and Sean Gallagher.",
                  text)

    # 4) Witness who identified the fingerprint (the original attributes this to
    #    an unnamed witness right after the narrative).
    text = text.replace(
        "identified as the suspect's fingerprints by one of the witnesses",
        "identified as Kyle Marsh's fingerprints by the hotel receptionist Amber Hughes",
    )
    text = text.replace(
        "one of the suspects had touched the outside of the door",
        "Kyle Marsh had touched the outside of the door",
    )

    # 5) Drop any remaining redaction asterisk runs.
    text = re.sub(r"\*{3,}", "", text)
    return text


def main() -> None:
    text = transform(SRC.read_text())
    OUT_TXT.write_text(text)

    doc = fitz.open()
    import textwrap
    lines: list[str] = []
    for para in text.split("\n"):
        if not para.strip():
            lines.append("")
            continue
        lines += textwrap.wrap(para, width=95) or [""]
    PAGE_LINES = 52
    for i in range(0, len(lines), PAGE_LINES):
        page = doc.new_page()
        page.insert_textbox(fitz.Rect(50, 50, 560, 800),
                            "\n".join(lines[i:i + PAGE_LINES]),
                            fontsize=9, fontname="helv")
    doc.save(str(OUT_PDF))
    doc.close()

    rt = "\n".join(p.get_text() for p in fitz.open(str(OUT_PDF)))
    print(f"txt chars: {len(text)}  pdf chars: {len(rt)}  pages: {fitz.open(str(OUT_PDF)).page_count}")
    cast = ["Declan Ferris", "Sean Gallagher", "Niamh Ferris", "Kyle Marsh",
            "Dean Foster", "Tyler Quinn", "Raj Anand", "Amber Hughes",
            "Pearce", "Wells", "Reilly", "Pryce"]
    print("cast present in PDF:", {n: (n in rt) for n in cast})
    # Confirm no original surnames leaked through.
    leaked = [r for r in OFFICERS if re.search(rf"\b{r}\b", rt)]
    print("leaked original surnames:", leaked or "none")


if __name__ == "__main__":
    main()

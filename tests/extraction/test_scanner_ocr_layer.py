"""
A scanned notice whose PDF carries the scanner's own invisible OCR layer: DFS Vacancy Circular No. 07/08/2026-DRT
(tests/fixtures/llm/dfs/Image_962.pdf, the stored copy from the live drafting run of 06-10-2026; meta.json names its
URL and sha256), with the answer nvidia/nemotron-3-ultra-550b-a55b gave for it in that run (replayed).

That run accepted 0 of 9 offered fields. The text layer was read as a born-digital one, so only exact matching
applied, and the layer's OCR misreadings ("Government of lndia", "i. ls, or has been") failed quotes that are
printed on the page. The page is OCR text and now gets the OCR near match; a near match never accepts a different
number, so the model's closing date "31.12.2026", which is not what the page shows (the date is handwritten and
illegible in the layer), stays rejected.
"""
import json
from pathlib import Path

import pytest

from govcareeros.backend.documents.pdf_text import extract_pages
from govcareeros.backend.extraction.llm_extractor import extract
from govcareeros.backend.extraction.validator import find_quote
from govcareeros.backend.llm.provider import LLMResponse

FIXTURES = Path(__file__).parents[1] / "fixtures"
DFS = FIXTURES / "llm" / "dfs"
PDF = DFS / "Image_962.pdf"
RECORDING = json.loads((DFS / "Image_962.nemotron-3-ultra-550b-a55b.llm.json").read_text(encoding="utf-8"))

class ReplayProvider:
    name = "nvidia"
    chunk_chars = 120_000

    def __init__(self, recording):
        self.model, self.prompt_version = recording["model"], recording["prompt_version"]
        self._answers = [c["raw"] for c in recording["chunks"]]

    async def complete_json(self, system, user, schema):
        return LLMResponse(self._answers.pop(0), self.model, 0.0)

def test_the_scanners_invisible_text_layer_is_ocr_text():
    pages = extract_pages(PDF.read_bytes(), ocr=False)  # no Tesseract involved: the text is the PDF's own layer
    assert len(pages) == 8 and all(p.ocr for p in pages)
    assert "Government of lndia" in pages[0].text  # the layer's reading of "Government of India"
    # A born-digital notice keeps its text layer as exact text
    born_digital = extract_pages((FIXTURES / "ssc" / "Notice_of_adv_je_2026.pdf").read_bytes(), ocr=False)
    assert not any(p.ocr for p in born_digital)

@pytest.mark.asyncio
async def test_recorded_answer_on_the_scanned_circular():
    pages = extract_pages(PDF.read_bytes(), ocr=False)
    result = await extract(pages, ReplayProvider(RECORDING))
    [vacancy] = result.vacancies
    f = vacancy.fields
    accepted = {name for name, v in f.items() if v.accepted}
    assert accepted == {"organization", "essential_qualification", "experience", "mode_of_selection"}
    for name in accepted:
        assert f[name].match.startswith("OCR_FUZZY") and f[name].page in (1, 2)
    # What is stored is the page's own text, OCR reading included, never the model's spelling
    assert f["organization"].quote == "Government of lndia Ministry of Finance Department of Financial Services"
    assert f["organization"].page == 1
    assert "i. ls, or has been, a Judge of a High Court" in f["essential_qualification"].quote
    # The circular prints "Search-Cum-Section Committee"; the model wrote "Selection": the printed words are stored
    assert f["mode_of_selection"].quote.startswith("The Search-Cum-Section Committee") and f["mode_of_selection"].page == 2

    # The closing date on page 2 is handwritten; the model's "31.12.2026" is from para 2 on page 1 ("vacancy (s)
    # arising till 31.12.2026"), not the closing date
    assert not f["last_date"].accepted and f["last_date"].reason == "quote not found in document"
    assert not f["how_to_apply"].accepted and f["how_to_apply"].reason == "quote not found in document"
    # Unchanged: a post name that is not the quote's words, a count the quote does not state, a tenure age
    assert f["post_name"].reason == "post name is not whole words of its quote"
    assert f["vacancy_count"].reason == "2 does not appear in the quote"
    assert f["age_limit"].reason == "no upper age limit recognisable in the quote"

def test_a_near_match_with_a_different_number_is_not_a_match():
    pages = extract_pages(PDF.read_bytes(), ocr=False)
    quote = RECORDING["chunks"][0]["raw"]["vacancies"][0]["how_to_apply"]["quote"]
    assert quote.endswith("latest by 5:00 PM on or before 31.12.2026")
    assert find_quote(quote, pages, 2) is None
    # The same sentence up to the illegible date is on page 2
    page, text, match = find_quote(quote[:quote.index(" on or before")], pages, 2)
    assert page == 2 and text.startswith("Duly filled in original applications") and text.endswith("latest by 5:00 PM")

"""
Syllabus section of a UPSC examination notice -> papers, topics and subtopics with page provenance.

Deterministic rules (no LLM):
- The syllabus starts after the heading "STANDARD AND SYLLABI" / "B. STANDARD AND SYLLABUS ..." (Appendix-I)
  and ends at the next "APPENDIX" heading.
- A paper heading is a line whose text before any "(" is upper case, e.g. "GENERAL ECONOMICS – I (For IES only)";
  the parenthesised part is kept as the paper's note. "PART A:" lines label the part of the topics that follow.
- A topic starts at a numbered item at the start of a line ("1.", "(iv)"), numbered 1, 2, 3... within its paper;
  an out-of-sequence number is ordinary text. The title is the text before the first "—" or ":" and the rest is
  split into subtopics at sentence ends, semicolons and top-level commas (a comma list of single words such as
  "Price, income and substitution effects" stays one subtopic).
- A paper without numbered topics (General English, General Studies) becomes one topic named after the paper,
  with its sentences as subtopics.
- Every topic and subtopic keeps the page its text starts on; subtopic text is the notice's own wording.
"""
import re
from dataclasses import dataclass, field
from typing import List, Optional, Sequence, Tuple

from govcareeros.backend.documents.pdf_text import PageText

PARSER_VERSION = "upsc_syllabus/1"

# Headings only (upper case): the notice also mentions "Standard and Syllabi ... in Appendix-I" in running text
_START_RE = re.compile(r"^\s*(?:[A-Z]\.\s*)?STANDARD\s+AND\s+SYLLAB")
_END_RE = re.compile(r"^\s*APPENDIX")
_PAGE_HEADER_RE = re.compile(r"^\s*(Page\s+\d+\s+of\s+\d+|\d{1,3})\s*$", re.I)
_PART_RE = re.compile(r"^\s*PART\s*[-–]?\s*([A-Z])\s*:?\s*(.*)$")
_SECTION_RE = re.compile(r"^\s*SECTION\s*[-–]?\s*[IVX]+\s*$", re.I)
_NUM_ITEM_RE = re.compile(r"^\s*(\d{1,2})\.\s*(.*)$")
_ROMAN_ITEM_RE = re.compile(r"^\s*\((i{1,3}|iv|v|vi{1,3}|ix|x|xi{1,3})\)\s*(.*)$")
_ROMAN = {"i": 1, "ii": 2, "iii": 3, "iv": 4, "v": 5, "vi": 6, "vii": 7, "viii": 8, "ix": 9, "x": 10,
          "xi": 11, "xii": 12, "xiii": 13}
# Sentence end: a word of 2+ letters (or a closing bracket) then ". " and a capital; keeps "H.C.F." and "A.K. Sen" whole
_SENTENCE_RE = re.compile(r"(?<=[A-Za-z\)]{2})\.\s+(?=[A-Z(])")

@dataclass
class ParsedSubtopic:
    text: str
    page: int

@dataclass
class ParsedTopic:
    order: int  # position in the paper (numbering restarts per part in the notice)
    title: str
    page: int
    part: Optional[str] = None
    subtopics: List[ParsedSubtopic] = field(default_factory=list)

@dataclass
class ParsedPaper:
    name: str
    page: int
    note: Optional[str] = None
    topics: List[ParsedTopic] = field(default_factory=list)

@dataclass
class ParsedSyllabus:
    parser_version: str
    papers: List[ParsedPaper] = field(default_factory=list)
    warnings: List[str] = field(default_factory=list)

class _Text:
    """Text joined from PDF lines, remembering the page each character came from."""

    def __init__(self):
        self.text, self.marks = "", []  # marks: (offset, page)

    def add(self, line: str, page: int) -> None:
        line = re.sub(r"\s+", " ", line).strip()
        if not line:
            return
        if self.text.endswith("-") and line[:1].isalpha():
            sep = ""  # word split over two lines: "Rosenstein-" + "Roden"
        else:
            sep = " " if self.text else ""
        self.marks.append((len(self.text) + len(sep), page))
        self.text += sep + line

    def page_at(self, offset: int) -> int:
        page = self.marks[0][1]
        for start, p in self.marks:
            if start > offset:
                break
            page = p
        return page

def _is_paper_heading(line: str) -> bool:
    head = line.split("(", 1)[0]
    letters = [c for c in head if c.isalpha()]
    return len(letters) >= 4 and all(c.isupper() for c in letters) and re.search(r"[A-Z]{3}", head) is not None

def _paper_name(line: str) -> Tuple[str, Optional[str]]:
    head, _, rest = line.partition("(")
    name = re.sub(r"\s+", " ", head).strip(" :")
    name = re.sub(r"\s*[-–—]\s*([IVX]+)$", r"-\1", name)  # "GENERAL ECONOMICS – I" -> "GENERAL ECONOMICS-I"
    name = " ".join("-".join(p if re.fullmatch(r"[IVX]+", p) else p.capitalize() for p in w.split("-")) for w in name.split(" "))
    notes = re.findall(r"\(([^)]*)\)", "(" + rest) if rest else []
    note = "; ".join(n.strip() for n in notes if n.strip()) or None
    return name, note

def _split_subtopics(text: str, start: int, body: _Text) -> List[ParsedSubtopic]:
    """Subtopics of `text`, which begins at offset `start` of `body`."""
    pieces: List[Tuple[int, str]] = []  # (offset in text, piece)
    depth, begin = 0, 0
    bounds = {m.start() for m in _SENTENCE_RE.finditer(text)}
    for i, ch in enumerate(text):
        if ch in "([":
            depth += 1
        elif ch in ")]":
            depth = max(0, depth - 1)
        if i in bounds or (depth == 0 and ch in ",;"):
            pieces.append((begin, text[begin:i]))
            begin = i + 1
    pieces.append((begin, text[begin:]))

    strip = " .;,:"
    merged: List[Tuple[int, str]] = []  # (offset in text, subtopic)
    for off, raw in pieces:
        piece = raw.strip(strip)
        if not piece:
            continue
        lead = off + len(raw) - len(raw.lstrip(strip))
        if merged:
            prev_off, prev = merged[-1]
            few_letters = sum(c.isalpha() for c in piece) < 3  # "2, 3, 4, 5, 9 and 11", "G-20"
            word_list = off > 0 and text[off - 1] == "," and len(prev.split()) == 1 and piece[:1].islower()
            if few_letters or word_list:
                merged[-1] = (prev_off, text[prev_off:lead + len(piece)].strip(strip))
                continue
        merged.append((lead, piece))
    return [ParsedSubtopic(t, body.page_at(start + o)) for o, t in merged]

def _title_and_rest(text: str) -> Tuple[str, int]:
    """Topic title and the offset where its content starts."""
    cut = [i for i in (text.find("—"), text.find(":")) if 0 < i <= 90]
    if cut:
        i = min(cut)
        return text[:i].strip(" .-–"), i + 1
    first = _SENTENCE_RE.split(text, 1)[0]
    return first.strip(" ."), 0

def _lines(pages: Sequence[PageText]):
    started = False
    for page in pages:
        for line in page.text.splitlines():
            if not started:
                if _START_RE.search(line):
                    started = True
                continue
            if _END_RE.match(line):
                return
            if _PAGE_HEADER_RE.match(line) or not line.strip():
                continue
            yield page.page_number, line

def parse_syllabus(pages: Sequence[PageText]) -> ParsedSyllabus:
    result = ParsedSyllabus(PARSER_VERSION)
    paper: Optional[ParsedPaper] = None
    part: Optional[str] = None
    # Open text: the current topic's (topic_meta = (number, part, page)) or the paper's introduction (topic_meta None)
    block: Optional[_Text] = None
    topic_meta: Optional[Tuple[int, Optional[str], int]] = None
    intro: Optional[_Text] = None

    def close_block():
        nonlocal block, topic_meta, intro
        if block is not None and block.text:
            if topic_meta is None:
                intro = block
            else:
                number, topic_part, page = topic_meta
                title, content_at = _title_and_rest(block.text)
                topic = ParsedTopic(order=len(paper.topics) + 1, title=title, page=page, part=topic_part)
                topic.subtopics = (_split_subtopics(block.text[content_at:], content_at, block) if content_at
                                   else [ParsedSubtopic(title, page)])
                paper.topics.append(topic)
        block, topic_meta = None, None

    def close_paper():
        nonlocal intro
        close_block()
        if paper is None:
            return
        if not paper.topics and intro is not None:
            topic = ParsedTopic(order=1, title=paper.name, page=intro.page_at(0))
            topic.subtopics = _split_subtopics(intro.text, 0, intro)
            paper.topics.append(topic)
        if not paper.topics:
            result.warnings.append(f"paper {paper.name!r} (page {paper.page}): no syllabus text")
        result.papers.append(paper)
        intro = None

    for page_no, line in _lines(pages):
        if _SECTION_RE.match(line):
            continue
        if paper is not None and (part_m := _PART_RE.match(line)):
            close_block()
            label = part_m.group(2).strip(" :")
            part = f"Part {part_m.group(1)}" + (f": {label}" if label else "")
            continue
        if paper is not None and (item := _NUM_ITEM_RE.match(line) or _ROMAN_ITEM_RE.match(line)):
            number = int(item.group(1)) if item.group(1).isdigit() else _ROMAN[item.group(1)]
            # Numbering restarts in each part ("PART B" begins again at 1.)
            done = sum(1 for t in paper.topics if t.part == part) + (1 if topic_meta else 0)
            if number == done + 1:
                close_block()
                block, topic_meta = _Text(), (number, part, page_no)
                block.add(item.group(2), page_no)
                continue
        if _is_paper_heading(line):
            close_paper()
            name, note = _paper_name(line)
            paper, part = ParsedPaper(name=name, page=page_no, note=note), None
            continue
        if paper is None:
            continue  # the "standard" paragraph before the first paper
        if block is None:
            block = _Text()
        block.add(line, page_no)
    close_paper()
    return result

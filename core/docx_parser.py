"""Parse .docx paper into structured representation: chapters, sections, citations."""

import re
import hashlib
from dataclasses import dataclass, field
from docx import Document


CITATION_RE = re.compile(r"\[(\d+(?:\s*(?:[,;，；]|[-–—])\s*\d+)*)\]")


def citation_numbers(text: str) -> list[int]:
    numbers = []
    for part in re.split(r"[,;，；]", text):
        ends = re.split(r"[-–—]", part.strip())
        a, b = int(ends[0]), int(ends[-1])
        if a < 1 or b < a or b - a > 10000:
            raise ValueError('Invalid citation range')
        numbers.extend(range(a, b + 1))
    return numbers


def is_reference_heading(text: str) -> bool:
    return re.sub(r'\s+', '', text).casefold() in {
        '参考文献', '引用文献', '参考书目', 'references', 'bibliography', 'workscited'}


def is_post_reference_heading(text: str) -> bool:
    compact = re.sub(r'\s+', '', text).casefold()
    return bool(re.match(r'^(致谢|附录|声明|作者简介|研究成果|在读期间|攻读.*期间|已发表|发表论文|acknowledg[e]?ments?|appendix|appendices|declaration)', compact))


def text_hash(text: str) -> str:
    return hashlib.sha256(text.encode('utf-8')).hexdigest()


def plain_paragraph(para) -> bool:
    """Only ordinary runs of text; never flatten fields, links, drawings or revisions."""
    from docx.oxml.ns import qn
    allowed = {qn('w:pPr'), qn('w:r')}
    return (all(child.tag in allowed for child in para._p)
            and not para._p.xpath('.//w:sectPr')
            and all(child.tag in {qn('w:rPr'), qn('w:t')} for run in para.runs for child in run._r))


def document_snapshot(path: str | bytes) -> dict:
    from pathlib import Path
    from io import BytesIO
    raw = path if isinstance(path, bytes) else Path(path).read_bytes()
    doc = Document(BytesIO(raw))
    return {
        'schema_version': 1,
        'source_sha256': hashlib.sha256(raw).hexdigest(),
        'paragraphs': [{'index': i, 'text': p.text, 'text_sha256': text_hash(p.text),
                        'style': p.style.name if p.style else '',
                        'plain': plain_paragraph(p)} for i, p in enumerate(doc.paragraphs)],
        'warnings': ['Tables, fields, notes and tracked changes are not plain-text editing targets.'],
    }


@dataclass
class Section:
    heading: str
    text: str = ""
    citations: list[str] = field(default_factory=list)  # detected [1], [2,3] etc.
    start_char: int = 0  # character position in document for insertion targeting


@dataclass
class Chapter:
    title: str
    intro: str = ""
    sections: list[Section] = field(default_factory=list)


@dataclass
class Paper:
    title: str
    chapters: list[Chapter] = field(default_factory=list)
    references_section: str = ""  # raw text of the references/bibliography section


class DocxParser:
    """Extract structured paper content from a .docx file."""

    CHAPTER_PATTERN = re.compile(r"^第[一二三四五六七八九十\d]+章\s")
    CHAPTER_DIGIT_PATTERN = re.compile(r"^([1-9]\d*)\.(?!\d)\s*\S")
    HEADING_PATTERNS = [
        re.compile(r"^\d+\.\d+\.\d+"),
        re.compile(r"^\d+\.\d+"),
    ]
    FRONT_MATTER_SKIP = {"摘要", "关键词", "中图分类号", "Abstract", "Keywords"}
    REF_SECTION_PATTERNS = [
        re.compile(r"^参考文献"),
        re.compile(r"^References"),
        re.compile(r"^Bibliography"),
    ]
    CITATION_PATTERN = re.compile(r"\[(\d+(?:[,;\s]*\d+)*)\]")

    def __init__(self, docx_path: str):
        self.docx_path = docx_path
        self._char_pos = 0

    def parse(self) -> Paper:
        doc = Document(self.docx_path)

        # Detect title: first short-bold paragraph that isn't front-matter boilerplate
        title = ""
        for i, para in enumerate(doc.paragraphs[:15]):
            text = para.text.strip()
            if not text or len(text) < 5:
                continue
            is_bold = any(run.bold for run in para.runs if run.bold)
            has_front_matter = any(kw in text for kw in self.FRONT_MATTER_SKIP)
            if has_front_matter:
                continue
            # Title is typically bold and short (<80 chars, not a full sentence)
            if is_bold and len(text) < 80:
                title = text
                break
        if not title:
            # Fallback: first non-empty non-front-matter paragraph
            for para in doc.paragraphs[:15]:
                text = para.text.strip()
                if text and not any(kw in text for kw in self.FRONT_MATTER_SKIP):
                    title = text
                    break

        chapters = []
        current_chapter = None
        current_section = None
        buffer_texts = []
        in_references = False
        references_texts = []
        after_references = False

        for para in doc.paragraphs:
            text = para.text.strip()
            if not text:
                continue
            style = para.style.name if para.style else ""

            # Detect references section boundary
            if is_reference_heading(text):
                in_references = True
                if current_section and buffer_texts:
                    current_section.text = "\n".join(buffer_texts)
                    buffer_texts = []
                if current_chapter:
                    chapters.append(current_chapter)
                    current_chapter = None
                current_section = None
                references_texts.append(text)
                continue

            if in_references:
                if is_post_reference_heading(text) or style.startswith('Heading'):
                    in_references = False
                    after_references = True
                    continue
                references_texts.append(text)
                continue

            if after_references:
                continue

            # Detect chapter headers: "第X章", "N.xxx" digit pattern, or "Heading 1" style
            is_chapter = (
                self.CHAPTER_PATTERN.match(text)
                or style == "Heading 1"
                or (self.CHAPTER_DIGIT_PATTERN.match(text) and len(text) < 40)
            )
            if is_chapter:
                if current_section and buffer_texts:
                    current_section.text = "\n".join(buffer_texts)
                    buffer_texts = []
                if current_chapter:
                    chapters.append(current_chapter)
                current_chapter = Chapter(title=text, sections=[])
                current_section = None
                continue

            # Detect section headers
            is_heading = any(p.match(text) for p in self.HEADING_PATTERNS) or style.startswith("Heading")
            if is_heading:
                if current_section and buffer_texts:
                    current_section.text = "\n".join(buffer_texts)
                    buffer_texts = []
                current_section = Section(heading=text, start_char=self._char_pos)
                if current_chapter is None:
                    current_chapter = Chapter(title='', sections=[])
                if current_chapter:
                    current_chapter.sections.append(current_section)
                continue

            if current_section is not None:
                cites = self._extract_citations(text)
                for c in cites:
                    if c not in current_section.citations:
                        current_section.citations.append(c)
                buffer_texts.append(text)
            elif current_chapter is not None:
                if not current_chapter.intro:
                    current_chapter.intro = text
                else:
                    current_chapter.intro += "\n" + text

        # Flush remaining
        if current_section and buffer_texts:
            current_section.text = "\n".join(buffer_texts)
        if current_chapter:
            chapters.append(current_chapter)

        return Paper(
            title=title,
            chapters=chapters,
            references_section="\n".join(references_texts),
        )

    @staticmethod
    def _extract_citations(text: str) -> list[str]:
        """Extract citation numbers from inline markers like [1], [2,3], [5-7]."""
        cites = []
        for match in CITATION_RE.finditer(text):
            for number in citation_numbers(match.group(1)):
                num = str(number)
                if num not in cites:
                    cites.append(num)
        return cites


def find_uncited_sections(paper: Paper) -> list[dict]:
    """Identify sections/paragraphs that lack citations and may need them.

    Returns list of {chapter, section_heading, text_snippet, existing_citations}.
    """
    candidates = []
    for ch in paper.chapters:
        # Chapter intro without citations
        if ch.intro and len(ch.intro) > 200:
            candidates.append({
                "chapter": ch.title,
                "section": "引言/概述",
                "text": ch.intro[:500],
                "existing_citations": [],
            })
        for sec in ch.sections:
            if len(sec.text) > 200 and not sec.citations:
                candidates.append({
                    "chapter": ch.title,
                    "section": sec.heading,
                    "text": sec.text[:500],
                    "existing_citations": sec.citations,
                })
    return candidates

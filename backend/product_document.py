"""저장된 허가 XML에서 텍스트와 표의 원래 순서·셀 병합을 보존한다."""

import re
from html import unescape
from html.parser import HTMLParser
from xml.etree import ElementTree


class UnsupportedDocument(ValueError):
    """구조를 안전하게 표현할 수 없는 문서다."""


class TableReader(HTMLParser):
    # 표의 원본 행과 셀만 수집하며 외부 리소스와 실행 코드는 받지 않는다.
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.rows = []
        self.row = None
        self.cell = None
        self.text_parts = []
        self.stack = []

    # 허용된 태그와 제한된 병합 속성만 처리한다.
    def handle_starttag(self, tag, attrs):
        if tag not in {"table", "tbody", "thead", "tfoot", "tr", "td", "th", "p", "div", "span", "b", "strong", "em", "i", "br"}:
            raise UnsupportedDocument()
        if tag == "br":
            if self.cell is not None:
                self.text_parts.append("\n")
            return
        self.stack.append(tag)
        if len(self.stack) > 40:
            raise UnsupportedDocument()
        if tag == "tr":
            if self.row is not None or self.cell is not None or len(self.rows) >= 200:
                raise UnsupportedDocument()
            self.row = []
        elif tag in {"td", "th"}:
            if self.row is None or self.cell is not None or len(self.row) >= 30:
                raise UnsupportedDocument()
            attributes = dict(attrs)
            spans = []
            for name in ("rowspan", "colspan"):
                value = attributes.get(name, "1")
                if not isinstance(value, str) or not re.fullmatch(r"[1-9]\d?", value) or int(value) > 20:
                    raise UnsupportedDocument()
                spans.append(int(value))
            self.cell = {"header": tag == "th", "rowspan": spans[0], "colspan": spans[1]}
            self.text_parts = []
        elif tag in {"p", "div"} and self.cell is not None and self.text_parts:
            self.text_parts.append("\n")

    # 닫는 순서가 맞지 않거나 빈 행이면 구조를 추측하지 않는다.
    def handle_endtag(self, tag):
        if not self.stack or self.stack.pop() != tag:
            raise UnsupportedDocument()
        if tag in {"td", "th"}:
            if self.cell is None or self.row is None:
                raise UnsupportedDocument()
            text = "".join(self.text_parts).replace("\xa0", " ").strip()
            self.cell["text"] = re.sub(r"[ \t]*\n[ \t]*", "\n", text)
            self.row.append(self.cell)
            self.cell = None
        elif tag == "tr":
            if self.row is None or self.cell is not None or not self.row:
                raise UnsupportedDocument()
            self.rows.append({"cells": self.row})
            self.row = None

    # HTML의 자체 닫힘 줄바꿈을 일반 줄바꿈과 동일하게 처리한다.
    def handle_startendtag(self, tag, attrs):
        self.handle_starttag(tag, attrs)
        if tag != "br":
            self.handle_endtag(tag)

    # 셀 이외의 의미 있는 텍스트는 누락하지 않고 구조화 실패로 처리한다.
    def handle_data(self, data):
        if self.cell is not None:
            self.text_parts.append(data)
        elif data.strip():
            raise UnsupportedDocument()

    # 주석이나 선언을 표 내용으로 실행하지 않는다.
    def handle_decl(self, decl):
        raise UnsupportedDocument()


# 병합 영역이 표 밖으로 나가거나 다른 셀과 겹치면 출력하지 않는다.
def valid_table(rows):
    occupied = set()
    width = None
    for row_index, row in enumerate(rows):
        column = 0
        for cell in row["cells"]:
            while (row_index, column) in occupied:
                column += 1
            if column + cell["colspan"] > 30 or row_index + cell["rowspan"] > len(rows):
                return False
            for y in range(row_index, row_index + cell["rowspan"]):
                for x in range(column, column + cell["colspan"]):
                    if (y, x) in occupied:
                        return False
                    occupied.add((y, x))
            column += cell["colspan"]
        while (row_index, column) in occupied:
            column += 1
        if width is None:
            width = column
        if column != width or any((row_index, x) not in occupied for x in range(width)):
            return False
    return bool(rows and width)


# 허가 XML의 텍스트와 표 블록을 순서대로 반환하고 위험·미지원 형식은 거부한다.
def structured_document(value, text_converter):
    if not isinstance(value, str) or len(value.encode("utf-8")) > 180_000:
        return None
    source = value.strip()
    if source.startswith("&lt;"):
        source = unescape(source)
    if not source.startswith("<") or re.search(r"<!\s*(DOCTYPE|ENTITY)|(?:<|&(?:amp;){0,2}lt;)\s*(?:script|iframe|object|embed|img|svg|math|sup|sub)\b", source, re.I):
        return None
    try:
        root = ElementTree.fromstring(source)
        if root.tag != "DOC":
            return None
        blocks = []

        # 문서 트리를 따라 원래의 제목·본문·표 순서를 유지한다.
        def visit(node, depth=0):
            if depth > 40 or len(blocks) > 1000:
                raise UnsupportedDocument()
            title = node.attrib.get("title", "").strip()
            if title and node.tag != "DOC":
                blocks.append({"type": "text", "text": unescape(title)})
            if node.tag == "PARAGRAPH" and node.attrib.get("tagName", "").lower() == "table":
                if list(node) or not node.text:
                    raise UnsupportedDocument()
                reader = TableReader()
                reader.feed(node.text)
                reader.close()
                if reader.stack or reader.cell is not None or reader.row is not None or not valid_table(reader.rows):
                    raise UnsupportedDocument()
                blocks.append({"type": "table", "rows": reader.rows})
            else:
                if node.text and node.text.strip():
                    text = text_converter(node.text)
                    if not text:
                        raise UnsupportedDocument()
                    blocks.append({"type": "text", "text": text})
                for child in node:
                    visit(child, depth + 1)
                    if child.tail and child.tail.strip():
                        text = text_converter(child.tail)
                        if not text:
                            raise UnsupportedDocument()
                        blocks.append({"type": "text", "text": text})

        visit(root)
        return blocks if any(block["type"] == "table" for block in blocks) else None
    except (ElementTree.ParseError, UnsupportedDocument, ValueError, RecursionError):
        return None

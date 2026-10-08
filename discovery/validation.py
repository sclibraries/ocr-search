"""Inspect bounded HTML without executing it, loading external entities or retaining text."""
from html.parser import HTMLParser
import re

VOID = set('area base br col embed hr img input link meta param source track wbr'.split())
BOX = re.compile(r'\bbbox\s+(-?\d+)\s+(-?\d+)\s+(-?\d+)\s+(-?\d+)')


def bbox(title):
    match = BOX.search(title or '')
    return list(map(int, match.groups())) if match else None


class HocrParser(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.stack, self.pages, self.words = [], [], []
        self.invalid = False
        self.current_word = None

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        classes = attrs.get('class', '').split()
        if tag not in VOID:
            self.stack.append(tag)
        if 'ocr_page' in classes:
            self.pages.append(bbox(attrs.get('title')))
        if set(classes) & {'ocrx_word', 'ocr_word'}:
            self.current_word = {'box': bbox(attrs.get('title')), 'text': '', 'depth': len(self.stack)}
            self.words.append(self.current_word)

    def handle_startendtag(self, tag, attrs):
        self.handle_starttag(tag, attrs)
        if tag not in VOID:
            self.handle_endtag(tag)

    def handle_endtag(self, tag):
        if tag in VOID:
            return
        if not self.stack or self.stack[-1] != tag:
            self.invalid = True
        else:
            if self.current_word and len(self.stack) == self.current_word['depth']:
                self.current_word = None
            self.stack.pop()

    def handle_data(self, data):
        if self.current_word:
            self.current_word['text'] += data


def inspect_hocr(data):
    if b'<!ENTITY' in data.upper():
        return {'status': 'unsupported_entity_declaration'}
    try:
        text = data.decode('utf-8-sig')
    except UnicodeDecodeError:
        return {'status': 'unsupported_encoding'}
    parser = HocrParser()
    parser.feed(text)
    parser.close()
    if not parser.pages:
        return {'status': 'not_hocr'}
    if parser.invalid or parser.stack:
        return {'status': 'malformed_html'}
    if len(parser.pages) != 1:
        return {'status': 'multiple_ocr_pages', 'page_count': len(parser.pages)}
    page = parser.pages[0]
    if not page or page[0:2] != [0, 0] or page[2] <= 0 or page[3] <= 0:
        return {'status': 'invalid_coordinates'}
    skipped_blank_words = 0
    for word in parser.words:
        box = word['box']
        # Legacy exports contain whitespace placeholders with no drawable area.
        if box and not word['text'].strip():
            inside_page = 0 <= box[0] <= box[2] <= page[2] and 0 <= box[1] <= box[3] <= page[3]
            zero_area = box[0] == box[2] or box[1] == box[3]
            if inside_page and zero_area:
                skipped_blank_words += 1
                continue
        if not box or not (0 <= box[0] < box[2] <= page[2] and 0 <= box[1] < box[3] <= page[3]):
            return {'status': 'invalid_coordinates'}
    count = sum(bool(word['text'].strip()) for word in parser.words)
    return {'status': 'valid_hocr' if count else 'empty_hocr', 'word_count': count,
            'ocr_width': page[2], 'ocr_height': page[3], 'skipped_blank_words': skipped_blank_words}

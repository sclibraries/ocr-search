"""Convert a single-page hOCR document to Solr OCR Highlighting MiniOCR."""
import re
import xml.etree.ElementTree as ET

XML_NAMESPACE = 'http://www.w3.org/XML/1998/namespace'
BBOX = re.compile(r'\bbbox\s+(\d+)\s+(\d+)\s+(\d+)\s+(\d+)')
PAGE_NUMBER = re.compile(r'\bppageno\s+([^;\s]+)')
ALTERNATIVE_MARKER = '\u21ff'


def _has_class(element, *names):
    return bool(set(element.get('class', '').split()) & set(names))


def _bounds(element, description):
    match = BBOX.search(element.get('title', ''))
    if not match:
        raise ValueError(f'{description} has no bbox coordinates')
    left, top, right, bottom = map(int, match.groups())
    if left > right or top > bottom:
        raise ValueError(f'{description} has invalid bbox coordinates')
    return left, top, right, bottom


def _word_text(word):
    alternatives = [
        element for element in word.iter()
        if element is not word and _has_class(element, 'alternatives')
    ]
    if alternatives:
        forms = []
        for alternative in alternatives[0]:
            text = ''.join(alternative.itertext()).strip()
            if text and text not in forms:
                forms.append(text)
        if forms:
            return ALTERNATIVE_MARKER.join(forms)
    return ''.join(word.itertext()).strip()


def _page_identifier(page):
    if page.get('id'):
        return page.get('id')
    match = PAGE_NUMBER.search(page.get('title', ''))
    return match.group(1) if match else 'page-1'


def hocr_to_miniocr(hocr):
    """Return compact MiniOCR markup for one well-formed hOCR page.

    The input must contain one ``ocr_page`` and at least one word with a valid
    bounding box. Coordinates are emitted as MiniOCR x/y/width/height values.
    """
    if not isinstance(hocr, str):
        raise TypeError('hOCR input must be text')
    if '<!ENTITY' in hocr.upper():
        raise ValueError('entity declarations are not supported')
    try:
        document = ET.fromstring(hocr)
    except ET.ParseError as error:
        raise ValueError('invalid hOCR XML') from error

    pages = [element for element in document.iter() if _has_class(element, 'ocr_page')]
    if len(pages) != 1:
        raise ValueError('expected exactly one hOCR page')
    page = pages[0]
    left, top, right, bottom = _bounds(page, 'hOCR page')
    if (left, top) != (0, 0) or right == 0 or bottom == 0:
        raise ValueError('hOCR page bbox must start at 0,0 and have positive dimensions')
    width, height = right, bottom

    words = [element for element in page.iter()
             if _has_class(element, 'ocrx_word', 'ocr_word')]
    if not words:
        raise ValueError('expected nonempty hOCR page')
    geometry = {}
    for word in words:
        x, y, word_right, word_bottom = _bounds(word, 'hOCR word')
        if not (0 <= x <= word_right <= width and 0 <= y <= word_bottom <= height):
            raise ValueError('hOCR word coordinates outside page bounds')
        text = _word_text(word)
        if not text:
            raise ValueError('hOCR word has no text')
        geometry[id(word)] = (x, y, word_right - x, word_bottom - y, text)

    line_words = []
    assigned = set()
    for line in page.iter():
        if not _has_class(line, 'ocr_line', 'ocrx_line'):
            continue
        current = [word for word in line.iter()
                   if id(word) in geometry and id(word) not in assigned]
        if current:
            line_words.append(current)
            assigned.update(id(word) for word in current)
    remaining = [word for word in words if id(word) not in assigned]
    if remaining:
        line_words.append(remaining)

    root = ET.Element('ocr')
    mini_page = ET.SubElement(root, 'p', {
        f'{{{XML_NAMESPACE}}}id': _page_identifier(page),
        'wh': f'{width} {height}',
    })
    block = ET.SubElement(mini_page, 'b')
    for line in line_words:
        mini_line = ET.SubElement(block, 'l')
        for index, word in enumerate(line):
            x, y, word_width, word_height, text = geometry[id(word)]
            mini_word = ET.SubElement(mini_line, 'w', {
                'x': f'{x} {y} {word_width} {word_height}',
            })
            mini_word.text = text
            if index < len(line) - 1:
                mini_word.tail = ' '
    return ET.tostring(root, encoding='unicode', short_empty_elements=True)

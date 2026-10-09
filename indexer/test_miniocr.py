import importlib.util
from pathlib import Path
import unittest
import xml.etree.ElementTree as ET

FIXTURES = Path(__file__).resolve().parent / 'fixtures' / 'hocr'

_MODULE_PATH = Path(__file__).with_name('miniocr.py')
if _MODULE_PATH.exists():
    _SPEC = importlib.util.spec_from_file_location('ocr_miniocr', _MODULE_PATH)
    _MODULE = importlib.util.module_from_spec(_SPEC)
    _SPEC.loader.exec_module(_MODULE)
    hocr_to_miniocr = _MODULE.hocr_to_miniocr
else:
    hocr_to_miniocr = None


class MiniOcrTests(unittest.TestCase):
    def require_converter(self):
        self.assertIsNotNone(hocr_to_miniocr, 'hOCR to MiniOCR converter is missing')

    def test_conversion_preserves_page_size_lines_words_and_boxes(self):
        self.require_converter()
        source = (FIXTURES / 'demo-a-02.hocr').read_text()
        converted = hocr_to_miniocr(source)
        root = ET.fromstring(converted)
        page = root.find('p')
        words = root.findall('.//w')

        self.assertEqual(root.tag, 'ocr')
        self.assertEqual(page.get('{http://www.w3.org/XML/1998/namespace}id'), 'page-a-2')
        self.assertEqual(page.get('wh'), '600 800')
        self.assertEqual(len(root.findall('.//l')), 1)
        self.assertEqual([word.text for word in words], [
            'A', 'silver', 'falcon', 'mascot', 'watches', 'over', 'the', 'library.'
        ])
        self.assertEqual(words[1].get('x'), '110 100 70 30')

    def test_conversion_is_smaller_for_all_synthetic_pilot_pages(self):
        self.require_converter()
        files = sorted(FIXTURES.glob('demo-*.hocr'))
        original_size = sum(path.stat().st_size for path in files)
        converted_size = sum(len(hocr_to_miniocr(path.read_text()).encode('utf-8')) for path in files)

        self.assertEqual(len(files), 4)
        self.assertLess(converted_size, original_size)

    def test_conversion_rejects_input_without_a_page_and_word_box(self):
        self.require_converter()
        for source in (
            '<html><body>no OCR page</body></html>',
            '<html><body><div class="ocr_page" title="bbox 0 0 10 10">'
            '<span class="ocrx_word">text</span></div></body></html>',
        ):
            with self.subTest(source=source), self.assertRaises(ValueError):
                hocr_to_miniocr(source)

    def test_conversion_escapes_xml_text_without_changing_word_text(self):
        self.require_converter()
        source = '''<html><body><div class="ocr_page" id="p1" title="bbox 0 0 20 10">
          <span class="ocr_line"><span class="ocrx_word" title="bbox 1 1 19 9">A &amp; B</span></span>
        </div></body></html>'''

        root = ET.fromstring(hocr_to_miniocr(source))

        self.assertEqual(root.find('.//w').text, 'A & B')


if __name__ == '__main__':
    unittest.main()

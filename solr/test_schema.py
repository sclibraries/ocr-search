from pathlib import Path
import unittest
import xml.etree.ElementTree as ET

SCHEMA = Path(__file__).resolve().parent / 'config' / 'conf' / 'schema.xml'
SOLR_CONFIG = Path(__file__).resolve().parent / 'config' / 'conf' / 'solrconfig.xml'


class SchemaV2Tests(unittest.TestCase):
    def setUp(self):
        self.root = ET.parse(SCHEMA).getroot()
        self.fields = {field.get('name'): field for field in self.root.findall('field')}

    def test_version_two_page_fields_are_declared(self):
        expected_types = {
            'series': 'string',
            'issue_date': 'string',
            'year': 'integer',
            'source_bucket': 'string',
            'source_key': 'string',
            'source_version_id': 'string',
            'source_etag': 'string',
            'schema_version': 'integer',
        }
        for name, field_type in expected_types.items():
            with self.subTest(field=name):
                self.assertIn(name, self.fields)
                self.assertEqual(self.fields[name].get('type'), field_type)

    def test_grouping_and_facet_fields_support_doc_values(self):
        for name in ('item_id', 'collection_id', 'series', 'issue_date', 'year'):
            with self.subTest(field=name):
                self.assertIn(name, self.fields)
                self.assertEqual(self.fields[name].get('docValues'), 'true')

    def test_page_metadata_remains_stored_and_pilot_fields_remain_required(self):
        for name in ('title', 'page_label', 'source_manifest_url', 'ocr'):
            with self.subTest(field=name):
                self.assertEqual(self.fields[name].get('stored'), 'true')
        for name in ('id', 'corpus_id', 'access', 'item_id', 'collection_id'):
            with self.subTest(field=name):
                self.assertEqual(self.fields[name].get('required'), 'true')
        self.assertIn('schema_version', self.fields)
        self.assertNotEqual(self.fields['schema_version'].get('required'), 'true')

    def test_select_handler_enables_facet_component(self):
        config = ET.parse(SOLR_CONFIG).getroot()
        components = config.find("./requestHandler[@name='/select']/arr[@name='components']")

        self.assertIsNotNone(components)
        self.assertIn('facet', [component.text for component in components.findall('str')])


if __name__ == '__main__':
    unittest.main()

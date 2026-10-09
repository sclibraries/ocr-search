import unittest

from verify_schema import validate_solr_url


class VerifierUrlTests(unittest.TestCase):
    def test_accepts_local_candidate_core_url(self):
        url = 'http://127.0.0.1:49152/solr/ocr_next'

        try:
            result = validate_solr_url(url)
        except ValueError:
            self.fail('validator rejected a local candidate core URL')

        self.assertEqual(result, url)

    def test_rejects_nonlocal_solr_urls(self):
        for url in (
            'https://solr.example.test/solr/ocr',
            'http://192.0.2.10:8983/solr/ocr',
            'http://127.0.0.1:8983/solr/ocr',
            'http://127.0.0.1:18983/solr/ocr',
            'http://127.0.0.1:8983/solr/admin/cores',
        ):
            with self.subTest(url=url), self.assertRaises(ValueError):
                validate_solr_url(url)


if __name__ == '__main__':
    unittest.main()

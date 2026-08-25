"""Unit tests for bin/ingest_docs.py (pure functions).

extract_pdf/extract_epub/main are thin subprocess/IO glue over pdftotext,
pandoc, and the filesystem; they are validated end-to-end by ingesting real
documents (the reader-decoding corpus: 12 documents, 1.3M words). The logic
worth pinning lives in the pure helpers below.

Run: python3 -m unittest discover tests
"""
import hashlib
import importlib.util
import sys
import tempfile
import unittest
from pathlib import Path

BIN = Path(__file__).resolve().parent.parent / 'bin' / 'ingest_docs.py'
spec = importlib.util.spec_from_file_location('ingest_docs', BIN)
ingest_docs = importlib.util.module_from_spec(spec)
sys.modules['ingest_docs'] = ingest_docs
spec.loader.exec_module(ingest_docs)


class TestSlugify(unittest.TestCase):
    def test_basic(self):
        self.assertEqual(ingest_docs.slugify('The Act of Reading'),
                         'the-act-of-reading')

    def test_punctuation_and_accents(self):
        self.assertEqual(ingest_docs.slugify('S/Z — Barthes: étude!'),
                         's-z-barthes-etude')

    def test_truncates_to_80(self):
        self.assertLessEqual(len(ingest_docs.slugify('x' * 200)), 80)

    def test_empty_falls_back(self):
        self.assertEqual(ingest_docs.slugify('!!!'), 'doc')


class TestParseTitleAuthor(unittest.TestCase):
    def test_trailing_author_parens(self):
        t, a = ingest_docs.parse_title_author(
            'The Act of Reading (Wolfgang Iser)')
        self.assertEqual(t, 'The Act of Reading')
        self.assertEqual(a, 'Wolfgang Iser')

    def test_series_parens_not_greedy(self):
        # the LAST parenthesized group is the author
        t, a = ingest_docs.parse_title_author(
            'Why We Read Fiction (Theory and Interpretation) (Lisa Zunshine)')
        self.assertEqual(a, 'Lisa Zunshine')
        self.assertIn('Theory and Interpretation', t)

    def test_no_author(self):
        t, a = ingest_docs.parse_title_author('Plain Title')
        self.assertEqual(t, 'Plain Title')
        self.assertIsNone(a)


class TestClean(unittest.TestCase):
    def test_rejoins_hyphenated_linebreaks(self):
        self.assertIn('comprehension',
                      ingest_docs.clean('compre-\nhension of text'))

    def test_preserves_real_hyphens(self):
        self.assertIn('well-known', ingest_docs.clean('a well-known fact'))

    def test_collapses_blank_runs(self):
        out = ingest_docs.clean('a\n\n\n\n\nb')
        self.assertEqual(out, 'a\n\nb\n')

    def test_strips_soft_hyphens(self):
        self.assertEqual(ingest_docs.clean('read­ing'), 'reading\n')


class TestStripPageFurniture(unittest.TestCase):
    def _pages(self, n, header='RUNNING HEADER'):
        return '\f'.join(
            f'{header}\nBody text of page {i} with content.\n{i}'
            for i in range(1, n + 1))

    def test_removes_recurring_header_and_page_numbers(self):
        out = ingest_docs.strip_page_furniture(self._pages(30))
        self.assertNotIn('RUNNING HEADER', out)
        self.assertNotIn('\n7\n', '\n' + out + '\n')
        self.assertIn('Body text of page 7', out)

    def test_keeps_rare_lines(self):
        text = self._pages(30) + '\fA unique closing line of prose.'
        out = ingest_docs.strip_page_furniture(text)
        self.assertIn('A unique closing line of prose.', out)

    def test_roman_numeral_pages_removed(self):
        out = ingest_docs.strip_page_furniture('Front matter.\nxiv\fMore.\nxv')
        self.assertNotIn('xiv', out)
        self.assertNotIn('xv', out)


class TestSha256(unittest.TestCase):
    def test_matches_hashlib(self):
        with tempfile.NamedTemporaryFile(delete=False) as f:
            f.write(b'forge')
            p = Path(f.name)
        try:
            self.assertEqual(ingest_docs.sha256(p),
                             hashlib.sha256(b'forge').hexdigest())
        finally:
            p.unlink()


if __name__ == '__main__':
    unittest.main()

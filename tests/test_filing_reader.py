"""Reader parsing and explanation-cache tests; no network or model inference."""
import unittest
from contextlib import nullcontext
from unittest.mock import Mock, patch

from filing_reader.prepare import prepare_html, read_labels
from filing_reader.server import Reader, Explanation


class ReaderTests(unittest.TestCase):
    def test_inline_fact_keeps_period_scale_source_and_excludes_hidden(self):
        content = '''<html><head></head><body>
        <ix:header><xbrli:context id="c"><xbrli:entity><xbrli:identifier>123</xbrli:identifier></xbrli:entity>
        <xbrli:period><xbrli:instant>2025-12-31</xbrli:instant></xbrli:period></xbrli:context>
        <xbrli:unit id="u"><xbrli:measure>iso4217:USD</xbrli:measure></xbrli:unit>
        <ix:hidden><ix:nonfraction name="us-gaap:Assets">99</ix:nonfraction></ix:hidden></ix:header>
        <p id="note">Assets include cash and other resources used in the business. This paragraph provides additional context for the reported amount.</p>
        <ix:nonfraction id="original" name="us-gaap:Assets" contextref="c" unitref="u" scale="6">350</ix:nonfraction>
        <script>alert(1)</script><a href="javascript:alert(1)" onclick="bad()">Bad</a></body></html>'''
        rendered, facts, passages = prepare_html(content, 'https://example.com/report.htm', {})
        self.assertEqual(len(facts), 1)
        fact = facts['reader-fact-0']
        self.assertEqual(fact['context']['instant'], '2025-12-31')
        self.assertEqual(fact['unit'], 'iso4217:USD')
        self.assertEqual(fact['scale'], '6')
        self.assertEqual(fact['source'], 'https://example.com/report.htm#original')
        self.assertEqual(passages[0]['source'], 'https://example.com/report.htm#note')
        self.assertNotIn('alert(1)', rendered)
        self.assertNotIn('onclick', rendered)
        self.assertIn('/static/filing.js', rendered)

    def test_label_arcs_resolve_definition_to_concept(self):
        xml = '''<link:linkbase xmlns:link="http://www.xbrl.org/2003/linkbase" xmlns:xlink="http://www.w3.org/1999/xlink">
        <link:labelLink><link:loc xlink:label="concept" xlink:href="custom.xsd#acme_Assets"/>
        <link:label xlink:label="meaning" xlink:role="http://www.xbrl.org/2003/role/documentation" xml:lang="en">Resources controlled by the company.</link:label>
        <link:labelArc xlink:from="concept" xlink:to="meaning"/></link:labelLink></link:linkbase>'''
        labels = read_labels(xml, 'https://example.com/labels.xml')
        self.assertEqual(labels['acme:Assets'][0]['role'], 'documentation')
        self.assertEqual(labels['acme:Assets'][0]['text'], 'Resources controlled by the company.')

    def make_reader(self):
        reader = Reader.__new__(Reader)
        reader.engine = Mock()
        reader.engine.begin.side_effect = lambda: nullcontext(Mock())
        reader.model = Mock(model='replay-model')
        reader.cache_version = 'test'
        reader.filings = {
            'AAPL-2025': {
                'revision': 'revision',
                'facts': {
                    'reader-fact-0': {
                        'title': 'Assets', 'tag': 'us-gaap:Assets',
                        'display_value': '350', 'context': {},
                    },
                },
            },
        }
        reader.rank_passages = Mock(return_value=[
            {'id': 'reader-passage-0', 'text': 'Asset disclosure.'},
        ])
        return reader

    def test_saved_explanation_reused_without_second_model_call(self):
        reader = self.make_reader()
        reader.model.ask.return_value = Explanation(claims=[{'explanation': 'Asset context.', 'source_ids': ['reader-passage-0']}])
        cache = {}
        def save(connection, filing, fact, version, answer):
            cache['answer'] = answer
        with patch('filing_reader.server.cached_explanation', side_effect=lambda *args: cache.get('answer')), patch('filing_reader.server.save_explanation', side_effect=save):
            first = reader.explain('AAPL-2025', 'reader-fact-0')
            self.assertEqual(reader.explain('AAPL-2025', 'reader-fact-0'), first)
        reader.model.ask.assert_called_once()
        reader.rank_passages.assert_called_once_with('AAPL-2025', 'Assets')
        self.assertEqual(first['evidence'][0]['text'], 'Asset disclosure.')

    def test_invalid_model_citation_is_not_saved(self):
        reader = self.make_reader()
        reader.model.ask.return_value = Explanation(claims=[{'explanation': 'Unsupported.', 'source_ids': ['invented']}])
        with patch('filing_reader.server.cached_explanation', return_value=None), patch('filing_reader.server.save_explanation') as save:
            with self.assertRaisesRegex(ValueError, 'valid source'):
                reader.explain('AAPL-2025', 'reader-fact-0')
            save.assert_not_called()

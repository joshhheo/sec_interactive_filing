"""Reader parsing and explanation-cache tests; no network or model inference."""
import unittest
from contextlib import nullcontext
from unittest.mock import Mock, patch

from lxml import html

from filing_reader.prepare import prepare_html
from filing_reader.server import Reader, Explanation, concept_key


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
        rendered, facts, passages = prepare_html(html.fromstring(content), 'https://example.com/report.htm')
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

    def test_inline_relationship_links_only_the_named_fact(self):
        _, facts, passages = prepare_html(
            html.fromstring("""<html><body>
            <ix:relationship arcrole="http://www.xbrl.org/2003/arcrole/fact-footnote"
                fromRefs="revenue" toRefs="note"/>
            <ix:nonfraction id="revenue" name="us-gaap:Revenues">100</ix:nonfraction>
            <ix:nonfraction id="other" name="us-gaap:Revenues">95</ix:nonfraction>
            <ix:footnote id="note">Includes a one-time adjustment.</ix:footnote>
            </body></html>"""),
            'https://example.com/report.htm',
        )
        note = passages[-1]
        self.assertEqual(facts['reader-fact-0']['footnotes'], [note['id']])
        self.assertNotIn('footnotes', facts['reader-fact-1'])
        self.assertEqual(note['source'], 'https://example.com/report.htm#note')

    def test_quarter_and_year_to_date_use_different_baselines(self):
        def revenue(value, start, end):
            return {
                'tag': 'us-gaap:Revenues', 'unit': 'iso4217:USD',
                'display_value': str(value), 'format': None, 'scale': '0', 'sign': '',
                'context': {'startdate': start, 'enddate': end},
                'source': 'https://example.com/report.htm',
            }

        reader = Reader.__new__(Reader)
        reader.filings = {
            'q1': {'ticker': 'MSFT', 'year': 2025, 'fiscal_period': 'Q1', 'period_end': '2024-09-30'},
            'q2': {'ticker': 'MSFT', 'year': 2025, 'fiscal_period': 'Q2', 'period_end': '2024-12-31'},
        }
        reader.periods = {('MSFT', 2025, 'Q1'): 'q1'}
        quarterly = revenue(30, '2024-10-01', '2024-12-31')
        year_to_date = revenue(55, '2024-07-01', '2024-12-31')
        key = concept_key(quarterly)
        reader.concepts = {
            'q1': {key: [revenue(25, '2024-07-01', '2024-09-30')]},
            'q2': {key: [quarterly, year_to_date, revenue(50, '2023-07-01', '2023-12-31')]},
        }
        self.assertEqual(reader.comparison('q2', quarterly)['percent'], '+20.0%')
        self.assertEqual(reader.comparison('q2', year_to_date)['percent'], '+10.0%')

    def test_q1_uses_previous_fiscal_year_q3_for_eps(self):
        current = {
            'tag': 'us-gaap:EarningsPerShareDiluted', 'unit': 'iso4217:USDxbrli:shares',
            'display_value': '3.00', 'format': None, 'scale': '0', 'sign': '',
            'context': {'startdate': '2025-07-01', 'enddate': '2025-09-30'},
            'source': 'https://example.com/q1.htm',
        }
        prior = {
            **current, 'display_value': '2.00',
            'context': {'startdate': '2025-01-01', 'enddate': '2025-03-31'},
            'source': 'https://example.com/q3.htm',
        }
        reader = Reader.__new__(Reader)
        reader.filings = {
            'q1': {'ticker': 'MSFT', 'year': 2026, 'fiscal_period': 'Q1', 'period_end': '2025-09-30'},
            'q3': {'period_end': '2025-03-31'},
        }
        reader.periods = {('MSFT', 2025, 'Q3'): 'q3'}
        key = concept_key(current)
        reader.concepts = {'q1': {key: [current]}, 'q3': {key: [prior]}}
        result = reader.comparison('q1', current)
        self.assertEqual(result['amount_change'], '+$1.00 per share')
        self.assertEqual(result['percent'], '+50.0%')
        self.assertEqual(result['sources'], [prior['source']])
        self.assertEqual(result['basis'], 'FY2025 Q3')
        reader.periods.clear()
        self.assertIsNone(reader.comparison('q1', current))

    def test_amendment_flags_only_a_changed_matching_fact(self):
        original = {
            "tag": "us-gaap:Assets", "unit": "iso4217:USD",
            "context": {"instant": "2025-06-30", "identifier": "123"},
            "display_value": "120", "scale": "6", "sign": "", "format": None,
            "source": "https://example.com/amendment.htm#fact",
        }
        amendment = {
            "form": "10-K/A", "filed": "2025-09-01", "document_id": "0",
            "facts": {
                "same": original,
                "other": {**original, "tag": "custom:Assets", "display_value": "115"},
            },
        }
        reader = Reader.__new__(Reader)
        filing = {"amendments": [amendment]}
        self.assertEqual(reader.corrected_values(filing, original, 120_000_000), [])
        amendment["facts"]["same"] = {**original, "display_value": "115"}
        corrections = reader.corrected_values(filing, original, 120_000_000)
        self.assertEqual(corrections[0]["amount"], "$115,000,000")
        self.assertEqual(corrections[0]["source"], original["source"])

    def make_reader(self):
        reader = Reader.__new__(Reader)
        reader.engine = Mock()
        reader.engine.begin.side_effect = lambda: nullcontext(Mock())
        reader.model = Mock(model='replay-model')
        reader.cache_version = 'test'
        reader.filings = {
            'AAPL-2025': {
                'revision': 'revision', 'period_end': '2025-09-27',
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
        reader.rank_passages.assert_any_call('AAPL-2025', 'Assets', 'current_reports')
        self.assertEqual(first['evidence'][0]['text'], 'Asset disclosure.')

    def test_invalid_model_citation_is_not_saved(self):
        reader = self.make_reader()
        reader.model.ask.return_value = Explanation(claims=[{'explanation': 'Unsupported.', 'source_ids': ['invented']}])
        with patch('filing_reader.server.cached_explanation', return_value=None), patch('filing_reader.server.save_explanation') as save:
            with self.assertRaisesRegex(ValueError, 'valid source'):
                reader.explain('AAPL-2025', 'reader-fact-0')
            save.assert_not_called()

    def test_filing_question_preserves_reported_label_and_citation(self):
        reader = self.make_reader()
        reader.indexes = {}
        filing = reader.filings['AAPL-2025']
        filing['accession'] = 'accession'
        filing['facts']['reader-fact-0'].update({
            'id': 'reader-fact-0', 'title': 'Revenue From Contract With Customer',
            'tag': 'us-gaap:RevenueFromContractWithCustomerExcludingAssessedTax',
            'row_label': 'Total net sales', 'scale': '6', 'unit': 'iso4217:USD',
            'format': None, 'sign': '',
            'source': 'https://www.sec.gov/filing#sales',
            'context': {'startdate': '2024-09-29', 'enddate': '2025-09-27'},
        })

        def answer(instruction, packet, response_type):
            source = packet['passages'][0]
            self.assertIn('Filing label: Total net sales.', source['text'])
            self.assertIn('$350,000,000', source['text'])
            return response_type.model_validate({
                'claims': [{'explanation': 'Total net sales were $350 million.', 'source_ids': [source['id']]}],
            })

        reader.model.ask.side_effect = answer
        direct = reader.ask('AAPL-2025', 'What were total net sales this year?')
        self.assertIn('$350,000,000', direct['claims'][0]['explanation'])
        self.assertEqual(direct['claims'][0]['source_ids'], ['reader-fact-0'])
        reader.model.ask.assert_not_called()

        result = reader.ask('AAPL-2025', 'Where are total net sales disclosed?')
        self.assertEqual(result['accession'], 'accession')
        self.assertEqual(result['claims'][0]['source_ids'], ['reader-fact-0'])
        reader.model.ask.assert_called_once()

    def test_ambiguous_earnings_question_clarifies_without_model_or_evidence(self):
        reader = self.make_reader()
        reader.filings['AAPL-2025']['accession'] = 'accession'
        result = reader.ask('AAPL-2025', 'How much did Apple make this year?')
        self.assertIn('revenue', result['clarification'])
        self.assertEqual([choice['label'] for choice in result['choices']], ['Revenue', 'Net income'])
        self.assertTrue(all('this year?' in choice['question'] for choice in result['choices']))
        self.assertEqual(result['claims'], [])
        self.assertEqual(result['evidence'], [])
        reader.model.ask.assert_not_called()

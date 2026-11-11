"""Verify the retained local-model transport without running Ollama."""
import io
import json
import unittest
from unittest.mock import patch
from urllib.error import HTTPError

from filing_reader.model import Ollama, ModelUnavailable, ModelContextExceeded
from filing_reader.server import Explanation


class ModelTests(unittest.TestCase):
    def test_local_structured_request(self):
        response = io.BytesIO(json.dumps({'message': {'content': '{"claims": []}'}}).encode())
        with patch('filing_reader.model.urlopen', return_value=response) as send:
            answer = Ollama('test-model').ask('Explain', {}, Explanation)
        request = send.call_args.args[0]
        body = json.loads(request.data)
        self.assertEqual(request.full_url, 'http://127.0.0.1:11434/api/chat')
        self.assertEqual(body['model'], 'test-model')
        self.assertEqual(body['format'], Explanation.model_json_schema())
        self.assertEqual(answer.claims, [])

    def test_oversized_evidence_does_not_send_request(self):
        with patch('filing_reader.model.urlopen') as send:
            with self.assertRaises(ModelContextExceeded):
                Ollama('test-model').ask('Explain', {'text': 'x' * 10000}, Explanation)
            send.assert_not_called()

    def test_missing_model_reports_install_command(self):
        error = HTTPError('http://localhost', 404, 'Not found', {}, None)
        with patch('filing_reader.model.urlopen', side_effect=error):
            with self.assertRaisesRegex(ModelUnavailable, 'ollama pull test-model'):
                Ollama('test-model').ask('Explain', {}, Explanation)

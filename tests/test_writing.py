import contextlib
import io
import json
import os
from pathlib import Path
import secrets
import sys
import unittest
from unittest.mock import MagicMock, patch
from urllib.error import HTTPError

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))
from collectors.writing import WritingError, generate_publications


class WritingTests(unittest.TestCase):
    def setUp(self):
        self.article = {'title': 'Verified title', 'url': 'https://example.com/story', 'body': 'One fact'}

    def test_disabled_does_not_call_provider(self):
        with patch.dict(os.environ, {'AI_ENABLED': ''}), patch('collectors.writing.build_opener') as opener:
            self.assertIsNone(generate_publications(self.article))
            opener.assert_not_called()

    def test_enabled_without_key_stops_instead_of_faking_ai(self):
        with patch.dict(os.environ, {'AI_ENABLED': 'true', 'MARKETING_AI_KEY': ''}), self.assertRaises(WritingError):
            generate_publications(self.article)

    def test_structured_response_returns_three_formats(self):
        drafts = [{'channel': channel, 'subject': 'Subject', 'body': 'Original draft'} for channel in ('social', 'messenger', 'email')]
        response = {'output': [{'content': [{'type': 'output_text', 'text': json.dumps({'drafts': drafts})}]}]}
        opener = MagicMock()
        opener.open.return_value.__enter__.return_value.read.return_value = json.dumps(response).encode()
        with patch.dict(os.environ, {'AI_ENABLED': 'true', 'MARKETING_AI_KEY': secrets.token_urlsafe(32)}), patch('collectors.writing.build_opener', return_value=opener):
            self.assertEqual(generate_publications(self.article), drafts)
            request = opener.open.call_args.args[0]
            self.assertEqual(request.full_url, 'https://api.openai.com/v1/responses')
            data = json.loads(request.data)
            self.assertIn('недоверенные данные', data['instructions'])
            self.assertIn(self.article['url'], data['input'])

    def test_provider_error_does_not_echo_credentials(self):
        key = secrets.token_urlsafe(32)
        opener = MagicMock()
        opener.open.side_effect = HTTPError('https://api.openai.com/', 401, key, {}, None)
        with patch.dict(os.environ, {'AI_ENABLED': 'true', 'MARKETING_AI_KEY': key}), patch('collectors.writing.build_opener', return_value=opener):
            with self.assertRaises(WritingError) as error:
                generate_publications(self.article)
            self.assertNotIn(key, str(error.exception))

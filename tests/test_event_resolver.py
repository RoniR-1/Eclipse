import io
import json
import os
import unittest
from urllib.error import HTTPError
from unittest.mock import patch

from financial_twin import event_resolver, server


class FakeConnection:
    def __init__(self, rows):
        self.rows = rows
        self.writes = []

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False

    def execute(self, query, values=()):
        if query.startswith('SELECT'):
            self.query = query
            self.values = values
            return self
        self.writes.append(values)
        self.rows[:] = [{'normalized_key': values[1], 'display_name': values[2], 'keywords': [values[2]], 'params': values[4].obj, 'source_urls': values[5].obj, 'note': values[6], 'valid': True}]
        return self

    def fetchall(self):
        return self.rows

    def fetchone(self):
        return next((row for row in self.rows if row['normalized_key'] == self.values[0]), None)


class EventResolverTests(unittest.TestCase):
    def test_miss_is_persisted_and_next_lookup_hits_cache(self):
        conn = FakeConnection([])
        response = {'params': {'annual_tuition_chf': 1200, 'duration_months': -4}, 'source_urls': ['https://example.org'], 'note': 'Estimate'}
        with patch.dict(os.environ, {'DATABASE_URL': 'postgres://test', 'AI_API_KEY': 'key'}), patch.object(event_resolver.psycopg, 'connect', return_value=conn), patch.object(event_resolver, 'web_lookup', return_value=response) as lookup:
            first = event_resolver.resolve_event('education', 'Degree at Example')
            second = event_resolver.resolve_event('education', 'Degree at Example')
        self.assertEqual(first['source'], 'web_search')
        self.assertEqual(second['source'], 'database_cache')
        self.assertEqual(second['params'], {'annual_tuition_chf': 1200})
        self.assertEqual(second['note'], 'Estimate')
        self.assertEqual(lookup.call_count, 1)

    def test_expired_alias_refreshes_original_key(self):
        conn = FakeConnection([{'normalized_key': 'education:bachelor:example', 'display_name': 'Bachelor Example', 'keywords': ['bachelor at example'], 'params': {}, 'source_urls': [], 'valid': False}])
        response = {'params': {'duration_months': 36}, 'source_urls': ['https://example.org'], 'note': 'Estimate'}
        with patch.dict(os.environ, {'DATABASE_URL': 'postgres://test', 'AI_API_KEY': 'key'}), patch.object(event_resolver.psycopg, 'connect', return_value=conn), patch.object(event_resolver, 'web_lookup', return_value=response):
            event_resolver.resolve_event('education', 'bachelor at example')
        self.assertEqual(conn.writes[0][1], 'education:bachelor:example')

    def test_gemini_grounding_citations_replace_model_urls(self):
        body = {'candidates': [{'content': {'parts': [{'text': json.dumps({'params': {}, 'source_urls': ['https://invented.test'], 'note': ''})}]}, 'groundingMetadata': {'groundingChunks': [{'web': {'uri': 'https://source.test'}}]}}]}
        response = io.BytesIO(json.dumps(body).encode())
        with patch.dict(os.environ, {'AI_BASE_URL': ''}), patch.object(server, 'urlopen', return_value=response) as request:
            result = server.gemini_json('query', {}, 'key', web_search=True)
        self.assertEqual(result['source_urls'], ['https://source.test'])
        payload = json.loads(request.call_args.args[0].data)
        self.assertEqual(payload['tools'], [{'google_search': {}}])

    def test_configured_google_host_uses_native_grounding_and_model_fallback(self):
        body = {'candidates': [{'content': {'parts': [{'text': '{"params": {}, "note": ""}'}]}, 'groundingMetadata': {'groundingChunks': [{'web': {'uri': 'https://source.test'}}]}}]}
        response = io.BytesIO(json.dumps(body).encode())
        error = HTTPError('https://example.test', 429, 'limited', {}, None)
        with patch.dict(os.environ, {'AI_BASE_URL': 'https://generativelanguage.googleapis.com/v1beta/openai', 'AI_API_KEY_MODEL': 'first,second'}), patch.object(server, 'urlopen', side_effect=[error, response]) as request:
            result = server.gemini_json('query', {}, 'key', web_search=True)
        self.assertEqual(result['source_urls'], ['https://source.test'])
        self.assertIn('/models/second:generateContent', request.call_args.args[0].full_url)
        self.assertEqual(json.loads(request.call_args.args[0].data)['tools'], [{'google_search': {}}])

    def test_failed_lookup_returns_defaults_path(self):
        conn = FakeConnection([])
        with patch.dict(os.environ, {'DATABASE_URL': 'postgres://test', 'AI_API_KEY': 'key'}), patch.object(event_resolver.psycopg, 'connect', return_value=conn), patch.object(event_resolver, 'web_lookup', side_effect=ValueError('unavailable')):
            self.assertIsNone(event_resolver.resolve_event('education', 'Degree at Example'))

    def test_alias_and_canonical_key_match(self):
        row = {'normalized_key': 'education:master:unil', 'display_name': 'Master — University of Lausanne', 'keywords': ['master unil'], 'params': {'duration_months': 24}, 'source_urls': [], 'valid': True}
        conn = FakeConnection([row])
        with patch.dict(os.environ, {'DATABASE_URL': 'postgres://test'}), patch.object(event_resolver.psycopg, 'connect', return_value=conn):
            self.assertEqual(event_resolver.resolve_event('education', 'master’s degree at UNIL')['params'], row['params'])
            self.assertEqual(event_resolver.resolve_event('education', 'education:master:unil')['params'], row['params'])

    def test_malformed_lookup_returns_none(self):
        conn = FakeConnection([])
        with patch.dict(os.environ, {'DATABASE_URL': 'postgres://test', 'AI_API_KEY': 'key'}), patch.object(event_resolver.psycopg, 'connect', return_value=conn), patch.object(event_resolver, 'web_lookup', return_value={'params': [], 'source_urls': []}):
            self.assertIsNone(event_resolver.resolve_event('education', 'Example'))


if __name__ == '__main__':
    unittest.main()

import json
import math
import os
import re
import unicodedata

import psycopg
from psycopg.rows import dict_row
from psycopg.types.json import Jsonb

from .projection import EVENT_TYPES


def normalize(value):
    value = re.sub(r"(?i)(bachelor|master)[’']s\b", r'\1', value)
    value = unicodedata.normalize('NFKD', value.casefold())
    words = re.sub(r'[^a-z0-9]+', ' ', value.encode('ascii', 'ignore').decode()).split()
    return '-'.join(word for word in words if word not in {'degree', 'at', 'the', 'in', 'an', 'a', 'of'})


def list_presets():
    url = os.environ.get('DATABASE_URL')
    if not url:
        return []
    with psycopg.connect(url, row_factory=dict_row) as conn:
        return conn.execute('SELECT event_type, normalized_key, display_name, keywords, params, source_urls, source_type FROM life_event_knowledge WHERE expires_at IS NULL OR expires_at > now() ORDER BY display_name').fetchall()


def web_lookup(event_type, description, api_key):
    from . import server
    fields = EVENT_TYPES[event_type]['params']
    schema = {'type': 'object', 'properties': {'params': {'type': 'object', 'properties': {name: {'type': 'number'} for name in fields}}, 'source_urls': {'type': 'array', 'items': {'type': 'string'}}, 'note': {'type': 'string'}}, 'required': ['params', 'source_urls', 'note']}
    prompt = f'Search the web for current Swiss financial figures for {event_type}: {description}. Return JSON with params, source_urls and note. Parameter names and bounds: ' + json.dumps({k: [v['min'], v['max']] for k, v in fields.items()}) + '. Use monthly net CHF for income. Omit unsupported or unknown parameters. Include actual source URLs.'
    return server.gemini_json(prompt, schema, api_key, web_search=True)


def resolve_event(event_type, description, api_key=None):
    try:
        return _resolve_event(event_type, description, api_key)
    except (psycopg.Error, ValueError, TypeError, KeyError):
        return None


def _resolve_event(event_type, description, api_key=None):
    if event_type not in EVENT_TYPES or not isinstance(description, str) or not description.strip():
        return None
    url = os.environ.get('DATABASE_URL')
    if not url:
        return None
    normalized = normalize(description)
    key = description if description.startswith(event_type + ':') else event_type + ':' + normalized
    with psycopg.connect(url, row_factory=dict_row) as conn:
        row = conn.execute(
            'SELECT *, expires_at IS NULL OR expires_at > now() AS valid '
            'FROM life_event_knowledge WHERE normalized_key = %s AND event_type = %s',
            (key, event_type),
        ).fetchone()
        if not row:
            rows = conn.execute(
                'SELECT *, expires_at IS NULL OR expires_at > now() AS valid '
                'FROM life_event_knowledge WHERE event_type = %s', (event_type,),
            ).fetchall()
            for candidate in rows:
                aliases = [candidate['normalized_key'].split(':', 1)[-1],
                           candidate['display_name'], *(candidate['keywords'] or [])]
                if any(isinstance(alias, str) and normalize(alias) == normalized for alias in aliases):
                    row = candidate
                    break
        if row:
            if row['valid']:
                return {'params': row['params'], 'source': 'database_cache',
                        'source_urls': row['source_urls'],
                        'note': row.get('note') or row['display_name']}
            key = row['normalized_key']
    from . import server
    if not (api_key or server.configured_api_keys()):  # any key from .env, including AI_API_KEY_1, _2, ...
        return None
    result = web_lookup(event_type, description, api_key or None)
    if not isinstance(result, dict) or not isinstance(result.get('params'), dict):
        return None
    if not isinstance(result.get('source_urls'), list):
        return None
    urls = [u for u in result['source_urls']
            if isinstance(u, str) and re.match(r'^https?://', u)]
    if not urls:
        return None
    params = {}
    for name, value in result['params'].items():
        field = EVENT_TYPES[event_type]['params'].get(name)
        if not field or isinstance(value, bool) or not isinstance(value, (int, float)):
            continue
        if not field['min'] <= value <= field['max']:
            continue
        if not math.isfinite(value) or (field['integer'] and value != int(value)):
            continue
        params[name] = value
    if not params:
        return None
    try:
        ttl = max(1, int(os.environ.get('LIFE_EVENT_CACHE_TTL_DAYS', '180')))
    except ValueError:
        ttl = 180
    note = result.get('note') if isinstance(result.get('note'), str) else ''
    with psycopg.connect(url, row_factory=dict_row) as conn:
        conn.execute(
            """INSERT INTO life_event_knowledge
            (event_type, normalized_key, display_name, keywords, params, source_urls,
             source_type, note, expires_at)
            VALUES (%s, %s, %s, %s, %s, %s, 'ai_web_search', %s, now() + %s * interval '1 day')
            ON CONFLICT (normalized_key) DO UPDATE SET params = EXCLUDED.params,
            source_urls = EXCLUDED.source_urls, source_type = EXCLUDED.source_type,
            note = EXCLUDED.note, updated_at = now(), expires_at = EXCLUDED.expires_at""",
            (event_type, key, description, Jsonb([description]), Jsonb(params), Jsonb(urls), note, ttl),
        )
    return {'params': params, 'source': 'web_search', 'source_urls': urls, 'note': note}

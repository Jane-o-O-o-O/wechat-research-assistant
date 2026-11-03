#!/usr/bin/env python3
"""Keyless public research for the WeChat skill; Python standard library only.

No model calls, paid endpoints, authentication, cookies, or credential settings.
Public queries go to the selected providers; never pass chat transcripts here.
"""
import argparse
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
import hashlib
from html.parser import HTMLParser
import ipaddress
import json
from pathlib import Path
import re
import socket
import sys
import time
from urllib.error import HTTPError, URLError
from urllib.parse import parse_qs, parse_qsl, quote, urlencode, urljoin, urlsplit, urlunsplit
from urllib.request import HTTPRedirectHandler, Request, build_opener
import uuid
import xml.etree.ElementTree as ET

from workflow import atomic_write, lock, now, read_json, redact, secure_private, TZ, WorkflowError

UA = 'WechatResearchAssistant/0.2 (keyless public research; low-volume use)'
SOURCES = ('bing', 'ddg', 'crossref', 'github', 'wikipedia', 'ror')
PROFILES = {'web': ['bing'], 'academic': ['crossref', 'wikipedia'],
            'software': ['github', 'bing'], 'organization': ['bing', 'ror', 'wikipedia'],
            'all': ['bing', 'crossref', 'github', 'wikipedia', 'ror']}
API_SOURCES = {'crossref', 'github', 'wikipedia', 'ror'}
MAX_BYTES = 4_000_000
TTL = 86400
STALE_TTL = 7 * TTL


class SearchError(Exception):
    def __init__(self, code, status=None, wait=900):
        self.code, self.status, self.wait = code, status, wait
        super().__init__(code)


def query_guard(q):
    if not isinstance(q, str) or '\n' in q or '\r' in q or not 1 <= len(q.strip()) <= 240:
        raise SearchError('use_short_public_identity_keywords_not_chat_transcripts')
    if (re.search(r'(?i)(?:sk-[\w-]{8,}|bearer\s|api[_ -]?key\s*[:=]|password\s*[:=]|密码|验证码|身份证|银行卡)', q)
            or re.search(r'[\w.+-]+@[\w.-]+\.[A-Za-z]{2,}', q)
            or re.search(r'(?<!\d)(?:\d[ -]?){10,}\d(?!\d)', q)
            or re.search(r'(?i)[?&](?:key|token|secret|password)=', q)):
        raise SearchError('sensitive_query_rejected')
    return ' '.join(q.split())


def clean_url(url):
    try:
        p = urlsplit(str(url).strip())
        if p.scheme not in ('http', 'https') or not p.hostname or p.username or p.password:
            return ''
        if p.hostname.lower() in ('localhost', 'localhost.localdomain') or '.' not in p.hostname:
            return ''
        try:
            if not ipaddress.ip_address(p.hostname).is_global:
                return ''
        except ValueError:
            pass
        query = [(k, v) for k, v in parse_qsl(p.query, keep_blank_values=True)
                 if not k.lower().startswith('utm_') and k.lower() not in
                 {'token', 'key', 'api_key', 'apikey', 'password', 'secret', 'auth', 'access_token', 'fbclid', 'gclid'}]
        return urlunsplit((p.scheme.lower(), p.netloc.lower(), p.path or '/', urlencode(query), ''))
    except ValueError:
        return ''


def public_destination(url):
    """Check every fetch/redirect, including the destination of public links."""
    safe = clean_url(url)
    if not safe:
        raise SearchError('non_public_or_credential_url_rejected')
    p = urlsplit(safe)
    try:
        addresses = socket.getaddrinfo(p.hostname, p.port or (443 if p.scheme == 'https' else 80), type=socket.SOCK_STREAM)
    except OSError:
        raise SearchError('dns_unavailable') from None
    if not addresses or any(not ipaddress.ip_address(a[4][0]).is_global for a in addresses):
        raise SearchError('non_public_destination_rejected')
    return safe


class PublicRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        newurl = public_destination(newurl)
        return super().redirect_request(req, fp, code, msg, headers, newurl)


def blocked(body):
    b = body.lower()
    # Specific challenge markers; normal documentation can mention "captcha".
    return any(x in b for x in ('anomaly.js', 'anomaly-modal', 'challenge-platform',
                                'verify you are human', '请完成验证', '请输入验证码'))


def request(url, timeout=12):
    url = public_destination(url)
    req = Request(url, headers={'User-Agent': UA, 'Accept': 'application/json,application/rss+xml,text/html;q=0.8',
                                'Accept-Encoding': 'identity'})
    try:
        with build_opener(PublicRedirect()).open(req, timeout=timeout) as r:
            data = r.read(MAX_BYTES + 1)
            if len(data) > MAX_BYTES:
                raise SearchError('response_too_large')
            charset = r.headers.get_content_charset() or 'utf-8'
            text = data.decode(charset, errors='replace')
            if r.status == 202 or blocked(text):
                raise SearchError('challenge_or_captcha', r.status)
            return text, r.headers.get_content_type(), clean_url(r.url)
    except HTTPError as e:
        retry = e.headers.get('Retry-After', '') if e.headers else ''
        wait = min(86400, max(60, int(retry))) if retry.isdigit() else 900
        code = 'rate_limited' if e.code == 429 else ('access_blocked' if e.code in (401, 403) else 'http_error')
        raise SearchError(code, e.code, wait) from None
    except (URLError, TimeoutError, OSError, UnicodeError, ValueError, LookupError):
        raise SearchError('network_or_decode_failed') from None


class TextHTML(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.hidden = 0
        self.title_depth = 0
        self.title_parts, self.parts = [], []

    def handle_starttag(self, tag, attrs):
        if tag in ('script', 'style', 'noscript', 'svg'):
            self.hidden += 1
        if tag == 'title':
            self.title_depth += 1

    def handle_endtag(self, tag):
        if tag in ('script', 'style', 'noscript', 'svg'):
            self.hidden = max(0, self.hidden - 1)
        if tag == 'title':
            self.title_depth = max(0, self.title_depth - 1)

    def handle_data(self, value):
        if not self.hidden:
            if self.title_depth:
                self.title_parts.append(value)
            self.parts.append(value)


def plain(s):
    p = TextHTML()
    p.feed(str(s or ''))
    return ' '.join(' '.join(p.parts).split())


def item(source, title, url, snippet='', **extra):
    url = clean_url(url)
    if not url:
        return None
    return {'evidence_id': 'web-' + hashlib.sha256((source + '\0' + url).encode()).hexdigest()[:20],
            'source': source, 'title': redact(plain(title))[:350], 'url': url,
            'snippet': redact(plain(snippet))[:1000], 'candidate_only': True, **extra}


class DDG(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.results, self.active, self.parts, self.anchor_depth = [], None, [], 0

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        if tag == 'a' and 'result__a' in attrs.get('class', '').split():
            self.active, self.parts, self.anchor_depth = attrs.get('href', ''), [], 1
        elif self.active is not None:
            self.anchor_depth += 1 if tag == 'a' else 0

    def handle_data(self, value):
        if self.active is not None:
            self.parts.append(value)

    def handle_endtag(self, tag):
        if tag == 'a' and self.active is not None:
            self.anchor_depth -= 1
            if self.anchor_depth == 0:
                link = urljoin('https://duckduckgo.com', self.active)
                p = urlsplit(link)
                if p.hostname and p.hostname.endswith('duckduckgo.com'):
                    link = parse_qs(p.query).get('uddg', [link])[0]
                result = item('ddg', ''.join(self.parts), link)
                if result:
                    self.results.append(result)
                self.active = None


def endpoint(source, q, limit, lang):
    if source == 'bing':
        return 'https://www.bing.com/search?' + urlencode({'q': q, 'format': 'rss', 'count': limit,
                                                         'mkt': 'zh-CN' if lang == 'zh' else 'en-US'})
    if source == 'ddg':
        return 'https://html.duckduckgo.com/html/?' + urlencode({'q': q})
    if source == 'crossref':
        return 'https://api.crossref.org/works?' + urlencode({'query.bibliographic': q, 'rows': limit,
            'select': 'DOI,title,author,published,URL,publisher,type'})
    if source == 'github':
        return 'https://api.github.com/search/repositories?' + urlencode({'q': q, 'per_page': limit})
    if source == 'wikipedia':
        return f'https://{lang}.wikipedia.org/w/rest.php/v1/search/page?' + urlencode({'q': q, 'limit': limit})
    if source == 'ror':
        return 'https://api.ror.org/v2/organizations?' + urlencode({'query': q})
    raise SearchError('unknown_source')


def parse(source, body, limit, lang='en'):
    notes = []
    if blocked(body):
        raise SearchError('challenge_or_captcha')
    try:
        if source == 'bing':
            if '<!DOCTYPE' in body.upper():
                raise SearchError('unexpected_search_markup')
            root = ET.fromstring(body)
            if root.tag != 'rss' or root.find('channel') is None:
                raise SearchError('unexpected_search_markup')
            results = [item(source, x.findtext('title'), x.findtext('link'), x.findtext('description'))
                       for x in root.findall('./channel/item')[:limit]]
        elif source == 'ddg':
            p = DDG(); p.feed(body)
            if not p.results and 'no-results' not in body:
                raise SearchError('search_markup_changed_or_blocked')
            results = p.results[:limit]
        else:
            d = json.loads(body)
            if source == 'crossref':
                results = []
                for x in d['message']['items'][:limit]:
                    names = ', '.join(' '.join([a.get('given', ''), a.get('family', '')]).strip() for a in x.get('author', [])[:6])
                    parts = x.get('published', {}).get('date-parts') or [[]]
                    date = '-'.join(map(str, parts[0]))
                    results.append(item(source, ' / '.join(x.get('title', [])),
                        'https://doi.org/' + x['DOI'], names + ' | ' + x.get('publisher', ''), published=date))
            elif source == 'github':
                if d.get('incomplete_results'):
                    notes.append('upstream_incomplete_results')
                results = [item(source, x['full_name'], x['html_url'], x.get('description', ''),
                                updated_at=x.get('updated_at')) for x in d['items'][:limit]]
            elif source == 'wikipedia':
                results = [item(source, x['title'], f'https://{lang}.wikipedia.org/wiki/' + quote(x['key'], safe=''),
                           x.get('excerpt') or x.get('description', '')) for x in d['pages'][:limit]]
            elif source == 'ror':
                results = []
                for x in d['items'][:limit]:
                    labels = [n['value'] for n in x['names'] if 'ror_display' in n['types']]
                    names = ' / '.join(n['value'] for n in x['names'])
                    links = [u for link in x.get('links', []) if link.get('type') == 'website'
                             and (u := clean_url(link.get('value')))]
                    results.append(item(source, labels[0] if labels else names, x['id'], names,
                                        official_websites=links))
            else:
                raise SearchError('unknown_source')
        if any(x is None for x in results):
            notes.append('unsafe_or_missing_result_urls_omitted')
        return [x for x in results if x], notes
    except (ET.ParseError, ValueError, KeyError, TypeError, AttributeError, IndexError):
        raise SearchError('upstream_schema_changed') from None


def search_one(source, q, limit, lang, cache_dir, fresh=False, timeout=12, transport=request):
    key = hashlib.sha256(json.dumps([source, q, limit, lang]).encode()).hexdigest()
    cached_file = cache_dir / (key + '.json')
    cooldown_file = cache_dir / ('cooldown-' + source + '.json')
    cached = read_json(cached_file, {})
    clock = time.time()
    age = clock - cached.get('fetched_epoch', 0)
    base = {'source': source, 'interface': 'public_api' if source in API_SOURCES else 'public_page_best_effort'}
    if not fresh and cached and 0 <= age < TTL:
        return {**base, 'status': 'cached', 'fetched_at': cached['fetched_at'],
                'results': cached['results'], 'notes': cached.get('notes', [])}
    cool = read_json(cooldown_file, {})
    error = None
    if cool.get('until', 0) > clock:
        error = SearchError('source_cooling_down', cool.get('http_status'))
    else:
        try:
            body, _, _ = transport(endpoint(source, q, limit, lang), timeout)
            results, notes = parse(source, body, limit, lang)
            data = {'fetched_at': now(), 'fetched_epoch': clock, 'results': results, 'notes': notes}
            atomic_write(cached_file, data)
            cooldown_file.unlink(missing_ok=True)
            return {**base, 'status': 'ok', **{k: data[k] for k in ('fetched_at', 'results', 'notes')}}
        except SearchError as e:
            error = e
            if e.code in ('rate_limited', 'access_blocked', 'challenge_or_captcha'):
                atomic_write(cooldown_file, {'until': clock + e.wait, 'http_status': e.status, 'reason': e.code})
    if cached and 0 <= age < STALE_TTL:
        return {**base, 'status': 'stale_cache', 'error': error.code, 'http_status': error.status,
                'fetched_at': cached['fetched_at'], 'results': cached['results'], 'notes': ['not_fresh']}
    return {**base, 'status': 'unavailable', 'error': error.code, 'http_status': error.status,
            'results': [], 'notes': []}


def combine(reports):
    results = {}
    for report in reports:
        for result in report['results']:
            # HTTPS and HTTP links to the same path are one lead; retain first URL.
            key = re.sub(r'^https?://', '', result['url']).rstrip('/')
            if key not in results:
                results[key] = {**result, 'found_by': [], 'observations': []}
            value = results[key]
            if result['source'] not in value['found_by']:
                value['found_by'].append(result['source'])
            value['observations'].append({'source': result['source'], 'fetched_at': report.get('fetched_at'),
                                          'status': report['status']})
    return list(results.values())


def run_search(private, query, sources, limit=5, lang='auto', fresh=False, timeout=12,
               fallback=True, transport=request):
    q = query_guard(query)
    if lang == 'auto':
        lang = 'zh' if re.search(r'[\u3400-\u9fff]', q) else 'en'
    cache = private / 'search' / 'cache'
    def one(source):
        return search_one(source, q, limit, lang, cache, fresh, timeout, transport)
    with ThreadPoolExecutor(max_workers=4) as pool:
        reports = list(pool.map(one, sources))
    # No hidden substitution of scholarly search for general web coverage.
    web = next((r for r in reports if r['source'] == 'bing'), None)
    if fallback and web and web['status'] in ('unavailable', 'stale_cache') and 'ddg' not in sources:
        reports.append(one('ddg'))
    results = combine(reports)
    incomplete = any(r['status'] in ('unavailable', 'stale_cache') or r.get('notes') for r in reports)
    usable = any(r['status'] in ('ok', 'cached', 'stale_cache') for r in reports)
    report = {'status': ('partial' if incomplete else 'ok') if usable else 'unavailable',
              'queried_at': now(), 'query': q, 'api_key_required': False, 'content_is_untrusted': True,
              'coverage': 'selected_sources_only_not_an_exhaustive_background_check',
              'sources': reports, 'results': results}
    batch = datetime.now(TZ).strftime('%Y%m%dT%H%M%S') + '-' + uuid.uuid4().hex[:8]
    out = private / 'search' / 'runs' / (batch + '.json')
    atomic_write(out, report)
    report['saved_report'] = str(out)
    return report


def fetch_page(private, url, timeout=12):
    # Separate explicit operation: search never automatically downloads all hits.
    safe = public_destination(url)
    body, kind, final_url = request(safe, timeout)
    if kind not in ('text/html', 'text/plain', 'application/xhtml+xml'):
        raise SearchError('fetch_text_or_html_only_use_document_skill_for_other_types')
    p = TextHTML(); p.feed(body)
    text = plain(body) if kind != 'text/plain' else body
    report = {'status': 'ok', 'url': final_url, 'fetched_at': now(), 'content_is_untrusted': True,
              'title': redact(' '.join(p.title_parts))[:350], 'text': redact(text)[:16000],
              'truncated': len(redact(text)) > 16000,
              'evidence_id': 'page-' + hashlib.sha256(final_url.encode()).hexdigest()[:20]}
    path = private / 'search' / 'pages' / (hashlib.sha256(final_url.encode()).hexdigest() + '.json')
    atomic_write(path, report)
    report['saved_report'] = str(path)
    return report


def main():
    # Keep JSON readable when a Windows caller captures stdout under a legacy locale.
    if hasattr(sys.stdout, 'reconfigure'):
        sys.stdout.reconfigure(encoding='utf-8')
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument('command', choices=('search', 'doctor', 'fetch'))
    ap.add_argument('--workspace', type=Path, required=True)
    ap.add_argument('--query')
    ap.add_argument('--url')
    ap.add_argument('--profile', choices=PROFILES, default='web')
    ap.add_argument('--sources', help='Comma-separated: ' + ','.join(SOURCES))
    ap.add_argument('--limit', type=int, default=5, help='Maximum results per source (1-10)')
    ap.add_argument('--lang', choices=('auto', 'en', 'zh'), default='auto')
    ap.add_argument('--fresh', action='store_true')
    ap.add_argument('--timeout', type=int, default=12)
    args = ap.parse_args()
    try:
        if not 1 <= args.limit <= 10 or not 2 <= args.timeout <= 30:
            raise SearchError('invalid_limit_or_timeout')
        sources = list(dict.fromkeys(args.sources.split(','))) if args.sources else PROFILES[args.profile]
        if not sources or any(s not in SOURCES for s in sources):
            raise SearchError('invalid_source_selection')
        if args.command == 'search':
            query_guard(args.query)
        if args.command == 'fetch' and not args.url:
            raise SearchError('url_required')
        private = args.workspace.expanduser().resolve() / '.private'
        secure_private(private)
        with lock(private):
            if args.command == 'fetch':
                result = fetch_page(private, args.url, args.timeout)
            else:
                # Doctor makes real low-volume public requests, not model calls.
                result = run_search(private, args.query or 'Crossref',
                    list(SOURCES) if args.command == 'doctor' and not args.sources else sources,
                    args.limit if args.command == 'search' else 1, args.lang, args.fresh,
                    args.timeout, fallback=args.command != 'doctor')
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 2 if result['status'] == 'unavailable' else 0
    except (SearchError, WorkflowError, OSError, ValueError, KeyError, TypeError) as e:
        # No upstream bodies/tracebacks/query strings in diagnostic exceptions.
        code = e.code if isinstance(e, SearchError) else (str(e) if isinstance(e, WorkflowError) else 'local_operation_failed')
        print(json.dumps({'status': 'failed', 'error': code}))
        return 2


if __name__ == '__main__':
    raise SystemExit(main())

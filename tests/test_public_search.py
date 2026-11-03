"""No network or credentials; fixtures exercise source failure and evidence reuse."""
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

SCRIPTS = Path(__file__).resolve().parents[1] / 'skills/wechat-research-assistant/scripts'
sys.path.insert(0, str(SCRIPTS))
import public_search as s

RSS = '<rss><channel><item><title>Example Institute</title><link>https://example.org/about?utm_source=test</link><description>Public background</description></item></channel></rss>'
DDG = '<html><a class="result__a" href="//duckduckgo.com/l/?uddg=https%3A%2F%2Fexample.org%2Fabout">Example <b>Institute</b></a></html>'
GITHUB = json.dumps({'items': [{'full_name': 'fixture/project', 'html_url': 'https://github.com/fixture/project', 'description': 'Public software'}]})


class SearchTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.p = Path(self.temp.name) / '.private'
        s.secure_private(self.p)

    def tearDown(self):
        self.temp.cleanup()

    def test_rss_html_and_link_normalization(self):
        results, notes = s.parse('bing', RSS, 5)
        self.assertEqual(results[0]['url'], 'https://example.org/about')
        self.assertTrue(results[0]['candidate_only'])
        self.assertFalse(notes)
        ddg, _ = s.parse('ddg', DDG, 5)
        self.assertEqual(ddg[0]['url'], results[0]['url'])
        self.assertEqual(ddg[0]['title'], 'Example Institute')
        self.assertEqual(s.parse('bing', '<rss><channel/></rss>', 5)[0], [])

    def test_challenge_is_not_empty_results(self):
        with self.assertRaises(s.SearchError) as e:
            s.parse('ddg', '<form class="anomaly-modal">challenge</form>', 5)
        self.assertEqual(e.exception.code, 'challenge_or_captcha')
        with self.assertRaises(s.SearchError):
            s.parse('bing', '<html>login required</html>', 5)

    def test_bing_failure_falls_back_and_remains_partial(self):
        def network(url, timeout):
            if 'bing.com' in url:
                raise s.SearchError('network_or_decode_failed')
            self.assertIn('duckduckgo.com', url)
            return DDG, 'text/html', url
        report = s.run_search(self.p, 'Example Institute', ['bing'], transport=network)
        self.assertEqual(report['status'], 'partial')
        self.assertEqual([r['source'] for r in report['sources']], ['bing', 'ddg'])
        self.assertEqual(report['results'][0]['found_by'], ['ddg'])
        self.assertTrue(Path(report['saved_report']).exists())

    def test_partial_source_schema_error_does_not_hide_other_hits(self):
        def network(url, timeout):
            return (GITHUB if 'github.com' in url else '{}'), 'application/json', url
        report = s.run_search(self.p, 'Public software', ['github', 'ror'], transport=network)
        self.assertEqual(report['status'], 'partial')
        self.assertEqual(len(report['results']), 1)
        self.assertEqual(report['sources'][1]['error'], 'upstream_schema_changed')

    def test_cache_avoids_request_and_stale_preserves_original_date(self):
        cache = self.p / 'search/cache'
        first = s.search_one('bing', 'Public query', 5, 'en', cache,
                             transport=lambda u, t: (RSS, 'text/xml', u))
        def unavailable(u, t):
            raise s.SearchError('network_or_decode_failed')
        cached = s.search_one('bing', 'Public query', 5, 'en', cache, transport=unavailable)
        self.assertEqual(cached['status'], 'cached')
        stale = s.search_one('bing', 'Public query', 5, 'en', cache, fresh=True, transport=unavailable)
        self.assertEqual(stale['status'], 'stale_cache')
        self.assertEqual(stale['fetched_at'], first['fetched_at'])
        self.assertEqual(stale['results'], first['results'])

    def test_rate_limit_cooldown_applies_to_different_queries(self):
        calls = []
        def limited(u, t):
            calls.append(u)
            raise s.SearchError('rate_limited', 429, wait=1200)
        a = s.search_one('github', 'Query one', 5, 'en', self.p/'search/cache', transport=limited)
        b = s.search_one('github', 'Query two', 5, 'en', self.p/'search/cache', fresh=True, transport=limited)
        self.assertEqual(a['error'], 'rate_limited')
        self.assertEqual(b['error'], 'source_cooling_down')
        self.assertEqual(len(calls), 1)

    def test_queries_reject_secrets_and_transcripts_before_network(self):
        for q in ['密码: fake-secret', '13900001234', '6222 1234 5678 9012',
                  'one line\nsecond line', 'person@example.org']:
            with self.assertRaises(s.SearchError):
                s.run_search(self.p, q, ['bing'], transport=lambda u, t: self.fail('network called'))

    def test_private_urls_and_redirect_destinations_rejected(self):
        for url in ['http://127.0.0.1/', 'http://localhost/', 'file:///tmp/test', 'https://user:pass@example.org']:
            self.assertFalse(s.clean_url(url))
        addresses = [(2, 1, 6, '', ('10.0.0.1', 443))]
        with patch.object(s.socket, 'getaddrinfo', return_value=addresses):
            with self.assertRaises(s.SearchError):
                s.public_destination('https://example.org/')

    def test_dedup_retains_independent_sources_and_stale_status(self):
        a = s.item('bing', 'Title', 'http://example.org/page')
        b = s.item('ddg', 'Title', 'https://example.org/page')
        combined = s.combine([{'source': 'bing', 'status': 'stale_cache', 'fetched_at': 'old', 'results': [a]},
                              {'source': 'ddg', 'status': 'ok', 'fetched_at': 'new', 'results': [b]}])
        self.assertEqual(len(combined), 1)
        self.assertEqual(combined[0]['found_by'], ['bing', 'ddg'])
        self.assertEqual(combined[0]['observations'][0]['status'], 'stale_cache')

    def test_structured_sources_are_leads_not_identity_assertions(self):
        cr = json.dumps({'message': {'items': [{'DOI': '10.0000/example', 'title': ['A paper'], 'published': {'date-parts': []}}]}})
        r = json.dumps({'items': [{'id': 'https://ror.org/example', 'names': [{'value': 'Institute', 'types': ['ror_display']}],
                                  'links': [{'type': 'website', 'value': 'https://example.org/'}]}]})
        results, _ = s.parse('crossref', cr, 5)
        self.assertTrue(results[0]['candidate_only'])
        org, _ = s.parse('ror', r, 5)
        self.assertEqual(org[0]['official_websites'], ['https://example.org/'])


if __name__ == '__main__':
    unittest.main()

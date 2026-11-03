"""Synthetic fixtures only; no real WeChat records or model calls."""
import importlib.util
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from types import SimpleNamespace

SCRIPT = Path(__file__).resolve().parents[1] / 'skills/wechat-research-assistant/scripts/workflow.py'
spec = importlib.util.spec_from_file_location('workflow', SCRIPT)
w = importlib.util.module_from_spec(spec)
spec.loader.exec_module(w)


def row(n, **extra):
    # Intentionally larger than JS's exact-integer range.
    return dict({'_cursor': 'opaque-' + str(n), 'localId': str(n), 'shard': '0',
                 'serverId': str(9223372036854700000 + n), 'localType': '1',
                 'createTime': str(1793690000 + n), 'content': '请周五确认测试范围',
                 'sender': 'fixture-other', 'isSend': False}, **extra)


class FakeReader:
    def __init__(self, pages, account='fixture-self'):
        self.pages = pages
        self.identity = account
        self.calls = []

    def account(self):
        return self.identity

    def page(self, contact, size, cursor, since):
        self.calls.append((contact, cursor))
        value = self.pages[(contact, cursor)]
        if isinstance(value, Exception):
            raise value
        return value


class WorkflowTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.p = self.root / '.private'
        w.secure_private(self.p)
        self.c = {'self_wxid': 'fixture-self', 'conversations': [
            {'slug': 'alpha', 'id': 'fixture-a'}], 'page_size': 2, 'max_pages': 10, '_since': 1793600000}

    def tearDown(self):
        self.temp.cleanup()

    def run_capture(self, pages, state=None):
        s = state or w.bound_state(self.p, self.c)
        r = FakeReader(pages)
        result = w.capture(self.p, self.c, s, r)
        return s, result, r

    def test_pagination_durable_report_then_cursor_and_id_precision(self):
        # Two messages share a time/sort position, and a page repeats one row.
        a, b, c = row(1), row(2, createTime=row(1)['createTime']), row(3)
        s, result, r = self.run_capture({('fixture-a', None): [b, a],
            ('fixture-a', 'opaque-2'): [c, b], ('fixture-a', 'opaque-3'): []})
        self.assertEqual(result[0]['new_unique_messages'], 3)
        self.assertIsNone(s['conversations']['alpha']['cursor'])
        p = w.read_json(self.p / 'pending/alpha.json')
        archive = w.read_json(self.p / 'archives/alpha.json')
        self.assertIn('server:' + a['serverId'], archive['messages'])
        review = (self.p / p['review']).read_text()
        self.assertEqual(len(review.splitlines()), 3)
        evidence = json.loads(review.splitlines()[0])['evidence_id']
        report = self.root / '分析批次/alpha' / (p['batch'] + '.md')
        w.atomic_write(report, f"# alpha {p['batch']}\n\n来源 {evidence}：对方提出确认测试范围，截止日需结合上下文核实。\n", False)
        w.commit(self.root, self.p, self.c, 'alpha', p['batch'], str(report.relative_to(self.root)))
        self.assertEqual(w.read_json(self.p / 'state.json')['conversations']['alpha']['cursor'], 'opaque-3')
        self.assertFalse((self.p / 'pending/alpha.json').exists())
        s2, result2, _ = self.run_capture({('fixture-a', 'opaque-3'): []})
        self.assertEqual(result2[0]['status'], 'no_returned_messages')
        self.assertEqual(len(w.read_json(self.p / 'archives/alpha.json')['messages']), 3)

    def test_partial_failure_keeps_cursor_and_continues(self):
        self.c['conversations'].append({'slug': 'beta', 'id': 'fixture-b'})
        s, result, _ = self.run_capture({('fixture-a', None): [row(1)],
            ('fixture-a', 'opaque-1'): w.WorkflowError('reader_command_failed'),
            ('fixture-b', None): [row(4)], ('fixture-b', 'opaque-4'): []})
        self.assertEqual([x['status'] for x in result], ['failed', 'pending_analysis'])
        self.assertIsNone(s['conversations']['alpha']['cursor'])
        self.assertFalse((self.p / 'pending/alpha.json').exists())
        self.assertTrue((self.p / 'pending/beta.json').exists())

    def test_account_switch_prevents_read(self):
        r = FakeReader({}, 'different-account')
        with self.assertRaisesRegex(w.WorkflowError, 'account_mismatch'):
            w.capture(self.p, self.c, w.bound_state(self.p, self.c), r)
        self.assertEqual(r.calls, [])

    def test_looping_reader_fails_without_advancing(self):
        s, result, _ = self.run_capture({('fixture-a', None): [row(1)],
                                      ('fixture-a', 'opaque-1'): [row(1)]})
        self.assertEqual(result[0]['error'], 'pagination_not_advancing')
        self.assertIsNone(s['conversations']['alpha']['cursor'])

    def test_pending_batch_is_not_replaced(self):
        self.run_capture({('fixture-a', None): [row(1)], ('fixture-a', 'opaque-1'): []})
        before = (self.p / 'pending/alpha.json').read_bytes()
        _, result, r = self.run_capture({})
        self.assertEqual(result[0]['status'], 'pending_analysis')
        self.assertEqual(r.calls, [])
        self.assertEqual((self.p / 'pending/alpha.json').read_bytes(), before)

    def test_missing_report_cannot_commit(self):
        self.run_capture({('fixture-a', None): [row(1)], ('fixture-a', 'opaque-1'): []})
        p = w.read_json(self.p / 'pending/alpha.json')
        with self.assertRaises(w.WorkflowError):
            w.commit(self.root, self.p, self.c, 'alpha', p['batch'], 'absent.md')
        self.assertIsNone(w.read_json(self.p / 'state.json')['conversations']['alpha']['cursor'])

    def test_private_writer_cannot_change_permissions_outside_private(self):
        before = self.root.stat().st_mode
        with self.assertRaises(w.WorkflowError):
            w.atomic_write(self.root / 'unrelated/new.json', {'private': True})
        self.assertEqual(self.root.stat().st_mode, before)
        self.assertFalse((self.root / 'unrelated').exists())

    def test_redaction_and_xml_credentials_never_in_review(self):
        samples = ['卡号: 6222 1234 5678 9012', '身份证：110101199901010012',
                   '手机号：13900001234', '密码: abc~2!XY', 'Abc~2!XY',
                   'https://example.invalid/data?token=fixture-secret', 'API Key: fixture-secret']
        for s in samples:
            text = w.redact(s)
            self.assertNotIn(s.split(': ')[-1], text)
        r = row(1, localType=str((57 << 32) | 49), content='<msg><appmsg><title>确认收到</title><type>57</type><refermsg><content>密码: fake-secret</content></refermsg><aeskey>hidden-media-key</aeskey></appmsg></msg>')
        text = json.dumps(w.review_of(r, 'alpha', 'fixture-self'), ensure_ascii=False)
        self.assertNotIn('fake-secret', text)
        self.assertNotIn('hidden-media-key', text)
        self.assertNotIn('<appmsg>', text)
        self.assertIn('quote', text)

    def test_archive_then_interrupted_staging_does_not_lose_analysis(self):
        # Simulate a crash after archive replacement but before a pending manifest.
        w.atomic_write(self.p / 'archives/alpha.json', {'source_id': 'fixture-a',
                       'messages': {w.message_key(row(1)): row(1)}})
        _, result, _ = self.run_capture({('fixture-a', None): [row(1)], ('fixture-a', 'opaque-1'): []})
        self.assertEqual(result[0]['new_unique_messages'], 0)
        self.assertEqual(result[0]['analysis_messages'], 1)

    def test_identity_and_source_binding(self):
        w.atomic_write(self.p / 'state.json', {'version': 1, 'self_wxid': 'fixture-self',
            'conversations': {'alpha': {'id': 'different-contact', 'cursor': 'old'}}})
        with self.assertRaisesRegex(w.WorkflowError, 'source_binding'):
            w.bound_state(self.p, self.c)
        review = w.review_of(row(1, sender='fixture-third-person', isSend=True), 'alpha', 'fixture-self')
        self.assertNotEqual(review['attribution'], '本人')

    def test_reader_json_and_error_do_not_expose_upstream_body(self):
        reader = w.Reader('fixture-reader.exe')
        response = SimpleNamespace(returncode=0, stdout=json.dumps({'success': True, 'data': [row(1)]}))
        with patch.object(w.subprocess, 'run', return_value=response) as call:
            result = reader.page('fixture-a', 200, 'opaque-cursor', 0)
            self.assertEqual(result[0]['serverId'], row(1)['serverId'])
            self.assertEqual(call.call_args.args[0], ['fixture-reader.exe', '--json', 'chat', 'messages', 'fixture-a', '--limit', '200', '--after', 'opaque-cursor'])
        response.stdout = json.dumps({'success': False, 'error': {'message': 'private-body-should-not-leak'}})
        response.returncode = 4
        with patch.object(w.subprocess, 'run', return_value=response):
            with self.assertRaises(w.WorkflowError) as e:
                reader.page('fixture-a', 200, None, 0)
        self.assertEqual(str(e.exception), 'reader_command_failed')

    def test_page_limit_is_failure_not_empty_success(self):
        self.c['max_pages'] = 1
        s, result, _ = self.run_capture({('fixture-a', None): [row(1)]})
        self.assertEqual(result[0]['error'], 'page_limit_reached_incomplete')
        self.assertIsNone(s['conversations']['alpha']['cursor'])

    @unittest.skipIf(os.name == 'nt', 'POSIX modes only; Windows DACL requires live acceptance')
    def test_permissions_and_lock(self):
        self.run_capture({('fixture-a', None): [row(1)], ('fixture-a', 'opaque-1'): []})
        for p in self.p.rglob('*'):
            self.assertEqual(p.stat().st_mode & 0o777, 0o700 if p.is_dir() else 0o600)
        with w.lock(self.p):
            with self.assertRaises(w.WorkflowError):
                with w.lock(self.p):
                    pass

    @unittest.skipUnless(os.name == 'nt', 'Windows DACL only')
    def test_windows_private_acl_is_protected_and_limited(self):
        import subprocess
        code = r'''
$ErrorActionPreference = 'Stop'
Import-Module (Join-Path $PSHOME 'Modules\Microsoft.PowerShell.Security\Microsoft.PowerShell.Security.psd1') -ErrorAction Stop
$acl = Get-Acl -LiteralPath $env:WECHAT_WORKFLOW_TEST_DIR
$self = [System.Security.Principal.WindowsIdentity]::GetCurrent().User.Value
$sids = @($acl.Access | ForEach-Object { $_.IdentityReference.Translate([System.Security.Principal.SecurityIdentifier]).Value })
[pscustomobject]@{ protected = $acl.AreAccessRulesProtected; self = $self; sids = $sids } | ConvertTo-Json -Compress
'''
        p = subprocess.run(['powershell.exe', '-NoProfile', '-NonInteractive', '-Command', code],
                           capture_output=True, env=dict(os.environ, WECHAT_WORKFLOW_TEST_DIR=str(self.p)), timeout=30)
        self.assertEqual(p.returncode, 0)
        result = json.loads(p.stdout.decode('utf-8-sig'))
        self.assertTrue(result['protected'])
        self.assertEqual(set(result['sids']), {result['self'], 'S-1-5-18'})


if __name__ == '__main__':
    unittest.main()

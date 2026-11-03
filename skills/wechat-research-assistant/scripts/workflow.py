#!/usr/bin/env python3
"""Read-only selected-chat capture. Analysis is done by the invoking Codex skill.

Standard library only. Does not install readers, extract keys, send messages,
run a model, or make network requests. Upstream contract: references/windows.md.
"""
import argparse
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
import hashlib
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import tempfile
import uuid
import xml.etree.ElementTree as ET

TZ = timezone(timedelta(hours=8), 'Asia/Shanghai')
SLUG = re.compile(r'[a-z0-9][a-z0-9_-]{0,63}\Z')


class WorkflowError(Exception):
    pass


def now():
    return datetime.now(TZ).isoformat(timespec='seconds')


def read_json(path, default=None):
    if not path.exists():
        if default is not None:
            return default
        raise WorkflowError('missing_local_file')
    try:
        # Avoid losing server IDs above 2^53 in downstream JSON runtimes.
        return json.loads(path.read_text(encoding='utf-8-sig'))
    except (ValueError, OSError):
        raise WorkflowError('invalid_local_json') from None


def secure_private(path):
    path.mkdir(parents=True, exist_ok=True, mode=0o700)
    if path.is_symlink():
        raise WorkflowError('private_directory_is_symlink')
    if os.name != 'nt':
        path.chmod(0o700)
        return
    # Set a new protected DACL, rather than leaving pre-existing explicit grants.
    # The path is passed through env, not interpolated into PowerShell code.
    code = r'''
$ErrorActionPreference = 'Stop'
$p = $env:WECHAT_WORKFLOW_PRIVATE_DIR
$acl = New-Object System.Security.AccessControl.DirectorySecurity
$acl.SetAccessRuleProtection($true, $false)
$sid = [System.Security.Principal.WindowsIdentity]::GetCurrent().User
$acl.SetOwner($sid)
$sys = New-Object System.Security.Principal.SecurityIdentifier('S-1-5-18')
foreach ($who in @($sid, $sys)) {
 $r = New-Object System.Security.AccessControl.FileSystemAccessRule($who, 'FullControl', 'ContainerInherit,ObjectInherit', 'None', 'Allow')
 $acl.AddAccessRule($r)
}
Set-Acl -LiteralPath $p -AclObject $acl
'''
    env = dict(os.environ, WECHAT_WORKFLOW_PRIVATE_DIR=str(path))
    try:
        p = subprocess.run(['powershell.exe', '-NoProfile', '-NonInteractive',
                            '-Command', code], env=env, capture_output=True,
                           timeout=30)
    except (OSError, subprocess.TimeoutExpired):
        raise WorkflowError('private_acl_setup_failed') from None
    if p.returncode:
        raise WorkflowError('private_acl_setup_failed')


def atomic_write(path, value, private=True):
    if private and '.private' not in path.parent.parts:
        raise WorkflowError('private_output_requires_private_directory')
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700 if private else 0o755)
    if private and os.name != 'nt':
        for parent in (path.parent, *path.parent.parents):
            parent.chmod(0o700)
            if parent.name == '.private':
                break
    if path.is_symlink():
        raise WorkflowError('output_is_symlink')
    data = value if isinstance(value, str) else json.dumps(value, ensure_ascii=False, indent=2) + '\n'
    fd, temp = tempfile.mkstemp(prefix='.write-', dir=path.parent)
    try:
        if os.name != 'nt':
            os.fchmod(fd, 0o600 if private else 0o644)
        with os.fdopen(fd, 'w', encoding='utf-8', newline='\n') as f:
            f.write(data)
            f.flush()
            os.fsync(f.fileno())
        os.replace(temp, path)
    finally:
        if os.path.exists(temp):
            os.unlink(temp)


@contextmanager
def lock(private):
    p = private / 'workflow.lock'
    try:
        fd = os.open(p, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    except FileExistsError:
        raise WorkflowError('another_run_or_stale_lock') from None
    try:
        with os.fdopen(fd, 'w', encoding='utf-8') as f:
            f.write(str(os.getpid()))
        yield
    finally:
        p.unlink(missing_ok=True)


def redact(text):
    """Conservative aid, not a guarantee. Whitelist fields before calling this."""
    text = str(text)
    text = re.sub(r'https?://[^\s<>"\]]+', '[链接已省略；原始链接仅本地]', text)
    text = re.sub(r'[\w.+-]+@[\w.-]+\.[A-Za-z]{2,}', '[邮箱省略]', text)
    # Cover formatted bank numbers and national IDs as well as contiguous digits.
    text = re.sub(r'(?<!\d)(?:\d[ -]?){14,18}[\dXx]?(?!\d)', '[金融或身份号码省略]', text)
    text = re.sub(r'(?<!\d)1[3-9]\d{9}(?!\d)', '[手机号省略]', text)
    text = re.sub(r'(?i)(?:sk-|bearer\s+)[A-Za-z0-9_.~+/=\-]{6,}', '[凭据省略]', text)
    text = re.sub(r'(?i)((?:密码|口令|验证码|密钥|api[_ -]?key|token|password|secret)\s*[:：=]\s*)[^\n]+', r'\1[凭据省略]', text)
    text = re.sub(r'(?<!\w)[A-Za-z0-9_+/=\-]{28,}(?!\w)', '[长标识省略]', text)
    # Standalone ASCII secrets may contain punctuation such as ~ or !.
    lines = []
    for line in text.splitlines():
        t = line.strip()
        if re.fullmatch(r'[\x21-\x7e]{6,128}', t) and re.search(r'\d', t):
            line = '[独立账号或凭据省略]'
        lines.append(line)
    return '\n'.join(lines)


def body_of(row):
    body = str(row['content'])
    kind = int(row['localType']) & 0xffffffff
    if kind == 1:
        return body, 'direct_text'
    # Never emit raw XML, URLs, media credentials, or packedInfo.
    if kind == 49 and len(body) < 2_000_000 and '<!DOCTYPE' not in body.upper():
        try:
            root = ET.fromstring(body)
            app = root if root.tag == 'appmsg' else root.find('.//appmsg')
            if app is not None:
                title = app.findtext('title', default='')
                subtype = app.findtext('type', default='')
                if subtype == '57':
                    quote = app.findtext('refermsg/content', default='')
                    if '<' in quote and '>' in quote:
                        quote = '[引用为结构化内容，待查看原件]'
                    return title + '\n[引用，不代表当前发言] ' + quote, 'quote'
                if subtype == '19':
                    return '[转发聊天记录，内部历史尚未展开] ' + title, 'forwarded_unread'
                return '[应用消息/文件/链接，仅标题] ' + title, 'attachment_title_only'
        except ET.ParseError:
            pass
    return '[非文本消息；需另行读取原件]', 'attachment_unread'


def validate_row(row):
    required = ('_cursor', 'localId', 'shard', 'localType', 'content')
    if not isinstance(row, dict) or any(k not in row for k in required):
        raise WorkflowError('reader_message_schema_mismatch')
    if not isinstance(row['_cursor'], str) or not row['_cursor']:
        raise WorkflowError('reader_cursor_missing')
    if not isinstance(row['content'], str):
        raise WorkflowError('reader_content_not_text')
    try:
        int(row['localType'])
        int(row['localId'])
        int(row['shard'])
        ts = int(row.get('createTime') or 0)
        if not 0 <= ts < 253402214400:
            raise ValueError()
    except (ValueError, TypeError):
        raise WorkflowError('reader_message_schema_mismatch') from None


def message_key(row):
    sid = str(row.get('serverId') or '')
    if sid and sid != '0':
        return 'server:' + sid
    return 'local:' + ':'.join(str(row.get(k, '')) for k in ('shard', 'localId', 'createTime'))


def review_of(row, source, self_id):
    key = message_key(row)
    evidence = source + '-' + hashlib.sha256(key.encode()).hexdigest()[:20]
    sender = str(row.get('sender') or '')
    attribution = '本人' if sender == self_id else ('对方/群成员' if sender else '发送者缺失，身份待核实')
    body, source_kind = body_of(row)
    ts = int(row.get('createTime') or 0)
    when = datetime.fromtimestamp(ts, TZ).isoformat() if ts else '时间缺失'
    return {'evidence_id': evidence, 'time': when, 'sender': sender,
            'attribution': attribution, 'source_kind': source_kind,
            'text': redact(body)}


class Reader:
    def __init__(self, exe):
        self.exe = exe

    def call(self, *args):
        try:
            p = subprocess.run([self.exe, '--json', *args], capture_output=True,
                               timeout=120, encoding='utf-8', errors='strict')
        except (OSError, subprocess.TimeoutExpired, UnicodeError):
            raise WorkflowError('reader_unavailable_or_timeout') from None
        # No upstream stdout/stderr in exceptions: they may contain secrets/body.
        try:
            doc = json.loads(p.stdout, parse_int=str)
        except (ValueError, TypeError):
            raise WorkflowError('reader_invalid_json') from None
        if p.returncode or not isinstance(doc, dict) or doc.get('success') is not True:
            raise WorkflowError('reader_command_failed')
        if 'data' not in doc:
            raise WorkflowError('reader_data_missing')
        return doc['data']

    def account(self):
        return self.call('config', 'get', 'wxid')

    def page(self, conversation, size, cursor, since):
        args = ['chat', 'messages', conversation, '--limit', str(size)]
        args += ['--after', cursor] if cursor else ['--since', str(since)]
        return self.call(*args)


def config(private):
    c = read_json(private / 'settings.json')
    if not isinstance(c, dict) or not isinstance(c.get('self_wxid'), str) or c['self_wxid'] == 'YOUR_OWN_WXID':
        raise WorkflowError('configure_own_account_first')
    if not c.get('reader_executable'):
        raise WorkflowError('configure_reader_executable_first')
    sources = c.get('conversations')
    if not isinstance(sources, list) or not sources:
        raise WorkflowError('select_conversations_first')
    seen, ids = set(), set()
    for s in sources:
        if (not isinstance(s, dict) or not isinstance(s.get('slug'), str)
                or not SLUG.fullmatch(s['slug']) or not isinstance(s.get('id'), str) or not s['id']):
            raise WorkflowError('invalid_conversation_configuration')
        if s['slug'] in seen or s['id'] in ids:
            raise WorkflowError('duplicate_conversation_configuration')
        seen.add(s['slug']); ids.add(s['id'])
    try:
        since = datetime.fromisoformat(c['initial_since'])
        if since.tzinfo is None:
            raise ValueError()
        c['_since'] = int(since.timestamp())
        if not 1 <= int(c.get('page_size', 200)) <= 1000 or not 1 <= int(c.get('max_pages', 1000)) <= 10000:
            raise ValueError()
    except (KeyError, ValueError, TypeError):
        raise WorkflowError('invalid_initial_range_or_page_limits') from None
    return c


def bound_state(private, c):
    s = read_json(private / 'state.json', {'version': 1, 'self_wxid': c['self_wxid'], 'conversations': {}})
    if s.get('version') != 1 or s.get('self_wxid') != c['self_wxid']:
        raise WorkflowError('account_or_state_version_changed_use_new_workspace')
    for source in c['conversations']:
        previous = s.get('conversations', {}).get(source['slug'], {})
        if previous.get('id', source['id']) != source['id']:
            raise WorkflowError('source_binding_changed_use_new_slug')
    return s


def verify_account(reader, c):
    if reader.account() != c['self_wxid']:
        raise WorkflowError('reader_account_mismatch')


def capture(private, c, state, reader):
    """Stage successful sources; never advance a source cursor here."""
    verify_account(reader, c)
    results = []
    for source in c['conversations']:
        slug = source['slug']
        existing = state['conversations'].setdefault(slug, {'id': source['id'], 'cursor': None})
        pending_path = private / 'pending' / (slug + '.json')
        if pending_path.exists():
            results.append({'source': slug, 'status': 'pending_analysis'})
            continue
        batch = datetime.now(TZ).strftime('%Y%m%dT%H%M%S') + '-' + uuid.uuid4().hex[:8]
        base = private / 'batches' / slug / batch
        cursor = existing.get('cursor')
        start_cursor = cursor
        visited = {cursor} if cursor else set()
        gathered = {}
        try:
            for n in range(int(c.get('max_pages', 1000))):
                rows = reader.page(source['id'], int(c.get('page_size', 200)), cursor, c['_since'])
                if not isinstance(rows, list):
                    raise WorkflowError('reader_page_not_array')
                # Raw pages stay private even when later validation fails.
                atomic_write(base / ('page-%05d.json' % n), rows)
                if not rows:
                    break
                for row in rows:
                    validate_row(row)
                    gathered[message_key(row)] = row
                candidate = rows[0]['_cursor']  # newest among the next closest rows
                if candidate in visited:
                    raise WorkflowError('pagination_not_advancing')
                visited.add(candidate)
                cursor = candidate
            else:
                raise WorkflowError('page_limit_reached_incomplete')
            # Re-check before staging so a profile switch during the run is visible.
            verify_account(reader, c)
            archive_path = private / 'archives' / (slug + '.json')
            archive = read_json(archive_path, {'source_id': source['id'], 'messages': {}})
            if archive.get('source_id') != source['id']:
                raise WorkflowError('archive_source_mismatch')
            new = [r for k, r in gathered.items() if k not in archive['messages']]
            # If an earlier staging write was interrupted after archiving, analyze
            # all rows returned after the old cursor, rather than losing that delta.
            analyze = list(gathered.values())
            archive['messages'].update(gathered)
            atomic_write(archive_path, archive)
            if analyze:
                reviews = [review_of(r, slug, c['self_wxid']) for r in analyze]
                reviews.sort(key=lambda r: (r['time'], r['evidence_id']))
                review_path = base / 'review.jsonl'
                atomic_write(review_path, ''.join(json.dumps(r, ensure_ascii=False) + '\n' for r in reviews))
                pending = {'source': slug, 'source_id': source['id'], 'batch': batch,
                           'start_cursor': start_cursor, 'candidate_cursor': cursor,
                           'captured_at': now(), 'new_unique_messages': len(new),
                           'analysis_messages': len(analyze), 'skipped': 'not_reported_by_reader',
                           'review': str(review_path.relative_to(private).as_posix())}
                atomic_write(base / 'manifest.json', pending)
                atomic_write(pending_path, pending)
                existing['last_check'] = now()
                existing['status'] = 'pending_analysis'
                results.append({'source': slug, 'status': 'pending_analysis', 'batch': batch,
                                'new_unique_messages': len(new), 'analysis_messages': len(analyze)})
            else:
                existing.update(last_check=now(), status='no_returned_messages')
                results.append({'source': slug, 'status': 'no_returned_messages'})
        except (WorkflowError, OSError, ValueError, OverflowError, TypeError) as e:
            # Do not print upstream exception text or content-bearing file paths.
            error = str(e) if isinstance(e, WorkflowError) else 'local_write_failed'
            existing.update(last_check=now(), status='failed', error=error)
            results.append({'source': slug, 'status': 'failed', 'error': error})
        # A source's cursor stays unchanged until its report and shared docs exist.
        atomic_write(private / 'state.json', state)
    return results


def commit(workspace, private, c, source, batch, report):
    workspace, private = workspace.resolve(), private.resolve()
    if source not in {s['slug'] for s in c['conversations']}:
        raise WorkflowError('source_not_selected')
    state = bound_state(private, c)
    pending_path = private / 'pending' / (source + '.json')
    p = read_json(pending_path)
    entry = state['conversations'].get(source, {})
    if p['batch'] != batch or entry.get('cursor') not in (p['start_cursor'], p['candidate_cursor']):
        raise WorkflowError('stale_batch_or_cursor')
    file = (workspace / report).resolve()
    if not file.is_relative_to(workspace) or file.is_relative_to(private) or not file.is_file():
        raise WorkflowError('report_must_be_inside_workspace_outside_private')
    text = file.read_text(encoding='utf-8-sig')
    if batch not in text or source not in text or len(text.strip()) < 40:
        raise WorkflowError('report_missing_batch_binding')
    review_path = private / p['review']
    ids = [json.loads(line)['evidence_id'] for line in review_path.read_text(encoding='utf-8').splitlines() if line]
    if ids and not any(e in text for e in ids):
        raise WorkflowError('report_missing_evidence_reference')
    entry.update(cursor=p['candidate_cursor'], id=p['source_id'], last_success=now(),
                 status='committed', report=str(file.relative_to(workspace).as_posix()),
                 report_sha256=hashlib.sha256(file.read_bytes()).hexdigest())
    entry.pop('error', None)
    state['conversations'][source] = entry
    atomic_write(private / 'state.json', state)
    p.update(committed_at=now(), report=entry['report'], report_sha256=entry['report_sha256'])
    atomic_write(private / 'batches' / source / batch / 'committed.json', p)
    pending_path.unlink()
    return {'source': source, 'status': 'committed'}


def init(workspace, private):
    if (private / 'settings.json').exists():
        raise WorkflowError('workspace_already_initialized')
    since = (datetime.now(TZ) - timedelta(days=7)).replace(hour=0, minute=0, second=0, microsecond=0)
    atomic_write(private / 'settings.json', {'reader_executable': '', 'self_wxid': 'YOUR_OWN_WXID',
                 'initial_since': since.isoformat(), 'page_size': 200, 'max_pages': 1000, 'conversations': []})
    files = {'用户目标.md': '# 用户目标\n\n待填写：关注谁、什么目的、允许哪些资料进入当前模型、何时提醒。\n',
             '联系人与项目.md': '# 联系人与项目\n\n由用户确认稳定身份与来源范围；同一来源可支持多个项目。\n',
             '信息分析.md': '# 信息分析\n\n事实、对方表述、公开资料与分析推断分开记录；每项保留证据与日期。\n',
             '待办.md': '# 待办\n\n只登记有明确来源的承诺、任务及期限。\n',
             '.gitignore': '.private/\n__pycache__/\n*.pyc\n'}
    for name, content in files.items():
        if not (workspace / name).exists():
            atomic_write(workspace / name, content, private=False)
    return {'status': 'initialized_config_required'}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('command', choices=['init', 'doctor', 'sync', 'commit'])
    parser.add_argument('--workspace', required=True, type=Path)
    parser.add_argument('--source')
    parser.add_argument('--batch')
    parser.add_argument('--report')
    args = parser.parse_args()
    workspace = args.workspace.expanduser().resolve()
    private = workspace / '.private'
    try:
        secure_private(private)
        with lock(private):
            if args.command == 'init':
                result = init(workspace, private)
            else:
                c = config(private)
                state = bound_state(private, c)
                reader = Reader(c['reader_executable'])
                if args.command == 'doctor':
                    verify_account(reader, c)
                    result = {'status': 'account_matches', 'selected_sources': len(c['conversations']),
                              'windows_live_acceptance': 'required', 'message_read': 'not_checked'}
                elif args.command == 'sync':
                    result = capture(private, c, state, reader)
                else:
                    if not all([args.source, args.batch, args.report]):
                        raise WorkflowError('commit_requires_source_batch_report')
                    result = commit(workspace, private, c, args.source, args.batch, args.report)
        print(json.dumps(result, ensure_ascii=False))
        return 2 if isinstance(result, list) and any(r['status'] == 'failed' for r in result) else 0
    except (WorkflowError, OSError, ValueError, TypeError, KeyError, OverflowError):
        e = sys.exc_info()[1]
        print(json.dumps({'status': 'failed', 'error': str(e) if isinstance(e, WorkflowError) else 'local_operation_failed'}, ensure_ascii=False))
        return 2


if __name__ == '__main__':
    sys.exit(main())

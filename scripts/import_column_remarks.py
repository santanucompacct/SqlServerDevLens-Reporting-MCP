#!/usr/bin/env python3
"""Import reviewed JSON field remarks through a loopback-only private admin API.

Dry run by default. Does not publish a catalog or change SQL objects/credentials.
Uses only the Python standard library.
"""
import argparse
import ipaddress
import json
import os
from pathlib import Path
import re
import secrets
import sys
import time
from urllib.error import HTTPError, URLError
from urllib.parse import urlsplit
from urllib.request import Request, HTTPRedirectHandler, build_opener


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, request, file, code, message, headers, new_url):
        return None  # Never forward the administrator credential to a redirect.


def admin_origin(value):
    parsed = urlsplit(value)
    try:
        local = parsed.hostname == 'localhost' or ipaddress.ip_address(parsed.hostname).is_loopback
    except (ValueError, TypeError):
        local = False
    if (not local or parsed.scheme not in {'http', 'https'} or parsed.username or parsed.password
            or parsed.path not in {'', '/'} or parsed.query or parsed.fragment):
        raise ValueError('Admin URL must be a loopback origin, normally an SSH tunnel.')
    _ = parsed.port
    return value.rstrip('/')


def load_remarks(value):
    if isinstance(value, dict) and 'columns' in value:
        rows = value['columns']
    elif isinstance(value, dict):
        rows = [{'name': name, 'remarks': remarks} for name, remarks in value.items()]
    else:
        rows = value
    if not isinstance(rows, list) or not rows:
        raise ValueError('Input must contain a nonempty columns list or name-to-remark mapping.')
    result = {}
    for row in rows:
        if not isinstance(row, dict):
            raise ValueError('Every input column must be an object.')
        name, remarks = row.get('name'), row.get('remarks', row.get('description'))
        if (not isinstance(name, str) or not re.fullmatch(r'[A-Za-z_][A-Za-z0-9_]{0,127}', name)
                or not isinstance(remarks, str) or not remarks.strip() or len(remarks) > 2000):
            raise ValueError('Each column needs a valid name and nonblank remark of at most 2000 characters.')
        key = name.casefold()
        if key in result:
            raise ValueError('Duplicate column names in the input.')
        result[key] = remarks
    return result


def import_remarks(api, source, schema, object_name, apply=False, backup=None):
    for name in (schema, object_name):
        if not re.fullmatch(r'[A-Za-z_][A-Za-z0-9_]{0,127}', name):
            raise ValueError('Schema/object name is outside the reporting identifier format.')
    remarks = load_remarks(source)
    state = api('state')
    target = {'connection': state['setup']['connection'], 'schema_name': schema, 'object_name': object_name}
    current = api('object', target)
    columns = current['columns']
    names = {column['name'].casefold() for column in columns}
    if not names or len(names) != len(columns) or names != set(remarks):
        raise ValueError('Input column names must match the complete live object exactly; nothing was saved.')
    summary = {'schema': schema, 'object': object_name, 'columns': len(columns),
               'demo': bool(current.get('demo')), 'applied': False, 'published': False}
    if not apply:
        return summary
    path = Path(backup) if backup else Path('.devlens-import-backups') / (
        object_name + '-' + str(int(time.time())) + '-' + secrets.token_hex(4) + '.json')
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    raw = json.dumps({'schema_name': schema, 'object_name': object_name,
                      'revision': current['revision'], 'columns': columns}, indent=2).encode()
    descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(descriptor, 'wb') as output:
        output.write(raw)
    payload = {**target, 'revision': current['revision'],
               'columns': [{'name': column['name'], 'remarks': remarks[column['name'].casefold()]}
                           for column in columns]}
    api('object/save', payload)  # Single mutation. Never retry an uncertain write.
    verified = api('object', target)['columns']
    if ({column['name'].casefold(): column['remarks'] for column in verified} != remarks
            or len(verified) != len(columns)):
        raise RuntimeError('Save verification did not match; inspect the current server state before retrying.')
    summary.update(applied=True, backup=str(path))
    return summary


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--admin-url', default='http://127.0.0.1:8101')
    parser.add_argument('--input', required=True)
    parser.add_argument('--schema', default='dbo')
    parser.add_argument('--object', required=True, dest='object_name')
    parser.add_argument('--apply', action='store_true', help='Save after validating; default is dry run.')
    parser.add_argument('--backup', help='Private pre-change JSON file; must not already exist.')
    args = parser.parse_args(argv)
    try:
        origin = admin_origin(args.admin_url)
        token = os.environ.get('DEVLENS_ADMIN_TOKEN', '')
        if not token:
            raise ValueError('Set DEVLENS_ADMIN_TOKEN privately in your environment.')
        opener = build_opener(NoRedirect())

        def api(endpoint, body=None):
            request = Request(origin + '/admin/api/' + endpoint,
                              data=json.dumps(body).encode() if body is not None else None,
                              headers={'Authorization': 'Bearer ' + token, 'Content-Type': 'application/json'})
            try:
                with opener.open(request, timeout=70) as response:
                    return json.load(response)
            except HTTPError as error:
                raise RuntimeError('Admin API HTTP ' + str(error.code) + '; no automatic retry. Reload state before retrying.') from None
            except URLError:
                raise RuntimeError('Private admin API unavailable; check the SSH tunnel and service.') from None

        source = json.loads(Path(args.input).read_text(encoding='utf-8'))
        print(json.dumps(import_remarks(api, source, args.schema, args.object_name, args.apply, args.backup)))
        return 0
    except (ValueError, RuntimeError, OSError) as error:
        print(str(error), file=sys.stderr)
        return 1


if __name__ == '__main__':
    raise SystemExit(main())

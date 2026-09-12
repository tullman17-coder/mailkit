#!/usr/bin/env python3
"""Compile the native checks and exercise HTTP without accessing a real mailbox."""
import http.server
import json
from pathlib import Path
import plistlib
import subprocess
import threading
import urllib.parse

root = Path(__file__).resolve().parents[1]


class Fixture(http.server.BaseHTTPRequestHandler):
    def log_message(self, *args):
        pass

    def do_GET(self):
        assert self.headers['Authorization'] == 'Bearer local-fixture-token'
        route = urllib.parse.urlparse(self.path).path
        if route == '/credential-leak':
            raise AssertionError('Native client followed an API redirect')
        if route == '/v1/redirect':
            self.send_response(302)
            self.send_header('Location', '/credential-leak')
            self.end_headers()
            return
        reason = {'/v1/provider-error': 'Incorrect app password', '/v1/token-error': 'invalid token'}.get(route)
        self.respond(None if reason else [self.account('work@example.com')], reason)

    def do_POST(self):
        assert self.headers['Authorization'] == 'Bearer local-fixture-token'
        parsed = urllib.parse.urlparse(self.path)
        assert urllib.parse.parse_qs(parsed.query) == {'mailbox': ['Saved & receipts/2026']}
        assert json.loads(self.rfile.read(int(self.headers['Content-Length']))) == {'name': 'Example'}
        self.respond(self.account(urllib.parse.unquote(parsed.path.rsplit('/', 1)[-1])))

    @staticmethod
    def account(account_id):
        return dict(id=account_id, name='Example', address='work@example.com', provider='imap', auth='password')

    def respond(self, data, reason=None):
        body = json.dumps(dict(ok=reason is None, schema='mailkit.response.v1', data=data,
                               error={'code': 'auth', 'message': reason} if reason else None)).encode()
        self.send_response(401 if reason else 200)
        self.send_header('Content-Type', 'application/json')
        self.send_header('Content-Length', str(len(body)))
        self.end_headers()
        self.wfile.write(body)


if __name__ == '__main__':
    info = plistlib.loads((root / 'clients/apple/MailKit/Info.plist').read_bytes())
    assert info['BGTaskSchedulerPermittedIdentifiers'] == ['org.zermo.mailkit.engine-refresh']
    assert info['UIBackgroundModes'] == ['fetch']
    (root / 'build').mkdir(exist_ok=True)
    binary = root / 'build/apple-checks'
    subprocess.run(['xcrun', 'swiftc', '-parse-as-library', '-strict-concurrency=complete', '-warnings-as-errors',
                    'clients/apple/MailKit/Models.swift', 'clients/apple/MailKit/MailAPI.swift',
                    'clients/apple/Tests/Checks.swift', '-module-cache-path', 'build/swift-check-cache',
                    '-o', str(binary)], cwd=root, check=True)
    server = http.server.ThreadingHTTPServer(('127.0.0.1', 0), Fixture)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    try:
        subprocess.run([str(binary), f'http://127.0.0.1:{server.server_port}'], check=True)
    finally:
        server.shutdown()
        server.server_close()

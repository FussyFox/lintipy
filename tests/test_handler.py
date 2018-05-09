import json
import logging
import subprocess
from unittest.mock import Mock

import httpretty
import pytest
from botocore.vendored import requests

from lintipy import Handler, TIMED_OUT


class TestHandler:

    def test_init(self):
        hnd = Handler('zen of python', 'this', '1', '2', '3')
        assert hnd.label == 'zen of python'
        assert hnd.cmd == 'this'
        assert hnd.cmd_args == ('1', '2', '3')

    def test_hook(self, handler):
        assert handler.hook['installation']['id'] == 234

    def test_sha(self, handler):
        assert handler.sha == '0d1a26e67d8f5eaf1f6ba5c57fc3c7d91ac0fd1c'

    def test_archive_url(self, handler):
        assert handler.archive_url == (
            'https://api.github.com/repos/baxterthehacker/public-repo/'
            'tarball/0d1a26e67d8f5eaf1f6ba5c57fc3c7d91ac0fd1c'
        )

    def test_check_runs_url(self, handler):
        assert handler.check_runs_url == (
            'https://api.github.com/repos/github/baxterthehacker/public-repo/'
            '5/check-runs'
        )

    def test_session(self, handler):
        handler._token = 123
        assert handler.session
        assert 'Authorization' in handler.session.headers
        assert handler.session.headers['Authorization'] == 'token 123'

    def test_full_name(self, handler):
        handler._session = requests.Session()
        assert handler.download_code()

    @httpretty.activate
    def test_call(self, handler, caplog):
        check_run_url = 'http://api.github.com/check/3'
        httpretty.register_uri(
            httpretty.POST, handler.check_runs_url,
            body=json.dumps({'url': check_run_url}),
            status=201,
            content_type='application/json',
        )
        httpretty.register_uri(
            httpretty.PATCH, check_run_url,
            data='',
            status=200,
            content_type='application/json',
        )
        handler._session = requests.Session()
        handler.download_code = lambda: '.'
        with caplog.at_level(logging.INFO, logger='lintipy'):
            handler(handler.event, {})
        assert "linter exited with status code 0 in " in caplog.text

        handler.cmd = 'doesnotexit'
        with caplog.at_level(logging.INFO, logger='lintipy'):
            handler(handler.event, {})
        assert "linter exited with status code 1 in " in caplog.text

        handler.hook['action'] = 'completed'
        with caplog.at_level(logging.INFO, logger='lintipy'):
            handler(handler.event, {})
        assert "The suite has been completed, no action required." in caplog.text

    @httpretty.activate
    def test_timeout(self, handler, caplog):
        check_run_url = 'http://api.github.com/check/3'
        httpretty.register_uri(
            httpretty.POST, handler.check_runs_url,
            body=json.dumps({'url': check_run_url}),
            status=201,
            content_type='application/json',
        )
        httpretty.register_uri(
            httpretty.PATCH, check_run_url,
            data='',
            status=200,
            content_type='application/json',
        )
        handler.cmd = 'tests.timeout'
        handler.cmd_timeout = 1
        handler._session = requests.Session()
        handler._s3 = Mock()
        handler.download_code = lambda: '.'
        with pytest.raises(subprocess.TimeoutExpired) as e:
            handler(handler.event, {})
        assert "timed out after 1 seconds" in str(e)

    def test_installation_id(self, handler):
        assert handler.installation_id == 234

    def test_download_code(self, handler):
        handler._session = requests.Session()
        assert handler.download_code()

    def test_download_code_timeout(self, handler):
        def _timeout(*args, **kwargs):
            raise requests.Timeout('connection time out')

        data = {}

        def update_check_run(status, summary, conclusion=None):
            data['status'] = status
            data['summary'] = summary
            data['conclusion'] = conclusion
        handler._session = requests.Session()
        handler._session.get = _timeout
        handler.download_timeout = float('1e-10')
        handler.update_check_run = update_check_run
        with pytest.raises(requests.Timeout):
            handler.download_code()
        assert data['conclusion'] == TIMED_OUT
        assert data['summary'] == 'Downloading code timed out after 1e-10s'

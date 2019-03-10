import json
import logging
import os
import subprocess
from pathlib import Path

import httpretty
import pytest
from botocore.vendored import requests

from lintipy import CheckRun, TIMED_OUT

BASE_DIR = Path(os.path.dirname(__file__))


def sns():
    with open(BASE_DIR / 'fixtures' / 'sns.json') as f:
        return json.load(f)


def check_run_event():
    with open(BASE_DIR / 'fixtures' / 'checkRunEvent.json') as f:
        return 'check_run', f.read()


class TestCheckRun:

    @pytest.fixture()
    def handler(self):
        notice = sns()
        hnd = CheckRun('zen of python', 'this', '1', '2', '3')
        subject, message = check_run_event()
        notice['Records'][0]['Sns']['Subject'] = subject
        notice['Records'][0]['Sns']['Message'] = message
        hnd.event = notice
        hnd.hook = json.loads(message)
        hnd._session = requests.Session()
        return hnd

    def test_as_handler(self, handler):
        hdl = CheckRun.as_handler('zen of python', 'this', '1', '2', '3')
        assert callable(hdl)
        assert hdl.__name__ == 'CheckRun'

        with pytest.raises(ValueError):
            hdl(handler.event, {})

    def test_init(self):
        hnd = CheckRun('zen of python', 'this', '1', '2', '3')
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

    def test_session(self, handler):
        handler._session = None
        handler._token = 123
        assert handler.session
        assert 'Authorization' in handler.session.headers
        assert handler.session.headers['Authorization'] == 'token 123'

    def test_full_name(self, handler):
        assert handler.download_code()

    @httpretty.activate
    def test_call(self, handler, caplog):
        httpretty.register_uri(
            httpretty.PATCH, handler.check_run_url,
            data='',
            status=200,
            content_type='application/json',
        )
        handler.download_code = lambda: '.'
        with caplog.at_level(logging.INFO, logger='lintipy'):
            handler(handler.event, {})
        assert "linter exited with status code 0 in " in caplog.text

        handler.cmd = 'doesnotexit'
        with caplog.at_level(logging.INFO, logger='lintipy'):
            handler(handler.event, {})
        assert "linter exited with status code 1 in " in caplog.text

        handler.hook['action'] = 'updated'
        handler.event['Records'][0]['Sns']['Message'] = json.dumps(handler.hook)
        with caplog.at_level(logging.INFO, logger='lintipy'):
            handler(handler.event, {})
        assert "No action required." in caplog.text

    @httpretty.activate
    def test_timeout(self, handler, caplog):
        httpretty.register_uri(
            httpretty.PATCH, handler.check_run_url,
            data='',
            status=200,
            content_type='application/json',
        )
        handler.cmd = 'tests.timeout'
        handler.cmd_timeout = 1
        handler._session = requests.Session()
        handler.download_code = lambda: '.'
        with pytest.raises(subprocess.TimeoutExpired) as e:
            handler(handler.event, {})
        assert "timed out after 1 seconds" in str(e)

    def test_installation_id(self, handler):
        assert handler.installation_id == 234

    def test_download_code(self, handler):
        assert handler.download_code()

    def test_download_code_timeout(self, handler):
        def _timeout(*args, **kwargs):
            raise requests.Timeout('connection time out')

        data = {}

        def update_check_run(status, summary, conclusion=None):
            data['status'] = status
            data['summary'] = summary
            data['conclusion'] = conclusion

        handler._session.get = _timeout
        handler.download_timeout = float('1e-10')
        handler.update_check_run = update_check_run
        with pytest.raises(requests.Timeout):
            handler(handler.event, {})
        assert data['conclusion'] == TIMED_OUT
        assert data['summary'] == 'Downloading code timed out after 1e-10s'

import logging
from unittest.mock import Mock

import httpretty
import pytest
from botocore.vendored import requests

from lintipy import Handler


class TestHandler:

    def test_init(self):
        hnd = Handler('some linter', 'echo', '1', '2', '3')
        assert hnd.label == 'some linter'
        assert hnd.cmd == 'echo'
        assert hnd.cmd_args == ('1', '2', '3')

    def test_hook(self, handler):
        assert handler.hook['installation']['id'] == 234

    def test_sha(self, handler):
        assert handler.sha == '0d1a26e67d8f5eaf1f6ba5c57fc3c7d91ac0fd1c'

    def test_code_has_changed(self, handler):
        assert handler.code_has_changed

    def test_event_type(self, handler):
        assert handler.event_type in ['push', 'pull_request']

    def test_archive_url(self, handler):
        assert handler.archive_url == (
            'https://api.github.com/repos/baxterthehacker/public-repo/'
            'tarball/0d1a26e67d8f5eaf1f6ba5c57fc3c7d91ac0fd1c'
        )

    def test_statuses_url(self, handler):
        assert handler.statuses_url == (
            'https://api.github.com/repos/baxterthehacker/public-repo/'
            'statuses/0d1a26e67d8f5eaf1f6ba5c57fc3c7d91ac0fd1c'
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
        httpretty.register_uri(
            httpretty.POST, handler.statuses_url,
            data='',
            status=201,
            content_type='application/json',
        )
        handler._session = requests.Session()
        handler._s3 = Mock()
        handler.download_code = lambda: '.'
        with caplog.at_level(logging.INFO, logger='lintipy'):
            handler(handler.event, {})
        assert "linter exited with status code 1 in " in caplog.text

    def test_download_code(self, handler):
        handler._session = requests.Session()
        assert handler.download_code()

    @httpretty.activate
    def test_set_status(self, handler, caplog):
        httpretty.register_uri(
            httpretty.POST, handler.statuses_url,
            data='',
            status=201,
            content_type='application/json',
        )

        handler._session = requests.Session()
        with caplog.at_level(logging.INFO, logger='lintipy'):
            handler.set_status('success', 'some description')
            assert "success: some description" in caplog.text
        handler.set_status('success', 'some description',
                           target_url='https://example.com')

        with pytest.raises(ValueError) as e:
            handler.set_status('not_valid', 'some description')
        assert "'not_valid' is not a valid state" in str(e)

        httpretty.register_uri(
            httpretty.POST, handler.statuses_url,
            data='',
            status=400,
            content_type='application/json',
        )
        with pytest.raises(requests.HTTPError):
            handler.set_status('success', 'some description')

    def test_get_log_url(self, handler):
        assert handler.get_log_url() == (
            'https://lambdalint.github.io/gh/?'
            'app=some+linter&repo=baxterthehacker%2Fpublic-repo'
            '&ref=0d1a26e67d8f5eaf1f6ba5c57fc3c7d91ac0fd1c'
        )

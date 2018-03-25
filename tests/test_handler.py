import pytest

from lintipy import Handler


@pytest.fixture()
def handler(sns):
    hnd = Handler('some linter', 'echo', '1', '2', '3')
    hnd.event = sns
    return hnd


class TestHandler:

    def test_init(self):
        hnd = Handler('some linter', 'echo', '1', '2', '3')
        assert hnd.label == 'some linter'
        assert hnd.cmd == 'echo'
        assert hnd.cmd_args == ('1', '2', '3')

    def test_hook(self, handler):
        assert handler.hook == ['some', 'json']

    def test_sha(self, handler, pull_request_event, push_event):
        handler._hook = push_event
        assert handler.sha is None

        handler.event['Records'][0]['Sns']['Subject'] = 'PushEvent'
        handler._hook = push_event
        assert handler.sha == '0d1a26e67d8f5eaf1f6ba5c57fc3c7d91ac0fd1c'

        handler.event['Records'][0]['Sns']['Subject'] = 'PullRequestEvent'
        handler._hook = pull_request_event
        assert handler.sha == '0d1a26e67d8f5eaf1f6ba5c57fc3c7d91ac0fd1c'

    def test_code_has_changed(self, handler, pull_request_event, push_event):
        handler.event['Records'][0]['Sns']['Subject'] = 'PushEvent'
        handler._hook = push_event
        assert handler.code_has_changed

        handler.event['Records'][0]['Sns']['Subject'] = 'PullRequestEvent'
        handler._hook = pull_request_event
        assert handler.code_has_changed

    def test_event_type(self, handler):
        assert handler.event_type == 'TestInvoke'

import json
import os
from pathlib import Path

import pytest

from lintipy import Handler

BASE_DIR = Path(os.path.dirname(__file__))


@pytest.fixture()
def sns():
    with open(BASE_DIR / 'fixtures' / 'sns.json') as f:
        return json.load(f)


def push_event():
    with open(BASE_DIR / 'fixtures' / 'pushEvent.json') as f:
        return 'push', f.read()


def pull_request_event():
    with open(BASE_DIR / 'fixtures' / 'pullRequestEvent.json') as f:
        return 'pull_request', f.read()


@pytest.fixture(params=[push_event, pull_request_event])
def handler(request, sns):
    hnd = Handler('some linter', 'echo', '1', '2', '3')
    subject, message = request.param()
    sns['Records'][0]['Sns']['Subject'] = subject
    sns['Records'][0]['Sns']['Message'] = message
    hnd.event = sns
    return hnd

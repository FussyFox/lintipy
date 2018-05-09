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


def check_suite_event():
    with open(BASE_DIR / 'fixtures' / 'checkSuiteEvent.json') as f:
        return 'check_suite', f.read()


@pytest.fixture(params=[check_suite_event])
def handler(request, sns):
    hnd = Handler('zen of python', 'this', '1', '2', '3')
    subject, message = request.param()
    sns['Records'][0]['Sns']['Subject'] = subject
    sns['Records'][0]['Sns']['Message'] = message
    hnd.event = sns
    return hnd

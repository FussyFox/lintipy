import json
import os
from pathlib import Path

import pytest

BASE_DIR = Path(os.path.dirname(__file__))


@pytest.fixture()
def sns():
    with open(BASE_DIR / 'fixtures' / 'sns.json') as f:
        return json.load(f)


@pytest.fixture()
def push_event():
    with open(BASE_DIR / 'fixtures' / 'pushEvent.json') as f:
        return json.load(f)


@pytest.fixture()
def pull_request_event():
    with open(BASE_DIR / 'fixtures' / 'pullRequestEvent.json') as f:
        return json.load(f)


"""AWS Lambda handlers for GitHub events wrapped in SNS messages."""
import datetime
import io
import json
import logging
import os
import resource
import subprocess  # nosec
import tarfile
import tempfile
import time

import jwt
from botocore.vendored import requests

logger = logging.getLogger('lintipy')

QUEUED = 'queued'
IN_PROGRESS = 'in_progress'
COMPLETED = 'completed'

STATUS_STATES = {QUEUED, IN_PROGRESS, COMPLETED}
"""
Accepted statuses for GitHub's check suite API.

.. seealso:: https://developer.github.com/v3/checks/runs/#parameters
"""


class GitHubEvent:
    """Base handler for AWS lambda consuming GitHub events wrapped in SNS messages."""

    def __init__(self):
        self._token = None
        self._hook = None
        self._session = None
        self.event = None
        self.integration_id = os.environ.get('INTEGRATION_ID')

        pem = os.environ.get('PEM', '')
        self.pem = '\n'.join(pem.split('\\n'))

    def __call__(self, event, context):
        self.event = event
        self.context = context
        self.hook = json.loads(self.event['Records'][0]['Sns']['Message'])
        logger.debug(self.hook)

    @property
    def installation_id(self):
        return self.hook['installation']['id']

    @property
    def token(self):
        """Return OAuth access token from GibHub via the installations API."""
        if self._token is None:
            now = int(time.time())
            exp = 300
            payload = {
                # issued at time
                'iat': now,
                # JWT expiration time
                'exp': now + exp,
                # Integration's GitHub identifier
                'iss': self.integration_id
            }
            bearer = jwt.encode(payload, self.pem, algorithm='RS256')
            headers = {
                'Accept': 'application/vnd.github.machine-man-preview+json',
                'Authorization': 'Bearer %s' % bearer.decode(encoding='UTF-8')
            }
            url = (
                    'https://api.github.com/installations/'
                    '%s/access_tokens' % self.installation_id
            )
            logger.info('requesting new token')
            res = requests.post(url, headers=headers)
            res.raise_for_status()
            self._token = res.json()['token']
        return self._token

    @property
    def session(self):
        if not self._session:
            self._session = requests.Session()
            self._session.headers.update({
                'Authorization': 'token %s' % self.token,
                'Accept': 'application/vnd.github.antiope-preview+json',
            })
        return self._session


class DownloadCodeMixin:
    """
    Mixin that allows downloading code.

    Subclasses must inherit from `.GitHubEvent` and implement ``archive_url``.
    """

    download_timeout = 30

    def download_code(self):
        """Download code to local filesystem storage and return path."""
        logger.info('Downloading: %s', self.archive_url)
        response = self.session.get(self.archive_url, timeout=self.download_timeout)
        response.raise_for_status()
        path = tempfile.mkdtemp()
        logger.info("Extracting file file to: %s", path)
        with io.BytesIO() as bs:
            bs.write(response.content)
            bs.seek(0)
            with tarfile.open(fileobj=bs, mode='r:gz') as fs:
                fs.extractall(path)
            folder = os.listdir(path)[0]
            return os.path.join(path, folder)


SUCCESS = 'success'
FAILURE = 'failure'
NEUTRAL = 'neutral'
CANCELLED = 'cancelled'
TIMED_OUT = 'timed_out'
ACTION_REQUIRED = 'action_required'

CONCLUSIONS = {SUCCESS, FAILURE, NEUTRAL, CANCELLED, TIMED_OUT, ACTION_REQUIRED}
"""
Accepted conclusions for GitHub's check suite API.

.. seealso:: https://developer.github.com/v3/checks/runs/#parameters
"""

PYTHONPATH = 'PYTHONPATH'


class CheckRun(DownloadCodeMixin, GitHubEvent):
    """Handle GitHub check_run event wrapped in an SNS message."""

    CREATED = 'created'
    UPDATED = 'updated'
    REREQUESTED = 'rerequested'

    ACTIONS = {CREATED, UPDATED, REREQUESTED}

    cmd_timeout = 200

    def __init__(self, label: str, cmd: str, *cmd_args: str,
                 cmd_timeout=200, **kwargs):
        super().__init__(**kwargs)
        self.label = label
        self.cmd = cmd
        self.cmd_args = cmd_args
        self.cmd_timeout = cmd_timeout

    def __call__(self, event, context):
        """AWS Lambda function handler."""
        super().__call__(event, context)
        if self.hook['action'] not in [self.CREATED, self.REREQUESTED]:
            logger.info("No action required.")
            return  # Do not execute linter.

        self.update_check_run(IN_PROGRESS, "Downloading code...")
        try:
            code_path = self.download_code()
        except requests.Timeout:
            self.update_check_run(
                COMPLETED,
                'Downloading code timed out after %ss' % self.download_timeout,
                TIMED_OUT
            )
            raise
        self.update_check_run(IN_PROGRESS, "Running linter...")
        code, log = self.run_process(code_path)

        if code == 0:
            self.update_check_run(
                COMPLETED, "```\n%s\n```" % log, SUCCESS
            )
        else:
            self.update_check_run(
                COMPLETED, "```\n%s\n```" % log, FAILURE
            )

    @property
    def sha(self):
        return self.hook['check_run']['head_sha']

    @property
    def check_run_url(self):
        return self.hook['check_run']['url']

    @property
    def archive_url(self):
        return self.hook['repository']['archive_url'].format(**{
            'archive_format': 'tarball',
            '/ref': '/%s' % self.sha,
        })

    def get_env(self):
        """
        Return environment but add the file dir to the ``PYTHONPATH``.

        Returns:
            dict: Environment

        """
        env = os.environ.copy()
        env[PYTHONPATH] = ":".join([
            os.path.dirname(os.path.realpath(__file__)),
            env.get(PYTHONPATH, ''),
        ])
        return env

    def run_process(self, code_path):
        """
        Run linter command as sub-processes.

        Returns:
            tuple[int, str]: Tuple containing exit code and URI to log file.

        """
        logger.info('Running: %s %s', self.cmd, ' '.join(self.cmd_args))
        try:
            process = subprocess.run(
                ('python', '-m', self.cmd) + self.cmd_args,
                stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                cwd=code_path, env=self.get_env(),
                timeout=self.cmd_timeout,
            )
        except subprocess.TimeoutExpired:
            self.update_check_run(
                COMPLETED, 'Command timed out after %ss' % self.cmd_timeout, TIMED_OUT
            )
            raise
        else:
            info = resource.getrusage(resource.RUSAGE_CHILDREN)
            log = process.stdout.decode()
            logger.debug(log)
            logger.debug('exit %s', process.returncode)
            logger.info(
                'linter exited with status code %s in %ss' % (process.returncode, info.ru_utime)
            )
            return (
                process.returncode,
                log
            )

    def update_check_run(self, status, summary, conclusion=None):
        data = {
            'name': self.label,
            'status': status,
            'output': {
                'title': self.label,
                'summary': summary,
            }
        }
        if conclusion:
            data['conclusion'] = conclusion
        if status == COMPLETED:
            data['completed_at'] = datetime.datetime.utcnow().strftime('%Y-%m-%dT%H:%M:%SZ')
        response = self.session.patch(self.check_run_url, json=data)
        response.raise_for_status()

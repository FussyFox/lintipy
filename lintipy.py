"""Run static file linters on AWS lambda."""
import datetime
import json
import logging
import os
import resource
import subprocess  # nosec
import tarfile
import tempfile
import time
from io import BytesIO

import jwt
from botocore.vendored import requests

__all__ = ('Handler', 'logger')

logger = logging.getLogger('lintipy')


QUEUED = 'queued'
IN_PROGRESS = 'in_progress'
COMPLETED = 'completed'

STATUS_STATES = {QUEUED, IN_PROGRESS, COMPLETED}
"""
Accepted statuses for GitHub's check suite API.

.. seealso:: https://developer.github.com/v3/checks/runs/#parameters
"""

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


class Handler:
    """Handle GitHub web hooks via SNS message."""

    FAQ_URL = 'https://lambdalint.github.io/#faq'

    def __init__(self, label: str, cmd: str, *cmd_args: str,
                 integration_id: str = None, bucket: str = None,
                 region: str = None, pem: str = None, cmd_timeout=200, download_timeout=30):
        self.label = label
        self.cmd = cmd
        self.cmd_args = cmd_args
        self._token = None
        self._hook = None
        self._s3 = None
        self._session = None
        self.event = None
        self.check_run_url = None
        self.integration_id = integration_id or os.environ.get('INTEGRATION_ID')
        self.bucket = bucket or os.environ.get('BUCKET', 'lambdalint')
        self.region = region or os.environ.get('REGION', 'eu-west-1')
        self.cmd_timeout = cmd_timeout
        self.download_timeout = download_timeout

        pem = pem or os.environ.get('PEM', '')
        self.pem = '\n'.join(pem.split('\\n'))

    def __call__(self, event, context):
        """AWS Lambda function handler."""
        self.event = event
        logger.debug(event)

        if self.hook['action'] == 'completed':
            logger.info("The suite has been completed, no action required.")
            return  # Do not execute linter.

        self.create_check_run("Downloading code...")
        code_path = self.download_code()
        self.update_check_run(IN_PROGRESS, "Running linter...")
        ru_time, code, target_url = self.run_process(code_path)

        if code == 0:
            self.update_check_run(
                COMPLETED, "%s succeeded in %ss" % (self.cmd, ru_time), SUCCESS
            )
        else:
            self.update_check_run(
                COMPLETED, "%s failed in %ss" % (self.cmd, ru_time), FAILURE
            )

    @property
    def hook(self):
        if self._hook is None:
            self._hook = json.loads(self.event['Records'][0]['Sns']['Message'])
            logger.debug(self._hook)
        return self._hook

    @property
    def head_branch(self):
        return self.hook['check_suite']['head_branch']

    @property
    def sha(self):
        return self.hook['check_suite']['head_sha']

    @property
    def check_runs_url(self):
        return "https://api.github.com/repos/{full_name}/check-runs".format(
            full_name=self.hook['repository']['full_name']
        )

    @property
    def archive_url(self):
        return self.hook['repository']['archive_url'].format(**{
            'archive_format': 'tarball',
            '/ref': '/%s' % self.sha,
        })

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

    def get_env(self):
        """
        Return environment but add the file dir to the ``PYTHONPATH``.

        Returns:
            dict: Environment

        """
        env = os.environ.copy()
        PYTHONPATH = 'PYTHONPATH'
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
            log = process.stdout
            logger.debug(log)
            logger.debug('exit %s', process.returncode)
            logger.info(
                'linter exited with status code %s in %ss' % (process.returncode, info.ru_utime)
            )
            return (
                info.ru_utime,
                process.returncode,
                log
            )

    def download_code(self):
        """Download code to local filesystem storage."""
        logger.info('Downloading: %s', self.archive_url)
        try:
            response = self.session.get(self.archive_url, timeout=self.download_timeout)
        except requests.Timeout:
            self.update_check_run(
                COMPLETED, 'Downloading code timed out after %ss' % self.download_timeout,
                TIMED_OUT
            )
            raise
        else:
            response.raise_for_status()
            with BytesIO() as bs:
                bs.write(response.content)
                bs.seek(0)
                path = tempfile.mkdtemp()
                with tarfile.open(fileobj=bs, mode='r:gz') as fs:
                    fs.extractall(path)
                folder = os.listdir(path)[0]
                return os.path.join(path, folder)

    def create_check_run(self, summary):
        data = {
            'name': self.label,
            'head_branch': self.head_branch,
            'head_sha': self.sha,
            'status': IN_PROGRESS,
            'started_at': datetime.datetime.utcnow().isoformat(),
            'output': {
                'title': self.label,
                'summary': summary,
            }
        }
        response = self.session.post(self.check_runs_url, json=data)
        response.raise_for_status()
        self.check_run_url = response.json()['url']

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
            data['completed_at'] = datetime.datetime.utcnow().isoformat()
        response = self.session.patch(self.check_run_url, json=data)
        response.raise_for_status()

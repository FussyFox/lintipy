"""Run static file linters on AWS lambda."""
import json
import logging
import os
import tarfile
import tempfile
import time
from io import BytesIO
from subprocess import Popen, PIPE, STDOUT

import boto3
import jwt
from botocore.vendored import requests

__all__ = ('Handler', 'logger')

logger = logging.getLogger('lintipy')


PUSH_EVENT = 'push'
PULL_REQUEST_EVENT = 'pull_request'


class Handler:
    """Handle GitHub web hooks via SNS message."""

    def __init__(self, label: str, cmd: str, *cmd_args: str,
                 integration_id: str = None, bucket: str = None,
                 region: str = None, pem: str = None):
        self.label = label
        self.cmd = cmd
        self.cmd_args = cmd_args
        self._token = None
        self._hook = None
        self._s3 = None
        self._session = None
        self.event = None
        self.integration_id = integration_id or os.environ.get('INTEGRATION_ID')
        self.bucket = bucket or os.environ.get('BUCKET', 'lambdalint')
        self.region = region or os.environ.get('REGION', 'eu-west-1')

        pem = pem or os.environ.get('PEM', '')
        self.pem = '\n'.join(pem.split('\\n'))

    def __call__(self, event, context):
        """AWS Lambda function handler."""
        self.event = event

        if not self.code_has_changed:
            return  # Do not execute linter.

        data = {
            "state": "pending",
            "context": self.label,
        }
        self.session.post(self.statuses_url, json=data).raise_for_status()
        code_path = self.download_code()
        code, data["target_url"] = self.run_process(code_path)
        logger.info('linter exited with status code %s' % code)

        if code == 0:
            data.update({
                "state": "success",
                "description": "%s succeeded!" % self.cmd,
            })
        else:
            data.update({
                "state": "failure",
                "description": "%s failed!" % self.cmd,
            })
        logger.info('setting final status')
        self.session.post(self.statuses_url, json=data).raise_for_status()

    @property
    def event_type(self):
        return self.event['Records'][0]['Sns']['Subject']

    @property
    def s3(self):
        if self._s3 is None:
            self._s3 = boto3.client('s3', region_name=self.region)
        return self._s3

    @property
    def hook(self):
        if self._hook is None:
            self._hook = json.loads(self.event['Records'][0]['Sns']['Message'])
        return self._hook

    @property
    def full_name(self):
        return self.hook['repository']['full_name']

    @property
    def statuses_url(self):
        return self.hook['repository']['statuses_url'].format(sha=self.sha)

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
    def code_has_changed(self):
        if self.event_type == PUSH_EVENT:
            return True
        elif self.event_type == PULL_REQUEST_EVENT:
            return self.hook['action'] in [
                "opened", "edited", "reopened"
            ]

    @property
    def sha(self):
        if self.event_type == PUSH_EVENT:
            return self.hook['head_commit']['id']
        elif self.event_type == PULL_REQUEST_EVENT:
            return self.hook['pull_request']['head']['sha']

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
            res = requests.post(url, headers=headers)
            res.raise_for_status()
            self._token = res.json()['token']
        return self._token

    @property
    def session(self):
        if not self._session:
            self._session = requests.Session()
            self._session.headers.update({
                'Authorization': 'token %s' % self.token
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
        process = Popen(
            ('python', '-m', self.cmd) + self.cmd_args,
            stdout=PIPE, stderr=STDOUT,
            cwd=code_path, env=self.get_env(),
        )
        process.wait()
        log = process.stdout.read()

        key = os.path.join(self.cmd, self.full_name, "%s.log" % self.sha)
        self.s3.put_object(
            ACL='public-read',
            Bucket=self.bucket,
            Key=key,
            Body=log,
            ContentType='text/plain'
        )
        return (
            process.returncode,
            "https://{0}.s3.amazonaws.com/{1}".format(self.bucket, key),
        )

    def download_code(self):
        """Download code to local filesystem storage."""
        response = self.session.get(self.archive_url)
        response.raise_for_status()
        with BytesIO() as bs:
            bs.write(response.content)
            bs.seek(0)
            path = tempfile.mktemp()
            with tarfile.open(fileobj=bs, mode='r:gz') as fs:
                fs.extractall(path)
            folder = os.listdir(path)[0]
            return os.path.join(path, folder)

#!/usr/bin/env python
# -*- mode:python -*-
# Copyright (c) 2016 Primiano Tucci -- www.primianotucci.com
#
# Redistribution and use in source and binary forms, with or without
# modification, are permitted provided that the following conditions are met:
#
# * Redistributions of source code must retain the above copyright notice, this
#     list of conditions and the following disclaimer.
# * Redistributions in binary form must reproduce the above copyright notice,
#     this list of conditions and the following disclaimer in the documentation
#     and/or other materials provided with the distribution.
# * The name of Primiano Tucci may not be used to endorse or promote products
#     derived from this software without specific prior written permission.
#
# THIS SOFTWARE IS PROVIDED BY THE COPYRIGHT HOLDERS AND CONTRIBUTORS "AS IS"
# AND ANY EXPRESS OR IMPLIED WARRANTIES, INCLUDING, BUT NOT LIMITED TO, THE
# IMPLIED WARRANTIES OF MERCHANTABILITY AND FITNESS FOR A PARTICULAR PURPOSE ARE
# DISCLAIMED. IN NO EVENT SHALL THE COPYRIGHT OWNER OR CONTRIBUTORS BE LIABLE
# FOR ANY DIRECT, INDIRECT, INCIDENTAL, SPECIAL, EXEMPLARY, OR CONSEQUENTIAL
# DAMAGES (INCLUDING, BUT NOT LIMITED TO, PROCUREMENT OF SUBSTITUTE GOODS OR
# SERVICES; LOSS OF USE, DATA, OR PROFITS; OR BUSINESS INTERRUPTION) HOWEVER
# CAUSED AND ON ANY THEORY OF LIABILITY, WHETHER IN CONTRACT, STRICT LIABILITY,
# OR TORT (INCLUDING NEGLIGENCE OR OTHERWISE) ARISING IN ANY WAY OUT OF THE USE
# OF THIS SOFTWARE, EVEN IF ADVISED OF THE POSSIBILITY OF SUCH DAMAGE.

""" git-grive: a git subcommand to push/pull patchsets to/from Google Drive. """

import apiclient
import apiclient.discovery
import argparse
import getpass
import httplib2
import logging
import oauth2client
import oauth2client.client
import oauth2client.tools
import os
import subprocess
import sys
import tempfile
import time


GDRIVE_DIR_NAME = 'git-drive'
DIR_TYPE = 'application/vnd.google-apps.folder'
CUR_DIR = os.path.dirname(os.path.realpath(__file__))

# The secret here for an installed applications is not really a secret.
# It is just scrambled below to prevent silly bots to run out of my quota.
# An attacker can impersonate the app identity in OAuth2 flow. It should be
# fine as long as the redirect_uri associated with the client is localhost
# In that case attacker needs some process running on user's machine to
# successfully complete the flow and grab a token. When you have a malicious
# code running on your machine, you're screwed anyway.
OAUTH2_CLIENT_ID = '857140522958-5dhpnfms5eguvcj8052o8bmf6f8uguj7.apps.googleusercontent.com'
OAUTH2_CLIENT_SECRET = '21qRCbYlu3g4SF59MalgGx1o'[::-1]
OAUTH2_SCOPE = 'https://www.googleapis.com/auth/drive.file'


class GitGDrive(object):
    """git-gdrive main runner"""

    def __init__(self):
        self.gdrive = None
        self.gdrive_dir_id = None
        self.git_path = None
        self.oauth2_credentials = None

    def authorize(self, args):
        cred_file = os.path.expanduser('~/.git-gdrive.credentials')
        cred_store = oauth2client.file.Storage(cred_file)
        credentials = cred_store.get() if args.command != 'auth' else None
        if not credentials or credentials.invalid:
            flow = oauth2client.client.OAuth2WebServerFlow(
                OAUTH2_CLIENT_ID, OAUTH2_CLIENT_SECRET, OAUTH2_SCOPE,
                approval_prompt='force')
            credentials = oauth2client.tools.run_flow(flow, cred_store, args)
            print('Storing credentials to %s' % cred_file)
        return credentials

    def get_or_create_gdrive_dir(self):
        query = ('"root" in parents and trashed=false and mimeType="%s" '
                 'and title="%s"' % (DIR_TYPE, GDRIVE_DIR_NAME))
        fileobj = None
        file_list = self.api.list(q=query, maxResults=1).execute()['items']
        if file_list:
            fileobj = file_list[0]
        else:
            req_body = {'title': GDRIVE_DIR_NAME, 'mimeType': DIR_TYPE}
            fileobj = self.api.insert(body=req_body).execute()
        self.gdrive_dir_id = fileobj['id']

    def guess_revision_range(self):
        upstream = '@{upstream}'
        if self.run_git(['rev-parse', upstream], quiet_on_failure=True) is None:
            print('No upstream, falling back to pushing the most recent commit')
            upstream = 'HEAD^'
        revrange = '%s..HEAD' % upstream
        revlist = self.run_git(['log', r'--format=%h %s [%ae]', revrange])
        revlist = revlist.strip()
        if not revlist:
            print('Failed to build patch list')
            return None
        revlist = revlist.split('\n')
        if len(revlist) > 1:
            print('Uploading a patch consisting of %s commits (%s):' % (
                len(revlist), revrange))
            for row in revlist:
                print('    %s' % row)
            print()
        if len(revlist) > 10:
            ret = input('Continue y/[n]: ')
            if ret != 'y':
                return None
        return revrange

    def push_patch_to_gdrive(self, format_patch_args):
        patch = self.run_git(['format-patch', '--stdout'] + format_patch_args)
        if not patch:
            return 1
        branch = self.run_git(['rev-parse', '--abbrev-ref', 'HEAD']).strip()
        if not branch:
            return 1
        now = time.strftime('%Y-%m-%d_%H-%M')
        title = '%s-%s-%s.patch' % (getpass.getuser(), branch, now)
        print('Uploading /%s/%s' % (GDRIVE_DIR_NAME, title))
        tmpf = tempfile.NamedTemporaryFile(delete=False)
        tmpf.write(patch)
        tmpf.close()
        try:
            media = apiclient.http.MediaFileUpload(tmpf.name,
                                                   mimetype='text/plain')
            req_body = {'title': title, 'parents': [{'id': self.gdrive_dir_id}]}
            fileobj = self.api.insert(body=req_body, media_body=media).execute()
            print('[%s]' % fileobj['alternateLink'])
            print('')
            print('Upload successful. Use "git gdrive pull" to apply.')
            media = None
            fileobj = None
        finally:
          os.unlink(tmpf.name)

    def pull_and_apply_from_gdrive(self):
        query = '"%s" in parents and trashed=false' % self.gdrive_dir_id
        file_list = self.api.list(q=query, orderBy='modifiedDate desc',
                                  maxResults=20).execute()['items']
        if not file_list:
            print('There are no files in GDrive. \'git gdrive push\' first.')
            return 1
        print('Select which file to pull and apply:')
        for num, entry in enumerate(file_list, 1):
            print('    %d) %s' % (num, entry['title']))
        print('')
        file_to_pull = raw_input('Enter id or name, just ENTER to pull 1): ')
        file_to_pull = file_to_pull or '1'
        if file_to_pull.isdigit():
            file_to_pull = file_list[int(file_to_pull) - 1]
        print('Pulling /%s/%s' % (GDRIVE_DIR_NAME, file_to_pull['title']))
        patch_content = self.api.get_media(fileId=file_to_pull['id']).execute()
        tmpf = tempfile.NamedTemporaryFile(delete=False)
        tmpf.write(patch_content)
        tmpf.close()
        try:
            res = self.run_git(['am', '-3', tmpf.name], verbose=True)
            if res is not None:
                print('Patch applied')
            else:
                print('Patch failed. Run \'git am --abort\' to bail out')
        finally:
          os.unlink(tmpf.name)

    def main(self, main_args):
        logging.basicConfig()
        allowed_commands = 'auth | pull | push <optional range>'
        parser = argparse.ArgumentParser(
            description='Push/Pull patchsets to GDrive',
            usage='%(prog)s ' + allowed_commands,
            parents=[oauth2client.tools.argparser])
        parser.add_argument('command', help=allowed_commands)
        args, extra_args = parser.parse_known_args(main_args)

        credentials = self.authorize(args)
        if not credentials:
            return 1
        http = credentials.authorize(httplib2.Http())
        self.gdrive = apiclient.discovery.build('drive', 'v2', http=http)
        self.get_or_create_gdrive_dir()

        if args.command == 'push':
            format_patch_args = extra_args or [self.guess_revision_range()]
            if not format_patch_args or format_patch_args[0] is None:
                return 1
            return self.push_patch_to_gdrive(format_patch_args)

        elif args.command == 'pull':
            return self.pull_and_apply_from_gdrive()

    def run_git(self, args, verbose=False, quiet_on_failure=False):
        cmd = [self.gitcmd] + args
        if verbose:
            print('Running %s' % ' '.join(cmd))
        proc = subprocess.Popen(cmd, stdout=subprocess.PIPE,
                                stderr=subprocess.PIPE)
        (stdout, stderr) = proc.communicate()
        if proc.returncode == 0:
            return stdout
        if not quiet_on_failure:
            print('Failed (code: %d) executing: %s' % (
                proc.returncode, ' '.join(cmd)))
            print(stdout)
            print(stderr)
        return None

    @property
    def api(self):
        return self.gdrive.files()

    @property
    def gitcmd(self):
        if self.git_path:
            return self.git_path
        candidates = ['git', 'git.bat']
        while candidates:
            try:
                self.git_path = candidates[0]
                subprocess.check_output([self.git_path, '--version'])
                return self.git_path
            except Exception as _:
                candidates = candidates[1:]
        self.git_path = None
        print('Could not find a git binary to execute')


def main(args=None):
    if args is None:
        args = sys.argv[1:]
    sys.exit(GitGDrive().main(args))


if __name__ == '__main__':
    main()

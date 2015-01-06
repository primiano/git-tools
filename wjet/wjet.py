#!/usr/bin/env python
# -*- mode:python -*-
# Copyright (c) 2015 Primiano Tucci -- www.primianotucci.com
#
# Redistribution and use in source and binary forms, with or without
# modification, are permitted provided that the following conditions are met:
#
# * Redistributions of source code must retain the above copyright notice, this
#   list of conditions and the following disclaimer.
# * Redistributions in binary form must reproduce the above copyright notice,
#   this list of conditions and the following disclaimer in the documentation
#   and/or other materials provided with the distribution.
# * The name of Primiano Tucci may not be used to endorse or promote products
#   derived from this software without specific prior written permission.
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

import httplib
import logging
import multiprocessing
import os
import sys
import time
import zlib
import optparse


#TODO detect hung connections.

_ZLIB_WINDOW_BUFFER_SIZE = 16 + zlib.MAX_WBITS


class DownloadManyException(Exception):
  pass


class DownloadJobResult:
  def __init__(self, remote_path, local_path):
    self.remote_path = remote_path
    self.local_path = local_path
    self.bytes_downloaded = 0
    self.bytes_written = 0
    self.error = 0  # TODO restructure


def _GetCurrentWorker():
  return multiprocessing.current_process()


def _InitWorker(host):
  _GetCurrentWorker()._http_host = host
  _ResetConnectionForCurrentWorker()


def _ResetConnectionForCurrentWorker():
  worker = _GetCurrentWorker()
  try:
    worker._http_conn.close()
  except:
    pass

  proxy = os.getenv('GITCS_PROXY')
  worker._http_req_prefix = ''
  if proxy:
    worker._http_conn = httplib.HTTPSConnection(proxy)
    worker._http_req_prefix = worker._http_host
  elif worker._http_host.startswith('https://'):
    worker._http_conn = httplib.HTTPSConnection(worker._http_host[8:])
  else:
    host = worker._http_host.replace('http://', '')
    worker._http_conn = httplib.HTTPConnection(host)


# TODO Wrap and return error as part of the job result
def _DownloadWorkerJob(args):
  IO_BLOCK_SIZE = 16384
  remote_path, local_path = args
  res = DownloadJobResult(remote_path, local_path)
  worker = _GetCurrentWorker()
  for retry_backoff_sec in [0.1, 0]:  ###################################### [0.5, 2, 5]
    conn = worker._http_conn
    conn.request('GET', worker._http_req_prefix + remote_path,
        headers={'Connection': 'keep-alive', 'Accept-Encoding': 'gzip'})
    resp = conn.getresponse(buffering=True)

    # Retry logic.
    if resp.status != httplib.OK:
      res.error = resp.status
      if resp.status == 404:
        resp.read()  # Unblock for the next request.
        break  # Very unlikely that retrying will help in this case.
      # logging.warning('Request %s failed with error %s, retrying in %s sec.',
      #     remote_path, resp.status, retry_backoff_sec)
      time.sleep(retry_backoff_sec)
      _ResetConnectionForCurrentWorker()
      continue

    zdec = None
    if resp.getheader('content-encoding') == 'gzip':
      zdec = zlib.decompressobj(_ZLIB_WINDOW_BUFFER_SIZE)
    with open(local_path, 'wb') as local_fd:
      while True:
        data = resp.read(IO_BLOCK_SIZE)
        data_len = len(data)
        if data_len:
          res.bytes_downloaded += data_len
          if zdec:
            dec_data = zdec.decompress(data)
          else:
            dec_data = data
          res.bytes_written += len(dec_data)
          local_fd.write(dec_data)
        if data_len < IO_BLOCK_SIZE:
          if zdec:
            dec_data = zdec.flush()
            local_fd.write(dec_data)
            res.bytes_written += len(dec_data)
          break
  return res


def DownloadMany(host, iterable, jobs=8):
  pool = multiprocessing.Pool(jobs, initializer=_InitWorker, initargs=[host])
  for job_result in pool.imap_unordered(_DownloadWorkerJob, iterable):
    yield job_result
  pool.close()
  pool.join()


def _StdinReader():
  while True:
    line = sys.stdin.readline().rstrip('\r\n')
    if not line:
      break
    parts = line.split(' ', 2)
    if len(parts) != 2 or not parts[0].startswith('/'):
      print 'Malformed input line, skipping:\n' + line + '\n'
    else:
      yield parts


def _SignalHandler(_signal, _frame):
  cur_proc = multiprocessing.current_process()
  if not (cur_proc and cur_proc.daemon):
    sys.stderr.write('\nAborted by user!\n')
    sys.exit(0)


def main():
  import signal
  signal.signal(signal.SIGINT, _SignalHandler)

  parser = optparse.OptionParser(usage='%prog [options] host')
  parser.add_option('-j', '--jobs', type='int', default=None)
  options, args = parser.parse_args()
  if len(args) != 1:
    parser.print_usage()
    return 1
  host = args[0]

  completed = 0
  total_bytes_downloaded = 0
  total_bytes_written = 0
  errors = 0
  start_time = time.time()

  print 'Reading /remote/path /local/path tuples from stdin'
  print ''
  print '  Completed  | Down. [MB] | Speed [MB/s] | Z.ratio | Errors '
  print '-------------+------------+--------------+---------+--------'
  last_stats_update = 0
  for res in DownloadMany(host, _StdinReader(), options.jobs):
    total_bytes_downloaded += res.bytes_downloaded
    total_bytes_written += res.bytes_written
    if not res.error:
      completed += 1
    else:
      errors += 1
    now = time.time()
    if now - last_stats_update > 0.25:
      last_stats_update = now
      time_elapsed = max(now - start_time, 0.001)
      mb = total_bytes_downloaded / 1048576.0
      compr_ratio = 1.0 * total_bytes_written / max(total_bytes_downloaded, 1)
      print '\r%12d |%11.2f |%13.2f |%8.2f |%7d' % (completed,
                                                    mb,
                                                    mb / time_elapsed,
                                                    compr_ratio,
                                                    errors)

  return 0 if not errors else 1


if __name__ == '__main__':
  sys.exit(main())

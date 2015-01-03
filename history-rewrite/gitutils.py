# -*- mode:python -*-
# Copyright (c) 2014 Primiano Tucci -- www.primianotucci.com
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

"""
A set of helper functions to read / write Git loose objects.
"""

import hashlib
import os
import subprocess
import zlib


class SHA1:
  def __init__(self, value, hexvalue=None):
    assert len(value) == 20
    self.raw = value
    self.hex = value.encode('hex') if hexvalue is None else hexvalue
    assert len(self.hex) == 40

  @staticmethod
  def FromHex(hexvalue):
    return SHA1(hexvalue.decode('hex'), hexvalue)

  @staticmethod
  def HexToRaw(hexvalue):
    return hexvalue.decode('hex')

  @staticmethod
  def RawToHex(value):
    return value.encode('hex')


def Makedirs(path):
  try:
    os.makedirs(path)
  except OSError:
    pass


def WriteFileAtomic(file_path, data):
  tmp_path = '%s-%s.tmp' % (file_path, os.getpid())
  with open(tmp_path, 'wb') as f:
    f.write(data)
  os.rename(tmp_path, file_path)


def WriteGitObj(objtype, payload, objdir):
  data = ('%s %d\x00' % (objtype, len(payload))) + payload
  hasher = hashlib.sha1()
  hasher.update(data)
  sha1 = SHA1(hasher.digest())
  #basedir = os.path.join(_gitdir, 'objects', sha1.hex[0:2])
  basedir = os.path.join(objdir, sha1.hex[0:2])
  objpath = os.path.join(basedir, sha1.hex[2:])
  if not os.path.exists(objpath):
    Makedirs(basedir)
    WriteFileAtomic(objpath, zlib.compress(data, 1))
  return sha1


def ReadGitObj(sha1, objdir):
  assert(isinstance(sha1, SHA1))
  objpath = os.path.join(objdir, sha1.hex[0:2], sha1.hex[2:])
  with open(objpath, 'rb') as fin:
    data = zlib.decompress(fin.read())
  headlen = data.index('\x00')
  objtype, objlen = data[:headlen].split()
  objlen = int(objlen)
  payload = data[headlen + 1:]
  assert(len(data) == objlen + headlen + 1)
  return objtype, objlen, payload


def CopyGitBlobIntoFile(sha1, file_path, objdir):
  assert(isinstance(sha1, SHA1))
  objtype, _, data = ReadGitObj(sha1, objdir)
  assert(objtype == 'blob')
  WriteFileAtomic(file_path, data)


def ReadGitTree(sha1, objdir):
  """Returns a sorted list of tupled (mode, fname, sha1)"""
  objtype, _, data = ReadGitObj(sha1, objdir)
  #print 'READING ', sha1.hex, objtype
  assert(objtype == 'tree')
  s = 0
  entries = []
  while s < len(data):
    s1 = data.find(' ', s)
    s2 = data.find('\0', s)
    mode = data[s:s1]
    fname = data[(s1+1):s2]
    sha1 = SHA1(data[(s2+1):(s2+21)])
    s = s2 + 21
    entries.append((mode, fname, sha1))
  return entries


def _GitTreeEntryGetSortKey(entry):
  if entry[0][-5:-3] == '40':  # mode starts with 04 -> entry is a subtree.
    return entry[1] + '/'  # Awkward Git sorting legacy bug. See goo.gl/Xfh0BX.
  else:
    return entry[1]


def WriteGitTree(entries, objdir):
  payload = ''
  for e in sorted(entries, key=_GitTreeEntryGetSortKey):
    payload += e[0] + ' ' + e[1] + '\x00' + e[2].raw
  return WriteGitObj('tree', payload, objdir)


def GetCurGitDir():
  return subprocess.check_output(
      ['git', 'rev-parse', '--git-dir']).strip('\r\n')

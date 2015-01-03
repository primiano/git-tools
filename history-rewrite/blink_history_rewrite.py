#!/usr/bin/env python
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

"""Rewrite the history of Blink, moving large files out of the repo.

For each binary file (png, mp3, ...) under /LayoutTests:
 - The file is copied to DIRS.GCS/sha1.blob, where sha1 == Git SHA1 for the blob
   (i.e. files in the GCS have the same SHA1 of the blobs in the original repo).
 - The file.png is removed from the tree.
 - A new blob, named orig_filename.png.gitcs, is inserted in the tree. This
   file contains a ref to the final GCS bucket (gs://blink-gitcs/sha1.blob).

Furthermore, the /LayoutTests/.gitignore file is changed (or created) in order
to add ignore the _BIN_EXTS below.
"""

import multiprocessing
import os
import subprocess
import sys
import time
import traceback

sys.path.append(os.path.abspath(os.path.dirname(__file__)))
from gitutils import *


_SKIP_COPY_INTO_CGS = False

_BIN_EXTS = ({'.aif', '.bin', '.bmp', '.cur', '.gif', '.icm', '.ico', '.jpeg',
              '.jpg', '.m4a', '.m4v', '.mov', '.mp3', '.mp4', '.mpg', '.oga',
              '.ogg', '.ogv', '.otf', '.pdf', '.png', '.sitx', '.swf', '.tiff',
              '.ttf', '.wav', '.webm', '.webp', '.woff', '.woff2', '.zip'})

class DIRS:
  # The .git/objects dir containing the original loose objects.
  ORIGOBJS = None  # Will be figured out at runtime using git --git-dir.

  # Where the new git objects (trees, blobs) will be put.
  NEWOBJS = '/mnt/git-objects/'

  # Where the binary files (then uploaded to GCS) will be moved.
  GCS = '/mnt/gcs-bucket/'


# _tree_cache is a map of tree-ish -> translated tree-ish and is used to avoid
# re-translating sub-trees which are identical between subsequent commits
_tree_cache = multiprocessing.Manager().dict()

# A map of <original tree SHA1 (hex)> -> <translated SHA1> for top-level trees.
# Contains the results of _TranslateOneTree calls.
_root_trees = multiprocessing.Manager().dict()

# set of SHA1s (just to keep the total count)
_gcs_blobs = multiprocessing.Manager().dict()


def _BuildGitignoreMaybeCached(base_sha1=None):
  cache_key = base_sha1.raw if base_sha1 else 'blank'
  cached_gitignore = _tree_cache.get(cache_key)
  if cached_gitignore:
    return SHA1(cached_gitignore)
  else:
    gitignore = ''
    if base_sha1:
      gitignore = ReadGitObj(base_sha1, DIRS.ORIGOBJS)[2] + '\n'
    gitignore += '\n'.join(('*' + x for x in sorted(_BIN_EXTS))) + '\n'
    sha1 = WriteGitObj('blob', gitignore, DIRS.NEWOBJS)
    collision = _tree_cache.setdefault(cache_key, sha1.raw)
    assert(collision == sha1.raw)
    return sha1


def _MangleTree(root_sha1, in_tests_dir=False, indent=0):
  assert(isinstance(root_sha1, SHA1))
  changed = False
  entries = []
  cached_translation = _tree_cache.get(root_sha1.raw)
  if cached_translation:
    return SHA1(cached_translation)

  # if indent == 0: print '\n', root_sha1.hex
  base_gitignore_sha1 = None
  for mode, fname, sha1 in ReadGitTree(root_sha1, DIRS.ORIGOBJS):
    old_sha1_raw = sha1.raw
    # if indent == 0: print '  ', mode, fname
    if mode[0] == '1':  # It's a file
      _, ext = os.path.splitext(fname)
      if in_tests_dir:
        if indent == 1 and fname == '.gitignore':
          base_gitignore_sha1 = sha1
          continue  # Will be added below
        elif ext.lower() in _BIN_EXTS:
          csfname = sha1.hex + '.blob'
          _gcs_blobs[sha1.raw] = 1
          if not _SKIP_COPY_INTO_CGS:
            CopyGitBlobIntoFile(sha1, DIRS.GCS + csfname, DIRS.ORIGOBJS)
          csref = 'src gs://blink-gitcs/' + csfname + '\n'
          sha1 = WriteGitObj('blob', csref, DIRS.NEWOBJS)
          fname += '.gitcs'
          changed = True
    else:
      assert(mode == '40000')
      if in_tests_dir or fname.endswith('Tests'):
        sha1 = _MangleTree(sha1, True, indent + 1)
        changed = True if old_sha1_raw != sha1.raw else changed
    entries.append((mode, fname, sha1))

  # Now add .gitignore in the right place.
  if in_tests_dir and indent == 1:
    # base_gitignore_sha1 maybe None if not present in the original tree.
    new_gitignore_sha1 = _BuildGitignoreMaybeCached(base_gitignore_sha1)
    entries.append(('100644', '.gitignore', new_gitignore_sha1))
    changed = True

  if changed:
    res = WriteGitTree(entries, DIRS.NEWOBJS)
  else:
    res =  root_sha1
  collision = _tree_cache.setdefault(root_sha1.raw, res.raw)
  assert(collision == res.raw)
  return res


def _TranslateOneTree(treeish):
  try:
    # Do not bother checking if we already translated the tree. It is extremely
    # unlikely (i.e. empty commits) and is not worth the overhead of checking.
    mangled_sha1 = _MangleTree(SHA1.FromHex(treeish))
    collision = _root_trees.setdefault(treeish, mangled_sha1.hex)
    assert(collision == mangled_sha1.hex)
  except Exception as e:
    sys.stderr.write('\n' + traceback.format_exc())
    raise


def _TimeToStr(seconds):
  tgmt = time.gmtime(seconds)
  return time.strftime('%Hh:%Mm:%Ss', tgmt)


def _RewriteTrees(trees):
  pool = multiprocessing.Pool(int(multiprocessing.cpu_count() * 2))

  pending = len(trees)
  done = 0
  tstart = time.time()
  checkpoint_done = 0
  checkpoint_time = tstart
  for _ in pool.imap_unordered(_TranslateOneTree, trees):
    done += 1
    now = time.time()
    done_since_checkpoint = done - checkpoint_done
    if done == pending or (done & 63) == 1:
      compl_rate = (now - checkpoint_time) / done_since_checkpoint
      eta = _TimeToStr((pending - done) * compl_rate)
      print '\r%d / %d Trees rewritten (%.1f trees/sec), ETA: %s      ' % (
          done, pending, 1 / compl_rate, eta),
      sys.stdout.flush()
    # Keep a window of the last 5s of rewrites for ETA calculation.
    if now - checkpoint_time > 5:
      checkpoint_done = done
      checkpoint_time = now

  pool.close()
  pool.join()
  elapsed = time.time() - tstart
  print '\nTree rewrite completed in %s (%.1f trees/sec)' % (
      _TimeToStr(elapsed), done / elapsed)
  print 'Extracted %d files into %s' % (len(_gcs_blobs), DIRS.GCS)


def _RewriteCommits(revs):
  root_trees = _root_trees.copy()  # Un-proxied local copy for faster lookups.
  total = len(revs)
  done = 0
  last_parent = None
  last_rewritten_parent = None
  tstart = time.time()
  for rev in revs:
    objtype, objlen, payload = ReadGitObj(SHA1.FromHex(rev), DIRS.ORIGOBJS)
    assert(objtype == 'commit')
    assert(payload[0:5] == 'tree ')  # A commit obj should begin with a tree ptr
    orig_tree = payload[5:45]
    new_tree = root_trees[orig_tree]
    assert(len(new_tree) == 40)
    new_payload = 'tree ' + new_tree + '\n'
    parent = None
    if not last_parent:
      assert(payload[46:52] != 'parent')
      new_payload += payload[46:]
    else:
      assert(payload[46:52] == 'parent')
      parent = payload[53:93]
      assert(parent == last_parent)
      new_payload += 'parent ' + last_rewritten_parent + '\n'
      new_payload += payload[94:]

    last_parent = rev
    sha1 = WriteGitObj('commit', new_payload, DIRS.NEWOBJS)
    last_rewritten_parent = sha1.hex
    done += 1
    if done % 100 == 1 or done == len(revs):
      compl_rate = (time.time() - tstart) / done
      eta = _TimeToStr((total - done) * compl_rate)
      print '\r%d / %d Commits rewritten (%.1f commits/sec), ETA: %s      ' % (
          done, total, 1 / compl_rate, eta),
      sys.stdout.flush()

  print '\n'
  print 'Your new HEAD is %s (which replaced %s)' % (last_rewritten_parent,
                                                     last_parent)


def main():
  print 'New git objects:', DIRS.NEWOBJS
  Makedirs(DIRS.NEWOBJS)

  DIRS.ORIGOBJS = os.path.join(GetCurGitDir(), 'objects') + '/'
  print 'Orig objects:', DIRS.ORIGOBJS

  if not _SKIP_COPY_INTO_CGS:
    print 'GCS staging area:', DIRS.GCS
    Makedirs(DIRS.GCS)
  else:
    print 'WARNING: Omitting GCS object generation.'

  print ''
  revs = []
  trees = []

  if len(sys.argv) > 1:
    print 'Reading cached rev-list + trees from ' + sys.argv[1]
    reader = open(sys.argv[1])
  else:
    cmd = ['git', 'rev-list', '--format=%T', '--reverse', 'master']
    print 'Running [%s], might take a while' % ' '.join(cmd)
    proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, bufsize=1048576)
    reader = proc.stdout

  # The output of rev-list will be as follows:
  # commit abcdef1234 <- commit-ish (irrelevant for us here)
  # 567890abcdef      <- tree-ish
  # commit ....
  while True:
    line = reader.readline()
    if not line:
      break
    line = line.rstrip('\r\n')
    if line.startswith('commit'):
      rev = line[7:]
      assert(len(rev) == 40)
      revs.append(rev)
      continue
    else:
      assert(len(line) == 40)
      trees.append(line)

  assert(len(revs) == len(trees))
  print 'Got %d revisions to rewrite' % len(revs)


  print '\nStep 1: Rewriting trees in parallel'
  _RewriteTrees(trees)

  print '\nStep 2: Rewriting commits serially'
  _RewriteCommits(revs)

  print 'You should now run git fsck NEW_HEAD_SHA. You are a fool if you don\'t'


if __name__ == '__main__':
  sys.exit(main())

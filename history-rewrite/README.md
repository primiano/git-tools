How to rewrite the history of 170k commits and 2.8 million objects in 19 minutes
with ~300 lines of python code.

The adventures of a Git pervert, a python fanatic and a performance freak.


Abstract
--------
I recently faced the challenging problem of rewriting, pretty much for fun, the
history of [a very large project](http://chromium.org/blink) (170k commits,
2.8 million objects, 5GB compressed).

The rewrite process consisted of replacing binary files in the repo (.png and
other extensions, circa 50k per revision) with textual URLs.
`git-filter-branch` is the reference tool for doing these kinds of operations.
Unfortunately, for various reasons explained in this post, git-filter-branch
doesn't scale (it would have taken ~months on a bulky machine).
The nature of the problem makes it not (easily) suitable for tools like
[BFG](http://rtyley.github.io/bfg-repo-cleaner/), which is able to easily scale
with simpler instances of the problem.

This post is a collection of tricks, notes and scripts which I figured out to
rewrite Git history in a parallel and efficient way (avg. rate of ~150 commits
per second) with 300 lines of python (and just standard libs).

Due to the high level of black magic involved (writing git objects without
using git), I do not promote nor endorse this as a general solution, as there
is a high change to screw up your repo (even though, for cases like mine, it
might be the only option left).

Given the number of git internals and parallelization tricks being
discussed, I hope this can be an interesting reading to (re)discover the beauty
of python and the simplicity of Git.

This post assumes a very solid understanding of the Git object model. I suggest
[this short read from the Git community book](http://schacon.github.io/gitbook/1_the_git_object_model.html)
as a prerequisite.

[Link to the source code](https://github.com/primiano/git-tools/tree/master/history-rewrite)

Problem statement
-----------------
The problem I am trying to solve here is the following: the Blink repo has been
historically been stuffed with hundreds of thousands of binary files (e.g.,
.png, and others), which got updated fairly frequently.
This is notoriously bad for Git.

These days I was wondering: what if we always kept those binaries out of the
repo (keeping just a ref / URL in Git) and fetched separately using alternate
techniques such as
[Git Annex](http://git-annex.branchable.com/),
[Git FAT](https://github.com/jedbrown/git-fat) or
[Git Media](https://github.com/alebedev/git-media) ?
What would have been the size and performance characteristics of the repo if we
used them through the years?

The specific large file offloading technique is beyond the scope of this post.
For the sake of this reading, my goal is replacing all these binary files in
the repo with textual urls.
More in details, what I want to achieve is reconstructing a similar repo with an
identical history (w.r.t commits and authors) modulo the following tree / blob
modifications:
 - Replace each .png file (and other 30 exts.) introduced by each commit
   (but only under the /*Tests/ dirs) with a cheap reference file containing a
   URL (pointing to a GCS bucket).
 - Add the 31 binary extensions to each .gitignore files under the /*Tests/ dirs
   (/LayoutTests/.gitignore, /ManualTests/.gitignore ...), creating the file if
   not existing.

The challenge is: how to achieve this in a reasonable timeline? Git history
rewriting is an extremely CPU and I/O intensive operation and can be time
consuming, even on a bulky (20 cores, 64 GB RAM) machine.


Failed attempts with git-filter-branch
--------------------------------------
[git-filter-branch](http://git-scm.com/docs/git-filter-branch) is the reference
tool for playing with Git history. It provides mainly three rewiring methods:

**--tree-filter** is, conceptually, the most straightforward way of mangling
files and their contents. For each commit `git-filter-branch` checks out a
working tree, runs a user-provided script, and records the state of the working
directory after the script execution. This would fit the problem I am trying to
solve but has one problem: it is I/O intensive and hence terribly slow for a
large repo.

In my specific case, a working tree for the Blink repo consists of ~173 k files
for a total of ~1.2 GB. The overall process (checkout + script + record tree)
takes approximately 1 min. a 2xSSD RAID0, which gives an ETA for the overall
rewrite process of:
`1 min x 170k commits = 118 days` (sad face).


**--index-filter** is a faster variant of the former which avoids checking out
any file on the disk. Similarly to the previous case it executes a user-provided
script for each commit. The difference is that, this time, the script is
expected to just mangle the index, using `git-add/rm --cached` or
[git-update-index](http://git-scm.com/docs/git-update-index).

Sadly, due to the high cardinality of files/dirs per single commit, the
index-equivalent filtering operations would have speed up the process up to just
5-10 s. per commit. A good relative improvement, but still a no-go as it gives
an ETA of ~1 month.

**--parent-filter** allows to replace the tree object of each commit with a
brand new one. The new trees must be reconstructed recursively using the lowest
level git plumbing commands such as `git-mktree` and `git-hash-object`.
Unfortunately, this doesn't scale that much compared to the rewriting based on
`--index-filter`.


###Bottlenecks of git-filter-branch
The following factors slowed down my attempts of using `git-filter-branch`:

**Lack of parallelism:** git-filter-branch rewrites at most one commit at a
time. It is a sensible limitation considering its relatively simple interface
but it just doesn't scale.

**Delta-compression:** Git stores objects in large, delta-compressed archives
called packfiles. This is great for day by day use, as it reduces sensibly
the disk space required. However, for heavy object I/O, having to walk the delta
chains causes non-negligible overheads.

**Disk I/O:** even with a 2xSSD RAID and a large availability of page-cache,
performing a high number of writes (to store the rewritten reference blobs and
corresponding trees) causes still noticeable I/O bottlenecks.

**Process overhead:** another significant contribution is brought by the
overhead incurred when issuing a storm of git plumbing commands.
Some of them (as `git-mktree`) support --batch mode to mitigate this issue.
Unfortunately, at the time of writing, `git-hash-object` (which is required to
replace the .png files with the .png.gitcs refs) doesn't.
Given the high number of binary files per tree I plan to rewrite (~50k per
commit), even a few ms. of process startup adds significant churn.


Performance tips
----------------
How to scale 4-5 orders of magnitude? In the light of the considerations above,
these tricks turned out to be highly useful:

### Use tmpfs as a scratch disk:
The more I read trough kernel sources, the more I amazed by the simplicity and
the efficiency of
[tmpfs](https://www.kernel.org/doc/Documentation/filesystems/tmpfs.txt).
It is a good way to get a truly no-barriers fs and speed up writes.

I don't have benchmarking data to support my next statement, but I am
strongly convinced that even when a tmpfs mount-point can't fit in RAM (i.e.
size >> Total Memory) tmpfs + swap outperforms any combination of writeback /
nobarrier / commit ext4 options.
The con is evident: it is not persistent (e.g., in the case of a power loss).
For the sake of my goal, it is a reasonalbe tradeoff, considering that I need
it only for a couple of hours.

    # /mnt will be my scratch disk.
    $ mount -t tmpfs none /mnt -o size=10g,noatime,nr_inodes=0


### Decompress pack files:
Getting rid of pack files speeds up things. You can ask git to uncompress all
the packs into individual loose objects (one file per blob / tree / commit).
This increases noticeably the size on the disk (5 GB -> ~80 GB) but speeds up
noticeably random R/W cycles.

    # This is the original 5 GB compressed repo.
    $ ls -Rlh /d/blink-original.git/objects/
    ...
    total 5.2G
    5.1G pack-1d84ab914a0cde37f8ced92cc62f863de38e215b.pack

Create a new empty repo and run `git unpack-objects` (*must be run a different
repo as it will not generate loose files for objects that are already part of a
.pack.*)

    $ mkdir /d/blink-loose.git
    $ cd /d/blink-loose.git
    $ git init --bare

    # Let's not get GC in our way
    $ git config gc.auto 0

    # Enable standalone gzip compr to loose objects.
    $ git config --global core.loosecompression 5

    # This can speed up pack files walking.
    $ git config core.deltaBaseCacheLimit 1G

    # Decompress all the packs (takes 1-2 hours)
    $ for P in $(find /d/blink-original.git -name '*.pack'); do \
      git unpack-objects < "$P"; \
      done


At this point, the objects/ folder has been filled with the 2.8 million files.
Note that git uses a two-level fs structure to store objects. For instance, the
object `9038fef784dacafdcfdce03fb12b90647bb52d2e` ends up into
`objects/90/38fef784dacafdcfdce03fb12b90647bb52d2e`.


Mangling git objects in python
------------------------------
The loose objects expansion paves the way to very powerful and easy rewrites.
Warm up you engines, here comes the most tough piece of git black magic: writing
 Git objects.

[This page of the Git Community Book](http://schacon.github.io/gitbook/1_the_git_object_model.html)
has an excellent (visual and textual) explanation of the object model for
blobs (files), trees (dirs) and commits.
I have only a few remarks worth adding:

**Objects are immutable by nature**: if you should only create new loose files,
never ever change the content of an existing one.

**Loose objects are always gzip-compressed**, even when
`loosecompression = 0`, in which case they are just deflate'd. Not a great deal,
this will require extra 4-5 lines of python.

**Objects headers**: every object (post gzip decompression) starts with a header
of the following form: `objtype content_size\x00<content>`, which is also the
reason why a blob-ish (`git hash-object file`) != `sha1sum file`.

**Entries in a tree are sorted** and the sorting logic is terribly awkward.
Define awkward?
*If you have two blobs in a tree, named foo and foo-bar, their correct ordering
is [foo, foo-bar], as one would naturally expect.
If you have two subtrees in a tree, with the same names, the correct ordering
is [foo-bar, foo] (^__^).*
The short version of the story is that, for legacy reasons, tree-entries are
sorted as if their names ends with a trailing slash (but the name in the parent
tree object does NOT have a trailing slash).
If you are curious about more details (or just not convinced) Linus gave an
explanation of the reasons
[in this post](http://www.spinics.net/lists/git/msg25856.html).

### Python modules to write Git objects
The beauty of Git's object model lies in its extreme simplicity. Blobs and trees
can be parsed with a dozen lines of python. There are libraries such as
[GitPython](https://pythonhosted.org/GitPython/) for doing that.
I took the fun of writing a small and fast one of my own, which you can find
here:

[github.com/primiano/git-tools/blob/master/history-rewrite/gitutils.py](https://github.com/primiano/git-tools/blob/master/history-rewrite/gitutils.py)

It is ~100 lines of python and very easy to read and understand.


Parallelizing the rewrite
-------------------------
I know, this might be puzzling: *"This is nonsense. Git objects are immutable.
Rewriting the history is, per its nature, non parallelizable"*.
This is not entirely true, though. Let's first visualize the problem:

         +--------------+                             +--------------+
         | commit c001  |      <-    ...      <-      | commit c999  |
         +--------------+                             +--------------+
         | tree: d001   |                             | tree: d999   |
         | (no parent)  |                             | parent: c998 |
         +--------------+                             +--------------+
                |                                             |
       +------------------+                         +------------------+
       |    Tree d001     |                         |    Tree d999     |
       +------------------+                         +------------------+
       | f001 README.txt  |                         | f004 README.txt  |
       | f002 file1.png   |                         | f005 file1.png   |
       | d002 subdir      |                         | d181 subdir      |
       +------------------+                         +------------------+
                 \                                           /
               +------------------+         +------------------+
               |     Tree d002    |         |    Tree d181     |
               +------------------+         +------------------+
               | f003 file2.png   |         | f006 file2.png   |
               +------------------+         | f007 file3.png   |
                                            +------------------+

### Commits rewritings are not parallelizable
The true part is that no subsequent commits can be produced until *Commit #1*
has been produced (and so on).
C#2 needs the SHA1 of C#1 to model the `parent: <C#1 SHA1>`
relationship. Hence the commit-ish of C#1 depends on the commit-ish of C#2.

The good news is that commits themselves are very quick to rewrite. It takes
a bunch of seconds if we have the trees ready.

### Trees rewritings *are* parallelizable
The juicy part is rewriting trees: this typically involves much more I/O (create
a new file for each new blob and a new object for each modified sub-tree)
and much more computation. The good news is that this process is
uber-parallelizable.

(Root) trees are independent of each other. Eventually they can share some
common sub-trees. However, given the immutable nature of Git objects, this is
not a blocker: if we produce the same changes to a common sub-tree, their
resulting tree-ishes will be identical and end up in the same file: (we might
eventually waste time double-translating some sub-trees, but that the result
will be correct); if we produce different changes to a common sub-tree, the
resulting tree-ishes will be different and they will point to different files.
If we want to get really fancy, we can then keep a shared cache of translated
trees to further speed up the process.

As a matter of facts, the python code looks very straightforward:

    def _RewriteTrees(trees):  # a list of root tree-ishes [d001...d999]
      pool = multiprocessing.Pool(multiprocessing.cpu_count() * 2)
      for _ in pool.imap_unordered(_TranslateOneTree, trees):
        done += 1
        if (done % 100) == 0 or done == pending:
          print '\r%d / %d Trees rewritten' (done, pending),
          sys.stdout.flush()

Where `_TranslateOneTree` contains the logic that strips out the .png files:

    def _TranslateOneTree(root_sha1, in_tests_dir=False):
      for mode, fname, sha1 in ReadGitTree(root_sha1, DIRS.ORIGOBJS):
        if mode[0] == '1':  # It's a file
          _, ext = os.path.splitext(fname)
          if in_tests_dir:
            if ext.lower() == '.png':
              csfname = sha1.hex + '.blob'
              CopyGitBlobIntoFile(sha1, DIRS.GCS + csfname, DIRS.ORIGOBJS)
              csref = 'src gs://blink-gitcs/' + csfname + '\n'
              sha1 = WriteGitObj('blob', csref, DIRS.NEWOBJS)
              fname += '.gitcs'
              changed = True
        else:  # It's a sub-tree.
          assert(mode == '40000')
          if in_tests_dir or fname.endswith('Tests'):
            old_sha1_raw = sha1.raw
            sha1 = _MangleTree(sha1, in_tests_dir=True)
            changed = True if old_sha1_raw != sha1.raw else changed
        entries.append((mode, fname, sha1))

      if changed:
        return WriteGitTree(entries, DIRS.NEWOBJS)
      else:
        return root_sha1

You can find the complete code, with more tricky edge cases and optimizations,
here:

### [github.com/primiano/git-tools/blob/master/history-rewrite](https://github.com/primiano/git-tools/blob/master/history-rewrite/)

Which produces this awesome rewrite:

    $ ~/blink_history_rewrite.py
    Got 168352 revisions to rewrite

    Step 1: Rewriting trees in parallel
    Tree rewrite completed in 00h:19m:07s (146.7 trees/sec)
    Extracted 258947 files into /mnt/gcs-bucket/

    Step 2: Rewriting commits serially
    168352 / 168352 Commits rewritten (8216.1 commits/sec), ETA: 00h:00m:00s

    Your new head is 0b5424070e3006102e0036deea1e2e263b871eaa
    (which corresponds to 88ac847721047fbc15ee964887bb23fc3cd6a34c)


It will write the new git objects for the rewritten history into
/mnt/git-objects.

First of all let's run a git fsck:

    $ export GIT_ALTERNATE_OBJECT_DIRECTORIES=/mnt/git-objects
    $ git fsck --strict 0b5424070e3006102e0036deea1e2e263b871eaa

At this point this can become new master:

    $ git update-ref refs/heads/master 0b5424070e3006102e0036deea1e2e263b871eaa

And the new repo can be finally repacked:

    $ git reflog expire --expire=now --all
    $ git repack -a -d

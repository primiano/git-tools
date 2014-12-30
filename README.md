#git-tools
A collection of git tools.

##git-gradual-push
git-gradual-push, as the name suggests, allows to push large revision ranges
in chunks (read: generating multiple push operations). This is to deal with
pushes of really large repos which cannot get digested by the Git server in a
single push.
It works by figuring out the total number of revisions, splitting it in chunks
of arbitrary size (4th arg), and issuing individual git push commands with the
intermediate revisions calculated.

### Example
    $ git-gradual-push origin HEAD refs/heads/master 10000
    Remember to: git config credential.helper 'cache --timeout=3600' to avoid surprises
    Reading rev-list for branch  HEAD
    Got 168278 revisions. Uploading in 17 chunks

    Push [1/17] (158278-th) 8f1021083ad8 -> refs/hidden/tmpupload
    + 4eb5bbc...8f10210 8f1021083ad8a887413ca373a3fa0dfa74e45f53 -> refs/hidden/tmpupload
    ...

    Push [11/17] (58278-th) 10d67c852902 -> refs/hidden/tmpupload
    Counting objects: 203744, done.
    Delta compression using up to 8 threads.
    Compressing objects: 100% (61768/61768), done.
    Writing objects: 100% (184761/184761), 48.62 MiB | 4.09 MiB/s, done.
    Total 184761 (delta 143752), reused 154745 (delta 121893)


##git-inspect-packs
git-inspect-packs is a low level tool to inspect git pack files.

Given a set of pack files (the tools operates on a directory, loading all the
packs in it), it can report:
  - The list of commits present in the pack files.
  - The subset of root commits (i.e. commits which have no parents in the
    pack files being inspected).
  - The cardinality and size of the blobs referenced by each commit.
  - The list of trees present in the pack files.
  - The subset of orphan trees (trees not referenced by any commit in the
    pack files being inspected)
  - The list of blobs.
  - The list of orphan blobs (blobs not referenced by any tree in the pack files
    being inspected).

It can be used to debug and inspect the content provided by a given set of
pack files. Conversely to most git operations it is designed to deal with
incomplete packs (i.e. references to missing objects).
The use case driving the development of this script has been debugging
anomalous packs being downloaded and exploding their content (and doing some
graph math on it).

### Example

    $ ls
    pack-d0c10508154396e8ae03a18a47f6fd0da97562fa.idx
    pack-d0c10508154396e8ae03a18a47f6fd0da97562fa.pack

    # See git-inspect-packs --help for more options
    $ git-inspect-packs --all --verbose

    ============================= TOTALS =============================
    Objects:         1882
      Commits:       108
        Root:        2  (commits not reachable by other commits)
        Reachable:   106
      Trees:         941
        Orphans:     27  (trees not referenced by any commit)
      Blobs:         833
        Orphans:     52  (blobs not referenced by any tree)
      Unknown:       0
    ==================================================================

    ========================== ROOT COMMITS ==========================
    2014-10-31 07e6b6b28ac3 Nick       Revert "[Sync] Fix bug where De
      Blobs 2 (28K)
    2014-10-31 558d1fadb000 yefim      Enabled enhanced bookmarks flag
      Blobs 215 (7067K)

    Blobs introduced by all root commits: 217, 7096K
    ==================================================================

    ======================= REACHABLE COMMITS ========================
    2014-10-31 41a3ca7c1751 Ben        Use the data reduction proxy in
      New blobs: 3 (49K)
    2014-10-31 6e11d0f286c4 Dana       cc: Damage the viewport and Upd
      New blobs: 1 (120K)
    ...
    ==================================================================

    ============================= FILES ==============================
    0K         revs:2        VERSION
    0K         revs:1        index.html
    ...
    11257K     revs:5        histograms.xml
    ==================================================================

    ========================== ORPHAN BLOBS ==========================
    4a3ad3c5aba5 0K     # Copyright 2014 The Chromium Au
    e1c960a15cc4 950K   <?xml version="1.0" encoding="ut
    ...
    ==================================================================

    ========================== ORPHAN TREES ==========================
    3af1755e0201 00_i18n_de.html 00_i18n_de2.html 00_i18n_en.html 00_i
    c424f3cd3a1b ApiaryClientFactory.java BlockingGCMRegistrar.java GC
    ...
    ==================================================================

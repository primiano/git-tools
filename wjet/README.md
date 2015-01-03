wjet: wget on steroids
----------------------

wjet is a simple and efficient HTTP(s) download script, similar to wget / curl,
optimized for a specific use case:

*downloading a large list of objects from a host as fast as possible*.
It has been explicitly designed to download large streams of objects from a
Google Cloud Storage bucket.

It uses HTTP keepalive, connection parallelism and gzip Content-encoding to
achieve this.

It takes as input a list of tuples of the form (/remote/path, /local/path)

It can be use either as a standalone tool (stdin streaming mode) or as part of a
python program.

### Usage in standalone mode
    $ cat download_list
    /buceket/foo /tmp/foo
    /bucket/bar /tmp/bar
    ....

    $ ./wjet.py https://storage.googleapis.com < download_list

        Completed  | Down. [MB] | Speed [MB/s] | Z.ratio | Errors
      -------------+------------+--------------+---------+--------
              1031 |     101.23 |         45.1 |    1.32 |      0

### Usage in python
    from wjet import DownloadMany

    # Feed from a static list
    downloads = [('/bucket/foo', '/tmp/foo'), ('/bucket/bar', '/tmp/bar')]
    res = DownloadMany('https://storage.googleapis.com', downloads, jobs=8)

    # Feed from an iterable
    def StreamingGen():
      yield '/bucket/foo', '/tmp/foo'
      yield '/bucket/bar'', '/tmp/bar'

    res = DownloadMany('https://storage.googleapis.com', StreamingGen(), jobs=8)

    # Iterate over results:
    for r in res:
      print 'Downloaded ', r.remote_path
      print ' Error: ', r.error
      print ' Bytes %d (%d compressed)' % (r.bytes_written, r.bytes_downloaded)

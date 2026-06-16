# Gunicorn configuration for wpaste.
#
# wpaste keeps per-process in-memory state: the `last_update_time` used by the
# /last-update long-poll and the TSVZ-backed `mainIndex`. With more than one
# worker, each process would have its own copy, so:
#   - clients polling worker B never see messages posted to worker A, and
#   - multiple processes writing the same mainIndex.tsv can clobber each other.
#
# It must therefore run as a SINGLE worker. Use threads (not workers) for
# concurrency. Gunicorn auto-loads this file when run from this directory:
#     gunicorn app:app
#
# If you ever need multiple workers, the shared state must first be moved out
# of process (e.g. a shared store), otherwise updates and the index will break.

workers = 1
threads = 4
bind = "127.0.0.1:8000"

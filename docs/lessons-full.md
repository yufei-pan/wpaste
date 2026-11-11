# Lessons (full)

## 2026-11-11 Delete removes the file
- Changed: deleting a message, and its expiry after its retention time, now removes the file with
  `os.remove`. The `RETENTION_SIZE` and `DELETED_RETENTION_TIME` config keys and the sweep's walk for
  `.deleted` files are gone. A config file that still sets either key loads fine and logs it as an
  ignored key.
- Why we had to change: deletion renamed every file under `RETENTION_SIZE` (100MB) to
  `<name>.deleted`. Until the 7-day sweep, it kept them forever. On one deployment that came to
  1,176 files and 969 MB by 2026-11-11, back to 2024-04, more than the live data. Content a
  private-board owner deleted on purpose stayed readable on disk. In about 2.5 years one file was
  copied back by hand.
- Original design was wrong because: the soft delete had no restore in the app. The index row was
  dropped, so even the original filename was lost, and recovery needed a shell on the server.
  Retention expiry went through the same path, so the 4h auto-delete kept every paste. The 7-day
  sweep bounded the space but still left deleted content readable for a week, for an undo nobody
  could use. The size threshold made the safety net depend on file size, which says nothing about
  how much a file is worth keeping.
- Lesson: a delete gives the space back and makes the content unreadable. A kept copy that nothing
  in the product can restore is a hidden archive, and an automatic expiry that goes through it keeps
  everything.

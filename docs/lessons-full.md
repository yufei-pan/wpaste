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

## 2026-11-18 Text files keep their name
- Changed: `POST /message` takes multipart parts named `text`. Each becomes a text post that keeps its
  file name (stored as `<id><ext>`, `.txt` when the name has none). The page shows such a post by its
  name: `.md` and `.markdown` as Markdown, `.html` and `.htm` as HTML, anything else as plain text,
  with the name above it. A part that isn't UTF-8, or is bigger than a pasted message may be, is kept
  as a file. Pasted text keeps the guessing from content. Text is now written and read as UTF-8
  whatever the server's locale. `main` joined `default` as a reserved board name. A file post named
  `.svg` also shows inline as an image.
- Why we had to change: a script sending a Markdown report had two choices, and both were wrong. As a
  `file`, it was a download, not something to read on a phone. As a pasted `message`, it lost its
  name (stored as `<id>.txt`), and the page guessed its kind from its content: a report of only
  headings and paragraphs has one weak Markdown sign, so it showed as plain text, while a Python file
  with `# comment` and `- item` lines could render as Markdown. A script that posts to "the public
  board" also needs a name for it that no private board can take.
- Original design was wrong because: every text post was a paste, so the page had only the content to
  go on. The guessing rules are tuned to keep code plain (they need strong signs), which is right for
  a paste and wrong for a file whose sender already said what it is. Reading text with the locale's
  default encoding made the bytes on disk mean different things on different servers.
- Lesson: a post's kind should come from what the sender knows (its name); guessing from content
  belongs only where nothing else is known.

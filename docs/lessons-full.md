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

## 2026-11-18 A POST that stores nothing is a 400
- Changed: `POST /message` and `/b/<slug>/message` answer `400 {"success": false, "error": "nothing to
  post"}` when the request stored nothing, where they answered `200 {"success": true}`. That covers a blank
  `message`, a `text` value that is a plain form value and not a file part, and a file part with no file
  name. The page shows "Nothing to send" for it. In the same round a `text` part holding a NUL byte is
  stored as a `file` (it is valid UTF-8, but binary), `/messages` and the curl view drop one leading BOM,
  and every text post is served as `text/plain; charset=utf-8` whatever its bytes look like.
- Why we had to change: the first `text` field answered success for a request that dropped what it carried.
  A script sending `text=...` as a form value, or a file whose name the client could not make, was told
  "saved" and found nothing on the board. Serving by content was the same kind of trust: text that began
  `%PDF-` came back as a PDF without the sandbox policy that every other upload gets.
- Original design was wrong because: success was the default of the handler, so every path that did
  nothing looked the same as one that did something; and the file's type was decided by its content where
  the post's own type was already known.
- Lesson: an endpoint that takes several kinds of part answers success only when it stored something, so a
  caller that sent the wrong shape is told at once; and where the server already knows what a stored thing
  is, it serves it as that, not as what its bytes could pass for.

## 2026-11-18 Setup takes only a well-formed secret
- Changed: `POST /b/<slug>/setup` answers `400 {"success": false, "message": "Invalid secret."}` for a secret that
  is not 32 characters of A-Z and 2-7, the form `/access` offers, where it stored any secret whose code verified
  (16 lower-case characters, a short or long one, one with padding or in lower case). Nothing in the page changes:
  it posts the candidate `/access` offered.
- Why we had to change: pyotp reads a lower-case or short base32 secret as a valid one, so setup stored it. A
  client that makes codes from the registry cannot tell such a secret from a misread one, and a code it guessed
  wrong would count toward the board's lockout, which locks the owner's phone too; so it accepts only the form the
  server makes.
- Original design was wrong because: the server checked that the secret and the code agreed, which any base32 string
  passes, and never that the secret had the form the server makes. The candidate came from the server, so the server
  could have required that form back. (It checks the form only: it keeps no list of the candidates it offered, so any
  well-formed secret whose code verifies is accepted, offered or not.)
- Lesson: when a server hands out a value in a fixed form for the client to send back, it accepts that form only, not
  whatever its verifier happens to tolerate; other readers of what it stores then need no guess. Say what is checked:
  the form, not that this very value was the one offered.

## 2026-11-18 Setup stores a display name of at most 64 characters
- Changed: `POST /b/<slug>/setup` stores the board's display name cut to 64 characters (`DISPLAY_MAX`, canonical_slug's
  maxlen; stripped, and the slug when nothing is left) and answers with the stored value. `/access` offers the same cut
  name. Before, it stored the name as sent, up to the form field's 64 MiB. The page sends the slug as the name, so
  nothing it sends is cut. The 400 for a bad secret and the curl view's setup hint now name the form: 32 characters
  of A-Z and 2-7.
- Why we had to change: setup is open to anyone, the name goes into the registry row, and the row is written again
  (setup writes it twice, every login once more). One anonymous POST with a 17 MiB name grew the registry past 16 MiB,
  where a tool that reads the whole registry at once refuses it, and a name over 16 MiB survives the registry's own
  compaction, so only deleting the board ends it.
- Original design was wrong because: the name was handled as a label, so no one asked how big it could be, and the
  only bound was the framework's per-field default, which is sized for a pasted log, not for a name.
- Lesson: a field that anyone can set and the server stores gets a cap where it comes in, at what its real use needs
  (here, the longest value the page itself sends), never at the framework's default for the largest legitimate field.

## 2026-11-19 One terminal rule, by Unicode category
- Changed: the plain-text (curl) view and `admin list` now make `_` of exactly the characters in the Unicode categories
  Cc, Cf, Cs, Zl and Zp (`terminal_text`, with `terminal_name` and the text bodies both calling it). Spaces of every kind
  now stay, where `str.isprintable` made every Unicode space but U+0020 into `_`: a macOS screenshot name (U+202F before
  AM/PM) reads as it was sent. Private-use and unassigned characters stay too. A text post's body in the curl view is now
  filtered the same way (newline and tab kept), where it was printed as stored, so an anonymous post on the public board
  holding an OSC 52 sequence can no longer set the clipboard of whoever curls the board. `/text/<id>` still sends the
  stored bytes. The download header's `safe_name` is unchanged: it only removes what gunicorn refuses.
- Why we had to change: four review rounds each found another way an outside name or text reached a terminal: a regex of
  controls, then `str.isprintable` (which also replaced the no-break spaces of real names and left the text bodies out),
  then a copy of each in the client. Each call site had picked its own character rule.
- Original design was wrong because: "safe to show on a terminal" was never decided once. Each new place that printed
  outside text wrote its own predicate, and each was right for the case in front of it and wrong for the next.
- Lesson: decide what is safe to show on a terminal once, by Unicode category, in one function, and make every place that
  prints outside text call it; a client of the server keeps a copy that its tests compare with this one over every code
  point. A reviewer's check for a new print site is then "does it call the function", not a re-derivation of the rule.

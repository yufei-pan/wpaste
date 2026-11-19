# Lessons

One line per lesson; the full story is in `docs/lessons-full.md`.

- **2026-11-11 Delete removes the file**: a delete gives the space back and makes the content unreadable; a kept copy nothing in the product can restore is a hidden archive, and an expiry routed through it keeps everything.
- **2026-11-18 Text files keep their name**: a post's kind comes from what the sender knows (its file name); guessing from content belongs only where nothing else is known, as with a paste.
- **2026-11-18 A POST that stores nothing is a 400**: an endpoint with several kinds of part answers success only when it stored something, so a caller that sent the wrong shape is told at once; and a stored thing the server knows the type of is served as that type, never as what its bytes could pass for.
- **2026-11-18 Setup takes only a well-formed secret**: when a server hands out a value in a fixed form for the client to send back, it accepts that form only, not whatever its verifier happens to tolerate (it does not check that this very value was the one it offered); other readers of what it stores then need no guess.
- **2026-11-18 Setup stores a display name of at most 64 characters**: a field that anyone can set and the server stores gets a cap where it comes in, at what its real use needs, never at the framework's default for the largest legitimate field.
- **2026-11-19 One terminal rule, by Unicode category**: what is safe to show on a terminal is decided once, by Unicode category (controls, format characters, surrogates, line and paragraph separators), and every place that prints outside text uses that one function; the joiners and the soft hyphen (Cf, but they shape ordinary text and no terminal acts on them) are the named exceptions; per-site predicates (a regex, `str.isprintable`, an inline copy) drifted for four rounds, and the curl view's text bodies were the one place with no rule at all.

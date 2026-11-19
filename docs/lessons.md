# Lessons

One line per lesson; the full story is in `docs/lessons-full.md`.

- **2026-11-11 Delete removes the file**: a delete gives the space back and makes the content unreadable; a kept copy nothing in the product can restore is a hidden archive, and an expiry routed through it keeps everything.
- **2026-11-18 Text files keep their name**: a post's kind comes from what the sender knows (its file name); guessing from content belongs only where nothing else is known, as with a paste.
- **2026-11-18 A POST that stores nothing is a 400**: an endpoint with several kinds of part answers success only when it stored something, so a caller that sent the wrong shape is told at once; and a stored thing the server knows the type of is served as that type, never as what its bytes could pass for.
- **2026-11-18 Setup takes only the secret it offered**: when a server hands out a value for the client to send back, it accepts that form only, not whatever its verifier happens to tolerate; other readers of what it stores then need no guess.

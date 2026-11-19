# Known limitations and deferrals

Deferred entries are planned work; known limitations are approved by the repo's owner. Reviewers read this first and
don't raise these again without new evidence.

### Successful setups and logins are not throttled
- Kind: known limitation.
- What the operator sees: the throttles cover `/access` lookups and wrong codes (a lockout per IP and per board). A
  client can make successful setups (each adds a board that stays until its owner or the operator deletes it, and
  writes its registry row twice) and successful logins to a board it owns (each appends the row with one more session
  token) as fast as the server answers. The registry file then grows by a few hundred bytes per request: at the worst
  (a 64-character board name, and a display name of 64 NUL characters, which TSVZ stores as its 5-byte `<sep>` each)
  a login appends 614 bytes and a setup 904, measured 2026-11-19 on wpaste's own `Boards` class (64 four-byte characters
  give 550 for a login). So about 27,000 logins or 18,000 setups reach 16 MiB, twice that with plain ASCII names, far
  more than the few KB a real registry holds, until TSVZ's periodic rewrite compacts the file (after
  `INDEX_REWRITE_INTERVAL`, 20 h by default, with no append). A tool that reads the whole registry at once can then
  refuse to read it. The display name is capped at 64 characters (2026-11-18), which removed the
  megabytes-per-request case.
- Why: a successful setup or login needs a code that matches a secret the client holds, so it was never counted as
  abuse; the failure throttles exist to stop guessing. A per-IP throttle on these needs a measured limit (every number
  has a reason) and is only as good as the client IP: with `TRUSTED_PROXY_HOPS` wrong, every visitor shares one
  bucket, and a throttle would then lock out the owner too. Reaching the sizes above takes a deliberate flood, and
  nothing fails silently: a reader that refuses says why, the page keeps working, and `admin remove-board` clears the
  junk boards. The fix, if needed: throttle successful setups and logins per IP the way `/access` is (same
  `RateLimiter`, a sliding window), sized from a measurement of real use.
- Revisit when: a registry past a few MB is seen, a flood of setups or logins is seen, or the next change to the
  throttles.
- Approved: 2026-11-19, the owner picked "Known limitation (Recommended)".

### The curl view shows the flags of England, Scotland and Wales with `_`
- Kind: known limitation.
- What the operator sees: in the plain-text view a terminal gets, a text post or a file name holding one of these three
  flags shows the black flag followed by `_` characters, one for each tag character: "Come on 🏴______!" for the
  England flag. The page and `/text/<id>` show and send the text as it was posted.
- Why: these three are the only emoji built from tag characters (U+E0020-E007F), which are invisible and are the known
  way to hide ASCII text inside a line. Keeping them would let an anonymous post on the public board carry text a
  person at a terminal cannot see. Keeping them only inside a flag sequence would make the rule depend on the characters
  around each one, unlike the per-character rule that both this view and its client copy use.
- Revisit when: the owner posts one of these flags and minds how it shows.
- Approved: 2026-11-19, the owner picked "Known limitation (Recommended)".

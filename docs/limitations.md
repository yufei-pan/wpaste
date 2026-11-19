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

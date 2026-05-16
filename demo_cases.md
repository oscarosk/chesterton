# Chesterton — Demo Cases

These are the three pre-scouted archaeology cases that Chesterton will be demonstrated against. All three are real code in the Flask repository (https://github.com/pallets/flask) — code that looks deletable on first glance but exists for a non-obvious, painful reason.

Each case represents a *different class* of mistake Chesterton catches:
1. Security (CVE prevention)
2. Cross-platform compatibility (Windows-specific bugs)
3. Cookie hardening (session security)

---

## Case 1 — The CVE that crossed repos *(HERO)*

**Original commit:** `aeed530e` — "Make sure that windows servers do not allow downloading arbitrary files"
**Author:** Armin Ronacher (Flask creator)
**Date:** December 23, 2010
**Demo target:** Flask repository at commit `aeed530e^` (or the Werkzeug repo today, where the protection now lives)

### The "looks deletable" pitch

Inside `flask/helpers.py`, an oddly-named module-level constant:

```python
# what separators does this operating system provide that are not a slash?
# this is used by the send_from_directory function to ensure that nobody is
# able to access files from outside the filesystem.
_os_alt_seps = list(sep for sep in [os.path.sep, os.path.altsep]
                    if sep not in (None, '/'))
```

And in `send_from_directory`, a loop that looks redundant alongside the existing `../` and absolute-path checks:

```python
filename = posixpath.normpath(filename)
for sep in _os_alt_seps:           # <- looks like dead Windows-specific bloat
    if sep in filename:
        raise NotFound()
if os.path.isabs(filename) or filename.startswith('../'):
    raise NotFound()
```

A junior dev cleaning up this file might delete `_os_alt_seps` and the loop. The reasoning seems airtight: "we already block `../` and absolute paths. What else could matter?"

### The actual history

Added by Armin Ronacher (Flask's creator) in 2010. The function `send_from_directory` is meant to safely serve user-provided file paths from a directory. On POSIX systems, `/` is the only path separator and `../` is the only way to escape a directory. On Windows, `\` is *also* a path separator. `posixpath.normpath` does not know about `\` — so a request like `/static/..\..\..\windows\system32\config\sam` passes the `../` and `os.path.isabs` checks but is interpreted by Windows as a directory-traversal path.

This is a CVE-class arbitrary-file-download vulnerability on every Flask Windows server.

Later, in commit `dc11cdb4`, `send_file` and `send_from_directory` moved to Werkzeug. The `_os_alt_seps` protection moved with them and still lives in Werkzeug today. The protection has traveled across repository boundaries for 15+ years because Windows hosting is still real.

### What breaks if you delete it

A path-traversal exploit on every Flask-on-Windows deployment:

```
GET /static/..\..\..\..\windows\system32\drivers\etc\hosts
GET /static/..\..\..\..\Users\Administrator\.aws\credentials
```

These bypass the existing checks and let an attacker read arbitrary files from the server's filesystem.

### Why this is the hero demo

- Added by **Flask's creator himself** — the demo gets to flash Armin Ronacher's commit in the video
- Looks like 7 lines of dead Windows-specific code
- Removal = CVE
- The Chesterton verdict can show the protection *followed `send_file` across a repo migration* — multi-source reasoning across repo boundaries, which is the kind of "genuinely large and messy codebase" archaeology no other team will demo

---

## Case 2 — The cryptic regex *(LIVE in current main)*

**Original commit:** `c38499bb` — "ignore colon with slash when split app_import_path"
**Author:** garenchan
**Date:** October 24, 2018
**Issue:** Flask issue #2961
**Demo target:** Current Flask main, `src/flask/cli.py:346`

### The "looks deletable" pitch

In `src/flask/cli.py` line 346:

```python
path, name = (
    re.split(r":(?![\\/])", self.app_import_path, maxsplit=1) + [None]
)[:2]
```

That `r":(?![\\/])"` is a negative-lookahead regex — overengineered-looking compared to the obvious:

```python
path, name = (self.app_import_path.split(':', 1) + [None])[:2]
```

A "simplification" PR could easily replace the regex with the cleaner `.split()` version. It looks more Pythonic. It passes all the basic tests.

### The actual history

Flask supports specifying an app to run as `module_path:app_variable`, e.g. `flask run --app myapp.py:create_app`. The `:` separates module path from variable name.

On Windows, file paths look like `C:\Users\me\app.py`. With the naive `.split(':', 1)`, Flask parses `C:\Users\me\app.py:create_app` as path=`C`, name=`\Users\me\app.py:create_app` — and crashes with `NoAppException: Could not import 'C'`.

The negative-lookahead `r":(?![\\/])"` says: "split on `:` only if NOT followed by `\` or `/`." This keeps the drive-letter colon intact and splits only on the real separator colon.

Fix shipped in 2018 for issue #2961.

### What breaks if you delete it

Every Windows Flask user passing an absolute path to `--app`:

```
C:\Users\dev> flask --app C:\Projects\myapp\app.py:create_app run
Error: Could not import 'C'
```

Broken `flask run` for the entire Windows ecosystem. Silent on Linux/Mac CI, so the regression ships.

### Why this is a strong second case

- Lives in current Flask main — Chesterton demos against fresh `git clone https://github.com/pallets/flask`
- Different *shape* of mistake than Case 1 — UX/cross-platform regression, not a security CVE
- The regex looks like a code smell to anyone who hasn't been bitten by it
- Tests catch this on Windows CI but a developer's local mac/linux box won't

---

## Case 3 — The one-line cookie fix

**Original commit:** `b707bf44` — "Preserve HttpOnly flag when deleting session cookie"
**Author:** uedvt359
**Date:** March 15, 2022
**Issue:** Flask issue #4485
**Demo target:** Current Flask main, `src/flask/sessions.py:356`

### The "looks deletable" pitch

In `src/flask/sessions.py`, inside `SecureCookieSessionInterface.save_session`, look at the `response.delete_cookie(...)` call:

```python
response.delete_cookie(
    name,
    domain=domain,
    path=path,
    secure=secure,
    samesite=samesite,
    httponly=httponly,   # <- looks redundant — we're deleting the cookie, not setting it
)
```

The `httponly=httponly` line on a `delete_cookie` call looks redundant or even nonsensical. We're *removing* the cookie — why does it need HTTP-only protection? A "tidy up" PR could plausibly delete this line.

### The actual history

Browsers don't actually "delete" cookies. The server "deletes" a cookie by sending a `Set-Cookie` response with the same cookie name, an empty value, and an expiration date in the past. The browser then discards the cookie on its next pass.

That replacement cookie is still a real cookie that exists in the browser for a brief moment before being purged. If it doesn't carry the `HttpOnly` flag, that brief-existence cookie is reachable from JavaScript on the page. An attacker with an XSS foothold could potentially read the session-deletion cookie and leak information about the user's session being terminated, or in pathological cases poison the deletion process.

Issue #4485, fixed in 2022, ensures the deletion cookie carries the same HttpOnly protection as the original.

### What breaks if you delete it

The session-deletion cookie is briefly readable from JavaScript on the page. Real-world exploitability is narrower than Case 1, but it's still an XSS-hardening regression — exactly the class of subtle session-handling bug that ends up in a security audit finding 6 months later.

### Why this is a good third case

- A literally single-line removal that "looks safe" — the simplest possible demo of Chesterton catching a non-obvious deletion
- Different *shape* again — cookie/session hardening, not path traversal or platform compat
- Tells the third leg of the story: "Chesterton doesn't just catch CVEs. It catches the small things that defense-in-depth depends on."

---

## Notes on multi-source reasoning

For each case, Chesterton's verdict will combine evidence from at least three sources:

1. **Git history** — the original commit, message, and author
2. **GitHub issue/PR** — for Cases 2 (#2961) and 3 (#4485), the linked issue threads contain the full failure-mode discussion. For Case 1, the commit message and changelog are the primary source.
3. **Tests** — each case has a corresponding test in the Flask test suite that would fail if the code were deleted. Chesterton surfaces the test name and what it asserts.

For Case 1 specifically, Chesterton also follows the protection *across repos* — showing the code's life in Flask 2010–2020 and its current home in Werkzeug. That cross-repo trace is the moment in the demo that makes the "Application of Technology" judging criterion impossible to score below 9/10.

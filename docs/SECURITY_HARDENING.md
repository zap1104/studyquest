# StudyQuest — Security Hardening Checklist

Tracks the phased hardening work. Each phase lists its acceptance criteria and
current status so the checklist can be resumed or audited later.

**Status: Phases 1–5 complete.** Phases 6+ are proposed and not started.

---

## Phase 0 — Setup ✅

- [x] Branch: `security-hardening`
- [x] `python-dotenv` confirmed as an existing dependency (no new package)
- [x] `.env.example` committed with placeholder values
- [x] `.env` confirmed gitignored

**Note:** `.env` already contained all four documented variables
(`DJANGO_SECRET_KEY`, `GEMINI_API_KEY`, `DEBUG`, `ALLOWED_HOSTS`); the values
were simply never read by `settings.py`.

---

## Phase 1 — Secret key & DEBUG ✅ Critical

**Files:** `studyquest/settings.py`, `.env`, `.env.example`

- [x] `SECRET_KEY` read from `os.environ.get("DJANGO_SECRET_KEY")`
- [x] `DEBUG` read from the environment via `env_bool("DEBUG", default=False)`
- [x] `.env` loaded with `dotenv` at the top of `settings.py`

  This is **not** redundant with the existing `dotenv` call in
  `courses/services.py`: `settings.py` is imported first, so the load has to
  happen there for any setting to see it.

- [x] Fail-fast when the key is missing and `DEBUG` is off:

  | Situation | Behaviour |
  |---|---|
  | `DEBUG=True`, no key | Random throwaway key + loud `stderr` warning |
  | `DEBUG=False`, no key | `RuntimeError` at startup |
  | Key present | Used as-is |

  The `DEBUG=True` carve-out keeps `manage.py check` and the test suite
  runnable for contributors and CI who have no `.env`.

- [x] **Local key rotated.** The key committed at
  `studyquest/settings.py:23` is treated as burned. A fresh key was generated
  with `get_random_secret_key()` and written to `.env` only. Rotating is the
  actual fix; the code change alone is not sufficient.

**Acceptance criteria — verified:**

```
git grep SECRET_KEY studyquest/settings.py   →  no literal key string
python manage.py check                       →  no issues
DEBUG=False + no key                         →  RuntimeError raised
```

**Deployment action still required:** any previously-deployed instance using
the old committed key must rotate it, and all existing sessions will be
invalidated by the rotation.

---

## Phase 2 — ALLOWED_HOSTS cleanup ✅

**Files:** `studyquest/settings.py`, `.env`, `.env.example`

- [x] `ALLOWED_HOSTS` built from a comma-separated env var, defaulting to
      `127.0.0.1,localhost`
- [x] Both hardcoded LAN IPs (`192.168.100.5`, `192.168.1.65`) removed from
      source; they now belong in a developer's local `.env`
- [x] `.env.example` documents the phone-testing pattern

**Acceptance criteria — verified:**

```
grep -E '192\.168' studyquest/settings.py    →  no matches
```

---

## Phase 3 — Production security settings ✅

**Files:** `studyquest/settings.py`

Gated behind `IS_PRODUCTION = not DEBUG`, so local HTTP development is
unaffected:

- [x] `SECURE_SSL_REDIRECT = True`
- [x] `SESSION_COOKIE_SECURE = True`
- [x] `CSRF_COOKIE_SECURE = True`
- [x] `SECURE_HSTS_SECONDS = 31536000` (1 year)
- [x] `SECURE_HSTS_INCLUDE_SUBDOMAINS = True`
- [x] `SECURE_HSTS_PRELOAD = True`
- [x] `SECURE_PROXY_SSL_HEADER = ("HTTP_X_FORWARDED_PROTO", "https")`
- [x] `SECURE_CONTENT_TYPE_NOSNIFF = True`
- [x] `X_FRAME_OPTIONS = "DENY"`

**Acceptance criteria — verified** with `DEBUG=False` and a placeholder key:

```
python manage.py check --deploy
```

| Warning | Setting | Status |
|---|---|---|
| `security.W004` | `SECURE_HSTS_SECONDS` | cleared |
| `security.W008` | `SECURE_SSL_REDIRECT` | cleared |
| `security.W009` | `SECRET_KEY` strength | **expected** — test key only |
| `security.W012` | `SESSION_COOKIE_SECURE` | cleared |
| `security.W016` | `CSRF_COOKIE_SECURE` | cleared |
| `security.W018` | `DEBUG` | cleared |

`W009` remains only because the check was run with a throwaway key; it
disappears once a real key is present in the environment.

**Deployment action required:** `SECURE_HSTS_PRELOAD` and a 1-year
`SECURE_HSTS_SECONDS` are committed to the browser's HSTS preload list. Only
enable preload once every subdomain is HTTPS-capable — this is effectively
irreversible for returning visitors.

---

## Phase 4 — Dead code cleanup ✅

**Files:** `courses/forms.py` (deleted)

- [x] Confirmed `CourseForm` had no importers — course creation builds `Course`
      objects directly in `views.py`
- [x] File deleted rather than left as an unreferenced form implying a
      validation path that never runs

**Acceptance criteria — verified:** full test suite passes unchanged.

---

## Phase 5 — Rate limiting on auth endpoints ✅

**Files:** `courses/views.py`, `courses/tests/test_auth_throttle.py`,
`studyquest/settings.py`

Chose a **cache-based throttle** over adding `django-ratelimit`, keeping the
dependency footprint unchanged.

- [x] `_is_throttled` / `_register_attempt` / `_clear_attempts` helpers in
      `views.py`, keyed by client IP and scoped per action (`login` / `signup`)
- [x] Applied to both the login and signup POST paths in `auth_portal`
- [x] Returns **HTTP 429** with a friendly message once the limit is exceeded
- [x] A successful login or signup clears the counter
- [x] `CACHES` configured (locmem) — required for the throttle to function
- [x] Defaults: 10 attempts per 300s, overridable via
      `LOGIN_THROTTLE_MAX_ATTEMPTS` / `LOGIN_THROTTLE_WINDOW_SECONDS`
- [x] **Plan-based course-generation quota left untouched** — that logic is
      already race-safe and is a separate concern

**Acceptance criteria — verified** (`courses/tests/test_auth_throttle.py`,
5 tests):

| Test | Asserts |
|---|---|
| `test_legitimate_single_login_is_unaffected` | A correct login still succeeds |
| `test_repeated_failures_are_throttled_with_429` | N+1 failures returns 429 |
| `test_successful_login_clears_the_attempt_counter` | Counter resets on success |
| `test_throttle_is_scoped_per_action` | Exhausted login does not block signup |
| `test_counter_expires_after_the_window` | Window expiry restores access |

**Known limitation:** the locmem cache is per-process. A multi-process or
multi-worker deployment needs a shared cache (Redis/Memcached) for the
throttle to be effective across workers.

**Client IP trust boundary:** `REMOTE_ADDR` is authoritative by default.
`HTTP_X_FORWARDED_FOR` is ignored to prevent client header spoofing unless
`TRUST_PROXY_HEADERS = True` is explicitly configured AND `REMOTE_ADDR` matches
an address in `TRUSTED_PROXY_IPS`.

---

## Proposed — not started

### Phase 6 — CI modernization
`.github/workflows/django.yml` pins `python-version: [3.7, 3.8, 3.9]`, but the
project runs Django 5.2 on Python 3.14. The workflow will fail if it runs. It
also invokes bare `manage.py test` with no secret or environment setup, which
will now raise under Phase 1 unless `DEBUG=True` is exported.

### Phase 7 — Broader endpoint throttling
Login/signup are covered. Consider extending the same throttle helper to
`focus_checkin` (JSON import) and course generation, which are
authenticated but still cost-bearing.

### Phase 8 — Dependency and upload hardening
Audit pinned versions for known CVEs; consider a malware/zip-bomb check
beyond the existing size and extension allowlists.

---

## Verification commands

```bash
python manage.py check
python manage.py check --deploy          # with DEBUG=False
python manage.py test                    # 313 tests
```
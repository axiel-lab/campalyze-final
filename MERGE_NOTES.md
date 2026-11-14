# What changed (Campalyze login merged into the Discord dashboard)

The Discord dashboard (FastAPI) is the main app. Campalyze's account system now
replaces its old single shared password.

**Login**
- Username + password instead of one shared password (`core/users.py`, new)
- Same SQLite schema + werkzeug hashes as Campalyze -> you can point
  `DATABASE_PATH` at your existing `campalyze.db` and keep your users
- Accounts seeded from ADMIN_*/USER_2_*/USER_3_* env vars (same as Campalyze);
  old `DASHBOARD_PASSWORD` still works as an `admin` account fallback
- Brute-force lockout: 5 failed tries per IP+username -> 5 min cooldown
- Disabling a user takes effect immediately (checked on every request + websocket)

**Admin panel**
- Admins see a **Users** button: create members, enable/disable them
- **Per-user Token Manager**: every user (members too) can add/edit their own
  tokens and run their own bots. Each member gets a private token file
  (`user_tokens/user_<id>.txt`, chmod 600) and a separate bot manager, so
  members only ever see/control their own bots, tokens and log lines.
  Admins keep the original shared pool (`TOKENS_FILE`) and see all logs.
- Disabling a member also stops their running bots immediately
- Saving tokens validates them (no spaces, min length, de-duplicated) and
  enforces `MEMBER_MAX_TOKENS` (default 25, 0 = unlimited) for members

**Other**
- `config.py` no longer blocks on a `getpass` prompt (it would hang on a hosting
  panel); it auto-creates a JWT secret instead
- New dependency: `werkzeug` (in requirements.txt)
- Start command: `python dashboard.py`

Campalyze's Flask templates/CSS are not used - the Discord dashboard already
has its own UI, so only the login/user-management logic was ported.

Not tested end-to-end: I could not install FastAPI in my sandbox. The user
store, lockout logic and dashboard JS syntax were tested; please run the app
once and try login / create user / disable user before relying on it.

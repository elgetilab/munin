# Account identity: self-service aliases, then a numeric user id

Status: Part A BUILT (approved 2026-11-17, defaults accepted); Part B approved, after Part A. Follows df2c2c5 (frozen `users.identity_email`, deployed 2026-11-17).

## Where we are

- Every service stores user data under `X-Munin-Email`. Since df2c2c5 that
  header carries `users.identity_email`: set once from the primary address,
  never changed, never given to another user. Signing in with any of a
  user's addresses lands in one account.
- `backend/scripts/maintenance/converge_emails.py` folds data that was
  already split across aliases onto the identity (chats.db, gateway.db,
  user_docs, Qdrant `user_docs`). Dry run on 2026-11-17: 4 conversations,
  1 artifact, 3 proposed memories, 3 uploaded docs, 46 Qdrant points across
  2 users (Matthias, Anna). Kirsten had nothing under her alias yet.
- Only admins can add addresses (Users tab).

## Part A: users add their own addresses

Settings > Account gets an "Email addresses" list with Add / Remove /
Make primary.

1. `POST /auth/me/emails {email}`: refuse when `_email_owner()` finds an
   owner (same 409 text as admin, without saying whose). Otherwise send a
   6-digit code to that address, stored under a separate key
   (`alias:<email>` in `otp_store`) so a pending alias code can never be
   used to sign in, and vice versa. Same send rate limit and lockout as login.
2. `POST /auth/me/emails/verify {email, code}`: on success attach as
   non-primary, and send a notice to the primary address ("an address was
   added to your Munin account; if this was not you, contact support").
3. `DELETE /auth/me/emails/{email}`: not the last address, and not the
   address of the current session (avoids signing yourself out mid-click).
   Kills that address's sessions, as the admin path does.
4. `PUT /auth/me/emails/{email}/primary`: allowed; since df2c2c5 this only
   changes display and where mail goes.
5. Admin Users tab unchanged (admins still add without a code).

Tests: code goes only to the new address; a login code does not verify an
alias and an alias code does not log in; owned and identity addresses are
refused; the primary gets the notice; removal rules.

## Part B: numeric user id replaces identity_email as the storage key

Goal: data keyed by `users.id`, so an address is purely a login and contact
detail.

1. munin-auth sends `X-Munin-User: u<id>` alongside `X-Munin-Email` (which
   becomes the primary address, for display and mail only).
2. Retrieval, gateway, upload hook switch their storage key from
   `X-Munin-Email` to `X-Munin-User`. The columns stay named `user_email`
   in this step; renaming them is churn with no user-visible gain.
3. Migration = `converge_emails.py` with the map `{identity_email: "u<id>"}`
   from a new `plan --to-user-id`. It already rewrites every keyed table
   found by schema, the user_docs hash dirs, and the Qdrant payload, so no
   second migration tool.
4. Gateway API keys resolve to `u<id>` via a key -> user lookup at auth
   (keys already store the owner; the converge run rewrites them).
5. Places that really need an address keep reading `X-Munin-Email`:
   ADMIN_EMAILS fallback, quota overrides in quotas.yml, check-role, mail.
   check-role also accepts `u<id>`.
6. Cutover: deploy header + migration in one window (backend and frontend
   hosts), because a service on the old key and data on the new one looks
   like an empty account again. Both scripts are dry run by default; a
   rollback is the pre-converge DB backups plus the previous images.

## Part B: detailed design (2026-11-17, after surveying the code)

What identity_email already gives: one account per person whatever address
they sign in with, and a key that never moves. What Part B adds on top:
addresses become fully reusable (today a removed identity address is locked
forever), no email addresses inside storage keys, logs, user_docs hashes or
Qdrant payloads, and a deleted user's data is keyed by a number, not a
person's address.

Survey of where the key is read:

- retrieval: three helpers (`_require_user_email` in main.py, 44 call
  sites; `_require_email` in research_routes.py; the MCP endpoint that sets
  `current_user_email`). Two places need a real address, not the key: the
  admin gate (`_require_admin_email` -> check-role) and KB paper uploads
  (contributors.yml is keyed by address).
- gateway: 11 endpoints read X-Munin-Email; API-key requests take the owner
  from `api_keys.user_email`. Uses of a real address: ADMIN_EMAILS fallback,
  quotas.yml overrides, the X-Munin-Name fallback (`email.split("@")[0]`).
  Admin usage and activity views list users by `user_email`.
- upload hook (tusd): KB contributor gate by address. Stays on X-Munin-Email.
- Caddy: copy_headers and the strip list for client-sent identity headers.
  X-Munin-User must be added to BOTH, or a client could set it.
- Scripts and benchmarks send X-Munin-Email straight to retrieval with
  test addresses (stress-test.py, munin_bench).

Steps:

1. auth: `/auth/check` adds `X-Munin-User: u<id>`; X-Munin-Email becomes
   the primary address. check-role accepts `u<id>` and returns the key,
   primary and role. lookup_user accepts `u<id>`.
2. Caddy: copy X-Munin-User, strip it from client requests.
3. gateway: forwards X-Munin-User; keys usage, keys and activity on it. For
   API-key requests it resolves `u<id>` -> address and role through
   check-role (bearer token, cached 60 s), so quotas, ADMIN_EMAILS and
   role work the same as in the browser.
4. retrieval: the three helpers return X-Munin-User, falling back to
   X-Munin-Email when it is absent (keeps scripts and benchmarks working;
   their data stays under their test addresses; `u\d+` and addresses
   cannot collide). Admin gate and KB uploads read X-Munin-Email.
5. Admin UI: usage and activity tables show the user's name and primary
   address by joining `u<id>` against the Users list it already loads.
6. Lift the "identity address cannot go to another user" guard once the
   cutover is done; identity_email stays as a record.
7. Cutover per host, no downtime window: deploy auth + Caddy + gateway
   (X-Munin-User is sent but nothing keys on it yet); then on hugin run
   converge with {identity: u<id>}, restart retrieval, run converge again to
   catch writes made in between (it is idempotent); same for gateway.db.

## Open questions (defaults in brackets)

1. Verify a self-added address by emailed code? [yes; without it anyone
   could claim an address they do not own and later sign in with it]
2. Notify the primary address when an alias is added? [yes]
3. Cap on addresses per user? [5]
4. Part B: keep the `user_email` column names? [yes, rename never]
5. Part B timing: right after Part A, or after v1.1? [after Part A]
6. Part B: after cutover, X-Munin-Email carries the primary address? [yes]
7. Part B: API-key requests resolve their owner's address through
   check-role, rather than storing an address on each key? [check-role]
8. Part B: retrieval falls back to X-Munin-Email as the key when
   X-Munin-User is missing, for scripts and benchmarks? [yes]
9. Part B: lift the reuse guard on removed identity addresses after
   cutover? [yes]

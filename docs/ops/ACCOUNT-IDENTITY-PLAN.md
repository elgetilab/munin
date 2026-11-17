# Account identity: self-service aliases, then a numeric user id

Status: PLAN, awaiting "proceed". Follows df2c2c5 (frozen `users.identity_email`).

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

## Open questions (defaults in brackets)

1. Verify a self-added address by emailed code? [yes; without it anyone
   could claim an address they do not own and later sign in with it]
2. Notify the primary address when an alias is added? [yes]
3. Cap on addresses per user? [5]
4. Part B: keep the `user_email` column names? [yes, rename never]
5. Part B timing: right after Part A, or after v1.1? [after Part A]

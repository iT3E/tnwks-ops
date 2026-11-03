# reassign-owner

`reassign-owner.sql` moves every object in one database from one role to
another without superuser, and without `REASSIGN OWNED BY` (which would also
hand over every other database the old role owns). It was used in 2026-11 to
move all apps off the shared `app` role, one database at a time, in three PRs
per batch:

1. Add the new role to `managed.roles`, make the old role a member of it
   (`inRoles`), set the Database CR `owner` to the new role, and add a one-time
   Job that runs this script as the old role (psql `-v old_role=... -v
   new_role=...`). The Job should exit 0 at once when nothing is left owned by
   the old role, so a rerun after an image bump can't hang.
2. Switch the app to the new role's login.
3. Remove the membership and the Job.

The Jobs were removed once done (see the git history of this directory for
one, e.g. `job-plausible.yaml`). Test on a scratch Postgres with the
database's real schema (`pg_dump --schema-only`) before running it for real.

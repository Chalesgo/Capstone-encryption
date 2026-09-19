# SealGuard deployment on Render

SealGuard uses a paid Render web service with one persistent disk. The first
deployment retains SQLite to preserve the current users, permissions, audit
history, and encrypted-file references without a database conversion.

## 1. Prepare a private repository

Keep the deployment repository private. The inherited Git history contains
database and document artifacts and must never be published. Confirm the fork
shows **Private** in GitHub before connecting Render.

Verify the destination before pushing:

```powershell
git remote set-url origin https://github.com/Griffeminie/Capstone-encryption.git
git remote -v
git fetch origin
git log --left-right --oneline origin/main...main
```

Only after confirming the repository is private and reviewing the deployment
commit should it be pushed with `git push -u origin main`. Sanitizing the
inherited history is still required before this repository is ever made public.

## 2. Make the local backup

Stop the local server and pause all uploads. Run:

```powershell
powershell -ExecutionPolicy Bypass -File .\scripts\create_render_backup.ps1
```

The script creates a sibling directory outside the repository containing a
consistent SQLite backup, the complete media tree, the existing RSA keypair,
and a checksum manifest. Keep two private copies.

## 3. Create the Render service

The repository includes `render-paid.yaml` for the disk-backed deployment. In
Render, use **New > Blueprint** and
select the private repository. The Blueprint supplies:

- Singapore region
- paid 512 MB web-service plan
- a 25 GB disk at `/opt/render/project/src/data`
- build and start commands
- `/health/` health check
- one Gunicorn worker and the daily integrity scheduler

Do not create a second manual Web Service if the Blueprint creates one. If a
manual draft already exists and has never deployed, delete the draft and use
the Blueprint.

Before the first successful health check, add these two Secret Files to the web
service using the unchanged local key contents:

| Filename | Runtime path |
| --- | --- |
| `private.pem` | `/etc/secrets/private.pem` |
| `public.pem` | `/etc/secrets/public.pem` |

The Blueprint may create the service and attempt its first deploy before the
dashboard lets you add these files. In that case `/health/` deliberately returns
503. Add both Secret Files and choose **Manual Deploy > Deploy latest commit**.

Set the email values marked `sync: false` in the Render dashboard. Render
automatically supplies its hostname and HTTPS URL to Django.

## 4. Restore existing data

Keep the service in Render maintenance mode while restoring. Transfer the
backup with Render SSH/SCP or Magic Wormhole:

- `db.sqlite3` to `/opt/render/project/src/data/db.sqlite3`
- the contents of `media` to `/opt/render/project/src/data/media`

Upload the database as `db.sqlite3.incoming`, then rename it to `db.sqlite3`
and restart the service. This prevents the running process from reading a
partially uploaded database. Do not upload SQLite `-wal` or `-shm` files.

Expected source totals at the time this guide was created:

- database: 87,375,872 bytes
- media: 25,105 files and 15,893,027,824 bytes

Run `python manage.py migrate --noinput` in the Render Shell after restoration,
then restart the service.

## 5. Release checks

Keep maintenance mode enabled until all checks pass:

1. `/health/` returns HTTP 200.
2. Existing administrator and user accounts can sign in.
3. Old PDFs from several users and dates decrypt and render.
4. Ownership, collaborator access, revocation, and audit logs are intact.
5. A new upload still opens after a service restart.
6. The integrity scan completes without exhausting memory.
7. Email delivery and a physical phone QR scan work over the Render HTTPS URL.

If the service is killed for exceeding memory during PDF work or integrity
scanning, move from the 512 MB plan to the 2 GB plan before releasing it.

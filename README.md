# SEALGUARD
Capstone Project – Barangay Sto. Niño, Biñan City | Contract Security and Authentication System

## Overview
SEALGUARD is a Django-based web application designed for secure government contract management. The system allows barangay staff to upload, encrypt, and manage procurement contracts, while providing the public a verification portal to confirm document authenticity using steganography-based encryption.

---

## Prerequisites
Before running the project, make sure you have:
- Python 3.11 or higher
- Git installed
- Visual Studio Code (recommended)

---

## Installation

### 1. Clone the Repository
```bash
git clone <repository-url>
cd SEALGUARD
```

### 2. Create and Activate a Virtual Environment
```bash
python -m venv venv
venv\Scripts\activate
```

### 3. Install Dependencies
```bash
pip install -r requirements.txt
```

### 4. Generate RSA Keys
RSA keys are not included in the repository for security reasons.
Generate them by running the following in the Django shell:

```bash
python manage.py shell
```

Then paste this inside the shell:

```python
from Crypto.PublicKey import RSA
import os

key = RSA.generate(2048)
os.makedirs('keys', exist_ok=True)

with open('keys/private.pem', 'wb') as f:
    f.write(key.export_key())

with open('keys/public.pem', 'wb') as f:
    f.write(key.publickey().export_key())

print("Keys generated successfully.")
exit()
```

### 5. Set Up Media Folders
Create the required media directories manually:
media/

├── contracts/

├── seals/

└── temp/

Place the official barangay seal image at:
media/seals/default_seal.png

### 6. Apply Database Migrations
```bash
python manage.py migrate
```

### 7. Create a Superuser
```bash
python manage.py createsuperuser
```

### 8. Run the Development Server
```bash
python manage.py runserver
```

The application will be available at:

http://127.0.0.1:8000

### Integrity Monitoring

SealGuard integrity scans are run manually or from a scheduled task so a
write-heavy scan cannot block account creation or other SQLite writes. Run one
with:

```powershell
$env:DEBUG='False'
python manage.py verify_integrity
```

The local Django `runserver` starts an integrity scan automatically in its serving
process (including after an autoreload), then triggers another every 24 hours while
the server remains running. Automatic scans run in the background. Daily triggers
are skipped when another scan is active; cancelling a scan does not queue a retry.
Administrators can also use **Run integrity scan** for an extra check and the
**x** control (tooltip: **Cancel scan**) to request cancellation. Staff see progress
without these controls. Set `SEALGUARD_RUN_STARTUP_INTEGRITY=0` to disable this
local automatic scheduler. It does not run during tests, migrations, or shell commands.

For deployments without `runserver`, use an external scheduler instead.
For daily Windows monitoring, create a Task Scheduler task that runs
`scripts/run_integrity_scan.ps1` once per day. Failed checks are written to
the terminal log and dashboard audit log; repeated identical failures are
limited to one audit entry per 24 hours.
An administrator can also start another scan from the Dashboard with the
refresh icon. If a scan is already running, a scheduled or manual trigger is
skipped so two scans cannot overlap.
Each completed run also appears as a compact `Integrity Scan` activity. Select
that activity in the dashboard to open its stored debug log. An administrator
can cancel an active scan from the dashboard. A cancelled scan keeps results
already checked, leaves unreached PDFs unchecked, and never labels an
unreached PDF as tampered.

---

## Features
- Public document verification portal — upload a PDF to check authenticity
- Physical QR code scanning for on-site contract verification
- Secure contract upload with automated encryption pipeline
- SHA-256 canonical fingerprint generation
- AES-256-CBC encryption with RSA key wrapping
- LSB steganography embedded into the official barangay seal
- PDF metadata signature storage for reliable verification
- Role-based access control for Admin, Staff, User, and public verification
- Permission-checked document editing, sharing, download, and deletion
- Audit-style verification log for debugging

---

## Admin Access
Navigate to:
http://127.0.0.1:8000/admin/

Create your own superuser account using Step 7 above. Only active superusers
can enter Django Admin; staff accounts use the application workspace and User
accounts are limited to read-only PDF viewing.
Do not share login credentials in this file.

---

## Roles and Permissions

SealGuard separates its application roles from Django's `is_staff` label. In
this project, `is_staff` means Staff workspace access; it does not grant
Django Admin access. Admin means a Django superuser.

### How the hybrid RBAC works

There are three layers working together:

1. **Role flags identify the account.** A superuser is an Admin, `is_staff` is
   Staff, and an account with neither flag is a User. Only a superuser can open
   `/admin/`.
2. **Django Groups describe broad permissions.** Each account is synchronized
   into `SealGuard Admin`, `SealGuard Staff`, or `SealGuard User`. The Group
   permissions page shows what that role is generally allowed to do. Changing
   Staff status automatically changes the matching group and removes stale
   SealGuard permissions from the account.
3. **SealGuard applies per-document rules.** `contracts/access.py` checks the
   specific PDF for every protected action. Ownership, collaboration, public
   status, trash status, and read-only restrictions determine whether the user
   may view, download, edit, share, publish, or delete that particular document.

The Group is therefore a role-level baseline, not a replacement for the
document-level check. A Staff member can have the general permission to manage
documents while still being blocked from a PDF they do not own or have access
to. A User can have the general permission to view PDFs while remaining unable
to download or modify them.

### Gmail email delivery for local testing

SealGuard uses the console email backend by default, so development emails are
printed in the terminal. To send real Gmail messages, enable 2-Step Verification
on the Gmail account, create a Google App Password, and place the following in
your local `.env` file:

```dotenv
EMAIL_BACKEND=django.core.mail.backends.smtp.EmailBackend
EMAIL_HOST=smtp.gmail.com
EMAIL_PORT=587
EMAIL_USE_TLS=True
EMAIL_HOST_USER=your-account@gmail.com
EMAIL_HOST_PASSWORD=your-16-character-app-password
DEFAULT_FROM_EMAIL=your-account@gmail.com
```

Restart Django after changing `.env`, then use account registration or password
recovery to send a test message. Do not use the normal Gmail password, commit
`.env`, or paste the App Password into source code. Google requires 2-Step
Verification for App Passwords and revokes them when the Google account
password changes.

| Capability | Admin (superuser) | Staff (`is_staff`) | User (neither flag) | Public / unauthenticated |
|---|---|---|---|---|
| Open `verify/` | Yes | Yes | Yes | Yes |
| Use physical verification | Yes | Yes | Yes | Yes |
| Open `list.html` and `dashboard` | Yes | Yes | Yes, read-only | No; public verification only |
| See private documents | All documents | All non-trashed documents | Uploaded or explicitly shared documents | No |
| View a PDF in SealGuard | Yes | Yes, read-only by default | Yes, read-only | Public documents only |
| Download a PDF | Yes | Yes, for viewable PDFs | No | Public documents only |
| Upload a document | Yes | Yes | No | No |
| Rename, tag, revise, encrypt, or change status | Yes | Yes, for authorized documents | No | No |
| Move documents or manage folders | Yes | Yes, for authorized documents | No | No |
| Move documents to Trash or restore them | Yes | Yes, for authorized documents | No | No |
| Permanently delete documents | Yes | Only where the normal document permission is granted | No | No |
| Grant or revoke document access | Yes | Authorized uploader only | No | No |
| Publish or unpublish a document | Yes | Authorized uploader only | No | No |
| View activity for authorized documents | All activity | Authorized-document activity | Authorized-document activity, read-only | No private activity |
| Export dashboard reports | Yes | Yes | No | No |
| Start or cancel an integrity scan | Yes | No | No | No |
| Enter `/admin/` | Yes | No | No | No |

The User role can preview assigned PDFs but cannot download them. Direct media
URLs enforce the same rule for private documents. A document marked public can
be viewed and downloaded without an account, by design.

The Django Admin Groups page mirrors these roles through the `SealGuard Admin`,
`SealGuard Staff`, and `SealGuard User` groups. Their permissions cover workspace,
PDF, revision, folder, tutorial, verification, reporting, invitation, and integrity
scan capabilities. These are role-level permissions; document ownership and
collaboration checks in `contracts/access.py` still decide which individual PDF a
user may view or change.

---

## Remote Advisor Demo with Ngrok

Use the included launcher to show SealGuard from another phone or computer without deploying it. Your laptop must remain powered on, connected to the internet, and running the launcher throughout the demo.

### One-Time Setup on Windows

1. Create a free account at https://ngrok.com.
2. Install the ngrok agent from the Microsoft Store or with:

```powershell
winget install ngrok -s msstore
```

3. Copy your authtoken from the ngrok dashboard and save it in ngrok's local configuration:

```powershell
ngrok config add-authtoken YOUR_TOKEN_HERE
```

Never paste the authtoken into `.env`, this README, source code, screenshots, or chat messages.

### Start the Demo

Activate the project's virtual environment, then run:

```powershell
powershell -ExecutionPolicy Bypass -File .\scripts\start_ngrok_demo.ps1
```

The launcher automatically:

- creates the ngrok HTTPS tunnel;
- reads the temporary public URL;
- allows only that hostname in Django;
- adds that exact HTTPS origin for CSRF-protected forms;
- starts Django on port 8000; and
- prints the link to open or send to your advisor.

Keep the PowerShell window open. Press `Ctrl+C` once to stop both Django and ngrok. The public link stops working immediately and a new URL may be issued the next time you launch it.

To use another local port:

```powershell
powershell -ExecutionPolicy Bypass -File .\scripts\start_ngrok_demo.ps1 -Port 8080
```

This is a temporary development demo, not a production deployment. The generated link is reachable from the public internet, so use test records, keep normal account permissions enabled, do not share administrator credentials, and stop the launcher as soon as the presentation is finished.

---

## Notes

### Stopping the Server
Press `Ctrl + C` to stop the Django development server.

### Keys and Media Files
The `keys/` and `media/` folders are excluded from the repository via `.gitignore`.
Every team member must generate their own RSA keys and set up the media folders locally.

### Demo Mode

For demonstration and testing purposes, the verification result can be controlled by the filename of the uploaded PDF:

| Filename contains | Result shown |
|---|---|
| `authentic`, `original`, `legit` | ✅ Authentic |
| `tampered`, `fake`, `incorrect`, `modified`, `altered`, `forged` | ❌ Tampered |
| `unknown` | ⚠️ Unknown |

The keyword can appear anywhere in the filename. For example, `Dela_Cruz_Contract_fake.pdf` will return a **Tampered** result.

> This demo behavior only applies when the keyword is present in the filename. All other uploads go through the full cryptographic verification pipeline.

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

SealGuard scans every stored PDF version when the Django development server
starts. The scan runs in the background so it does not block the first page
request. Run it manually with:

```powershell
$env:DEBUG='False'
python manage.py verify_integrity
```

For daily Windows monitoring, create a Task Scheduler task that runs
`scripts/run_integrity_scan.ps1` once per day. Failed checks are written to
the terminal log and dashboard audit log; repeated identical failures are
limited to one audit entry per 24 hours.
Each completed run also appears as a compact `Integrity Scan` activity. Select
that activity in the dashboard to open its stored debug log.

---

## Features
- Public document verification portal — upload a PDF to check authenticity
- Physical QR code scanning for on-site contract verification
- Secure contract upload with automated encryption pipeline
- SHA-256 canonical fingerprint generation
- AES-256-CBC encryption with RSA key wrapping
- LSB steganography embedded into the official barangay seal
- PDF metadata signature storage for reliable verification
- Role-based access control — Superuser, Staff, and Public
- Superuser-only contract deletion with file cleanup
- Audit-style verification log for debugging

---

## Admin Access
Navigate to:
http://127.0.0.1:8000/admin/

Create your own superuser account using Step 7 above.
Do not share login credentials in this file.

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

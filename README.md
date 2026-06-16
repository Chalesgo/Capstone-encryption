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

## Testing on Mobile Devices
To access the system from a phone, you can use Ngrok.

### Step 1: Download and Set Up Ngrok
Install Ngrok from https://ngrok.com and sign up for a free account.
Add your authtoken:
```bash
ngrok config add-authtoken YOUR_TOKEN_HERE
```

### Step 2: Start the Django Server
```bash
python manage.py runserver 0.0.0.0:8000
```

### Step 3: Open a New Terminal and Run Ngrok
```bash
ngrok http 8000
```

Ngrok will generate a public URL similar to:
https://abc123.ngrok-free.app

### Step 4: Update Django Settings
In `settings.py`, add the generated Ngrok URL to both `ALLOWED_HOSTS` and `CSRF_TRUSTED_ORIGINS`:

```python
ALLOWED_HOSTS = [
    'localhost',
    '127.0.0.1',
    '192.168.0.103',
    'abc123.ngrok-free.app'
]

CSRF_TRUSTED_ORIGINS = [
    'https://abc123.ngrok-free.app'
]
```

Restart the Django server after making changes.

> Note: The Ngrok URL changes every session on the free plan.
> Update `ALLOWED_HOSTS` and `CSRF_TRUSTED_ORIGINS` each time.

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

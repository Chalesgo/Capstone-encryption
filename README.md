# SEALGUARD
Capstone Project – Contract Security and Management System

## Overview

SEALGUARD is a Django-based web application designed for secure contract management. The system allows users to upload and view contracts while providing administrative tools for user management.

---

## Prerequisites

Before running the project, make sure you have:

- Python installed
- Git installed
- Visual Studio Code (recommended)

---

## Installation

### 1. Clone the Repository

```bash
git clone <repository-url>
cd SEALGUARD
```

### 2. Install Dependencies

```bash
pip install django
```

### 3. Apply Database Migrations

```bash
python manage.py migrate
```

### 4. Run the Development Server

```bash
python manage.py runserver
```

The application will be available at:

```
http://127.0.0.1:8000
```

---

## Features

- Upload contracts
- View contracts
- User management through Django Admin

---

## Admin Access

Navigate to:

```
http://127.0.0.1:8000/admin/
```

> username: Griff Password: Griffin0710*

---

## Testing on Mobile Devices

To access the system from a phone on a different network, you can use Ngrok.

### Step 1: Download Ngrok

Install Ngrok from:

https://ngrok.com/

### Step 2: Start the Django Server

```bash
python manage.py runserver
```

### Step 3: Open a New Terminal and Run Ngrok

```bash
ngrok http 8000
```

Ngrok will generate a public URL similar to:

```
https://abc123.ngrok.io
```

### Step 4: Update Allowed Hosts

In `settings.py`, add the generated Ngrok URL to `ALLOWED_HOSTS`:

```python
ALLOWED_HOSTS = [
    'localhost',
    '127.0.0.1',
    '192.168.0.103',
    'abc123.ngrok.io'
]
```

Restart the Django server after making changes.

---

## Notes

### Stopping the Server

Press:

```text
Ctrl + C
```

to stop the Django development server.

### Known Issue

- LSB pixel values may change after PDF rendering, which can affect steganography-based verification methods.

# Capstone-encryption/SEALGUARD
PABASA PARA SA SETUP

## Setup
1. git clone ...
2. SA CMD pip install django
3. TERMINAL SA VSCODE TYPE THIS python manage.py migrate
4. TO RUN python manage.py runserver

## Features
- Upload contracts
- View contracts


CTRL BREAK IS CTRL + C

ISSUE WITH LSB PIXELS KEEP GETTING CHANGED DUE TO PDF RENDERING

admin will be here to check users

griff
griffin0710*

http://127.0.0.1:8000/admin/login/?next=/admin/

How to deploy the system so you can see it on your phone (FOR TESTING PURPOSES)
1. download ngrok
2. run django server
   -python manage.py runserver
4. create new terminal and then run ngrok, you should see a url
   -ngrok http 8000
6. add the ngrok url into the allowed host in settings.py
   ALLOWED_HOSTS = ['localhost', '127.0.0.1', '192.168.0.103', 'abc123.ngrok.io']

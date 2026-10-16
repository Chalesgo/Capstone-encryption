"""Create ten fictional SealGuard staff accounts for the defense demo.

Run on the server (for example in the Render Shell) with:

    DEMO_STAFF_PASSWORD='choose-one' python manage.py shell -c "exec(open('scripts/create_demo_staff.py').read())"

Accounts that already exist are left unchanged. All new accounts share the
password in DEMO_STAFF_PASSWORD; without it, a random password is generated
and printed once at the end. Emails use the reserved example.com domain so no
real inbox receives SealGuard mail.
"""
import os
import secrets

from django.contrib.auth.models import User

from contracts.models import AccountSecurity

STAFF = [
    ('mreyes', 'Maricel', 'Reyes'),
    ('jdelacruz', 'Jomar', 'Dela Cruz'),
    ('asantos', 'Analyn', 'Santos'),
    ('rbautista', 'Rodel', 'Bautista'),
    ('kvillanueva', 'Kristine', 'Villanueva'),
    ('lmendoza', 'Lorna', 'Mendoza'),
    ('egarcia', 'Emmanuel', 'Garcia'),
    ('jramos', 'Joanna', 'Ramos'),
    ('pcastillo', 'Paolo', 'Castillo'),
    ('nflores', 'Nerissa', 'Flores'),
]

chosen_password = os.environ.get('DEMO_STAFF_PASSWORD', '')
if chosen_password and len(chosen_password) < 8:
    raise SystemExit('DEMO_STAFF_PASSWORD must be at least 8 characters.')
password = chosen_password or secrets.token_urlsafe(12)
created = []
for username, first_name, last_name in STAFF:
    if User.objects.filter(username__iexact=username).exists():
        print(f'skipped {username}: already exists')
        continue
    user = User(
        username=username,
        first_name=first_name,
        last_name=last_name,
        email=f'{username}@example.com',
        is_staff=True,
    )
    user.set_password(password)
    # The post_save signal places the account in the SealGuard Staff group.
    user.save()
    AccountSecurity.objects.create(user=user, email_verified=True, must_change_password=False)
    created.append(username)

print(f'created {len(created)} staff accounts: {", ".join(created) or "none"}')
if created and not chosen_password:
    print(f'shared password (shown once): {password}')

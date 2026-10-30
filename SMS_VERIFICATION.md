# Email and SMS verification

Staff invitation registration now asks for email and mobile number and offers Email or SMS for the six-digit code. Only the selected contact is verified. After the required password change, use Account > Verification contacts to verify the other contact or a replacement number. The last verified channel becomes the preference for account messages and administrator reset-request notifications.

Password recovery offers email or SMS. SMS requires a previously verified number. Administrator approval remains required. Codes expire after ten minutes, allow five attempts, and are bound to the requesting browser session. Registration and contact-code requests are throttled per recipient for one minute. Existing verified email accounts remain usable; SMS registration does not authorize recovery through its unverified email address.

QR access requests accept email or a mobile number as the contact method. They continue to require staff review without introducing a new OTP step. The queue labels contact details as requester-supplied, not verified.

## Demo delivery and Admin

Outgoing emails remain in Admin > Outgoing email inbox. SMS appears in Admin > Outgoing SMS inbox, including destination, message, time, and status. With the default configuration, SMS stays `queued`: it is stored locally, not transmitted to a phone. An administrator can use this inbox to demonstrate code entry. Only superusers can enter Admin. User records display the stored phone and verification flags as read-only fields; users verify changes through Verification contacts.

No SMS provider or n8n workflow is connected by this change. No external SMS charges occur with the empty backend setting. Existing email delivery settings still apply. Codes are hashed in challenge/reset state; the demo inboxes deliberately contain message bodies and codes and must remain restricted to administrators.

## Later n8n integration

The delivery seam is `contracts.messaging.send_message(channel, recipient, subject, body)`. Email uses Django's configured `EMAIL_BACKEND`, keeping its existing message log. SMS uses an optional dotted callable configured as `SMS_DELIVERY_BACKEND`; an empty value records a queued message only.

An SMS adapter receives keyword arguments `message_id`, `recipient`, `subject`, and `body`. It must return the Boolean `True` only after successful provider acceptance; `False`, another return value, or an exception marks the message failed. Current `sent` status means adapter/provider acceptance, not a handset delivery receipt. Use `message_id` for idempotency in the future workflow. Adding delivery callbacks, retries, retention, and a worker that dispatches queued messages remains future integration work. Do not bulk-send historical demo codes when connecting a provider.

A future adapter can submit to an authenticated HTTPS n8n webhook, which selects an SMS provider or email node. Keep code creation, expiry, attempt limits, contact verification, and account/document permissions in Django. Configure email via a custom Django email backend if routing it through n8n. The current cache-backed registration/contact challenges use the project's cache configuration; multi-worker deployments need a shared cache and atomic attempt/consume operations before production scaling.

## Email through n8n

The email seam now uses n8n when all of these environment variables are set:

```text
N8N_EMAIL_WEBHOOK_URL=https://your-n8n-host/webhook/sealguard/email-delivery
N8N_EMAIL_WEBHOOK_HEADER=X-SealGuard-Webhook
N8N_EMAIL_WEBHOOK_SECRET=use-the-value-from-the-n8n-header-credential
N8N_EMAIL_WEBHOOK_TIMEOUT=10
```

The webhook must return `{"ok":true,"channel":"email"}` with a 2xx status after the Gmail node accepts the message. If the URL is blank, SealGuard keeps using Django's configured email backend. If the URL is set but the secret or webhook response is invalid, or n8n rate-limits the request, the message is logged as not delivered but the verification code stays active and no error is shown; administrators can read the code in Admin > Outgoing email inbox. Do not commit the secret or paste it into source control. Set the n8n Gmail node's attribution option off if the email should not include the n8n footer.

Email delivery diagnostics are written by the `contracts.messaging` logger to the Django terminal. They include the local message ID, n8n host, HTTP status or response shape, and the final `EmailMessageLog.delivered` value. They never log the webhook secret or message body/code. A `404` normally means the production workflow is not active or the URL path is wrong; `401`/`403` means the header credential does not match; an accepted webhook followed by no message requires checking the Gmail node execution and credential in n8n.

## Validation

Run with a valid Boolean DEBUG setting:

```powershell
$env:DEBUG = 'True'
python manage.py migrate
python manage.py test contracts.sms_tests contracts.pdf_approval_tests --noinput
```

Real handset delivery requires a provider integration and separate end-to-end testing.

Validation on 2026-09-22: 43 SMS/contact and PDF approval tests passed. Django check and migration consistency check passed. Broader existing suites also reported four authorization-matrix assertion failures (expected redirects/permissions versus current responses) and one integrity-status label failure; those unrelated behaviors were not changed here. Browser layout and real handset delivery were not manually tested.

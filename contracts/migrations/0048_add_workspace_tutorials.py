from django.db import migrations
from django.db.models import Max


TUTORIALS = [
    (
        'How to open an encrypted SealGuard download', 'lock',
        'Open a .sgpdf download through SealGuard using your current document access.',
        '<ol><li>Download a document or revision from SealGuard. Encrypted downloads use the <strong>.sgpdf</strong> extension; bulk downloads place these files inside a ZIP.</li>'
        '<li>Sign in with an account allowed to view the document. If you are an external requester, complete the QR access approval process in the same browser.</li>'
        '<li>Choose <strong>Open encrypted document</strong> in the sidebar, select the .sgpdf file, and submit it.</li>'
        '<li>SealGuard checks the file and your current access before opening the PDF.</li></ol>'
        '<p>Renaming the file to .pdf will not decrypt it. A changed file, unavailable revision, or expired or revoked access can prevent opening. Ask staff for help or a current download.</p>',
    ),
    (
        'How to grant or remove document access', 'user',
        'Let another account manage a specific document using Manage access.',
        '<ol><li>As the document uploader or an administrator, open the document row options and choose <strong>Manage access</strong>.</li>'
        '<li>Search by username. Check the suggested account and its creation date before selecting it.</li>'
        '<li>Choose <strong>Grant access</strong>. Review the accounts shown in the access list.</li>'
        '<li>To withdraw access, remove the account from that list.</li></ol>'
        '<p>Document collaboration gives an eligible staff account editing and revision rights for that document. It does not make the account an administrator. Read-only users remain read-only.</p>'
        '<p>Staff can view documents and revision details without edit access. Greyed-out actions indicate a role or document restriction; hover over them on desktop to read the explanation. On mobile, use the document action menu.</p>',
    ),
    (
        'How to share a QR access sheet and approve viewing', 'shield',
        'Request and review temporary access to a private document without sharing its contents on the QR sheet.',
        '<ol><li>The uploader or administrator opens the document options and selects <strong>Download QR access sheet</strong>.</li>'
        '<li>Give the sheet to the intended requester. It contains a request link, not the document contents.</li>'
        '<li>The requester scans the QR code, fills in their details and reason, and verifies their email with the code sent to them.</li>'
        '<li>A staff member or administrator opens <strong>PDF access requests</strong>, reviews the request, and chooses <strong>Approve viewing</strong> or <strong>Reject</strong>.</li>'
        '<li>When approving, select 1 hour, 24 hours, or 3 days. The requester returns to the request page in the same browser to view the approved version.</li></ol>'
        '<p>Approval applies to the selected version and duration; it does not grant document editing rights. Use the Approved tab and <strong>Revoke access</strong> to end permission early.</p>',
    ),
    (
        'How to use request notifications', 'dashboard',
        'Open pending requests from the notification bell.',
        '<ol><li>Sign in and select the <strong>notification bell</strong> in the top bar.</li>'
        '<li>Read the request entries in the dropdown beneath the bell.</li>'
        '<li>Select a document access notification to open its corresponding request for review. Staff and administrators can review document access requests.</li>'
        '<li>Administrators can also select password reset notifications to open the corresponding request in the Admin Portal.</li></ol>'
        '<p>Opening a notification does not approve the request. Review its details and use the approval controls on the destination page.</p>',
    ),
    (
        'How to recover a forgotten password', 'lock',
        'Follow the Email, Verify, and Password stages to recover your account.',
        '<ol><li>Choose <strong>Forgot your password?</strong> on the login page.</li>'
        '<li>Enter the email connected to your account and request a verification code.</li>'
        '<li>If the page says the request is awaiting administrator approval, wait for approval and then check your email for the six-digit code.</li>'
        '<li>Enter the code at the Verify stage. The form submits automatically when all six digits are entered; use Verify code if needed.</li>'
        '<li>Once accepted, enter and confirm your new password at the Password stage, then sign in with it.</li></ol>'
        '<p>A completed reset uses up that request. A later forgotten-password request requires a new recovery process. The first-login password-change page is a separate step for accounts that must replace their initial password.</p>',
    ),
    (
        'How to mark a document as Draft or Final copy', 'check',
        'Understand document status and the administrator controls for changing it.',
        '<ol><li>An administrator locates the document in the list.</li>'
        '<li>Use the status control or row options to choose <strong>Mark as Final copy</strong>.</li>'
        '<li>Confirm after checking that the document is complete and verified.</li>'
        '<li>If further work is needed, use <strong>Return to Draft</strong>.</li></ol>'
        '<p>Only administrators can change this status, including in bulk. Staff can see the status but cannot change it.</p>'
        '<p><strong>Final copy does not publish the PDF or grant public access.</strong> Use the QR access request process when someone needs permission to view a private document.</p>',
    ),
    (
        'How to select and organize documents in bulk', 'folder',
        'Select eligible documents and move them into a folder together.',
        '<ol><li>Use the document list filters to find the documents you need. Press <strong>Enter</strong> to apply a typed search.</li>'
        '<li>Tick individual rows, use Shift-click for a range on desktop, or use Select all for eligible rows.</li>'
        '<li>Choose the bulk folder action and select the destination. Create a new folder in the picker if needed.</li>'
        '<li>Confirm the action and read its result message.</li></ol>'
        '<p>Select all skips documents you cannot manage. Selecting documents does not grant extra permissions; administrator-only actions remain restricted.</p>'
        '<p>On mobile, use the available row checkboxes and bulk controls. You can still open document details and revision history when editing is unavailable.</p>',
    ),
    (
        'How to run and follow an integrity scan', 'search',
        'Check stored document revisions and understand the scan progress shown on the dashboard.',
        '<ol><li>As an administrator, open the dashboard and choose <strong>Run integrity scan</strong>.</li>'
        '<li>While the scan is active, look for the <strong>Integrity scan in progress</strong> banner. Staff can also see this status.</li>'
        '<li>Administrators can open the scan details to review the checks and progress log.</li>'
        '<li>Let the scan finish, then review the Integrity Scan activity and any reported failures.</li>'
        '<li>If necessary, an administrator can request cancellation. The scanner stops at a document boundary, so the request may take time.</li></ol>'
        '<p>The scan checks stored revision fingerprints, links between revisions, and the current document file reference. A failed check needs investigation; an unchecked document is not automatically tampered.</p>'
        '<p>A cancelled or interrupted scan is incomplete. Run another scan when you need a complete check. Staff can follow progress but cannot start or cancel scans.</p>',
    ),
]


def add_tutorials(apps, schema_editor):
    tutorials = apps.get_model('contracts', 'Tutorial').objects.using(schema_editor.connection.alias)
    last_order = tutorials.aggregate(value=Max('sort_order'))['value']
    next_order = 0 if last_order is None else last_order + 1
    for title, icon, summary, content in TUTORIALS:
        _, created = tutorials.get_or_create(
            title=title,
            defaults={'icon': icon, 'summary': summary, 'content': content, 'sort_order': next_order},
        )
        if created:
            next_order += 1


class Migration(migrations.Migration):
    dependencies = [('contracts', '0047_documentaccesslink_is_active_and_more')]
    # Keep administrator-edited articles if the migration is rolled back.
    operations = [migrations.RunPython(add_tutorials, migrations.RunPython.noop)]

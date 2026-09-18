"""A printable access invitation containing no confidential document contents."""
from io import BytesIO

import qrcode
from reportlab.lib.colors import HexColor
from reportlab.lib.utils import ImageReader
from reportlab.pdfgen import canvas


def make_access_sheet(url, reference):
    output = BytesIO()
    pdf = canvas.Canvas(output, pagesize=(595, 842))
    pdf.setTitle('SealGuard document access request')
    pdf.setFillColor(HexColor('#173e70'))
    pdf.rect(0, 735, 595, 107, stroke=0, fill=1)
    pdf.setFillColor(HexColor('#ffffff'))
    pdf.setFont('Helvetica-Bold', 25)
    pdf.drawString(48, 786, 'SealGuard')
    pdf.setFont('Helvetica', 12)
    pdf.drawString(48, 762, 'Secure document access')
    pdf.setFillColor(HexColor('#173e70'))
    pdf.setFont('Helvetica-Bold', 23)
    pdf.drawString(48, 678, 'Scan to request access')
    pdf.setFillColor(HexColor('#52647a'))
    pdf.setFont('Helvetica', 11)
    pdf.drawString(48, 652, 'This sheet does not contain the confidential PDF.')
    qr = qrcode.make(url)
    image = BytesIO()
    qr.save(image, format='PNG')
    image.seek(0)
    pdf.drawImage(ImageReader(image), 177, 371, 240, 240, mask='auto')
    pdf.setFont('Helvetica-Bold', 11)
    pdf.drawCentredString(297.5, 350, f'Document reference: {reference.upper()}')
    pdf.setFont('Helvetica', 12)
    for y, line in zip((301, 273, 245), (
        '1. Scan the QR code and enter your details.',
        '2. Verify your email with the code we send you.',
        '3. Return to the page after staff approves your request.',
    )):
        pdf.drawString(66, y, line)
    pdf.setFont('Helvetica', 10)
    pdf.drawString(48, 192, 'Approval allows temporary viewing of a specific document version.')
    pdf.drawString(48, 175, 'Sharing this QR does not share an approval or a decryption key.')
    pdf.setStrokeColor(HexColor('#d7e0ec'))
    pdf.line(48, 146, 547, 146)
    pdf.setFont('Helvetica', 8)
    # Keep the complete fallback URL readable without extending beyond the page.
    from reportlab.lib.utils import simpleSplit
    lines = simpleSplit(url, 'Helvetica', 8, 499)
    if len(lines) == 1 and pdf.stringWidth(url, 'Helvetica', 8) > 499:
        lines = [url[i:i + 95] for i in range(0, len(url), 95)]
    for index, line in enumerate(lines[:5]):
        pdf.drawString(48, 125 - index * 12, line)
    pdf.linkURL(url, (48, 64, 547, 135), relative=0)
    pdf.setFont('Helvetica', 8)
    pdf.drawString(48, 40, 'Keep your approval page and email verification code private.')
    pdf.save()
    return output.getvalue()

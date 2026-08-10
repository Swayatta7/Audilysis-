import smtplib
import socket
from email.utils import parseaddr
from email.message import EmailMessage


def _normalize_recipients(recipients):
    if isinstance(recipients, str):
        recipients = [r.strip() for r in recipients.replace(";", ",").split(",") if r.strip()]
    return [recipient for recipient in (recipients or []) if recipient]


def _is_valid_email(value: str) -> bool:
    _, parsed = parseaddr(value or "")
    return bool(parsed and "@" in parsed and "." in parsed.rsplit("@", 1)[-1])


def send_report_email(smtp_host, smtp_port, sender_email, sender_password,
                      recipients, subject, body_html, report_pdf, report_filename):
    """
    Send the generated PDF report as an attachment. Stdlib only.
    Returns: (recipient_string, error_message)
    If successful, error_message is None.
    If failed, recipient_string is None.
    """
    try:
        recipients = _normalize_recipients(recipients)
        if not smtp_host:
            return None, "Email server unavailable. SMTP host is required."
        if not smtp_port:
            return None, "Email server unavailable. SMTP port is required."
        if not _is_valid_email(sender_email):
            return None, "Email sender address is invalid."
        if not recipients:
            return None, "Email recipient address is required."
        invalid_recipients = [recipient for recipient in recipients if not _is_valid_email(recipient)]
        if invalid_recipients:
            return None, "Email recipient address is invalid."
        if not report_pdf:
            return None, "Email delivery failed because the report attachment was empty."

        msg = EmailMessage()
        msg["Subject"] = subject
        msg["From"] = sender_email
        msg["To"] = ", ".join(recipients)
        msg.set_content("Your Audilysis 2.0 AI Mention Tracking report is attached. "
                        "Open the attached .pdf file to view it.")
        
        # Add the HTML alternative (short summary in email body)
        msg.add_alternative(body_html, subtype="html")
        
        # Attach the full report PDF file
        msg.add_attachment(
            report_pdf,
            maintype="application",
            subtype="pdf",
            filename=report_filename,
        )

        port = int(smtp_port)
        smtp_client = smtplib.SMTP_SSL if port == 465 else smtplib.SMTP
        with smtp_client(smtp_host, port, timeout=30) as server:
            server.ehlo()
            if port != 465 and server.has_extn("starttls"):
                server.starttls()
                server.ehlo()
            server.login(sender_email, sender_password)
            server.send_message(msg)

        return ", ".join(recipients), None

    except smtplib.SMTPAuthenticationError:
        return None, "Email authentication failed."
    except smtplib.SMTPRecipientsRefused:
        return None, "Email delivery failed because the recipient address was rejected."
    except smtplib.SMTPSenderRefused:
        return None, "Email sender address was rejected by the mail server."
    except smtplib.SMTPNotSupportedError:
        return None, "Email delivery failed because the server does not support the required TLS/authentication flow."
    except (smtplib.SMTPConnectError, smtplib.SMTPServerDisconnected, socket.timeout, TimeoutError):
        return None, "Email server unavailable."
    except (socket.gaierror, OSError):
        return None, "Email delivery failed because the mail server could not be reached."
    except ValueError:
        return None, "Email server unavailable. SMTP port is invalid."
    except Exception:
        return None, "Email delivery failed. The report is still available to download."

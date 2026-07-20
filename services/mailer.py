import smtplib
import socket
from email.message import EmailMessage

def send_report_email(smtp_host, smtp_port, sender_email, sender_password,
                      recipients, subject, body_html, report_pdf, report_filename):
    """
    Send the generated PDF report as an attachment. Stdlib only.
    Returns: (recipient_string, error_message)
    If successful, error_message is None.
    If failed, recipient_string is None.
    """
    try:
        if isinstance(recipients, str):
            recipients = [r.strip() for r in recipients.split(",") if r.strip()]

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

        with smtplib.SMTP(smtp_host, int(smtp_port), timeout=30) as server:
            server.starttls()  # secure the connection
            server.login(sender_email, sender_password)
            server.send_message(msg)

        return ", ".join(recipients), None

    except smtplib.SMTPAuthenticationError:
        return None, "Email authentication failed. For Gmail/Outlook use an App Password, not your normal account password."
    except (smtplib.SMTPConnectError, socket.timeout, OSError) as e:
        return None, f"Could not reach the mail server. Check the SMTP host and port. Error details: {str(e)}"
    except Exception as e:
        return None, f"Could not send the email: {str(e)}. The report is still available to download."

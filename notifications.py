import smtplib
from email.message import EmailMessage
import threading
import os

# --- CONFIGURE EMAIL HERE ---
EMAIL_SENDER = "pavan08045@gmail.com" 
EMAIL_PASSWORD = "dtrprbzayfecpyfu"
EMAIL_RECEIVER = "saimaneesh2005@gmail.com"

def send_alert_email(snapshot_path, person_name, timestamp):
    if "your_email" in EMAIL_SENDER:
        print(f"[MOCK EMAIL] Alert for {person_name}. Config in notifications.py")
        return

    def _send():
        try:
            msg = EmailMessage()
            msg['Subject'] = f"🚨 SECURITY ALERT: {person_name}"
            msg['From'] = EMAIL_SENDER
            msg['To'] = EMAIL_RECEIVER
            msg.set_content(f"Banned person detected at {timestamp}.")

            if os.path.exists(snapshot_path):
                with open(snapshot_path, 'rb') as f:
                    msg.add_attachment(f.read(), maintype='image', subtype='jpeg', filename='alert.jpg')

            with smtplib.SMTP_SSL('smtp.gmail.com', 465) as smtp:
                smtp.login(EMAIL_SENDER, EMAIL_PASSWORD)
                smtp.send_message(msg)
            print("[EMAIL SENT]")
        except Exception as e: print(f"[EMAIL FAIL] {e}")

    threading.Thread(target=_send).start()
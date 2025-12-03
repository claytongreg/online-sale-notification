#!/usr/bin/env python3
"""
POS Email Monitor and SMS Alert System
Monitors emails for POS notifications and sends SMS via Twilio for specific items
Also forwards matching emails to specified address
Features:
- Exact phrase matching in email body
- JSON-based duplicate prevention
- Email forwarding
- SMS alerts
"""

import imaplib
import email
from email.header import decode_header
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
import smtplib
import json
from datetime import datetime
import time
from twilio.rest import Client
import os
from pathlib import Path

# Load environment variables from .env file
try:
    from dotenv import load_dotenv

    load_dotenv()
except ImportError:
    print("Warning: python-dotenv not installed. Using system environment variables.")
    print("Install with: pip install python-dotenv")

# Configuration
EMAIL_ACCOUNT = os.getenv('EMAIL_ACCOUNT', 'your-email@gmail.com')
EMAIL_PASSWORD = os.getenv('EMAIL_PASSWORD', 'your-app-password')
IMAP_SERVER = os.getenv('IMAP_SERVER', 'imap.gmail.com')
IMAP_PORT = int(os.getenv('IMAP_PORT', '993'))
SMTP_SERVER = os.getenv('SMTP_SERVER', 'smtp.gmail.com')
SMTP_PORT = int(os.getenv('SMTP_PORT', '587'))

# Twilio Configuration
TWILIO_ACCOUNT_SID = os.getenv('TWILIO_ACCOUNT_SID', 'your-account-sid')
TWILIO_AUTH_TOKEN = os.getenv('TWILIO_AUTH_TOKEN', 'your-auth-token')
TWILIO_PHONE_NUMBER = os.getenv('TWILIO_PHONE_NUMBER', '+1234567890')
ALERT_PHONE_NUMBER = os.getenv('ALERT_PHONE_NUMBER', '+1234567890')

# Email forwarding configuration
FORWARD_TO_EMAIL = os.getenv('FORWARD_TO_EMAIL', 'info@ssiwellness.com')

# EXACT phrase that must appear in email body
EXACT_MATCH_PHRASE = os.getenv('EXACT_MATCH_PHRASE',
                               'ONLINE SALE - Auto-Pay Adult Fitness Membership with Card Key')

# Email monitoring settings
CHECK_INTERVAL = int(os.getenv('CHECK_INTERVAL', '60'))  # seconds
SENDER_EMAIL = os.getenv('SENDER_EMAIL', 'noreply@yourpos.com')  # Filter by sender
SUBJECT_FILTER = os.getenv('SUBJECT_FILTER', 'Sale Has Been Made Notification')  # Filter by subject

# Processed emails tracking file
PROCESSED_EMAILS_DIR = 'processed_emails'


class POSEmailMonitor:
    def __init__(self):
        self.twilio_client = Client(TWILIO_ACCOUNT_SID, TWILIO_AUTH_TOKEN)
        Path(PROCESSED_EMAILS_DIR).mkdir(exist_ok=True)
        self.processed_emails = self.load_processed_emails()

    def get_latest_json_file(self):
        """Get the most recent processed emails JSON file"""
        files = list(Path(PROCESSED_EMAILS_DIR).glob('processed_*.json'))
        if files:
            latest = max(files, key=lambda f: f.stat().st_mtime)
            return latest
        return None

    def cleanup_old_files(self, keep_file):
        """Delete all JSON files except the one to keep"""
        for file in Path(PROCESSED_EMAILS_DIR).glob('processed_*.json'):
            if file != keep_file:
                try:
                    file.unlink()
                    print(f"Deleted old file: {file}")
                except Exception as e:
                    print(f"Could not delete {file}: {e}")

    def load_processed_emails(self):
        """Load processed emails from most recent JSON file"""
        latest_file = self.get_latest_json_file()
        if latest_file:
            try:
                with open(latest_file, 'r') as f:
                    data = json.load(f)
                    print(f"Loaded {len(data)} previously processed emails from {latest_file}")
                    return data
            except Exception as e:
                print(f"Warning: Could not load {latest_file}: {e}")
                return {}
        return {}

    def save_processed_emails(self):
        """Save processed emails to new timestamped JSON file"""
        try:
            timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
            new_file = Path(PROCESSED_EMAILS_DIR) / f'processed_{timestamp}.json'
            with open(new_file, 'w') as f:
                json.dump(self.processed_emails, f, indent=2)
            print(f"Saved to {new_file}")
            # Clean up old files
            self.cleanup_old_files(new_file)
        except Exception as e:
            print(f"Warning: Could not save: {e}")

    def mark_as_processed(self, message_id, email_date, subject, body_preview):
        """Mark an email as processed and save to JSON"""
        self.processed_emails[message_id] = {
            'processed_at': datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
            'email_date': email_date,
            'subject': subject,
            'body_preview': body_preview[:100] + '...' if len(body_preview) > 100 else body_preview
        }
        self.save_processed_emails()

    def is_already_processed(self, message_id):
        """Check if email was already processed"""
        return message_id in self.processed_emails

    def connect_to_email(self):
        """Connect to email server via IMAP"""
        try:
            mail = imaplib.IMAP4_SSL(IMAP_SERVER, IMAP_PORT)
            mail.login(EMAIL_ACCOUNT, EMAIL_PASSWORD)
            return mail
        except Exception as e:
            print(f"Error connecting to email: {e}")
            return None

    def decode_email_subject(self, subject):
        """Decode email subject"""
        if subject is None:
            return ""
        decoded = decode_header(subject)
        subject_parts = []
        for content, encoding in decoded:
            if isinstance(content, bytes):
                try:
                    subject_parts.append(content.decode(encoding or 'utf-8'))
                except:
                    subject_parts.append(content.decode('utf-8', errors='ignore'))
            else:
                subject_parts.append(content)
        return ''.join(subject_parts)

    def extract_email_body(self, msg):
        """Extract email body text"""
        body = ""
        if msg.is_multipart():
            for part in msg.walk():
                content_type = part.get_content_type()
                if content_type == "text/plain":
                    try:
                        body = part.get_payload(decode=True).decode()
                        break
                    except:
                        pass
        else:
            try:
                body = msg.get_payload(decode=True).decode()
            except:
                pass
        return body

    def contains_exact_phrase(self, body):
        """Check if email body contains the EXACT phrase (case-insensitive)"""
        # Normalize whitespace and compare case-insensitively
        body_normalized = ' '.join(body.split())
        phrase_normalized = ' '.join(EXACT_MATCH_PHRASE.split())

        return phrase_normalized.lower() in body_normalized.lower()

    def forward_email(self, original_msg, email_body):
        """Forward the email to specified address"""
        try:
            print(f"Forwarding email to {FORWARD_TO_EMAIL}...")

            # Create forwarded message
            forward_msg = MIMEMultipart()
            forward_msg['From'] = EMAIL_ACCOUNT
            forward_msg['To'] = FORWARD_TO_EMAIL
            forward_msg['Subject'] = f"FWD: Online Sale Alert - {EXACT_MATCH_PHRASE}"

            # Create body with original email info
            body = f"""Online sale notification forwarded from POS system.

Item Detected: {EXACT_MATCH_PHRASE}
Time: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}

--- Original Email ---
From: {original_msg.get('From', 'Unknown')}
Subject: {self.decode_email_subject(original_msg.get('Subject', ''))}
Date: {original_msg.get('Date', 'Unknown')}

{email_body}
"""

            forward_msg.attach(MIMEText(body, 'plain'))

            # Send via SMTP
            with smtplib.SMTP(SMTP_SERVER, SMTP_PORT) as server:
                server.starttls()
                server.login(EMAIL_ACCOUNT, EMAIL_PASSWORD)
                server.send_message(forward_msg)

            print(f"✓ Email forwarded successfully to {FORWARD_TO_EMAIL}")
            return True

        except Exception as e:
            print(f"✗ Error forwarding email: {e}")
            return False

    def send_sms_alert(self):
        """Send SMS alert via Twilio"""
        try:
            message_body = "🔔 An online sale has been made!"

            message = self.twilio_client.messages.create(
                body=message_body,
                from_=TWILIO_PHONE_NUMBER,
                to=ALERT_PHONE_NUMBER
            )
            print(f"✓ SMS sent successfully (SID: {message.sid})")
            return True
        except Exception as e:
            print(f"✗ Error sending SMS: {e}")
            return False

    def process_email(self, mail, email_id):
        """Process a single email"""
        try:
            # Fetch the email
            status, msg_data = mail.fetch(email_id, '(RFC822)')
            if status != 'OK':
                return

            # Parse email
            raw_email = msg_data[0][1]
            msg = email.message_from_bytes(raw_email)

            # Get unique identifier
            message_id = msg.get('Message-ID', '')
            if not message_id:
                print("⚠ Email has no Message-ID, skipping...")
                return

            # Check if already processed (using JSON tracking)
            if self.is_already_processed(message_id):
                print(f"⚠ Email already processed (Message-ID: {message_id[:50]}...), skipping")
                return

            # Get sender and subject
            sender = msg.get('From', '')
            subject = self.decode_email_subject(msg.get('Subject', ''))

            # Check if subject matches our filter
            if SUBJECT_FILTER and SUBJECT_FILTER.lower() not in subject.lower():
                return  # Subject doesn't match

            # Get email body
            body = self.extract_email_body(msg)
            email_date = msg.get('Date', 'Unknown')

            print(f"\n{'=' * 70}")
            print(f"Processing email: {subject}")
            print(f"From: {sender}")
            print(f"Date: {email_date}")
            print(f"Message-ID: {message_id[:50]}...")
            print(f"{'=' * 70}")

            # Check if email contains EXACT phrase
            if self.contains_exact_phrase(body):
                print(f"✓ Email contains EXACT phrase: '{EXACT_MATCH_PHRASE}'")

                # Forward the email
                self.forward_email(msg, body)

                # Send SMS alert
                self.send_sms_alert()

                # Mark as processed
                self.mark_as_processed(message_id, email_date, subject, body)
                print(f"✓ Email marked as processed and saved")
            else:
                print(f"✗ Email does NOT contain exact phrase: '{EXACT_MATCH_PHRASE}'")
                print(f"   Searched in {len(body)} characters of email body")
                # Still mark as processed to avoid checking again
                self.mark_as_processed(message_id, email_date, subject, "No match - not forwarded")

        except Exception as e:
            print(f"Error processing email: {e}")
            import traceback
            traceback.print_exc()

    def check_new_emails(self):
        """Check for new emails and process them"""
        mail = self.connect_to_email()
        if not mail:
            return

        try:
            # Select inbox
            mail.select('INBOX')

            # Search for emails with specific subject (checks ALL emails, not just unread)
            if SUBJECT_FILTER:
                search_criteria = f'(SUBJECT "{SUBJECT_FILTER}")'
            else:
                search_criteria = 'ALL'

            status, messages = mail.search(None, search_criteria)

            if status == 'OK':
                email_ids = messages[0].split()

                if email_ids:
                    print(f"\nFound {len(email_ids)} email(s) matching criteria")
                    for email_id in email_ids:
                        self.process_email(mail, email_id)
                else:
                    print(".", end="", flush=True)

        except Exception as e:
            print(f"Error checking emails: {e}")
        finally:
            try:
                mail.close()
                mail.logout()
            except:
                pass

    def run(self):
        """Main monitoring loop"""
        print("=" * 70)
        print("POS Email Monitor - Starting...")
        print("=" * 70)
        print(f"Email: {EMAIL_ACCOUNT}")
        print(f"IMAP Server: {IMAP_SERVER}:{IMAP_PORT}")
        print(f"SMTP Server: {SMTP_SERVER}:{SMTP_PORT}")
        print(f"Subject filter: '{SUBJECT_FILTER}'")
        print(f"Exact phrase match: '{EXACT_MATCH_PHRASE}'")
        print(f"Forward to: {FORWARD_TO_EMAIL}")
        print(f"Alert phone: {ALERT_PHONE_NUMBER}")
        print(f"Check interval: {CHECK_INTERVAL} seconds")
        print(f"Tracking directory: {PROCESSED_EMAILS_DIR}")
        print(f"Previously processed: {len(self.processed_emails)} emails")
        print()
        print("NOTE: Checking ALL emails (read and unread) with subject filter")
        print("      Sender filter: DISABLED")
        print("=" * 70)

        while True:
            try:
                self.check_new_emails()
                time.sleep(CHECK_INTERVAL)
            except KeyboardInterrupt:
                print("\n\nStopping monitor...")
                print(f"Total emails processed this session: {len(self.processed_emails)}")
                break
            except Exception as e:
                print(f"\nUnexpected error: {e}")
                time.sleep(CHECK_INTERVAL)


if __name__ == "__main__":
    monitor = POSEmailMonitor()
    monitor.run()

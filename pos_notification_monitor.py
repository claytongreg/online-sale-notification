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
ALERT_PHONE_NUMBER_2 = os.getenv('ALERT_PHONE_NUMBER_2', '')  # Optional second number

# Email forwarding configuration
FORWARD_TO_EMAIL = os.getenv('FORWARD_TO_EMAIL', 'info@ssiwellness.com')

# Google Sheets configuration
GOOGLE_SHEETS_CREDS = os.getenv('GOOGLE_SHEETS_CREDS', '')  # JSON string of service account credentials
GOOGLE_SHEET_ID = os.getenv('GOOGLE_SHEET_ID', '1nXKE_2bIZ5eDKPPyQKiXDEL6VrGWddXBzBNflBQ1yCo')
LOCKBOX_CODE = os.getenv('LOCKBOX_CODE', '1125')

# EXACT phrase that must appear in email body
EXACT_MATCH_PHRASE = os.getenv('EXACT_MATCH_PHRASE', 'ONLINE SALE')

# Email monitoring settings
CHECK_INTERVAL = int(os.getenv('CHECK_INTERVAL', '60'))  # seconds
SENDER_EMAIL = os.getenv('SENDER_EMAIL', 'noreply@yourpos.com')  # Filter by sender
SUBJECT_FILTER = os.getenv('SUBJECT_FILTER', 'Sale Has Been Made Notification')  # Filter by subject


class POSEmailMonitor:
    def __init__(self):
        self.twilio_client = Client(TWILIO_ACCOUNT_SID, TWILIO_AUTH_TOKEN)

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

    def get_next_available_card(self):
        """Find the next unassigned card key from Google Sheets and return count"""
        try:
            import gspread
            from oauth2client.service_account import ServiceAccountCredentials
            import json
            
            if not GOOGLE_SHEETS_CREDS:
                print("⚠ Google Sheets credentials not configured")
                return None, None, None, 0
            
            # Parse credentials from environment variable
            creds_dict = json.loads(GOOGLE_SHEETS_CREDS)
            
            # Set up credentials
            scope = ['https://spreadsheets.google.com/feeds',
                     'https://www.googleapis.com/auth/drive']
            creds = ServiceAccountCredentials.from_json_keyfile_dict(creds_dict, scope)
            client = gspread.authorize(creds)
            
            # Open the sheet named "Lock Box Keys"
            sheet = client.open_by_key(GOOGLE_SHEET_ID).worksheet('Lock Box Keys')
            
            # Get all rows
            all_rows = sheet.get_all_values()
            
            # Count all available cards and find first available
            available_cards = []
            first_available = None
            first_available_row = None
            
            # Skip header row (row 1) and empty row (row 2)
            # Start from row 3 (index 2)
            for i, row in enumerate(all_rows[2:], start=3):  # Start counting from row 3
                letter = row[0] if len(row) > 0 else ""
                card_number = row[1] if len(row) > 1 else ""
                given_to = row[2] if len(row) > 2 else ""
                
                # If "Given to" column (C) is empty, this card is available
                if letter and card_number and not given_to.strip():
                    available_cards.append((letter, card_number, i))
                    if first_available is None:
                        first_available = (letter, card_number)
                        first_available_row = i
            
            total_available = len(available_cards)
            print(f"ℹ Total available cards: {total_available}")
            
            if first_available:
                letter, card_number = first_available
                print(f"✓ Found available card: {letter} - {card_number} at row {first_available_row}")
                return letter, card_number, first_available_row, total_available
            
            print("⚠ No available cards found in sheet")
            return None, None, None, 0
            
        except Exception as e:
            print(f"✗ Error accessing Google Sheets: {e}")
            import traceback
            traceback.print_exc()
            return None, None, None, 0

    def assign_card_to_customer(self, row_number, customer_name):
        """Update Google Sheet with customer name and date"""
        try:
            import gspread
            from oauth2client.service_account import ServiceAccountCredentials
            import json
            
            if not GOOGLE_SHEETS_CREDS:
                print("⚠ Google Sheets credentials not configured")
                return False
            
            # Parse credentials
            creds_dict = json.loads(GOOGLE_SHEETS_CREDS)
            scope = ['https://spreadsheets.google.com/feeds',
                     'https://www.googleapis.com/auth/drive']
            creds = ServiceAccountCredentials.from_json_keyfile_dict(creds_dict, scope)
            client = gspread.authorize(creds)
            
            # Open the sheet named "Lock Box Keys"
            sheet = client.open_by_key(GOOGLE_SHEET_ID).worksheet('Lock Box Keys')
            
            # Update column C (Given to), D (Date), and E (By Whom)
            today = datetime.now().strftime('%b %d, %Y')
            sheet.update_cell(row_number, 3, customer_name)  # Column C - Given to
            sheet.update_cell(row_number, 4, today)  # Column D - Date
            sheet.update_cell(row_number, 5, "Automated")  # Column E - By Whom
            
            print(f"✓ Updated Google Sheet: Row {row_number}, {today}")
            return True
            
        except Exception as e:
            print(f"✗ Error updating Google Sheets: {e}")
            import traceback
            traceback.print_exc()
            return False

    def send_low_inventory_alert(self, remaining_cards):
        """Send email alert when card inventory is critically low (1 or 0)"""
        try:
            print(f"Sending low inventory alert ({remaining_cards} remaining)...")
            
            msg = MIMEMultipart()
            msg['From'] = EMAIL_ACCOUNT
            msg['To'] = FORWARD_TO_EMAIL
            msg['Subject'] = "URGENT: ACCESS CARDS CRITICALLY LOW"
            
            body = f"""URGENT ALERT

The access card inventory is critically low!

Remaining cards after assignment: {remaining_cards}

Please restock the lockbox immediately to avoid disruption to new member sign-ups.

This is an automated alert from the POS Email Monitor system.
"""
            
            msg.attach(MIMEText(body, 'plain'))
            
            with smtplib.SMTP(SMTP_SERVER, SMTP_PORT) as server:
                server.starttls()
                server.login(EMAIL_ACCOUNT, EMAIL_PASSWORD)
                server.send_message(msg)
            
            print(f"✓ Low inventory alert sent")
            return True
            
        except Exception as e:
            print(f"✗ Error sending low inventory alert: {e}")
            return False

    def send_no_cards_alert(self, customer_name=None):
        """Send email and SMS when no cards are available to assign"""
        try:
            print(f"Sending NO CARDS alert...")
            
            # Send email
            msg = MIMEMultipart()
            msg['From'] = EMAIL_ACCOUNT
            msg['To'] = FORWARD_TO_EMAIL
            msg['Subject'] = "URGENT: ONLINE BOOKING FAILED - NO ACCESS CARDS"
            
            body = f"""URGENT ALERT - ACTION REQUIRED

An online booking could not be completed because there are NO access cards available!

Customer: {customer_name if customer_name else 'Unknown'}
Time: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}

The customer received a welcome email asking them to contact info@ssiwellness.com for card pickup.

IMMEDIATE ACTION NEEDED: Restock access cards and contact the customer to arrange pickup.

This is an automated alert from the POS Email Monitor system.
"""
            
            msg.attach(MIMEText(body, 'plain'))
            
            with smtplib.SMTP(SMTP_SERVER, SMTP_PORT) as server:
                server.starttls()
                server.login(EMAIL_ACCOUNT, EMAIL_PASSWORD)
                server.send_message(msg)
            
            print(f"✓ No cards alert email sent")
            
            # Send SMS to both numbers
            try:
                sms_body = "URGENT: ONLINE BOOKING FAILED - NO ACCESS CARDS AVAILABLE"
                message = self.twilio_client.messages.create(
                    body=sms_body,
                    from_=TWILIO_PHONE_NUMBER,
                    to=ALERT_PHONE_NUMBER
                )
                print(f"✓ No cards alert SMS sent to primary number")
                
                if ALERT_PHONE_NUMBER_2:
                    message2 = self.twilio_client.messages.create(
                        body=sms_body,
                        from_=TWILIO_PHONE_NUMBER,
                        to=ALERT_PHONE_NUMBER_2
                    )
                    print(f"✓ No cards alert SMS sent to second number")
            except Exception as sms_error:
                print(f"✗ SMS error: {sms_error}")
            
            return True
            
        except Exception as e:
            print(f"✗ Error sending no cards alert: {e}")
            return False

    def extract_customer_info(self, body):
        """Extract customer email and name from email body"""
        import re
        
        # Look for email pattern
        email_pattern = r'([a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,})'
        emails = re.findall(email_pattern, body)
        
        # Filter out the wellness living system emails
        customer_email = None
        for email_addr in emails:
            if 'wellnessliving.com' not in email_addr.lower() and 'ssiwellness.com' not in email_addr.lower():
                customer_email = email_addr
                break
        
        if not customer_email:
            return None, None
        
        # Try to find the name - it's usually on the line after the email
        lines = body.split('\n')
        customer_name = None
        for i, line in enumerate(lines):
            if customer_email in line:
                # Check next few lines for the name
                for j in range(i+1, min(i+5, len(lines))):
                    next_line = lines[j].strip()
                    # Skip empty lines and common footer text
                    if next_line and 'Wishing' not in next_line and 'SSI' not in next_line and '@' not in next_line:
                        # Strip HTML tags
                        customer_name = re.sub(r'<[^>]+>', '', next_line).strip()
                        break
                break
        
        return customer_email, customer_name

    def send_customer_email(self, customer_email, customer_name, card_letter=None, card_number=None):
        """Send a welcome email to the customer with card key instructions"""
        try:
            print(f"Sending welcome email to customer...")
            
            msg = MIMEMultipart()
            msg['From'] = EMAIL_ACCOUNT
            msg['To'] = customer_email
            msg['Cc'] = FORWARD_TO_EMAIL  # CC to info@ssiwellness.com
            msg['Subject'] = "Welcome to SSI Wellness Centre - Your Card Key Info"
            
            # Create email body with card key instructions
            if card_letter and card_number:
                body = f"""Hello {customer_name if customer_name else 'there'},

Thank you for your recent purchase at Salt Spring Island Wellness Centre!

We're excited to have you as a member. Your membership is now active.

TO ACCESS THE FACILITY:

1. Find the lockbox on the side of the bulletin board at the front door
2. The lockbox code is: {LOCKBOX_CODE}
3. Inside the lockbox, take the card key labeled with black sharpie: {card_letter} (card number: {card_number})
4. Use this card key to access the facility during your membership

IMPORTANT FACILITY RULES:

• Always wear indoor footwear
• Sign in EVERY time, even when entering with someone else
• Clean up after yourself
• Card keys are $20 if lost

Please keep your card key safe and follow all facility rules.

If you have any questions, please don't hesitate to reach out.

Best regards,
SSI Wellness Centre Team
info@ssiwellness.com
"""
            else:
                # Fallback if no card available
                body = f"""Hello {customer_name if customer_name else 'there'},

Thank you for your recent purchase at Salt Spring Island Wellness Centre!

We're excited to have you as a member. Your membership is now active.

Please contact us at info@ssiwellness.com to arrange your card key pickup.

IMPORTANT FACILITY RULES:

• Always wear indoor footwear
• Sign in EVERY time, even when entering with someone else
• Clean up after yourself
• Card keys are $20 if lost

Best regards,
SSI Wellness Centre Team
info@ssiwellness.com
"""
            
            msg.attach(MIMEText(body, 'plain'))
            
            # Send via SMTP - include CC recipient
            recipients = [customer_email, FORWARD_TO_EMAIL]
            with smtplib.SMTP(SMTP_SERVER, SMTP_PORT) as server:
                server.starttls()
                server.login(EMAIL_ACCOUNT, EMAIL_PASSWORD)
                server.sendmail(EMAIL_ACCOUNT, recipients, msg.as_string())
            
            print(f"✓ Customer email sent successfully")
            return True
            
        except Exception as e:
            print(f"✗ Error sending customer email: {e}")
            return False

    def contains_exact_phrase(self, body):
        """Check if email body contains the EXACT phrase (case-insensitive)"""
        # Normalize whitespace and compare case-insensitively
        body_normalized = ' '.join(body.split())
        phrase_normalized = ' '.join(EXACT_MATCH_PHRASE.split())

        return phrase_normalized.lower() in body_normalized.lower()

    def forward_email(self, original_msg, email_body):
        """Forward the email to specified address"""
        try:
            print(f"Forwarding email...")

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

            print(f"✓ Email forwarded successfully")
            return True

        except Exception as e:
            print(f"✗ Error forwarding email: {e}")
            return False

    def send_sms_alert(self, customer_name=None, card_letter=None, card_number=None):
        """Send SMS alert via Twilio to all configured numbers"""
        try:
            if customer_name and card_letter and card_number:
                message_body = f"{customer_name} has purchased online, card key {card_letter} {card_number}"
            else:
                message_body = "🔔 An online sale has been made!"

            # Send to primary number
            message = self.twilio_client.messages.create(
                body=message_body,
                from_=TWILIO_PHONE_NUMBER,
                to=ALERT_PHONE_NUMBER
            )
            print(f"✓ SMS sent to primary number (SID: {message.sid})")
            
            # Send to second number if configured
            if ALERT_PHONE_NUMBER_2:
                message2 = self.twilio_client.messages.create(
                    body=message_body,
                    from_=TWILIO_PHONE_NUMBER,
                    to=ALERT_PHONE_NUMBER_2
                )
                print(f"✓ SMS sent to second number (SID: {message2.sid})")
            
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

            # Get sender and subject
            sender = msg.get('From', '')
            subject = self.decode_email_subject(msg.get('Subject', ''))
            message_id = msg.get('Message-ID', '')

            # Check if sender matches (must be from wellnessliving.com)
            if 'wellnessliving.com' not in sender.lower():
                print(f"⚠ Email not from wellnessliving.com, skipping")
                return

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

                # Extract customer info
                customer_email, customer_name = self.extract_customer_info(body)
                if customer_email:
                    print(f"✓ Customer info extracted successfully")
                else:
                    print("⚠ Could not extract customer email from notification")

                # Forward the email
                self.forward_email(msg, body)

                # Get next available card key and assign it
                card_letter = None
                card_number = None
                remaining_cards = 0
                
                if customer_email and customer_name:
                    card_letter, card_number, row_number, total_available = self.get_next_available_card()
                    
                    if card_letter and card_number and row_number:
                        # Assign the card in Google Sheets
                        self.assign_card_to_customer(row_number, customer_name)
                        
                        # Calculate remaining cards AFTER assignment
                        remaining_cards = total_available - 1
                        
                        # Send low inventory alert if 1 or 0 cards remaining
                        if remaining_cards <= 1:
                            self.send_low_inventory_alert(remaining_cards)
                    else:
                        # No cards available - send urgent alert
                        print("⚠ NO CARDS AVAILABLE - Sending urgent alerts")
                        self.send_no_cards_alert(customer_name)

                # Send SMS alert with customer info
                self.send_sms_alert(customer_name, card_letter, card_number)
                
                # Send customer email with card key info
                if customer_email:
                    self.send_customer_email(customer_email, customer_name, card_letter, card_number)

                print(f"✓ Email processed successfully")
            else:
                print(f"✗ Email does NOT contain exact phrase: '{EXACT_MATCH_PHRASE}'")
                print(f"   Searched in {len(body)} characters of email body")

        except Exception as e:
            print(f"Error processing email: {e}")
            import traceback
            traceback.print_exc()

    def check_new_emails(self):
        """Check for new UNSEEN emails and process them"""
        mail = self.connect_to_email()
        if not mail:
            return

        try:
            # Select inbox
            mail.select('INBOX')

            # Search for UNSEEN emails with specific subject
            if SUBJECT_FILTER:
                search_criteria = f'(UNSEEN SUBJECT "{SUBJECT_FILTER}")'
            else:
                search_criteria = 'UNSEEN'

            status, messages = mail.search(None, search_criteria)

            if status == 'OK':
                email_ids = messages[0].split()

                if email_ids:
                    print(f"\nFound {len(email_ids)} UNSEEN email(s) matching criteria")
                    for email_id in email_ids:
                        self.process_email(mail, email_id)
                else:
                    print("No new unread emails")

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
        print(f"Exact phrase match: [CONFIGURED]")
        print(f"Forward to: [CONFIGURED]")
        print(f"Alert phone: [CONFIGURED]")
        print(f"Check interval: {CHECK_INTERVAL} seconds")
        print()
        print("NOTE: Checking UNSEEN emails only with subject filter")
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

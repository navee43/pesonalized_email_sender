# #!/usr/bin/env python3
# """
# Send a job-application email, with your resume and cover letter attached,
# to each address in a CSV or TXT file - one separate email per recipient.

# Examples:
#     python send_emails.py contacts/recipients.csv --dry-run
#     python send_emails.py contacts/recipients.csv
#     python send_emails.py contacts/recipients.txt --delay 15

# Setup (Gmail App Password, .env, attachments) is explained in README.md.
# """

# from __future__ import annotations

# import argparse
# import csv
# import io
# import math
# import mimetypes
# import os
# import re
# import smtplib
# import socket
# import ssl
# import sys
# import time
# from dataclasses import dataclass, field
# from datetime import datetime
# from email.message import EmailMessage
# from email.utils import formataddr, formatdate, make_msgid
# from pathlib import Path
# from typing import Callable

# try:
#     from dotenv import load_dotenv
# except ImportError:  # pragma: no cover
#     sys.exit("Missing dependency 'python-dotenv'. Run: pip install -r requirements.txt")


# # =============================================================================
# # CONFIGURATION - edit this section
# # =============================================================================

# SENDER_NAME = "Naveen Yadav"  # the name recipients see in the "From" field

# EMAIL_SUBJECT = "Application for Software Engineer / Full Stack Developer Role"

# EMAIL_BODY = """\
# Dear Hiring Manager,

# I hope you are doing well.

# I am Naveen Yadav, A B.Tech Information Technology student at IIIT Una, and I am writing to express my interest in Software Engineer / Full Stack Developer opportunities at your organization.

# I have hands-on experience working with technologies such as JavaScript, TypeScript, React.js, Node.js, Express.js, MongoDB, REST APIs, and Generative AI. I have also worked on real-world projects involving full-stack development, AI integrations, real-time communication, and performance optimization.

# I have attached my resume and cover letter for your consideration. I would be grateful for the opportunity to discuss how my skills and experience could contribute to your team.

# Thank you for your time and consideration.

# Best regards,
# Naveen Yadav
# B.Tech Information Technology
# IIIT Una
# """

# BASE_DIR = Path(__file__).resolve().parent

# # The attachment file names are what recruiters see, so a name like
# # "Naveen_Yadav_Resume.pdf" looks better than "resume.pdf".
# RESUME_PATH = BASE_DIR / "attachments" / "resume.pdf"
# COVER_LETTER_PATH = BASE_DIR / "attachments" / "cover_letter.pdf"

# CONTACTS_DIR = BASE_DIR / "contacts"
# LOG_FILE = BASE_DIR / "logs" / "email_log.csv"

# DELAY_SECONDS = 1        # pause between two emails
# MAX_RETRIES = 2             # extra attempts after a temporary SMTP failure
# RETRY_WAIT_SECONDS = 5      # wait before the 1st retry; doubles for the next one
# SMTP_TIMEOUT_SECONDS = 30   # give up on an unresponsive server after this long

# # Gmail rejects messages over 25 MB, and attachments grow ~33% when encoded.
# MAX_ATTACHMENTS_MB = 18

# # Personal Gmail accounts can send to about 500 recipients per rolling 24 hours
# # (everything you send counts). Warn well before that.
# DAILY_VOLUME_WARNING = 400

# # CSV header names accepted as the email column. Compared case-insensitively
# # and ignoring spaces, "-" and "_", so "Email Address" and "E-mail" also match.
# EMAIL_COLUMN_NAMES = {"email", "emails", "emailaddress", "emailid", "mail"}

# # =============================================================================


# STATUS_SENT = "SENT"
# STATUS_FAILED = "FAILED"
# STATUS_INVALID = "INVALID"
# STATUS_INTERRUPTED = "INTERRUPTED"
# LOG_COLUMNS = ["timestamp", "email", "status", "error"]

# # Error kinds used to decide whether to retry, skip, or stop.
# TEMPORARY = "temporary"   # retry (network blip, 4xx reply)
# PERMANENT = "permanent"   # give up on this recipient, continue with the next
# FATAL = "fatal"           # stop the whole run (bad login, sending limit hit)

# PLACEHOLDER_VALUES = {"", "your_email@gmail.com", "your_app_password"}

# # Server replies that mean "you've hit a sending limit" - never keep retrying these.
# LIMIT_REPLY_PATTERN = re.compile(
#     r"5\.4\.5|sending limit|quota|too many login|unusual rate|rate limit",
#     re.IGNORECASE,
# )

# EMAIL_PATTERN = re.compile(
#     # local part: allowed characters, no leading/trailing/double dots
#     r"[A-Za-z0-9!#$%&'*+/=?^_`{|}~-]+(?:\.[A-Za-z0-9!#$%&'*+/=?^_`{|}~-]+)*"
#     # domain: one or more labels plus a real top-level domain
#     r"@(?:[A-Za-z0-9](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?\.)+[A-Za-z]{2,63}"
# )


# class UserError(Exception):
#     """A problem the user can fix (missing file, bad setting). Shown without a traceback."""


# class SendError(Exception):
#     """This email failed; move on to the next recipient."""


# class FatalSMTPError(Exception):
#     """Every remaining email would fail the same way, so stop the run."""


# @dataclass
# class Config:
#     host: str
#     port: int
#     address: str
#     password: str = field(repr=False)  # never shown in output or tracebacks


# @dataclass
# class RecipientList:
#     valid: list[str] = field(default_factory=list)
#     invalid: list[tuple[int, str]] = field(default_factory=list)  # (line number, value)
#     duplicates: int = 0
#     email_column: str | None = None


# @dataclass
# class Attachment:
#     path: Path
#     data: bytes = field(repr=False)
#     maintype: str
#     subtype: str


# @dataclass
# class Summary:
#     total: int
#     sent: int = 0
#     failed: int = 0
#     interrupted: int = 0      # Ctrl+C pressed mid-send; delivery unknown
#     stop_reason: str | None = None
#     cancelled: bool = False

#     @property
#     def not_attempted(self) -> int:
#         return self.total - self.sent - self.failed - self.interrupted


# # -----------------------------------------------------------------------------
# # Loading and validating input
# # -----------------------------------------------------------------------------

# def rel(path: Path) -> str:
#     """Show a path relative to the project folder when possible."""
#     try:
#         return str(path.resolve().relative_to(BASE_DIR))
#     except ValueError:
#         return str(path)


# def validate_email(address: str) -> bool:
#     """Return True if the address looks like a deliverable email address."""
#     if len(address) > 254 or "@" not in address:
#         return False
#     local_part = address.rsplit("@", 1)[0]
#     return len(local_part) <= 64 and EMAIL_PATTERN.fullmatch(address) is not None


# def clean_address(raw: str) -> str:
#     """Trim whitespace, quotes, <brackets> and a 'mailto:' prefix."""
#     value = raw.strip().strip("\"'<>").strip()
#     if value.lower().startswith("mailto:"):
#         value = value[len("mailto:"):].strip()
#     return value


# def _read_text(path: Path) -> str:
#     try:
#         raw = path.read_bytes()
#     except OSError as exc:
#         raise UserError(f"Could not read {rel(path)}: {exc.strerror or exc}") from None
#     try:
#         return raw.decode("utf-8-sig")  # also strips the BOM Excel adds
#     except UnicodeDecodeError:
#         return raw.decode("latin-1")


# def _rows_from_txt(path: Path) -> list[tuple[int, str]]:
#     rows = []
#     for line_no, line in enumerate(_read_text(path).splitlines(), start=1):
#         value = line.strip()
#         if value and not value.startswith("#"):  # skip blank lines and comments
#             rows.append((line_no, value))
#     return rows


# def _rows_from_csv(path: Path) -> tuple[list[tuple[int, str]], str]:
#     text = _read_text(path)
#     if not text.strip():
#         raise UserError(f"{rel(path)} is empty.")

#     try:  # Excel in some regions saves CSVs with ";" instead of ","
#         dialect = csv.Sniffer().sniff(text[:4096], delimiters=",;\t")
#     except csv.Error:
#         dialect = csv.excel

#     reader = csv.reader(io.StringIO(text), dialect)
#     header = next(reader, [])
#     normalized = [re.sub(r"[\s_\-]+", "", cell.lower()) for cell in header]
#     column = next((i for i, name in enumerate(normalized) if name in EMAIL_COLUMN_NAMES), None)

#     if column is None:
#         if any(validate_email(clean_address(cell)) for cell in header):
#             raise UserError(
#                 f"The first row of {rel(path)} is an email address, not a column name.\n"
#                 "Add a header row with the word 'email' above the addresses,\n"
#                 "or save the list as a .txt file with one address per line."
#             )
#         found = ", ".join(repr(cell) for cell in header) or "(none)"
#         raise UserError(
#             f"No 'email' column found in {rel(path)}.\n"
#             f"Columns found: {found}\n"
#             "Rename the column that holds the addresses to 'email'."
#         )

#     rows = []
#     for row in reader:
#         if column < len(row) and row[column].strip():
#             rows.append((reader.line_num, row[column]))
#     return rows, header[column].strip()


# def load_recipients(path: Path) -> RecipientList:
#     """Read addresses from a .csv or .txt file, dropping invalid ones and duplicates."""
#     suffix = path.suffix.lower()
#     column = None
#     if suffix == ".txt":
#         rows = _rows_from_txt(path)
#     elif suffix == ".csv":
#         rows, column = _rows_from_csv(path)
#     else:
#         raise UserError(
#             f"Unsupported file type '{path.suffix}'. Use a .csv or .txt file "
#             "(in Excel: File > Save As > CSV)."
#         )

#     result = RecipientList(email_column=column)
#     seen: set[str] = set()
#     for line_no, raw in rows:
#         address = clean_address(raw)
#         if not address:
#             continue
#         if not validate_email(address):
#             result.invalid.append((line_no, raw.strip()))
#             continue
#         key = address.lower()  # HR@Company.com and hr@company.com are the same inbox
#         if key in seen:
#             result.duplicates += 1
#             continue
#         seen.add(key)
#         result.valid.append(address)
#     return result


# def resolve_contacts_path(value: str) -> Path:
#     """Accept a path as given, or a bare file name inside the project or contacts/ folder."""
#     candidates = [Path(value), BASE_DIR / value, CONTACTS_DIR / value]
#     for candidate in candidates:
#         if candidate.is_file():
#             return candidate
#     raise UserError(f"Contacts file not found: {value} (also looked in {rel(CONTACTS_DIR)}/)")


# def read_already_sent(log_path: Path) -> set[str]:
#     """Addresses marked SENT in earlier runs, lower-cased."""
#     if not log_path.is_file():
#         return set()
#     try:
#         with log_path.open(newline="", encoding="utf-8") as f:
#             return {
#                 row["email"].strip().lower()
#                 for row in csv.DictReader(f)
#                 if row.get("status") == STATUS_SENT and row.get("email")
#             }
#     except (OSError, csv.Error, UnicodeDecodeError, KeyError):
#         print(f"Warning: could not read {rel(log_path)}; not skipping previously sent addresses.")
#         return set()


# def load_attachments(paths: list[Path]) -> list[Attachment]:
#     """Read every attachment up front so problems show before anything is sent."""
#     missing = [p for p in paths if not p.is_file()]
#     if missing:
#         listing = "\n".join(f"  - {rel(p)}" for p in missing)
#         raise UserError(
#             f"Attachment file(s) not found:\n{listing}\n"
#             "Put your files in the attachments/ folder, or change RESUME_PATH / "
#             "COVER_LETTER_PATH at the top of send_emails.py."
#         )

#     attachments = []
#     for path in paths:
#         try:
#             data = path.read_bytes()
#         except OSError as exc:
#             raise UserError(f"Could not read attachment {rel(path)}: {exc.strerror or exc}") from None
#         if not data:
#             raise UserError(f"Attachment {rel(path)} is empty (0 bytes).")
#         if path.suffix.lower() == ".pdf" and not data.startswith(b"%PDF"):
#             print(f"Warning: {rel(path)} does not look like a real PDF file.")
#         content_type = mimetypes.guess_type(path.name)[0] or "application/octet-stream"
#         maintype, subtype = content_type.split("/", 1)
#         attachments.append(Attachment(path, data, maintype, subtype))

#     total_mb = sum(len(a.data) for a in attachments) / (1024 * 1024)
#     if total_mb > MAX_ATTACHMENTS_MB:
#         raise UserError(
#             f"Attachments add up to {total_mb:.1f} MB; keep them under {MAX_ATTACHMENTS_MB} MB "
#             "(Gmail rejects emails over 25 MB once encoded). Compress the PDFs."
#         )
#     return attachments


# def load_config() -> Config:
#     """Read SMTP settings from .env (or real environment variables)."""
#     env_file = BASE_DIR / ".env"
#     load_dotenv(env_file)

#     host = os.getenv("SMTP_HOST", "smtp.gmail.com").strip()
#     port_text = os.getenv("SMTP_PORT", "587").strip()
#     address = os.getenv("EMAIL_ADDRESS", "").strip()
#     password = os.getenv("EMAIL_PASSWORD", "").strip()

#     problems = []
#     if not host:
#         problems.append("SMTP_HOST is empty")
#     try:
#         port = int(port_text)
#         if not 0 < port < 65536:
#             raise ValueError
#     except ValueError:
#         problems.append(f"SMTP_PORT must be a port number such as 587 (got {port_text!r})")
#         port = 0
#     if address in PLACEHOLDER_VALUES:
#         problems.append("EMAIL_ADDRESS is not set")
#     elif not validate_email(address):
#         problems.append("EMAIL_ADDRESS is not a valid email address")
#     if password in PLACEHOLDER_VALUES:
#         problems.append("EMAIL_PASSWORD is not set")

#     if problems:
#         details = "\n".join(f"  - {p}" for p in problems)
#         hint = "" if env_file.is_file() else "\nNo .env file found: copy .env.example to .env and fill it in."
#         raise UserError(f"Email settings in .env are incomplete:\n{details}{hint}\nSee README.md, step 2.")

#     if host.lower().endswith(("gmail.com", "googlemail.com")):
#         # Google displays App Passwords as "abcd efgh ijkl mnop"; the spaces aren't part of it.
#         password = re.sub(r"\s+", "", password)

#     return Config(host=host, port=port, address=address, password=password)


# # -----------------------------------------------------------------------------
# # Building and sending email
# # -----------------------------------------------------------------------------

# def create_email(sender: str, recipient: str, attachments: list[Attachment]) -> EmailMessage:
#     """One message addressed to exactly one recipient - no CC or BCC."""
#     message = EmailMessage()
#     message["From"] = formataddr((SENDER_NAME, sender)) if SENDER_NAME else sender
#     message["To"] = recipient
#     message["Subject"] = EMAIL_SUBJECT
#     message["Date"] = formatdate(localtime=True)
#     message["Message-ID"] = make_msgid(domain=sender.rsplit("@", 1)[-1])
#     message.set_content(EMAIL_BODY)
#     for attachment in attachments:
#         message.add_attachment(
#             attachment.data,
#             maintype=attachment.maintype,
#             subtype=attachment.subtype,
#             filename=attachment.path.name,
#         )
#     return message


# class SMTPSession:
#     """A logged-in SMTP connection that reopens itself if it drops."""

#     def __init__(self, config: Config) -> None:
#         self.config = config
#         self.server: smtplib.SMTP | None = None

#     def connect(self) -> None:
#         self.close()
#         context = ssl.create_default_context()
#         if self.config.port == 465:  # implicit TLS
#             server: smtplib.SMTP = smtplib.SMTP_SSL(
#                 self.config.host, self.config.port, timeout=SMTP_TIMEOUT_SECONDS, context=context
#             )
#         else:  # STARTTLS, e.g. port 587
#             server = smtplib.SMTP(self.config.host, self.config.port, timeout=SMTP_TIMEOUT_SECONDS)
#         try:
#             if self.config.port != 465:
#                 server.ehlo()
#                 server.starttls(context=context)
#                 server.ehlo()
#             server.login(self.config.address, self.config.password)
#         except BaseException:
#             self._shutdown(server)
#             raise
#         self.server = server

#     def send(self, message: EmailMessage) -> None:
#         if self.server is None:
#             self.connect()
#         assert self.server is not None
#         self.server.send_message(message)

#     def close(self) -> None:
#         if self.server is not None:
#             self._shutdown(self.server)
#             self.server = None

#     @staticmethod
#     def _shutdown(server: smtplib.SMTP) -> None:
#         try:
#             server.quit()
#         except Exception:
#             server.close()


# def _server_text(exc: smtplib.SMTPResponseException) -> str:
#     text = exc.smtp_error.decode(errors="replace") if isinstance(exc.smtp_error, bytes) else str(exc.smtp_error)
#     text = " ".join(text.split())  # server replies often span several lines
#     return text if len(text) <= 160 else text[:157] + "..."


# def classify_error(exc: BaseException) -> tuple[str, str]:
#     """Return (TEMPORARY | PERMANENT | FATAL, short readable reason)."""
#     if isinstance(exc, smtplib.SMTPAuthenticationError):
#         return FATAL, f"SMTP authentication failed ({exc.smtp_code} {_server_text(exc)})"
#     if isinstance(exc, smtplib.SMTPNotSupportedError):
#         return FATAL, f"Mail server does not support a required feature: {exc}"
#     if isinstance(exc, smtplib.SMTPRecipientsRefused):
#         code, reply = next(iter(exc.recipients.values()), (550, b""))
#         text = reply.decode(errors="replace") if isinstance(reply, bytes) else str(reply)
#         kind = TEMPORARY if 400 <= code < 500 else PERMANENT
#         return kind, f"Recipient refused ({code} {' '.join(text.split())[:120]})"
#     if isinstance(exc, smtplib.SMTPResponseException):
#         reply = f"{exc.smtp_code} {_server_text(exc)}"
#         if LIMIT_REPLY_PATTERN.search(reply):
#             return FATAL, f"Sending limit reached ({reply})"
#         return (TEMPORARY if 400 <= exc.smtp_code < 500 else PERMANENT), f"Server error ({reply})"
#     if isinstance(exc, smtplib.SMTPServerDisconnected):
#         if "timed out" in str(exc):  # smtplib reports read timeouts this way
#             return TEMPORARY, f"Connection timed out after {SMTP_TIMEOUT_SECONDS}s"
#         return TEMPORARY, "Server closed the connection"
#     if isinstance(exc, (socket.timeout, TimeoutError)):
#         return TEMPORARY, f"Connection timed out after {SMTP_TIMEOUT_SECONDS}s"
#     if isinstance(exc, ssl.SSLCertVerificationError):
#         return FATAL, f"TLS certificate check failed ({exc.verify_message})"
#     if isinstance(exc, ssl.SSLError):
#         return FATAL, f"TLS error ({exc.reason or exc}) - check that SMTP_PORT matches the server"
#     if isinstance(exc, socket.gaierror):
#         return TEMPORARY, "Could not look up the mail server (check SMTP_HOST and your internet)"
#     if isinstance(exc, ConnectionRefusedError):
#         return TEMPORARY, "Connection refused (check SMTP_HOST and SMTP_PORT)"
#     if isinstance(exc, OSError):
#         return TEMPORARY, f"Network error ({exc.strerror or exc})"
#     if isinstance(exc, smtplib.SMTPException):
#         return PERMANENT, f"SMTP error ({exc})"
#     return PERMANENT, f"Unexpected error ({type(exc).__name__}: {exc})"


# def hint_for(reason: str) -> str:
#     """A next step for the most common setup problems."""
#     lowered = reason.lower()
#     if "authentication" in lowered:
#         return ("Check EMAIL_ADDRESS and EMAIL_PASSWORD in .env. For Gmail, EMAIL_PASSWORD must be a\n"
#                 "16-character App Password, not your normal password (README.md, step 4).")
#     if "limit" in lowered:
#         return "Your provider has paused sending. Wait (up to 24 hours for Gmail) before trying again."
#     if "certificate" in lowered:
#         return ("Python can't verify the server's certificate. On macOS with python.org Python,\n"
#                 "run 'Install Certificates.command' from your Python folder.")
#     if "tls" in lowered:
#         return "Use SMTP_PORT=587 (STARTTLS) or SMTP_PORT=465 (SSL)."
#     if any(word in lowered for word in ("timed out", "refused", "look up", "network", "closed")):
#         return ("Check SMTP_HOST / SMTP_PORT and your internet connection. Some college and office\n"
#                 "networks block ports 587 and 465 - try another network or a phone hotspot.")
#     return "See 'Common SMTP errors' in README.md."


# def _with_retries(action: Callable[[], None], session: SMTPSession) -> None:
#     """Run action, retrying temporary failures. Raises SendError or FatalSMTPError."""
#     for attempt in range(1, MAX_RETRIES + 2):
#         try:
#             action()
#             return
#         except Exception as exc:
#             kind, reason = classify_error(exc)
#             if kind == FATAL:
#                 raise FatalSMTPError(reason) from exc
#             if kind == PERMANENT or attempt > MAX_RETRIES:
#                 raise SendError(reason) from exc
#             wait = RETRY_WAIT_SECONDS * 2 ** (attempt - 1)
#             print(f"{reason}; retry {attempt}/{MAX_RETRIES} in {wait}s ... ", end="", flush=True)
#             session.close()  # start the retry on a fresh connection
#             time.sleep(wait)


# def send_email(session: SMTPSession, message: EmailMessage) -> None:
#     """Send one message, retrying temporary SMTP failures up to MAX_RETRIES times."""
#     _with_retries(lambda: session.send(message), session)


# def log_result(log_path: Path, email: str, status: str, error: str = "") -> None:
#     """Append one row to the CSV log (header is written when the file is new)."""
#     try:
#         log_path.parent.mkdir(parents=True, exist_ok=True)
#         is_new = not log_path.exists() or log_path.stat().st_size == 0
#         with log_path.open("a", newline="", encoding="utf-8") as f:
#             writer = csv.writer(f)
#             if is_new:
#                 writer.writerow(LOG_COLUMNS)
#             writer.writerow([datetime.now().strftime("%Y-%m-%d %H:%M:%S"), email, status, error])
#     except OSError as exc:
#         print(f"\nWarning: could not write to {rel(log_path)} ({exc.strerror or exc}).")


# def ensure_log_writable(log_path: Path) -> None:
#     try:
#         log_path.parent.mkdir(parents=True, exist_ok=True)
#         with log_path.open("a", encoding="utf-8"):
#             pass
#     except OSError as exc:
#         raise UserError(
#             f"Can't write to the log file {rel(log_path)} ({exc.strerror or exc}).\n"
#             "If it's open in Excel, close it and run again."
#         ) from None


# def wait_between_emails(seconds: float) -> None:
#     if seconds <= 0:
#         return
#     if not sys.stdout.isatty():
#         print(f"Waiting {seconds:g} seconds...")
#         time.sleep(seconds)
#         return
#     end = time.monotonic() + seconds
#     while (left := end - time.monotonic()) > 0:
#         shown = math.ceil(left)
#         print(f"\rWaiting {shown} second{'s' if shown != 1 else ''}... ", end="", flush=True)
#         time.sleep(min(1.0, left))
#     print("\r" + " " * 32 + "\r", end="", flush=True)


# def send_all(
#     recipients: list[str],
#     config: Config,
#     attachments: list[Attachment],
#     delay: float,
#     log_path: Path,
# ) -> Summary:
#     """Sign in once, then send to each recipient in turn. Ctrl+C stops cleanly."""
#     summary = Summary(total=len(recipients))
#     session = SMTPSession(config)

#     print(f"Signing in to {config.host}:{config.port} as {config.address} ... ", end="", flush=True)
#     try:
#         _with_retries(session.connect, session)
#     except (SendError, FatalSMTPError) as exc:
#         print("FAILED")
#         raise UserError(f"Could not sign in to the mail server: {exc}\n{hint_for(str(exc))}\n"
#                         "No emails were sent.") from None
#     print("OK")
#     print("\nStarting email delivery...\n")

#     in_flight: str | None = None
#     try:
#         for index, recipient in enumerate(recipients, start=1):
#             print(f"[{index}/{summary.total}] Sending to {recipient} ... ", end="", flush=True)
#             in_flight = recipient
#             try:
#                 send_email(session, create_email(config.address, recipient, attachments))
#             except SendError as exc:
#                 in_flight = None
#                 summary.failed += 1
#                 print(f"FAILED ({exc})")
#                 log_result(log_path, recipient, STATUS_FAILED, str(exc))
#             except FatalSMTPError as exc:
#                 in_flight = None
#                 summary.failed += 1
#                 summary.stop_reason = f"{exc}\n{hint_for(str(exc))}"
#                 print(f"FAILED ({exc})")
#                 log_result(log_path, recipient, STATUS_FAILED, str(exc))
#                 break
#             else:
#                 in_flight = None
#                 summary.sent += 1
#                 print("SENT")
#                 log_result(log_path, recipient, STATUS_SENT)

#             if index < summary.total:
#                 wait_between_emails(delay)
#     except KeyboardInterrupt:
#         summary.cancelled = True
#         summary.stop_reason = "Stopped with Ctrl+C."
#         if in_flight is not None:
#             summary.interrupted += 1
#             print("INTERRUPTED")
#             log_result(log_path, in_flight, STATUS_INTERRUPTED,
#                        "Stopped with Ctrl+C while sending; delivery unknown")
#         else:
#             print()
#     finally:
#         session.close()
#     return summary


# # -----------------------------------------------------------------------------
# # Command line
# # -----------------------------------------------------------------------------

# def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
#     parser = argparse.ArgumentParser(
#         description="Send your job application email to each address in a CSV or TXT file, one at a time.",
#         epilog=(
#             "examples:\n"
#             "  python send_emails.py contacts/recipients.csv --dry-run\n"
#             "  python send_emails.py contacts/recipients.csv\n"
#             "  python send_emails.py contacts/recipients.txt --delay 15"
#         ),
#         formatter_class=argparse.RawDescriptionHelpFormatter,
#     )
#     parser.add_argument("contacts", help="a .csv file with an 'email' column, or a .txt file with one address per line")
#     parser.add_argument("--delay", type=float, default=DELAY_SECONDS,
#                         help=f"seconds to wait between emails (default: {DELAY_SECONDS})")
#     parser.add_argument("--dry-run", action="store_true",
#                         help="show who would be emailed and check your setup, without sending anything")
#     parser.add_argument("--resend", action="store_true",
#                         help="also email addresses already marked SENT in the log")
#     parser.add_argument("--limit", type=int, metavar="N",
#                         help="send to at most N recipients this run")
#     parser.add_argument("--resume-file", type=Path, default=RESUME_PATH, metavar="PATH",
#                         help=f"resume to attach (default: {rel(RESUME_PATH)})")
#     parser.add_argument("--cover-letter-file", type=Path, default=COVER_LETTER_PATH, metavar="PATH",
#                         help=f"cover letter to attach (default: {rel(COVER_LETTER_PATH)})")
#     args = parser.parse_args(argv)
#     if args.delay < 0:
#         parser.error("--delay can't be negative")
#     if args.limit is not None and args.limit < 1:
#         parser.error("--limit must be at least 1")
#     return args


# def _size(num_bytes: int) -> str:
#     if num_bytes < 1024:
#         return f"{num_bytes} bytes"
#     if num_bytes < 1024 * 1024:
#         return f"{num_bytes / 1024:.0f} KB"
#     return f"{num_bytes / 1024 / 1024:.1f} MB"


# def report_loaded(loaded: RecipientList) -> None:
#     print(f"Found {len(loaded.valid)} valid email address{'es' if len(loaded.valid) != 1 else ''}.")
#     if loaded.invalid:
#         print(f"Skipping {len(loaded.invalid)} invalid:")
#         for line_no, value in loaded.invalid[:10]:
#             print(f"  line {line_no}: {value}")
#         if len(loaded.invalid) > 10:
#             print(f"  ... and {len(loaded.invalid) - 10} more")
#     if loaded.duplicates:
#         print(f"Ignored {loaded.duplicates} duplicate{'s' if loaded.duplicates != 1 else ''}.")


# def dry_run(recipients: list[str], attachment_paths: list[Path], delay: float) -> int:
#     print("\nWould send to:")
#     for number, address in enumerate(recipients, start=1):
#         print(f"{number}. {address}")

#     print("\nSetup check:")
#     ready = True
#     attachments: list[Attachment] = []
#     try:
#         attachments = load_attachments(attachment_paths)
#         names = ", ".join(f"{a.path.name} ({_size(len(a.data))})" for a in attachments)
#         print(f"  Attachments : OK - {names}")
#     except UserError as exc:
#         ready = False
#         print("  Attachments : " + str(exc).replace("\n", "\n                "))

#     sender = ""
#     try:
#         config = load_config()
#         sender = config.address
#         print(f"  Sender      : OK - {config.address} via {config.host}:{config.port} (login not tested)")
#     except UserError as exc:
#         ready = False
#         print("  Sender      : " + str(exc).replace("\n", "\n                "))

#     if sender:
#         print(f"  From        : {formataddr((SENDER_NAME, sender)) if SENDER_NAME else sender}")
#     print(f"  Subject     : {EMAIL_SUBJECT}")
#     if attachments:
#         size = len(create_email(sender or "you@example.com", recipients[0], attachments).as_bytes())
#         print(f"  Email size  : {_size(size)} each")
#     total_wait = delay * (len(recipients) - 1)
#     duration = f"{total_wait:.0f} seconds" if total_wait < 60 else f"{math.ceil(total_wait / 60)} minutes"
#     print(f"  Delay       : {delay:g}s between emails (about {duration} in total)")

#     print("\nNo emails were sent.")
#     if not ready:
#         print("Fix the setup problems above before a real run.")
#     return 0


# def print_summary(summary: Summary, log_path: Path) -> None:
#     line = "-" * 32
#     title = "Email sending stopped" if summary.stop_reason else "Email sending completed"
#     print(f"\n{line}\n{title}\n{line}\n")
#     print(f"Total recipients : {summary.total}")
#     print(f"Successfully sent: {summary.sent}")
#     print(f"Failed           : {summary.failed}")
#     if summary.interrupted:
#         print(f"Interrupted      : {summary.interrupted} (may or may not have been delivered)")
#     if summary.not_attempted:
#         print(f"Not attempted    : {summary.not_attempted}")
#     if summary.stop_reason:
#         print(f"\nStopped because: {summary.stop_reason}")
#     if summary.not_attempted or summary.interrupted:
#         print("Run the same command again later to continue - addresses already marked SENT are skipped.")
#     print(f"\nLog file: {rel(log_path)}")


# def run(args: argparse.Namespace) -> int:
#     if args.dry_run:
#         print("DRY RUN MODE\n")

#     contacts_path = resolve_contacts_path(args.contacts)
#     print("Loading recipients...")
#     loaded = load_recipients(contacts_path)
#     report_loaded(loaded)
#     recipients = loaded.valid

#     if not args.resend:
#         already_sent = read_already_sent(LOG_FILE)
#         skipped = [r for r in recipients if r.lower() in already_sent]
#         if skipped:
#             recipients = [r for r in recipients if r.lower() not in already_sent]
#             print(f"Skipping {len(skipped)} already emailed (marked SENT in {rel(LOG_FILE)}). "
#                   "Use --resend to include them.")

#     if args.limit is not None and len(recipients) > args.limit:
#         print(f"--limit {args.limit}: using the first {args.limit} of {len(recipients)} this run.")
#         recipients = recipients[: args.limit]

#     if not recipients:
#         print("\nNothing to send.")
#         return 0

#     if len(recipients) > DAILY_VOLUME_WARNING:
#         print(f"\nNote: {len(recipients)} recipients is a lot for one day. Personal Gmail allows about 500\n"
#               "per 24 hours, and the rest of your email counts too. Consider --limit to split the list.")

#     attachment_paths = [args.resume_file, args.cover_letter_file]
#     if args.dry_run:
#         return dry_run(recipients, attachment_paths, args.delay)

#     attachments = load_attachments(attachment_paths)
#     print("\nAttachments verified.")
#     for attachment in attachments:
#         print(f"  {attachment.path.name} ({_size(len(attachment.data))})")

#     config = load_config()
#     ensure_log_writable(LOG_FILE)
#     for line_no, value in loaded.invalid:
#         log_result(LOG_FILE, value, STATUS_INVALID, f"Invalid email address (line {line_no} of {contacts_path.name})")

#     print()
#     summary = send_all(recipients, config, attachments, args.delay, LOG_FILE)
#     print_summary(summary, LOG_FILE)

#     if summary.cancelled:
#         return 130
#     return 0 if summary.failed == 0 and summary.stop_reason is None else 1


# def main(argv: list[str] | None = None) -> int:
#     args = parse_args(argv)
#     try:
#         return run(args)
#     except UserError as exc:
#         print(f"\nError: {exc}", file=sys.stderr)
#         return 1
#     except KeyboardInterrupt:
#         print("\nCancelled. No further emails will be sent.")
#         return 130


# if __name__ == "__main__":
#     sys.exit(main())






#!/usr/bin/env python3
"""
Send personalised job-application emails from a file of records.

Each record in the input file (.txt or .csv) gives the recipient, the subject
and the email body, for example:

    HR: Ayush (inferred_from_email)
    EMAIL: ayush@47northproductions.com
    COMPANY: 47northproductions
    STATUS: success
    SUBJECT:
    Exploring Software Development Opportunities at 47northproductions
    EMAIL:
    Hi Ayush,
    ...
    Best regards,
    Naveen Yadav

Every record becomes one separate email, sent in file order, with your resume
(and, if you choose, a cover letter) attached. Results go to logs/email_log.csv.

Examples:
    python3 send_emails.py contacts/emails.txt --dry-run
    python3 send_emails.py contacts/emails.txt
    python3 send_emails.py contacts/emails.txt --delay 15 --limit 100

Setup (Gmail App Password, .env, attachments) is explained in README.md.
"""

from __future__ import annotations

import argparse
import csv
import io
import math
import mimetypes
import os
import re
import smtplib
import socket
import ssl
import sys
import time
from collections import Counter
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from email.message import EmailMessage
from email.utils import formataddr, formatdate, make_msgid
from pathlib import Path
from typing import Callable

try:
    from dotenv import load_dotenv
except ImportError:  # pragma: no cover
    sys.exit("Missing dependency 'python-dotenv'. Run: pip3 install -r requirements.txt")


# =============================================================================
# CONFIGURATION - edit this section
# =============================================================================

SENDER_NAME = "Naveen Yadav"  # the name recipients see in the "From" field

BASE_DIR = Path(__file__).resolve().parent

# Your resume is attached to every email.
RESUME_PATH = BASE_DIR / "attachments" / "resume.pdf"

# The cover letter is OPTIONAL and off by default. To attach one to every email,
# replace None with its path, for example:
#   COVER_LETTER_PATH = BASE_DIR / "attachments" / "cover_letter.pdf"
# (Or add it for a single run with --cover-letter-file.)
COVER_LETTER_PATH: Path | None = None

CONTACTS_DIR = BASE_DIR / "contacts"
LOG_FILE = BASE_DIR / "logs" / "email_log.csv"
PREVIEW_FILE = BASE_DIR / "logs" / "preview.csv"  # every parsed email, written by --dry-run

DELAY_SECONDS = 40          # pause between two emails
MAX_RETRIES = 2             # extra attempts after a temporary SMTP failure
RETRY_WAIT_SECONDS = 5      # wait before the 1st retry; doubles for the next one
SMTP_TIMEOUT_SECONDS = 30   # give up on an unresponsive server after this long

# Most emails to send in any 24 hours, counted from the log. Personal Gmail
# blocks sending for up to a day at about 500 recipients, and your normal
# email counts too, so this stays below that. Set to 0 to turn the cap off.
DAILY_SEND_CAP = 400

# Gmail rejects messages over 25 MB, and attachments grow ~33% when encoded.
MAX_ATTACHMENTS_MB = 18

# Drop lines like "Phone:" that have nothing after them from the END of each
# email, so a missing value doesn't show up as an empty label.
REMOVE_EMPTY_SIGNATURE_LINES = True

# =============================================================================


STATUS_SENT = "SENT"
STATUS_FAILED = "FAILED"
STATUS_INVALID = "INVALID"
STATUS_SKIPPED = "SKIPPED"
STATUS_INTERRUPTED = "INTERRUPTED"
LOG_COLUMNS = ["timestamp", "email", "status", "error"]
TIME_FORMAT = "%Y-%m-%d %H:%M:%S"

# Error kinds used to decide whether to retry, skip, or stop.
TEMPORARY = "temporary"   # retry (network blip, 4xx reply)
PERMANENT = "permanent"   # give up on this email, continue with the next
FATAL = "fatal"           # stop the whole run (bad login, sending limit hit)

PLACEHOLDER_VALUES = {"", "your_email@gmail.com", "your_app_password"}

# Server replies that mean "you've hit a sending limit" - never keep retrying these.
LIMIT_REPLY_PATTERN = re.compile(
    r"5\.4\.5|sending limit|quota|too many login|unusual rate|rate limit",
    re.IGNORECASE,
)

EMAIL_PATTERN = re.compile(
    # local part: allowed characters, no leading/trailing/double dots
    r"[A-Za-z0-9!#$%&'*+/=?^_`{|}~-]+(?:\.[A-Za-z0-9!#$%&'*+/=?^_`{|}~-]+)*"
    # domain: one or more labels plus a real top-level domain
    r"@(?:[A-Za-z0-9](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?\.)+[A-Za-z]{2,63}"
)

# --- Record-file format ------------------------------------------------------
# "KEY: value" lines. Keys are compared ignoring case, spaces, "_" and "-".
FIELD_LINE = re.compile(r"^\s*([A-Za-z][A-Za-z _-]{0,30}?)\s*:\s*(.*?)\s*$")
HEADER_KEYS = {"COMPANY", "STATUS", "SUBJECT"}
BODY_KEYS = {"EMAIL", "BODY", "EMAILBODY", "MESSAGE", "MAILBODY", "CONTENT"}
SEPARATOR_LINE = re.compile(r"^\s*([-=*_#~])\1{2,}\s*$")          # "-----", "=====" ...
EMPTY_LABEL_LINE = re.compile(r"^\s*[A-Za-z][A-Za-z .]{0,24}:\s*$")  # "Phone:" with no value
PLACEHOLDER_TEXT = re.compile(
    r"\[[^\]\n]{1,40}\]|\{\{?[^}\n]{1,40}\}?\}|<\s*(?:your|insert|company|name)[^>\n]{0,30}>",
    re.IGNORECASE,
)

# Column names accepted when the input is a real spreadsheet-style CSV.
ADDRESS_COLUMNS = {"email", "emailaddress", "emailid", "to", "recipient", "recipientemail", "hremail", "mailid"}
SUBJECT_COLUMNS = {"subject", "emailsubject", "mailsubject"}
BODY_COLUMNS = {"body", "emailbody", "message", "content", "mailbody", "emailcontent", "emailtext"}


class UserError(Exception):
    """A problem the user can fix (missing file, bad setting). Shown without a traceback."""


class SendError(Exception):
    """This email failed; move on to the next one."""


class FatalSMTPError(Exception):
    """Every remaining email would fail the same way, so stop the run."""


@dataclass
class Config:
    host: str
    port: int
    address: str
    password: str = field(repr=False)  # never shown in output or tracebacks


@dataclass
class Record:
    """One email from the input file."""
    number: int                        # position in the file, starting at 1
    line: int                          # line where the record starts
    raw_email: str | None = None       # the EMAIL: value exactly as written
    email: str = ""                    # the cleaned, validated address
    subject: str = ""
    body: str = ""
    company: str = ""
    status: str = ""
    problems: list[str] = field(default_factory=list)   # why it can't be sent
    warnings: list[str] = field(default_factory=list)   # sent anyway, but worth a look
    removed_lines: list[str] = field(default_factory=list)

    @property
    def label(self) -> str:
        return f"record {self.number} (line {self.line})"


@dataclass
class LoadResult:
    records: list[Record]                        # everything found, in file order
    ready: list[Record]                          # valid, first record for each address
    duplicates: list[tuple[Record, Record]]      # (skipped record, earlier record)
    source_format: str

    @property
    def invalid(self) -> list[Record]:
        return [r for r in self.records if r.problems]


@dataclass
class Attachment:
    path: Path
    data: bytes = field(repr=False)
    maintype: str
    subtype: str


@dataclass
class Summary:
    total: int
    sent: int = 0
    failed: int = 0
    interrupted: int = 0      # Ctrl+C pressed mid-send; delivery unknown
    stop_reason: str | None = None
    cancelled: bool = False

    @property
    def not_attempted(self) -> int:
        return self.total - self.sent - self.failed - self.interrupted


# -----------------------------------------------------------------------------
# Small helpers
# -----------------------------------------------------------------------------

def rel(path: Path) -> str:
    """Show a path relative to the project folder when possible."""
    try:
        return str(path.resolve().relative_to(BASE_DIR))
    except ValueError:
        return str(path)


def validate_email(address: str) -> bool:
    """Return True if the address looks like a deliverable email address."""
    if len(address) > 254 or "@" not in address:
        return False
    local_part = address.rsplit("@", 1)[0]
    return len(local_part) <= 64 and EMAIL_PATTERN.fullmatch(address) is not None


def clean_address(raw: str) -> str:
    """Trim whitespace, quotes, <brackets> and a 'mailto:' prefix."""
    value = raw.strip().strip("\"'<>").strip()
    if value.lower().startswith("mailto:"):
        value = value[len("mailto:"):].strip()
    return value


def _shorten(text: str, width: int) -> str:
    return text if len(text) <= width else text[: width - 3] + "..."


def _size(num_bytes: int) -> str:
    if num_bytes < 1024:
        return f"{num_bytes} bytes"
    if num_bytes < 1024 * 1024:
        return f"{num_bytes / 1024:.0f} KB"
    return f"{num_bytes / 1024 / 1024:.1f} MB"


def _read_text(path: Path) -> str:
    try:
        raw = path.read_bytes()
    except OSError as exc:
        raise UserError(f"Could not read {rel(path)}: {exc.strerror or exc}") from None
    try:
        return raw.decode("utf-8-sig")  # also strips the BOM Excel adds
    except UnicodeDecodeError:
        return raw.decode("latin-1")


def _normalise_column(name: str) -> str:
    return re.sub(r"[\s_\-]+", "", name.strip().lower())


# -----------------------------------------------------------------------------
# Reading the input file
# -----------------------------------------------------------------------------

def _field(line: str) -> tuple[str | None, str]:
    """Split a 'KEY: value' line into (normalised KEY, value), or (None, '')."""
    match = FIELD_LINE.match(line)
    if not match:
        return None, ""
    return re.sub(r"[\s_-]+", "", match.group(1)).upper(), match.group(2)


def _starts_record(lines: list[str], i: int, between_records: bool) -> bool:
    """Is line i the first line of a new record?"""
    key, value = _field(lines[i])
    if key not in ("HR", "EMAIL"):
        return False
    following = [_field(line) for line in lines[i + 1:i + 6]]
    if key == "HR":
        # "HR: name" followed by the record's other header lines
        return any(k in HEADER_KEYS or (k == "EMAIL" and v) for k, v in following)
    # Files without HR lines: an upper-case "EMAIL: address" followed by COMPANY/STATUS/SUBJECT.
    # (A signature line like "Email: me@x.com" never matches this.)
    return (
        between_records
        and bool(value)
        and lines[i].lstrip().startswith("EMAIL")
        and any(k in HEADER_KEYS for k, _ in following[:4])
    )


def parse_record_blocks(text: str) -> list[Record]:
    """Parse the HR / EMAIL / COMPANY / STATUS / SUBJECT / EMAIL-body block format."""
    lines = text.splitlines()
    parsed: list[tuple[Record, list[str], list[str]]] = []  # (record, subject lines, body lines)
    state = "outside"  # outside -> header -> subject -> body -> done

    for i, line in enumerate(lines):
        if _starts_record(lines, i, between_records=state in ("outside", "body", "done")):
            parsed.append((Record(number=len(parsed) + 1, line=i + 1), [], []))
            state = "header"
        if state in ("outside", "done"):
            continue

        record, subject_lines, body_lines = parsed[-1]
        if state == "body":
            if SEPARATOR_LINE.match(line):
                state = "done"
            else:
                body_lines.append(line.rstrip())
            continue

        key, value = _field(line)
        if state == "subject":
            if key in BODY_KEYS:  # "EMAIL:" on its own line starts the body
                state = "body"
                if value:
                    body_lines.append(value)
            elif line.strip():
                subject_lines.append(line.strip())
            continue

        # state == "header"
        if key == "SUBJECT":
            state = "subject"
            if value:
                subject_lines.append(value)
        elif key == "EMAIL" and record.raw_email is None:
            record.raw_email = value
        elif key in BODY_KEYS:  # body marker in a record that has no SUBJECT line
            state = "body"
            if value:
                body_lines.append(value)
        elif key == "COMPANY":
            record.company = value
        elif key == "STATUS":
            record.status = value
        elif key is None and record.raw_email == "" and validate_email(clean_address(line)):
            record.raw_email = line.strip()  # "EMAIL:" with the address on the next line

    records = []
    for record, subject_lines, body_lines in parsed:
        record.subject = " ".join(subject_lines)
        record.body = "\n".join(body_lines).strip()
        records.append(record)
    return records


def _csv_rows(text: str) -> list[tuple[int, list[str]]]:
    """All CSV rows with the line number each starts on."""
    try:  # Excel in some regions saves CSVs with ";" instead of ","
        dialect = csv.Sniffer().sniff(text[:4096], delimiters=",;\t")
    except csv.Error:
        dialect = csv.excel
    reader = csv.reader(io.StringIO(text), dialect)
    rows, next_line = [], 1
    for row in reader:
        rows.append((next_line, row))
        next_line = reader.line_num + 1
    return rows


def _looks_like_table(text: str) -> bool:
    first_row = next(csv.reader(io.StringIO(text.split("\n", 1)[0])), [])
    return len(first_row) >= 2 and any(_normalise_column(c) in SUBJECT_COLUMNS for c in first_row)


def parse_table(text: str, path: Path) -> list[Record]:
    """Parse a spreadsheet-style CSV with email / subject / body columns."""
    rows = _csv_rows(text)
    header = [_normalise_column(c) for c in rows[0][1]]
    data = rows[1:]

    def cell(row: list[str], column: int | None) -> str:
        return row[column] if column is not None and column < len(row) else ""

    def share_of_addresses(column: int) -> float:
        values = [cell(row, column) for _, row in data if cell(row, column).strip()]
        return sum(validate_email(clean_address(v)) for v in values) / len(values) if values else 0.0

    address_candidates = [i for i, name in enumerate(header) if name in ADDRESS_COLUMNS]
    address_col = max(address_candidates, key=share_of_addresses) if address_candidates else None
    subject_col = next(i for i, name in enumerate(header) if name in SUBJECT_COLUMNS)
    body_col = next((i for i, name in enumerate(header) if name in BODY_COLUMNS), None)
    if body_col is None:  # e.g. two columns both called "EMAIL": address + body
        body_col = next((i for i in address_candidates if i != address_col), None)
    company_col = next((i for i, name in enumerate(header) if name == "company"), None)
    status_col = next((i for i, name in enumerate(header) if name == "status"), None)

    if address_col is None or body_col is None:
        found = ", ".join(repr(c) for c in rows[0][1])
        raise UserError(
            f"{rel(path)} looks like a spreadsheet, but I couldn't find both an email-address\n"
            f"column and an email-body column. Columns found: {found}\n"
            "Name them 'email', 'subject' and 'body'."
        )

    records = []
    for line_no, row in data:
        if not any(c.strip() for c in row):
            continue
        records.append(Record(
            number=len(records) + 1,
            line=line_no,
            raw_email=cell(row, address_col),
            subject=" ".join(cell(row, subject_col).split()),
            body=cell(row, body_col).replace("\r\n", "\n").strip(),
            company=cell(row, company_col).strip(),
            status=cell(row, status_col).strip(),
        ))
    return records


def _check_record(record: Record) -> None:
    """Fill in record.email, problems and warnings; tidy the body."""
    raw = (record.raw_email or "").strip()
    candidates = [clean_address(part) for part in re.split(r"[\s,;/|]+", raw) if part]
    addresses = [c for c in candidates if validate_email(c)]
    if not raw:
        record.problems.append("no email address")
    elif not addresses:
        record.problems.append(f"invalid email address '{_shorten(raw, 60)}'")
    else:
        record.email = addresses[0]
        if len(addresses) > 1:
            record.warnings.append(f"EMAIL has {len(addresses)} addresses; using the first ({addresses[0]})")

    if not record.subject:
        record.problems.append("no subject")

    lines = record.body.split("\n") if record.body else []
    if REMOVE_EMPTY_SIGNATURE_LINES:
        while lines and (not lines[-1].strip() or EMPTY_LABEL_LINE.match(lines[-1])):
            if lines[-1].strip():
                record.removed_lines.append(lines[-1].strip())
            lines.pop()
        record.body = "\n".join(lines)
    if not record.body.strip():
        record.problems.append("no email body")

    for line in lines[-10:]:
        if EMPTY_LABEL_LINE.match(line):
            record.warnings.append(f"empty '{line.strip()}' line near the end")
    for placeholder in dict.fromkeys(PLACEHOLDER_TEXT.findall(record.subject + "\n" + record.body)):
        record.warnings.append(f"possible unfilled placeholder {placeholder}")
    if record.status and not record.status.lower().startswith("success"):
        record.warnings.append(f"STATUS is '{_shorten(record.status, 60)}'")


def load_records(path: Path) -> LoadResult:
    """Read every record, validate it, and drop repeats of the same address."""
    if path.suffix.lower() not in (".txt", ".csv"):
        raise UserError(f"Unsupported file type '{path.suffix}'. Use a .txt or .csv file.")
    text = _read_text(path)
    if not text.strip():
        raise UserError(f"{rel(path)} is empty.")

    if path.suffix.lower() == ".csv" and _looks_like_table(text):
        records, source_format = parse_table(text, path), "spreadsheet columns"
    else:
        records, source_format = parse_record_blocks(text), "record blocks"

    if not records:
        raise UserError(
            f"No email records found in {rel(path)}.\n"
            "Each record needs at least these lines:\n"
            "  EMAIL: hr@company.com\n"
            "  SUBJECT:\n"
            "  <the subject>\n"
            "  EMAIL:\n"
            "  <the email body...>\n"
            "See README.md, step 6."
        )

    ready: list[Record] = []
    duplicates: list[tuple[Record, Record]] = []
    first_by_address: dict[str, Record] = {}
    for record in records:
        _check_record(record)
        if record.problems:
            continue
        key = record.email.lower()  # HR@Company.com and hr@company.com are the same inbox
        if key in first_by_address:
            duplicates.append((record, first_by_address[key]))
            continue
        first_by_address[key] = record
        ready.append(record)
    return LoadResult(records, ready, duplicates, source_format)


def resolve_contacts_path(value: str) -> Path:
    """Accept a path as given, or a bare file name inside the project or contacts/ folder."""
    for candidate in (Path(value), BASE_DIR / value, CONTACTS_DIR / value):
        if candidate.is_file():
            return candidate
    raise UserError(f"Input file not found: {value} (also looked in {rel(CONTACTS_DIR)}/)")


def read_log(log_path: Path) -> list[dict[str, str]]:
    if not log_path.is_file():
        return []
    try:
        with log_path.open(newline="", encoding="utf-8") as f:
            return [row for row in csv.DictReader(f) if row]
    except (OSError, csv.Error, UnicodeDecodeError):
        print(f"Warning: could not read {rel(log_path)}; treating it as empty.")
        return []


def sent_addresses(log_rows: list[dict[str, str]]) -> set[str]:
    return {
        (row.get("email") or "").strip().lower()
        for row in log_rows
        if row.get("status") == STATUS_SENT and row.get("email")
    }


def recent_send_times(log_rows: list[dict[str, str]], now: datetime) -> list[datetime]:
    """Timestamps of emails sent in the last 24 hours, oldest first."""
    times = []
    for row in log_rows:
        if row.get("status") != STATUS_SENT:
            continue
        try:
            sent_at = datetime.strptime(row.get("timestamp") or "", TIME_FORMAT)
        except ValueError:
            continue
        if now - sent_at < timedelta(hours=24):
            times.append(sent_at)
    return sorted(times)


def load_attachments(resume: Path, cover_letter: Path | None) -> list[Attachment]:
    """Read the attachments up front so problems show before anything is sent."""
    if not resume.is_file():
        raise UserError(
            f"Resume not found: {rel(resume)}\n"
            "Put your resume there, or change RESUME_PATH at the top of send_emails.py."
        )
    if cover_letter is not None and not cover_letter.is_file():
        raise UserError(
            f"Cover letter not found: {rel(cover_letter)}\n"
            "The cover letter is optional: set COVER_LETTER_PATH = None at the top of\n"
            "send_emails.py to send without one, or fix the path."
        )

    attachments = []
    for path in [resume] + ([cover_letter] if cover_letter is not None else []):
        try:
            data = path.read_bytes()
        except OSError as exc:
            raise UserError(f"Could not read attachment {rel(path)}: {exc.strerror or exc}") from None
        if not data:
            raise UserError(f"Attachment {rel(path)} is empty (0 bytes).")
        if path.suffix.lower() == ".pdf" and not data.startswith(b"%PDF"):
            print(f"Warning: {rel(path)} does not look like a real PDF file.")
        content_type = mimetypes.guess_type(path.name)[0] or "application/octet-stream"
        maintype, subtype = content_type.split("/", 1)
        attachments.append(Attachment(path, data, maintype, subtype))

    total_mb = sum(len(a.data) for a in attachments) / (1024 * 1024)
    if total_mb > MAX_ATTACHMENTS_MB:
        raise UserError(
            f"Attachments add up to {total_mb:.1f} MB; keep them under {MAX_ATTACHMENTS_MB} MB "
            "(Gmail rejects emails over 25 MB once encoded). Compress the PDFs."
        )
    return attachments


def load_config() -> Config:
    """Read SMTP settings from .env (or real environment variables)."""
    env_file = BASE_DIR / ".env"
    load_dotenv(env_file)

    host = os.getenv("SMTP_HOST", "smtp.gmail.com").strip()
    port_text = os.getenv("SMTP_PORT", "587").strip()
    address = os.getenv("EMAIL_ADDRESS", "").strip()
    password = os.getenv("EMAIL_PASSWORD", "").strip()

    problems = []
    if not host:
        problems.append("SMTP_HOST is empty")
    try:
        port = int(port_text)
        if not 0 < port < 65536:
            raise ValueError
    except ValueError:
        problems.append(f"SMTP_PORT must be a port number such as 587 (got {port_text!r})")
        port = 0
    if address in PLACEHOLDER_VALUES:
        problems.append("EMAIL_ADDRESS is not set")
    elif not validate_email(address):
        problems.append("EMAIL_ADDRESS is not a valid email address")
    if password in PLACEHOLDER_VALUES:
        problems.append("EMAIL_PASSWORD is not set")

    if problems:
        details = "\n".join(f"  - {p}" for p in problems)
        hint = "" if env_file.is_file() else "\nNo .env file found: copy .env.example to .env and fill it in."
        raise UserError(f"Email settings in .env are incomplete:\n{details}{hint}\nSee README.md, step 3.")

    if host.lower().endswith(("gmail.com", "googlemail.com")):
        # Google displays App Passwords as "abcd efgh ijkl mnop"; the spaces aren't part of it.
        password = re.sub(r"\s+", "", password)

    return Config(host=host, port=port, address=address, password=password)


# -----------------------------------------------------------------------------
# Building and sending email
# -----------------------------------------------------------------------------

def create_email(sender: str, record: Record, attachments: list[Attachment]) -> EmailMessage:
    """One message addressed to exactly one recipient - no CC or BCC."""
    message = EmailMessage()
    message["From"] = formataddr((SENDER_NAME, sender)) if SENDER_NAME else sender
    message["To"] = record.email
    message["Subject"] = record.subject
    message["Date"] = formatdate(localtime=True)
    message["Message-ID"] = make_msgid(domain=sender.rsplit("@", 1)[-1])
    message.set_content(record.body + "\n")
    for attachment in attachments:
        message.add_attachment(
            attachment.data,
            maintype=attachment.maintype,
            subtype=attachment.subtype,
            filename=attachment.path.name,
        )
    return message


class SMTPSession:
    """A logged-in SMTP connection that reopens itself if it drops."""

    def __init__(self, config: Config) -> None:
        self.config = config
        self.server: smtplib.SMTP | None = None

    def connect(self) -> None:
        self.close()
        context = ssl.create_default_context()
        if self.config.port == 465:  # implicit TLS
            server: smtplib.SMTP = smtplib.SMTP_SSL(
                self.config.host, self.config.port, timeout=SMTP_TIMEOUT_SECONDS, context=context
            )
        else:  # STARTTLS, e.g. port 587
            server = smtplib.SMTP(self.config.host, self.config.port, timeout=SMTP_TIMEOUT_SECONDS)
        try:
            if self.config.port != 465:
                server.ehlo()
                server.starttls(context=context)
                server.ehlo()
            server.login(self.config.address, self.config.password)
        except BaseException:
            self._shutdown(server)
            raise
        self.server = server

    def send(self, message: EmailMessage) -> None:
        if self.server is None:
            self.connect()
        assert self.server is not None
        self.server.send_message(message)

    def close(self) -> None:
        if self.server is not None:
            self._shutdown(self.server)
            self.server = None

    @staticmethod
    def _shutdown(server: smtplib.SMTP) -> None:
        try:
            server.quit()
        except Exception:
            server.close()


def _server_text(exc: smtplib.SMTPResponseException) -> str:
    text = exc.smtp_error.decode(errors="replace") if isinstance(exc.smtp_error, bytes) else str(exc.smtp_error)
    text = " ".join(text.split())  # server replies often span several lines
    return _shorten(text, 160)


def classify_error(exc: BaseException) -> tuple[str, str]:
    """Return (TEMPORARY | PERMANENT | FATAL, short readable reason)."""
    if isinstance(exc, smtplib.SMTPAuthenticationError):
        return FATAL, f"SMTP authentication failed ({exc.smtp_code} {_server_text(exc)})"
    if isinstance(exc, smtplib.SMTPNotSupportedError):
        return FATAL, f"Mail server does not support a required feature: {exc}"
    if isinstance(exc, smtplib.SMTPRecipientsRefused):
        code, reply = next(iter(exc.recipients.values()), (550, b""))
        text = reply.decode(errors="replace") if isinstance(reply, bytes) else str(reply)
        kind = TEMPORARY if 400 <= code < 500 else PERMANENT
        return kind, f"Recipient refused ({code} {' '.join(text.split())[:120]})"
    if isinstance(exc, smtplib.SMTPResponseException):
        reply = f"{exc.smtp_code} {_server_text(exc)}"
        if LIMIT_REPLY_PATTERN.search(reply):
            return FATAL, f"Sending limit reached ({reply})"
        return (TEMPORARY if 400 <= exc.smtp_code < 500 else PERMANENT), f"Server error ({reply})"
    if isinstance(exc, smtplib.SMTPServerDisconnected):
        if "timed out" in str(exc):  # smtplib reports read timeouts this way
            return TEMPORARY, f"Connection timed out after {SMTP_TIMEOUT_SECONDS}s"
        return TEMPORARY, "Server closed the connection"
    if isinstance(exc, (socket.timeout, TimeoutError)):
        return TEMPORARY, f"Connection timed out after {SMTP_TIMEOUT_SECONDS}s"
    if isinstance(exc, ssl.SSLCertVerificationError):
        return FATAL, f"TLS certificate check failed ({exc.verify_message})"
    if isinstance(exc, ssl.SSLError):
        return FATAL, f"TLS error ({exc.reason or exc}) - check that SMTP_PORT matches the server"
    if isinstance(exc, socket.gaierror):
        return TEMPORARY, "Could not look up the mail server (check SMTP_HOST and your internet)"
    if isinstance(exc, ConnectionRefusedError):
        return TEMPORARY, "Connection refused (check SMTP_HOST and SMTP_PORT)"
    if isinstance(exc, OSError):
        return TEMPORARY, f"Network error ({exc.strerror or exc})"
    if isinstance(exc, smtplib.SMTPException):
        return PERMANENT, f"SMTP error ({exc})"
    return PERMANENT, f"Unexpected error ({type(exc).__name__}: {exc})"


def hint_for(reason: str) -> str:
    """A next step for the most common setup problems."""
    lowered = reason.lower()
    if "authentication" in lowered:
        return ("Check EMAIL_ADDRESS and EMAIL_PASSWORD in .env. For Gmail, EMAIL_PASSWORD must be a\n"
                "16-character App Password, not your normal password (README.md, step 4).")
    if "limit" in lowered:
        return ("Your provider has paused sending. Wait (up to 24 hours for Gmail), then run the\n"
                "same command again - emails already sent are skipped.")
    if "certificate" in lowered:
        return ("Python can't verify the server's certificate. On macOS with python.org Python,\n"
                "run 'Install Certificates.command' from Applications > Python 3.x.")
    if "tls" in lowered:
        return "Use SMTP_PORT=587 (STARTTLS) or SMTP_PORT=465 (SSL)."
    if any(word in lowered for word in ("timed out", "refused", "look up", "network", "closed")):
        return ("Check SMTP_HOST / SMTP_PORT and your internet connection. Some college and office\n"
                "networks block ports 587 and 465 - try another network or a phone hotspot.")
    return "See 'Common SMTP errors' in README.md."


def _with_retries(action: Callable[[], None], session: SMTPSession) -> None:
    """Run action, retrying temporary failures. Raises SendError or FatalSMTPError."""
    for attempt in range(1, MAX_RETRIES + 2):
        try:
            action()
            return
        except Exception as exc:
            kind, reason = classify_error(exc)
            if kind == FATAL:
                raise FatalSMTPError(reason) from exc
            if kind == PERMANENT or attempt > MAX_RETRIES:
                raise SendError(reason) from exc
            wait = RETRY_WAIT_SECONDS * 2 ** (attempt - 1)
            print(f"{reason}; retry {attempt}/{MAX_RETRIES} in {wait}s ... ", end="", flush=True)
            session.close()  # start the retry on a fresh connection
            time.sleep(wait)


def send_email(session: SMTPSession, message: EmailMessage) -> None:
    """Send one message, retrying temporary SMTP failures up to MAX_RETRIES times."""
    _with_retries(lambda: session.send(message), session)


def log_result(log_path: Path, email: str, status: str, error: str = "") -> None:
    """Append one row to the CSV log (header is written when the file is new)."""
    try:
        log_path.parent.mkdir(parents=True, exist_ok=True)
        is_new = not log_path.exists() or log_path.stat().st_size == 0
        with log_path.open("a", newline="", encoding="utf-8") as f:
            writer = csv.writer(f)
            if is_new:
                writer.writerow(LOG_COLUMNS)
            writer.writerow([datetime.now().strftime(TIME_FORMAT), email, status, error])
    except OSError as exc:
        print(f"\nWarning: could not write to {rel(log_path)} ({exc.strerror or exc}).")


def ensure_log_writable(log_path: Path) -> None:
    try:
        log_path.parent.mkdir(parents=True, exist_ok=True)
        with log_path.open("a", encoding="utf-8"):
            pass
    except OSError as exc:
        raise UserError(
            f"Can't write to the log file {rel(log_path)} ({exc.strerror or exc}).\n"
            "If it's open in Excel, close it and run again."
        ) from None


def wait_between_emails(seconds: float) -> None:
    if seconds <= 0:
        return
    if not sys.stdout.isatty():
        print(f"Waiting {seconds:g} seconds...")
        time.sleep(seconds)
        return
    end = time.monotonic() + seconds
    while (left := end - time.monotonic()) > 0:
        shown = math.ceil(left)
        print(f"\rWaiting {shown} second{'s' if shown != 1 else ''}... ", end="", flush=True)
        time.sleep(min(1.0, left))
    print("\r" + " " * 32 + "\r", end="", flush=True)


def send_all(
    records: list[Record],
    config: Config,
    attachments: list[Attachment],
    delay: float,
    log_path: Path,
) -> Summary:
    """Sign in once, then send each record's email in file order. Ctrl+C stops cleanly."""
    summary = Summary(total=len(records))
    session = SMTPSession(config)

    print(f"Signing in to {config.host}:{config.port} as {config.address} ... ", end="", flush=True)
    try:
        _with_retries(session.connect, session)
    except (SendError, FatalSMTPError) as exc:
        print("FAILED")
        raise UserError(f"Could not sign in to the mail server: {exc}\n{hint_for(str(exc))}\n"
                        "No emails were sent.") from None
    print("OK")
    print("\nStarting email delivery...\n")

    in_flight: Record | None = None
    try:
        for index, record in enumerate(records, start=1):
            print(f"[{index}/{summary.total}] Sending to {record.email} ... ", end="", flush=True)
            in_flight = record
            try:
                send_email(session, create_email(config.address, record, attachments))
            except SendError as exc:
                in_flight = None
                summary.failed += 1
                print(f"FAILED ({exc})")
                log_result(log_path, record.email, STATUS_FAILED, str(exc))
            except FatalSMTPError as exc:
                in_flight = None
                summary.failed += 1
                summary.stop_reason = f"{exc}\n{hint_for(str(exc))}"
                print(f"FAILED ({exc})")
                log_result(log_path, record.email, STATUS_FAILED, str(exc))
                break
            else:
                in_flight = None
                summary.sent += 1
                print("SENT")
                log_result(log_path, record.email, STATUS_SENT)

            if index < summary.total:
                wait_between_emails(delay)
    except KeyboardInterrupt:
        summary.cancelled = True
        summary.stop_reason = "Stopped with Ctrl+C."
        if in_flight is not None:
            summary.interrupted += 1
            print("INTERRUPTED")
            log_result(log_path, in_flight.email, STATUS_INTERRUPTED,
                       "Stopped with Ctrl+C while sending; delivery unknown")
        else:
            print()
    finally:
        session.close()
    return summary


# -----------------------------------------------------------------------------
# Reports
# -----------------------------------------------------------------------------

def _print_capped(items: list[str], limit: int = 10, indent: str = "    ") -> None:
    for item in items[:limit]:
        print(f"{indent}{item}")
    if len(items) > limit:
        print(f"{indent}... and {len(items) - limit} more (all listed in {rel(PREVIEW_FILE)} after a --dry-run)")


def report_loaded(loaded: LoadResult) -> None:
    print(f"Found {len(loaded.records)} record{'s' if len(loaded.records) != 1 else ''} ({loaded.source_format}).")
    print(f"  {len(loaded.ready)} ready to send")
    if loaded.invalid:
        print(f"  {len(loaded.invalid)} can't be sent:")
        _print_capped([f"{r.label}: {', '.join(r.problems)}" for r in loaded.invalid])
    if loaded.duplicates:
        print(f"  {len(loaded.duplicates)} skipped because the address already appears in an earlier record:")
        _print_capped([f"{r.label}: {r.email} (same as record {first.number})" for r, first in loaded.duplicates])

    removed = Counter(line for r in loaded.ready for line in r.removed_lines)
    for line, count in removed.items():
        print(f"  Removed an empty '{line}' line from the end of {count} email{'s' if count != 1 else ''}.")

    warned = [r for r in loaded.ready if r.warnings]
    if warned:
        print(f"  {len(warned)} will be sent but are worth checking:")
        _print_capped([f"{r.label}: {'; '.join(r.warnings)}" for r in warned])


def show_email(record: Record, attachment_names: list[str]) -> None:
    line = "-" * 64
    print(line)
    print(f"To          : {record.email or record.raw_email or '(missing)'}")
    print(f"Subject     : {record.subject or '(missing)'}")
    print(f"Attachments : {', '.join(attachment_names)}")
    if record.company:
        print(f"Company     : {record.company}")
    print(line)
    print(record.body or "(no body)")
    print(line)


def write_preview(loaded: LoadResult, already_sent: set[str], path: Path) -> bool:
    """Save every parsed record to a CSV you can open in Excel or Numbers."""
    duplicate_of = {r.number: first.number for r, first in loaded.duplicates}
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("w", newline="", encoding="utf-8-sig") as f:
            writer = csv.writer(f)
            writer.writerow(["record", "line", "email", "company", "subject", "body", "result", "notes"])
            for r in loaded.records:
                if r.problems:
                    result = "CAN'T SEND: " + ", ".join(r.problems)
                elif r.number in duplicate_of:
                    result = f"SKIPPED: same address as record {duplicate_of[r.number]}"
                elif r.email.lower() in already_sent:
                    result = "ALREADY SENT"
                else:
                    result = "READY"
                notes = r.warnings + [f"removed empty '{line}' line" for line in r.removed_lines]
                writer.writerow([r.number, r.line, r.email or (r.raw_email or ""), r.company,
                                 r.subject, r.body, result, "; ".join(notes)])
        return True
    except OSError as exc:
        print(f"Warning: could not write {rel(path)} ({exc.strerror or exc}). Close it if it's open.")
        return False


def dry_run(loaded: LoadResult, to_send: list[Record], already_sent: set[str],
            resume: Path, cover_letter: Path | None, delay: float) -> int:
    if to_send:
        print("\nWould send, in this order:")
        for number, record in enumerate(to_send[:25], start=1):
            print(f"{number:>4}. {record.email}  |  {_shorten(record.subject, 70)}")
        if len(to_send) > 25:
            print(f"      ... and {len(to_send) - 25} more")
    else:
        print("\nNothing would be sent.")

    print("\nSetup check:")
    ready = True
    attachments: list[Attachment] = []
    try:
        attachments = load_attachments(resume, cover_letter)
        print(f"  Resume       : OK - {attachments[0].path.name} ({_size(len(attachments[0].data))})")
    except UserError as exc:
        ready = False
        print("  Attachments  : " + str(exc).replace("\n", "\n                 "))
    if cover_letter is None:
        print("  Cover letter : not attached (optional)")
    elif len(attachments) > 1:
        print(f"  Cover letter : OK - {attachments[1].path.name} ({_size(len(attachments[1].data))})")

    sender = ""
    try:
        config = load_config()
        sender = config.address
        print(f"  Sender       : OK - {config.address} via {config.host}:{config.port} (login not tested)")
    except UserError as exc:
        ready = False
        print("  Sender       : " + str(exc).replace("\n", "\n                 "))
    if sender:
        print(f"  From         : {formataddr((SENDER_NAME, sender)) if SENDER_NAME else sender}")
    if DAILY_SEND_CAP > 0:
        print(f"  Daily cap    : {DAILY_SEND_CAP} emails per 24 hours")
    if to_send:
        total_wait = delay * (len(to_send) - 1)
        duration = f"{total_wait:.0f} seconds" if total_wait < 60 else f"{math.ceil(total_wait / 60)} minutes"
        print(f"  Delay        : {delay:g}s between emails (about {duration} for this run)")

    if to_send:
        names = [a.path.name for a in attachments] or [resume.name]
        print(f"\nFirst email in full ({to_send[0].label}):")
        show_email(to_send[0], names)
        if attachments:
            size = len(create_email(sender or "you@example.com", to_send[0], attachments).as_bytes())
            print(f"Email size: {_size(size)}")
        print("To read any other one: --preview <record number>")

    if write_preview(loaded, already_sent, PREVIEW_FILE):
        print(f"\nAll {len(loaded.records)} parsed emails saved to {rel(PREVIEW_FILE)} - open it to check them.")
    print("\nNo emails were sent.")
    if not ready:
        print("Fix the setup problems above before a real run.")
    return 0


def print_summary(summary: Summary, log_path: Path, still_unsent: int) -> None:
    line = "-" * 32
    title = "Email sending stopped" if summary.stop_reason else "Email sending completed"
    print(f"\n{line}\n{title}\n{line}\n")
    print(f"Total recipients : {summary.total}")
    print(f"Successfully sent: {summary.sent}")
    print(f"Failed           : {summary.failed}")
    if summary.interrupted:
        print(f"Interrupted      : {summary.interrupted} (may or may not have been delivered)")
    if summary.not_attempted:
        print(f"Not attempted    : {summary.not_attempted}")
    if summary.stop_reason:
        print(f"\nStopped because: {summary.stop_reason}")
    if still_unsent:
        print(f"\n{still_unsent} email{'s' if still_unsent != 1 else ''} in this file not sent yet. "
              "Run the same command again later to continue\n(emails already marked SENT are skipped).")
    print(f"\nLog file: {rel(log_path)}")


# -----------------------------------------------------------------------------
# Command line
# -----------------------------------------------------------------------------

def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Send one personalised email per record in a .txt or .csv file, in order.",
        epilog=(
            "examples:\n"
            "  python3 send_emails.py contacts/emails.txt --dry-run\n"
            "  python3 send_emails.py contacts/emails.txt --preview 12\n"
            "  python3 send_emails.py contacts/emails.txt\n"
            "  python3 send_emails.py contacts/emails.txt --delay 15 --limit 100"
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("input_file", help="the .txt or .csv file with EMAIL / SUBJECT / body records")
    parser.add_argument("--dry-run", action="store_true",
                        help="check everything and show what would be sent, without sending")
    parser.add_argument("--preview", type=int, metavar="N",
                        help="print record N in full exactly as it would be sent, then stop")
    parser.add_argument("--delay", type=float, default=DELAY_SECONDS,
                        help=f"seconds to wait between emails (default: {DELAY_SECONDS})")
    parser.add_argument("--limit", type=int, metavar="N",
                        help="send at most N emails this run")
    parser.add_argument("--resend", action="store_true",
                        help="also email addresses already marked SENT in the log")
    parser.add_argument("--resume-file", type=Path, default=RESUME_PATH, metavar="PATH",
                        help=f"resume to attach (default: {rel(RESUME_PATH)})")
    parser.add_argument("--cover-letter-file", type=Path, default=COVER_LETTER_PATH, metavar="PATH",
                        help="also attach this cover letter (optional; off by default)")
    args = parser.parse_args(argv)
    if args.delay < 0:
        parser.error("--delay can't be negative")
    if args.limit is not None and args.limit < 1:
        parser.error("--limit must be at least 1")
    if args.preview is not None and args.preview < 1:
        parser.error("--preview needs a record number (1 or more)")
    return args


def run(args: argparse.Namespace) -> int:
    if args.dry_run:
        print("DRY RUN MODE\n")

    input_path = resolve_contacts_path(args.input_file)
    print(f"Reading {rel(input_path)} ...")
    loaded = load_records(input_path)

    if args.preview is not None:
        record = next((r for r in loaded.records if r.number == args.preview), None)
        if record is None:
            raise UserError(f"There is no record {args.preview}; the file has {len(loaded.records)}.")
        names = [p.name for p in (args.resume_file, args.cover_letter_file) if p is not None]
        print(f"\n{record.label}:")
        show_email(record, names)
        for note in record.problems:
            print(f"CAN'T SEND: {note}")
        for note in record.warnings:
            print(f"Check: {note}")
        return 0

    report_loaded(loaded)
    log_rows = read_log(LOG_FILE)
    already_sent = sent_addresses(log_rows)
    to_send = loaded.ready

    if not args.resend:
        skipped = [r for r in to_send if r.email.lower() in already_sent]
        if skipped:
            to_send = [r for r in to_send if r.email.lower() not in already_sent]
            print(f"\nSkipping {len(skipped)} already emailed (marked SENT in {rel(LOG_FILE)}). "
                  "Use --resend to include them.")

    if DAILY_SEND_CAP > 0 and to_send:
        recent = recent_send_times(log_rows, datetime.now())
        room = max(0, DAILY_SEND_CAP - len(recent))
        if len(to_send) > room:
            if room == 0:
                next_slot = recent[len(recent) - DAILY_SEND_CAP] + timedelta(hours=24)
                print(f"\nDaily cap reached: {len(recent)} emails sent in the last 24 hours (cap {DAILY_SEND_CAP}).\n"
                      f"The next one can go out after {next_slot:%d %b %H:%M}. Run the same command again then.")
            else:
                print(f"\nDaily cap: {len(recent)} sent in the last 24 hours, so this run sends {room} "
                      f"of {len(to_send)} (cap {DAILY_SEND_CAP}).\nRun the same command again tomorrow for the rest.")
            to_send = to_send[:room]

    if args.limit is not None and len(to_send) > args.limit:
        print(f"\n--limit {args.limit}: sending the first {args.limit} of {len(to_send)} this run.")
        to_send = to_send[: args.limit]

    if args.dry_run:
        return dry_run(loaded, to_send, already_sent, args.resume_file, args.cover_letter_file, args.delay)

    if not to_send:
        print("\nNothing to send.")
        return 0

    attachments = load_attachments(args.resume_file, args.cover_letter_file)
    print("\nAttachments verified.")
    for attachment in attachments:
        print(f"  {attachment.path.name} ({_size(len(attachment.data))})")

    config = load_config()
    ensure_log_writable(LOG_FILE)

    # Record problems in the log once (not again on every re-run).
    logged = {(row.get("email"), row.get("status"), row.get("error")) for row in log_rows}
    problem_rows = [(r.email or (r.raw_email or "(missing)"), STATUS_INVALID,
                     f"{r.label} of {input_path.name}: {', '.join(r.problems)}") for r in loaded.invalid]
    problem_rows += [(r.email, STATUS_SKIPPED,
                      f"{r.label} of {input_path.name}: same address as record {first.number}")
                     for r, first in loaded.duplicates]
    for row in problem_rows:
        if row not in logged:
            log_result(LOG_FILE, *row)

    print()
    summary = send_all(to_send, config, attachments, args.delay, LOG_FILE)
    sent_now = sent_addresses(read_log(LOG_FILE))
    still_unsent = sum(1 for r in loaded.ready if r.email.lower() not in sent_now)
    print_summary(summary, LOG_FILE, still_unsent)

    if summary.cancelled:
        return 130
    return 0 if summary.failed == 0 and summary.stop_reason is None else 1


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    try:
        return run(args)
    except UserError as exc:
        print(f"\nError: {exc}", file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        print("\nCancelled. No further emails will be sent.")
        return 130


if __name__ == "__main__":
    sys.exit(main())

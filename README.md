# Bulk Email Sender for Job Applications

Reads a file of ready-made emails (one record per company), then sends each one as a **separate email**, one after another in the order they appear in the file. Every email uses its own recipient, subject and body from the file. Your resume is attached to each one, and attaching a cover letter is optional. Every result is written to a log.

```text
bulk-email-sender/
├── send_emails.py          the script (settings are at the top)
├── requirements.txt
├── .env                    your email login (never share or commit this)
├── .env.example            template for .env
├── .gitignore
├── attachments/
│   └── resume.pdf          you add this (cover_letter.pdf is optional)
├── contacts/
│   ├── sample_emails.txt   example of the record format
│   └── sample_table.csv    example of the spreadsheet format
└── logs/
    ├── email_log.csv       what happened to each email
    └── preview.csv         every parsed email, written by --dry-run
```

The commands below are for **macOS**. On Windows, type `python` and `pip` instead of `python3` and `pip3`.

---

## 1. Install the dependency

Open Terminal in the `bulk-email-sender` folder: type `cd ` (with a space after it), drag the folder into the Terminal window, and press Enter. Then run:

```bash
pip3 install -r requirements.txt
```

## 2. Create the `.env` file

The project already includes a `.env` with placeholder values. If it's missing, run `cp .env.example .env`.

Files that start with a dot are hidden in Finder. Press **Cmd + Shift + .** to show them, or open the file from Terminal with `open -e .env`.

## 3. Add your sender email

```env
SMTP_HOST=smtp.gmail.com
SMTP_PORT=587
EMAIL_ADDRESS=your_email@gmail.com
EMAIL_PASSWORD=your_app_password
```

`EMAIL_PASSWORD` must be a Gmail **App Password**, not your normal password (see step 4). The script never prints the password, and `.gitignore` keeps `.env` out of Git.

To change the name recipients see in the "From" field, edit `SENDER_NAME` at the top of `send_emails.py`.

## 4. Create a Gmail App Password

1. Open your Google Account, go to **Security**, and turn on **2-Step Verification**.
2. Go to **https://myaccount.google.com/apppasswords**.
3. Enter a name such as `Job application sender` and click **Create**.
4. **Copy the 16-character password before closing the window**, because Google shows it only once.
5. Paste it into `.env` as `EMAIL_PASSWORD`. You can include or leave out the spaces.

If you change your Google password, Google cancels all your App Passwords, so you'll need to make a new one. Work and school accounts often can't create App Passwords, so a personal Gmail account is simplest.

## 5. Add your resume (cover letter optional)

Put your resume at `attachments/resume.pdf`. Recruiters see the file name, so you can rename it (for example `Naveen_Yadav_Resume.pdf`) and update `RESUME_PATH` at the top of `send_emails.py` to match.

**The cover letter is off by default.** To attach one:
- **Every time:** set `COVER_LETTER_PATH = BASE_DIR / "attachments" / "cover_letter.pdf"` at the top of `send_emails.py`.
- **For a single run:** add `--cover-letter-file attachments/cover_letter.pdf` to the command.

## 6. Prepare the input file

### Record format (.txt or .csv)

This is what your generated file looks like. It can hold 500+ records:

```text
HR: Ayush (inferred_from_email)
EMAIL: ayush@47northproductions.com
COMPANY: 47northproductions
STATUS: success - very little company information found
SUBJECT:
Exploring Software Development / Full Stack / GenAI Opportunities at 47northproductions
EMAIL:
Hi Ayush,
I hope you're doing well.
...
Best regards,
Naveen Yadav
LinkedIn: https://www.linkedin.com/in/...
Phone:
HR: Next person...
```

For each record, the script takes:
- **Recipient:** the first `EMAIL: address` line.
- **Subject:** the line or lines after `SUBJECT:`.
- **Body:** everything after the second `EMAIL:` line, up to the next record's `HR:` line.

`HR`, `COMPANY` and `STATUS` are shown in reports but aren't sent. Blank lines between records, and separator lines like `=====`, are fine. A line like `Email: me@gmail.com` inside your signature stays part of the body.

### Spreadsheet format (.csv)

A normal CSV with columns named `email`, `subject` and `body` also works. `company` is optional, and a body cell can span several lines.

### What the script checks

- **Can't be sent:** a record with no email address, an invalid address, no subject or no body is skipped and logged as `INVALID`.
- **Same address twice:** only the first record for an address is sent. Later ones are logged as `SKIPPED`. Capital letters don't matter.
- **Tidied automatically:** an empty `Phone:`-style line at the very end of an email is removed, so recipients don't see a blank label. To turn this off, set `REMOVE_EMPTY_SIGNATURE_LINES = False`.
- **Flagged but still sent:** leftover placeholders like `[HR Name]`, a `STATUS` that doesn't start with "success", and an `EMAIL` field with more than one address (the first one is used).

## 7. Check before sending

**Dry run:** reads everything, checks your setup and sends nothing:

```bash
python3 send_emails.py contacts/emails.txt --dry-run
```

It shows how many records were found, which ones can't be sent and why, the first 25 recipients in order, and the first email in full. It also saves **every** parsed email to `logs/preview.csv`. Open that file in Numbers or Excel and scroll through the subjects and bodies before you send anything.

**See any single email in full:**

```bash
python3 send_emails.py contacts/emails.txt --preview 37
```

## 8. Send

**Tip:** first make a one-record file addressed to yourself and send it, so you can see exactly what recruiters will get.

```bash
python3 send_emails.py contacts/emails.txt
python3 send_emails.py contacts/emails.txt --delay 15
python3 send_emails.py contacts/emails.txt --limit 100
```

The script signs in once, so a wrong password stops it before anything is sent. It then sends in file order, waiting `DELAY_SECONDS` (10 by default) between emails:

```text
[1/400] Sending to ayush@47northproductions.com ... SENT
[2/400] Sending to hr@company2.com ... SENT
[3/400] Sending to jobs@company3.com ... FAILED (Recipient refused (550 5.1.1 ...))
```

| Option | What it does |
|---|---|
| `--dry-run` | Checks everything and shows what would be sent, without sending |
| `--preview N` | Prints record N in full, then stops |
| `--delay 15` | Seconds between emails (default 10) |
| `--limit 100` | Sends at most 100 emails in this run |
| `--resend` | Also emails addresses already marked `SENT` in the log |
| `--resume-file PATH` | Uses a different resume for this run |
| `--cover-letter-file PATH` | Also attaches this cover letter |

### 500+ emails: it takes more than one day

Personal Gmail stops you from sending at about **500 recipients in 24 hours**, and the rest of your email counts too. When that happens, Gmail can block **all** your sending for up to a day. To prevent this, the script has a built-in `DAILY_SEND_CAP = 400`. It counts what it sent in the last 24 hours, sends only up to the cap, and tells you when to come back.

To keep going, **run the same command again the next day.** Addresses already marked `SENT` are skipped automatically, so nobody gets your email twice. This also works after Ctrl+C, a network drop or a sending-limit stop.

Many cold emails a day from a new Gmail account can also start landing in spam. Smaller batches (for example `--limit 100` a day) are safer for your account's reputation.

## 9. Logging

Every real run adds rows to `logs/email_log.csv`:

```csv
timestamp,email,status,error
2026-09-28 10:30:12,ayush@47northproductions.com,SENT,
2026-09-28 10:30:25,hr@company2.com,FAILED,Recipient refused (550 5.1.1 ...)
2026-09-28 10:30:00,Not found,INVALID,record 5 (line 81) of emails.txt: invalid email address 'Not found'
2026-09-28 10:30:00,hr@company3.com,SKIPPED,record 9 (line 159) of emails.txt: same address as record 3
```

| Status | Meaning |
|---|---|
| `SENT` | Gmail accepted the email |
| `FAILED` | The email couldn't be sent. `error` says why |
| `INVALID` | The record is missing an address, subject or body, or the address is invalid |
| `SKIPPED` | Another record earlier in the file has the same address |
| `INTERRUPTED` | You pressed Ctrl+C while this email was sending. It may or may not have arrived |

Each row is written as soon as that email finishes. `INVALID` and `SKIPPED` rows are logged once, not again on every run. The log is also how the script knows what's already sent and how many emails went out in the last 24 hours, so don't delete it partway through a batch.

`SENT` means Gmail accepted the email. If an address doesn't exist, you'll usually get a bounce message ("Address not found") in your inbox a few minutes later. Everything the script sends also appears in your Gmail **Sent** folder.

## 10. Common errors

For temporary problems, the script automatically tries up to `MAX_RETRIES = 2` more times. A login failure or a sending limit stops the whole run.

| Error | What it means | Fix |
|---|---|---|
| `No email records found` | The file isn't in the record format | Check it matches step 6. A plain list of addresses is no longer used |
| `535 5.7.8 Username and Password not accepted` | Wrong login | Use an App Password (step 4) and check `EMAIL_ADDRESS` |
| `534 5.7.9 Application-specific password required` | Gmail needs an App Password | Follow step 4 |
| `Email settings in .env are incomplete` | `.env` still has the placeholder values | Follow steps 2 and 3 |
| `Resume not found` | No file at `RESUME_PATH` | Put it in `attachments/` or fix the path |
| `Cover letter not found` | You turned the cover letter on, but the file isn't there | Fix the path, or set `COVER_LETTER_PATH = None` |
| `Connection timed out` / `Connection refused` | Your computer can't reach Gmail | College and office Wi-Fi often block ports 587 and 465. Try a phone hotspot |
| `TLS certificate check failed` | Python can't verify Gmail's certificate | On a Mac with Python from python.org, run **Install Certificates.command** in Applications > Python 3.x |
| `550 5.4.5 Daily user sending limit exceeded` | Gmail's daily limit was reached | The script stops. Wait up to 24 hours, then run the same command again |
| `Daily cap reached` | The script's own `DAILY_SEND_CAP` protected you | Run again after the time it shows |
| `Recipient refused (550 5.1.1 ...)` | That address doesn't exist | Nothing to do. It's logged as `FAILED` |
| `ModuleNotFoundError: No module named 'dotenv'` | The package isn't installed | Run `pip3 install -r requirements.txt` |

## Quick reference

```bash
pip3 install -r requirements.txt
python3 send_emails.py contacts/emails.txt --dry-run
python3 send_emails.py contacts/emails.txt --preview 12
python3 send_emails.py contacts/emails.txt
python3 send_emails.py contacts/emails.txt --limit 100
python3 send_emails.py contacts/emails.txt --cover-letter-file attachments/cover_letter.pdf
python3 send_emails.py --help
```

# MediQueue — Smart Hospital Queue & Waiting-Time Optimization System

A runnable Flask + SQLite hackathon prototype with patient, staff and admin roles.

## Requirements
- Windows 10/11
- Python 3.10 or newer
- Internet connection for Bootstrap, Font Awesome and Socket.IO CDN assets

## Run in VS Code (Windows PowerShell)
1. Extract the ZIP and open the `MediQueue` folder in VS Code.
2. Open **Terminal → New Terminal**.
3. Create a virtual environment:
   ```powershell
   python -m venv venv
   ```
4. Activate it:
   ```powershell
   venv\Scripts\Activate.ps1
   ```
   If PowerShell blocks activation, use Command Prompt and run `venv\Scripts\activate.bat`.
5. Install packages:
   ```powershell
   pip install -r requirements.txt
   ```
6. Start the application (the database and demo data are initialized automatically):
   ```powershell
   python run.py
   ```
7. Open http://127.0.0.1:5000

Keep the terminal open while using the application. Press `Ctrl+C` to stop it.

## Demo logins
| Role | Email | Password |
|---|---|---|
| Admin | admin@mediqueue.local | Admin@123 |
| Staff | staff@mediqueue.local | Staff@123 |
| Patient | patient@mediqueue.local | Patient@123 |

Patients can also register using the registration page.

## Included features
- Role-specific patient, staff and admin dashboards
- Patient registration and login
- Department-based digital token generation
- Duplicate active-token prevention
- Queue position and estimated waiting time
- Staff call-next and complete/cancel token workflows
- Counter availability toggles
- Admin editing of user display names, departments, counters and hospital name
- Audit activity records
- SQLite database auto-initialization and demo seed data
- Socket.IO queue update events

## Important prototype notes
- This is a college hackathon demonstration prototype, not a certified hospital system.
- Waiting time is an estimate, not a guarantee.
- Do not enter real patient medical or sensitive information.
- Priority clinical triage is not implemented; only authorized clinical staff should make such decisions in a real deployment.
- Before public deployment, replace the development secret key, add CSRF protection, configure HTTPS, harden authorization and privacy controls, and review the database backup strategy.
- CDN assets require internet access. Core Flask routes and database operate locally.

## Reset demo database
Stop the server. Delete `instance/mediqueue.db` if it exists, then run `python run.py` again. The app recreates the database and demo accounts.

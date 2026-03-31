# NEXUS — VPS Control Panel

A professional Flask-based VPS management dashboard for deploying and managing Next.js, React, NestJS, and Node.js apps with PM2, Nginx, and Certbot.

## Features

- 🖥️ **Multi-server management** — Add unlimited VPS servers with encrypted credentials
- 📊 **Live server stats** — CPU, RAM, disk, uptime, OS info
- 📁 **File Manager** — Browse, view, edit, create, delete files over SSH
- 💻 **Web Terminal** — Full bash terminal with command history & `cd` support
- ⚙️ **PM2 Manager** — View, start, stop, restart, delete Node.js processes
- 🚀 **One-click Deploy** — Deploy Next.js, NestJS, React apps via PM2
- 🌐 **Nginx Config Generator** — Create reverse proxy configs instantly
- 🔒 **SSL via Certbot** — Issue Let's Encrypt certificates with one click

## Setup

```bash
# 1. Install dependencies
pip install -r requirements.txt

# 2. Run the app
python app.py
```

Open http://localhost:5000

## Security Notes

- Passwords are encrypted using Fernet (AES-128) before storage in `servers.json`
- A unique encryption key is auto-generated and stored in `secret.key`
- **Keep `secret.key` and `servers.json` private — do not commit them**
- For production, add authentication middleware

## Production Deployment

```bash
pip install gunicorn
gunicorn -w 4 -b 0.0.0.0:5000 app:app
```

## Stack Used

| Component | Purpose |
|-----------|---------|
| Flask | Backend API + HTML serving |
| Paramiko | SSH connections |
| Cryptography (Fernet) | Password encryption |
| PM2 | Node.js process manager (on VPS) |
| Nginx | Reverse proxy (on VPS) |
| Certbot | SSL certificates (on VPS) |

## File Structure

```
vps-manager/
├── app.py              # Flask backend
├── requirements.txt    # Python dependencies
├── templates/
│   └── index.html      # Full-featured SPA frontend
├── servers.json        # Encrypted server list (auto-created)
└── secret.key          # Fernet encryption key (auto-created)
```
# vps-manager

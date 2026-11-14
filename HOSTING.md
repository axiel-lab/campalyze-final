# Hosting

This is a Python app (FastAPI + WebSockets) that keeps accounts connected 24/7.
It needs a host that runs a **long-lived Python process**. Static hosts
(GitHub Pages, Netlify, Vercel) will not work.

## Option 1: your own PC (quickest)
1. `pip install -r requirements.txt`
2. Copy `.env.example` to `.env`, set `JWT_SECRET` and strong passwords.
3. `python dashboard.py`, then open http://localhost:8080
4. With `ENABLE_TUNNEL=true` (and cloudflared installed) a public https link is printed in the log.

## Option 2: VPS (best for 24/7)
Any small Ubuntu VPS works.
```
sudo apt update && sudo apt install -y python3-pip python3-venv screen
python3 -m venv venv && source venv/bin/activate
pip install -r requirements.txt
cp .env.example .env && nano .env
screen -S voice
python dashboard.py        # detach with Ctrl+A then D
```
Put Caddy or Nginx in front for HTTPS, or keep the Cloudflare tunnel on.

## Option 3: Railway / Render / Fly.io
- Build: `pip install -r requirements.txt`
- Start: `python dashboard.py`
- Set the variables from `.env.example` in the dashboard of the host.
- Set `PORT` to the port the host gives you, and `ENABLE_TUNNEL=false`.
- Use a paid or always-on plan; free tiers sleep, which disconnects the accounts.

## Option 4: Pterodactyl / game-panel hosting
Python egg, startup command `python dashboard.py`, upload the files, fill in `.env`.

Never commit `.env` or `tokens.txt`.

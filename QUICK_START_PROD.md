# Miximus — Quickstart Production Guide

All commands run in **WSL** (Ubuntu).

---

## START

```bash
# 1. Build frontend (only if code changed)
cd "/mnt/c/AML mixer/webapp/frontend"
npm run build

# 2. Start nginx + Tor (system services, survive reboots)
sudo systemctl start nginx
sudo systemctl start tor

# 3. Start Flask backend (background, logs to file)
cd "/mnt/c/AML mixer/webapp/backend"
source venv/bin/activate
nohup python app.py > /tmp/flask.log 2>&1 &
echo $! > /tmp/flask.pid
echo "Flask PID: $(cat /tmp/flask.pid)"
```

---

## STOP

```bash
# Stop Flask
pkill -f "python app.py"
# (if started with nohup/pid file: kill $(cat /tmp/flask.pid) && rm /tmp/flask.pid)

# Stop nginx + Tor
sudo systemctl stop nginx
sudo systemctl stop tor
```

---

## STATUS CHECK

```bash
# Is Flask alive?
kill -0 $(cat /tmp/flask.pid 2>/dev/null) 2>/dev/null && echo "Flask: UP" || echo "Flask: DOWN"

# Flask live logs
tail -f /tmp/flask.log

# nginx / Tor
sudo systemctl status nginx --no-pager
sudo systemctl status tor --no-pager

# Your .onion address
cat /var/lib/tor/miximus/hostname
```

---

## RESTART FLASK (after code change)

```bash
kill $(cat /tmp/flask.pid)
cd "/mnt/c/AML mixer/webapp/backend"
source venv/bin/activate
nohup python app.py > /tmp/flask.log 2>&1 &
echo $! > /tmp/flask.pid
echo "Flask PID: $(cat /tmp/flask.pid)"
```

---

## NOTES

- nginx serves the built frontend from `webapp/frontend/dist/` on port 80
- nginx proxies `/api/` requests to Flask on port 5000
- Tor forwards `.onion:80` → `localhost:80`
- Flask **must** run from WSL — `libmiximus.so` is Linux-only (zkSNARK proofs fail on Windows native)
- The `.env` file must be present at the project root with all keys filled in
- Database: `webapp/backend/miximus_dev.db` (SQLite) — back this up regularly

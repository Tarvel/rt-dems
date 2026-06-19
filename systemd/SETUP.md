# Auto-Start & Watchdog Setup Guide

This guide explains how to make the Smart Room system start automatically when the Raspberry Pi boots, and how the watchdog keeps it alive.

---

## How It Works

```
Pi powers on
    │
    ▼
systemd starts smartroom.service
    │
    ▼
startall.sh launches all services
    (Mosquitto, Django, Logger, Rule Engine, ML, Data Source)
    │
    ▼
60 seconds later, watchdog timer starts
    │
    ▼
Every 2 minutes: healthcheck.sh --fix
    │
    ├─ All services healthy? → log "HEALTHY", do nothing
    │
    └─ Any service down? → log "UNHEALTHY", restart smartroom.service
```

---

## Files

| File | Purpose |
|------|---------|
| `systemd/smartroom.service` | Main service — runs `startall.sh` on boot |
| `systemd/smartroom-watchdog.service` | Health check runner (oneshot) |
| `systemd/smartroom-watchdog.timer` | Triggers the health check every 2 minutes |
| `systemd/healthcheck.sh` | Checks Mosquitto, Django, ML, Logger, Rule Engine, data source |

---

## Installation (on the Raspberry Pi)

### 1. Verify paths in service files

The service files are configured for the Pi at:
```
User: grandmaster
Path: /home/grandmaster/Documents/project/PROJECT_CODE_4
```

If your setup is different, update the paths in `smartroom.service`, `smartroom-watchdog.service`, and `rule-engine.service` before copying.

### 2. Copy service files to systemd

```bash
sudo cp systemd/smartroom.service /etc/systemd/system/
sudo cp systemd/smartroom-watchdog.service /etc/systemd/system/
sudo cp systemd/smartroom-watchdog.timer /etc/systemd/system/
```

### 3. Make the health check executable

```bash
chmod +x systemd/healthcheck.sh
```

### 4. Reload systemd and enable services

```bash
# Reload to pick up the new service files
sudo systemctl daemon-reload

# Enable auto-start on boot
sudo systemctl enable smartroom.service
sudo systemctl enable smartroom-watchdog.timer

# Start everything right now
sudo systemctl start smartroom.service
sudo systemctl start smartroom-watchdog.timer
```

### 5. Verify it's running

```bash
# Check the main service
sudo systemctl status smartroom

# Check the watchdog timer
sudo systemctl list-timers | grep smartroom

# View live logs
sudo journalctl -u smartroom -f

# Run a manual health check
./systemd/healthcheck.sh
```

---

## What Happens on Reboot

1. Pi finishes booting and network comes online
2. systemd starts `smartroom.service` automatically
3. `startall.sh` launches: Mosquitto → Django → Logger → Rule Engine → ML → Data Source
4. After 60 seconds, the watchdog timer kicks in
5. Every 2 minutes, `healthcheck.sh --fix` checks all services
6. If any service is down, it restarts the entire system

---

## Commands Reference

### Day-to-day operations

```bash
# Start the system
sudo systemctl start smartroom

# Stop the system
sudo systemctl stop smartroom

# Restart the system
sudo systemctl restart smartroom

# Check status
sudo systemctl status smartroom
```

### Health checks

```bash
# Manual health check (report only)
./systemd/healthcheck.sh

# Manual health check (auto-fix if anything is down)
./systemd/healthcheck.sh --fix

# Check when the next watchdog run is scheduled
sudo systemctl list-timers | grep smartroom

# View watchdog logs
sudo journalctl -u smartroom-watchdog -n 20
```

### Logs

```bash
# Live-tail all smart room logs
sudo journalctl -u smartroom -f

# Last 50 lines
sudo journalctl -u smartroom -n 50

# Logs since last boot
sudo journalctl -u smartroom -b

# Health check history
sudo journalctl -t smartroom-health -n 20
```

### Enable/disable auto-start

```bash
# Disable auto-start (system won't start on reboot)
sudo systemctl disable smartroom.service
sudo systemctl disable smartroom-watchdog.timer

# Re-enable auto-start
sudo systemctl enable smartroom.service
sudo systemctl enable smartroom-watchdog.timer
```

---

## Changing the Data Source

The `DATA_SOURCE` variable in `.env` is read by `startall.sh` at boot:

```bash
# Edit .env
nano /home/pi/PROJECT_CODE/.env

# Change to:
DATA_SOURCE=hardware    # Group 1 live sensors
# or:
DATA_SOURCE=simulator   # CSV playback (default)

# Then restart to apply:
sudo systemctl restart smartroom
```

---

## Troubleshooting

| Problem | Solution |
|---------|----------|
| Service won't start | Check logs: `sudo journalctl -u smartroom -n 30` |
| "Permission denied" | Make sure `startall.sh` and `healthcheck.sh` have `chmod +x` |
| Wrong Python path | Verify the venv exists at `/home/pi/PROJECT_CODE/venv/` |
| Mosquitto fails to bind | Another Mosquitto instance may be running: `sudo killall mosquitto` |
| Health check shows ❌ but services seem fine | The check might be too early after boot — the 60s delay should handle this |
| Watchdog keeps restarting | Check which service is failing: run `./systemd/healthcheck.sh` manually |

---

## Uninstalling

```bash
sudo systemctl stop smartroom
sudo systemctl stop smartroom-watchdog.timer
sudo systemctl disable smartroom.service
sudo systemctl disable smartroom-watchdog.timer
sudo rm /etc/systemd/system/smartroom.service
sudo rm /etc/systemd/system/smartroom-watchdog.service
sudo rm /etc/systemd/system/smartroom-watchdog.timer
sudo systemctl daemon-reload
```

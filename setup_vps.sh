#!/bin/bash
# setup_vps.sh — Run once on a fresh DigitalOcean Droplet
# Usage: ssh root@your-vps 'bash -s' < setup_vps.sh

set -euo pipefail

echo "=== Research OS VPS Setup ==="

# System basics
apt-get update -qq
apt-get install -y -qq git tmux mosh python3-pip python3-venv nodejs npm curl

# Create non-root user
if ! id -u researcher &>/dev/null; then
    adduser --disabled-password --gecos "" researcher
    usermod -aG sudo researcher
    mkdir -p /home/researcher/.ssh
    cp ~/.ssh/authorized_keys /home/researcher/.ssh/
    chown -R researcher:researcher /home/researcher/.ssh
    echo "researcher ALL=(ALL) NOPASSWD:ALL" >> /etc/sudoers
    echo "Created user: researcher"
fi

# Switch to researcher user for the rest
su - researcher << 'USERSETUP'
set -euo pipefail

# Install Claude Code
npm install -g @anthropic-ai/claude-code 2>/dev/null || echo "Claude Code install - check manually"

# Install SkyPilot
pip install --break-system-packages "skypilot-nightly[runpod]" 2>/dev/null || echo "SkyPilot install - check manually"

# Install Python deps
pip install --break-system-packages arxiv requests pyyaml fastapi uvicorn wandb rclone-python

# Clone or init research-os repo
if [ ! -d ~/research-os ]; then
    echo "Place your research-os repo at ~/research-os"
    mkdir -p ~/research-os
fi

# Setup tmux config for better experience
cat > ~/.tmux.conf << 'TMUX'
set -g mouse on
set -g history-limit 50000
set -g default-terminal "screen-256color"
set -g status-right "%H:%M %d-%b"
TMUX

# Setup cron jobs
(crontab -l 2>/dev/null || true; echo "0 7 * * * cd ~/research-os && python3 scripts/daily_digest.py --score >> /tmp/digest.log 2>&1") | sort -u | crontab -
(crontab -l 2>/dev/null || true; echo "0 22 * * 0 cd ~/research-os && python3 scripts/spark_ideas.py >> /tmp/sparks.log 2>&1") | sort -u | crontab -
echo "Cron jobs installed: digest at 7am daily, sparks at 10pm Sunday"

echo ""
echo "=== Setup complete ==="
echo "Next steps:"
echo "  1. rsync your research-os/ repo to ~/research-os/"
echo "  2. tmux new -s research"
echo "  3. claude  (start Claude Code)"
echo "  4. In another tmux pane: uvicorn scripts.webhook_listener:app --host 0.0.0.0 --port 8080"
echo "  5. sky check  (verify RunPod connection)"
USERSETUP

# Open firewall for webhook
ufw allow 8080/tcp 2>/dev/null || true

echo "VPS setup done. SSH as: ssh researcher@$(hostname -I | awk '{print $1}')"

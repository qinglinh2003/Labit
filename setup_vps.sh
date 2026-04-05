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

# Install LABIT in editable mode
if [ -f ~/research-os/pyproject.toml ]; then
    pip install --break-system-packages -e ~/research-os
    labit --install-completion bash 2>/dev/null || true
fi

# Setup cron jobs
(crontab -l 2>/dev/null || true; echo "0 7 * * * cd ~/research-os && labit daily-summary --json >> /tmp/daily-summary.log 2>&1") | sort -u | crontab -
(crontab -l 2>/dev/null || true; echo "0 22 * * 0 cd ~/research-os && labit weekly-summary --json >> /tmp/weekly-summary.log 2>&1") | sort -u | crontab -
echo "Cron jobs installed: daily summary at 7am daily, weekly summary at 10pm Sunday"

echo ""
echo "=== Setup complete ==="
echo "Next steps:"
echo "  1. rsync your research-os/ repo to ~/research-os/"
echo "  2. tmux new -s research"
echo "  3. pip install --break-system-packages -e ~/research-os"
echo "  4. labit chat"
echo "  5. sky check  (verify RunPod connection)"
USERSETUP

echo "VPS setup done. SSH as: ssh researcher@$(hostname -I | awk '{print $1}')"

#!/usr/bin/env bash
# One-time setup of a fresh Ubuntu 22.04 or 24.04 server for QuickSurf. Run as root:
#   sudo bash server-setup.sh
# It installs Docker and git, turns on the firewall (SSH, HTTP, HTTPS only), automatic security updates and
# fail2ban, adds swap on small servers, and creates the "quicksurf" user that runs the app.
# With --harden-ssh it also turns off SSH password logins (only do that once your SSH key works).
set -euo pipefail

DEPLOY_USER=${DEPLOY_USER:-quicksurf}
[ "$(id -u)" = 0 ] || { echo "Run as root (sudo)." >&2; exit 1; }

export DEBIAN_FRONTEND=noninteractive
apt-get update -q
apt-get upgrade -yq
apt-get install -yq docker.io docker-compose-v2 git ufw fail2ban unattended-upgrades openssl
systemctl enable --now docker

timedatectl set-timezone UTC

# Firewall. Docker publishes only Caddy's 80/443 (deploy/compose.yml); never publish the database.
ufw default deny incoming
ufw default allow outgoing
ufw allow OpenSSH
ufw allow 80/tcp
ufw allow 443/tcp
ufw allow 443/udp
ufw --force enable

# Security updates install themselves.
dpkg-reconfigure -f noninteractive unattended-upgrades
systemctl enable --now fail2ban

# 2 GB of swap on servers with under 4 GB of memory, so a memory spike doesn't kill Postgres.
if ! swapon --show | grep -q . && [ "$(awk '/MemTotal/ {print $2}' /proc/meminfo)" -lt 4000000 ]; then
  fallocate -l 2G /swapfile && chmod 600 /swapfile && mkswap /swapfile && swapon /swapfile
  echo "/swapfile none swap sw 0 0" >>/etc/fstab
fi

# The user that owns the code and runs Docker. Being in the docker group is equivalent to root:
# only give this login to people you'd trust with the whole server.
if ! id "$DEPLOY_USER" >/dev/null 2>&1; then
  adduser --disabled-password --gecos "" "$DEPLOY_USER"
fi
usermod -aG docker "$DEPLOY_USER"
install -d -m 700 -o "$DEPLOY_USER" -g "$DEPLOY_USER" "/home/$DEPLOY_USER/.ssh"
if [ -n "${SUDO_USER:-}" ] && [ -f "/home/$SUDO_USER/.ssh/authorized_keys" ] && [ ! -f "/home/$DEPLOY_USER/.ssh/authorized_keys" ]; then
  install -m 600 -o "$DEPLOY_USER" -g "$DEPLOY_USER" "/home/$SUDO_USER/.ssh/authorized_keys" "/home/$DEPLOY_USER/.ssh/authorized_keys"
fi

if [ "${1:-}" = "--harden-ssh" ]; then
  [ -s "/home/$DEPLOY_USER/.ssh/authorized_keys" ] || { echo "Add an SSH key for $DEPLOY_USER first." >&2; exit 1; }
  printf 'PasswordAuthentication no\nKbdInteractiveAuthentication no\nPermitRootLogin prohibit-password\n' \
    >/etc/ssh/sshd_config.d/60-quicksurf.conf
  systemctl reload ssh
  echo "SSH password logins are off."
fi

echo
echo "Done. Next, as $DEPLOY_USER:"
echo "  git clone https://github.com/Ayindecodes/QuickSurf.git && cd QuickSurf"
echo "  cp deploy/.env.example deploy/.env && nano deploy/.env"
echo "  deploy/deploy.sh deploy"

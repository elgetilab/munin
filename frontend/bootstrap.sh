#!/usr/bin/env bash
# ─────────────────────────────────────────────────────────────────────────────
# Munin VPS Bootstrap Script
#
# Run as root on a fresh Ubuntu 24.04 VPS (Hetzner CAX21).
#
# Usage:
#   1. Create the VPS in Hetzner with your munin_admin.pub SSH key
#   2. SSH in as root: ssh -i munin_admin root@VPS_IP
#   3. Copy this script to the VPS and run it:
#      bash bootstrap.sh \
#        --admin-user varghele \
#        --tunnel-pubkey "ssh-ed25519 AAAA... munin-tunnel" \
#        --volume-id HC_Volume_XXXXXXXX
#
# What it does:
#   - Installs packages (ufw, fail2ban, docker, etc.)
#   - Creates admin user (sudo, SSH key copied from root)
#   - Creates restricted tunnel user (port-forwarding only)
#   - Hardens SSH (no root login, no passwords)
#   - Configures UFW (22, 80, 443)
#   - Configures fail2ban
#   - Installs Docker + Docker Compose
#   - Mounts Hetzner Volume at /mnt/uploads
#   - Creates ~/munin project directory for the admin user
#
# What it does NOT do (still manual):
#   - Generate secrets (openssl rand -hex 32 for AUTH_SECRET_KEY)
#   - Transfer project files (rsync from local machine)
#   - DNS configuration (HostEurope dashboard)
#   - docker compose up (run after transferring files)
#   - Cluster tunnel setup (done on the cluster side)
# ─────────────────────────────────────────────────────────────────────────────
set -euo pipefail

# ── Parse arguments ────────────────────────────────────────────────────────
ADMIN_USER=""
TUNNEL_PUBKEY=""
VOLUME_ID=""

while [[ $# -gt 0 ]]; do
    case $1 in
        --admin-user)  ADMIN_USER="$2";   shift 2 ;;
        --tunnel-pubkey) TUNNEL_PUBKEY="$2"; shift 2 ;;
        --volume-id)   VOLUME_ID="$2";    shift 2 ;;
        *) echo "Unknown option: $1"; exit 1 ;;
    esac
done

if [[ -z "$ADMIN_USER" ]]; then
    echo "Error: --admin-user is required"
    echo "Usage: bash bootstrap.sh --admin-user varghele --tunnel-pubkey 'ssh-ed25519 ...' --volume-id HC_Volume_XXXXXXXX"
    exit 1
fi

echo "=== Munin VPS Bootstrap ==="
echo "Admin user:  $ADMIN_USER"
echo "Tunnel key:  ${TUNNEL_PUBKEY:+(provided)}"
echo "Volume ID:   ${VOLUME_ID:-(skipped)}"
echo ""

# ── Must run as root ───────────────────────────────────────────────────────
if [[ $EUID -ne 0 ]]; then
    echo "Error: This script must be run as root"
    exit 1
fi

# ── Step 3: System update + packages ──────────────────────────────────────
echo ">>> Installing system packages..."
apt update && apt upgrade -y
apt install -y ufw fail2ban curl git rsync htop autossh

# ── Step 4: Create admin user ─────────────────────────────────────────────
echo ">>> Creating admin user: $ADMIN_USER"
if id "$ADMIN_USER" &>/dev/null; then
    echo "    User $ADMIN_USER already exists, skipping creation"
else
    adduser --disabled-password --gecos "" "$ADMIN_USER"
    usermod -aG sudo "$ADMIN_USER"
    # Allow passwordless sudo (optional, remove if you prefer password prompts)
    echo "$ADMIN_USER ALL=(ALL) NOPASSWD:ALL" > "/etc/sudoers.d/$ADMIN_USER"
    chmod 440 "/etc/sudoers.d/$ADMIN_USER"
fi

# Copy SSH key from root
mkdir -p "/home/$ADMIN_USER/.ssh"
cp /root/.ssh/authorized_keys "/home/$ADMIN_USER/.ssh/authorized_keys"
chown -R "$ADMIN_USER:$ADMIN_USER" "/home/$ADMIN_USER/.ssh"
chmod 700 "/home/$ADMIN_USER/.ssh"
chmod 600 "/home/$ADMIN_USER/.ssh/authorized_keys"

# ── Step 5: Create tunnel user ────────────────────────────────────────────
if [[ -n "$TUNNEL_PUBKEY" ]]; then
    echo ">>> Creating tunnel user..."
    if id tunnel &>/dev/null; then
        echo "    User tunnel already exists, skipping creation"
    else
        adduser --system --shell /usr/sbin/nologin tunnel
        usermod -d /home/tunnel tunnel
    fi

    mkdir -p /home/tunnel/.ssh
    echo "restrict,port-forwarding,command=\"/bin/false\" $TUNNEL_PUBKEY" \
        > /home/tunnel/.ssh/authorized_keys
    chown -R tunnel:nogroup /home/tunnel/.ssh
    chmod 700 /home/tunnel/.ssh
    chmod 600 /home/tunnel/.ssh/authorized_keys
else
    echo ">>> Skipping tunnel user (no --tunnel-pubkey provided)"
fi

# ── Step 6: Harden SSH ────────────────────────────────────────────────────
echo ">>> Hardening SSH..."
ALLOWED_USERS="$ADMIN_USER"
if [[ -n "$TUNNEL_PUBKEY" ]]; then
    ALLOWED_USERS="$ADMIN_USER tunnel"
fi

cat > /etc/ssh/sshd_config.d/99-munin-hardening.conf << EOF
PermitRootLogin no
PasswordAuthentication no
KbdInteractiveAuthentication no
UsePAM yes
X11Forwarding no
MaxAuthTries 3
AllowUsers $ALLOWED_USERS
GatewayPorts no
EOF

systemctl restart ssh
echo "    SSH hardened. Root login disabled."

# ── Step 7: Configure UFW ─────────────────────────────────────────────────
echo ">>> Configuring firewall (UFW)..."
ufw default deny incoming
ufw default allow outgoing
ufw allow 22/tcp
ufw allow 80/tcp
ufw allow 443/tcp
echo "y" | ufw enable

# ── Step 8: Configure fail2ban ────────────────────────────────────────────
echo ">>> Configuring fail2ban..."
cat > /etc/fail2ban/jail.local << 'EOF'
[DEFAULT]
bantime  = 1h
findtime = 10m
maxretry = 3

[sshd]
enabled = true
mode    = aggressive
EOF

systemctl enable fail2ban
systemctl restart fail2ban

# ── Step 9: Install Docker ────────────────────────────────────────────────
echo ">>> Installing Docker..."
if command -v docker &>/dev/null; then
    echo "    Docker already installed, skipping"
else
    curl -fsSL https://get.docker.com | sh
fi
usermod -aG docker "$ADMIN_USER"

# ── Step 10: Mount Hetzner Volume ─────────────────────────────────────────
if [[ -n "$VOLUME_ID" ]]; then
    echo ">>> Mounting Hetzner Volume ($VOLUME_ID)..."
    DEVICE="/dev/disk/by-id/scsi-0$VOLUME_ID"

    if [[ ! -e "$DEVICE" ]]; then
        echo "    Warning: Device $DEVICE not found. Is the volume attached?"
        echo "    Skipping mount. Run manually after attaching."
    else
        mkdir -p /mnt/uploads

        # Check if already mounted
        if mountpoint -q /mnt/uploads; then
            echo "    /mnt/uploads already mounted"
        else
            mount -o discard,defaults "$DEVICE" /mnt/uploads
        fi

        # Add to fstab if not already present
        if ! grep -q "$VOLUME_ID" /etc/fstab; then
            echo "$DEVICE /mnt/uploads ext4 discard,nofail,defaults 0 0" >> /etc/fstab
        fi

        mkdir -p /mnt/uploads/{staging,complete}
        chown -R 1000:1000 /mnt/uploads
        echo "    Volume mounted at /mnt/uploads"
        df -h /mnt/uploads
    fi
else
    echo ">>> Skipping volume mount (no --volume-id provided)"
    echo "    Using local disk for uploads at /mnt/uploads"
fi

# Always create upload directories (used by tusd + hook-service)
mkdir -p /mnt/uploads/{staging,complete}
chown -R 1000:1000 /mnt/uploads

# ── Create project directory ──────────────────────────────────────────────
echo ">>> Creating project directory..."
MUNIN_DIR="/home/$ADMIN_USER/munin"
mkdir -p "$MUNIN_DIR"
chown "$ADMIN_USER:$ADMIN_USER" "$MUNIN_DIR"

# ── Done ──────────────────────────────────────────────────────────────────
echo ""
echo "=== Bootstrap Complete ==="
echo ""
echo "Next steps:"
echo "  1. Log in as $ADMIN_USER (root login is now disabled):"
echo "     ssh -i munin_admin $ADMIN_USER@$(hostname -I | awk '{print $1}')"
echo ""
echo "  2. Transfer project files from your local machine:"
echo "     rsync -avz --exclude '.env' --exclude '.git' ./ $ADMIN_USER@VPS_IP:~/munin/"
echo "     scp .env $ADMIN_USER@VPS_IP:~/munin/.env"
echo ""
echo "  3. Start the stack (4 services: Caddy, munin-auth, tusd, hook-service):"
echo "     cd ~/munin && docker compose up -d"
echo ""
echo "  4. Configure DNS in HostEurope (A records for *.muninai.org → VPS IP)"
echo ""
echo "  5. Set up the cluster tunnel (update IP in munin-tunnel.service)"
echo ""
echo "  6. Install backfill cron (retries failed cluster ingests every 30 min):"
echo "     sudo cp ~/munin/scripts/backfill_contributed.py /usr/local/bin/"
echo "     sudo chmod +x /usr/local/bin/backfill_contributed.py"
echo "     sudo apt-get install -y python3-requests"
echo "     See scripts/install-backfill-cron.sh for the cron entry."
echo ""

#!/bin/bash
# =====================================================================
# Palworld dedicated server — one-shot provisioning for a FRESH Ubuntu box
# Installs Docker + deps, (optional) restores a backup, pulls the image,
# starts the server container, and sets up the Telegram backup cron.
# Tested target: Ubuntu 22.04/24.04, user with passwordless sudo (e.g. ubuntu).
#
#   chmod +x provision_palworld.sh && ./provision_palworld.sh
# =====================================================================
set -euo pipefail

################################  CONFIG  ##############################
CONTAINER_NAME="test-runner"
IMAGE="thijsvanloef/palworld-server-docker:latest"
DATA_DIR="/home/ubuntu/palworld-data"        # host dir mounted to /palworld
GAME_PORT="8211"                              # UDP (must be open in AWS Security Group)

# --- Server settings (match current server) ---
SERVER_NAME="${SERVER_NAME:-my-palworld}"
SERVER_PASSWORD="${SERVER_PASSWORD:-CHANGE_ME}"   # override: SERVER_PASSWORD=... ./provision_palworld.sh
ADMIN_PASSWORD="${ADMIN_PASSWORD:-CHANGE_ME}"     # override: ADMIN_PASSWORD=... ./provision_palworld.sh
PLAYERS="32"
MULTITHREADING="true"
REST_API_ENABLED="true"
REST_API_PORT="8212"
TZ_VAL="UTC"

# --- Optional restore: drop a backup zip here BEFORE running to restore it ---
# (the Telegram backups contain "Saved/..." so they extract into DATA_DIR/Pal)
RESTORE_ZIP="/home/ubuntu/restore/palworld_save.zip"

# --- Optional Telegram backup cron (set SETUP_BACKUP_CRON=false to skip) ---
SETUP_BACKUP_CRON="${SETUP_BACKUP_CRON:-false}"   # set true + fill BOT_TOKEN/CHAT_ID to enable
BOT_TOKEN="${BOT_TOKEN:-}"                        # Telegram bot token (from @BotFather)
CHAT_ID="${CHAT_ID:-}"                            # Telegram chat id (getUpdates)
BACKUP_CRON="0 20 * * *"                      # 20:00 UTC = 03:00 Vietnam, daily
#######################################################################

echo ">>> [1/6] Base packages..."
sudo apt-get update -y
sudo apt-get install -y ca-certificates curl zip unzip cron

echo ">>> [2/6] Docker..."
if ! command -v docker >/dev/null 2>&1; then
    curl -fsSL https://get.docker.com | sudo sh
fi
sudo systemctl enable --now docker
sudo systemctl enable --now cron

echo ">>> [3/6] Data directory..."
mkdir -p "$DATA_DIR"

echo ">>> [4/6] Optional restore..."
if [ -f "$RESTORE_ZIP" ]; then
    echo "    Restoring from $RESTORE_ZIP"
    mkdir -p "$DATA_DIR/Pal"
    unzip -o "$RESTORE_ZIP" -d "$DATA_DIR/Pal" >/dev/null
else
    echo "    No restore zip at $RESTORE_ZIP -> starting a fresh world."
fi
sudo chown -R 1000:1000 "$DATA_DIR"

echo ">>> [5/6] Pull image + (re)create container..."
sudo docker pull "$IMAGE"
sudo docker rm -f "$CONTAINER_NAME" 2>/dev/null || true
sudo docker run -d \
    --name "$CONTAINER_NAME" \
    --restart unless-stopped \
    -p ${GAME_PORT}:${GAME_PORT}/udp \
    -v "$DATA_DIR":/palworld \
    -e PUID=1000 -e PGID=1000 \
    -e TZ="$TZ_VAL" \
    -e PLAYERS="$PLAYERS" \
    -e SERVER_NAME="$SERVER_NAME" \
    -e SERVER_PASSWORD="$SERVER_PASSWORD" \
    -e ADMIN_PASSWORD="$ADMIN_PASSWORD" \
    -e MULTITHREADING="$MULTITHREADING" \
    -e COMMUNITY=false \
    -e RCON_ENABLED=false \
    -e REST_API_ENABLED="$REST_API_ENABLED" \
    -e REST_API_PORT="$REST_API_PORT" \
    -e UPDATE_ON_BOOT=true \
    "$IMAGE"

# Best-effort local firewall (AWS Security Group still must allow UDP $GAME_PORT)
if command -v ufw >/dev/null 2>&1 && sudo ufw status | grep -q "Status: active"; then
    sudo ufw allow ${GAME_PORT}/udp || true
fi

echo ">>> [6/6] Telegram backup cron..."
if [ "$SETUP_BACKUP_CRON" = "true" ]; then
    cat > /home/ubuntu/backup_palworld.sh <<'BKEOF'
#!/bin/bash
# Palworld save backup -> Telegram (daily via cron).
set -uo pipefail
BOT_TOKEN="__BOT_TOKEN__"
CHAT_ID="__CHAT_ID__"
PAL_DIR="/home/ubuntu/palworld-data/Pal"
BACKUP_DIR="/home/ubuntu/backups"
LOG="$BACKUP_DIR/backup.log"
DATE=$(date +"%Y-%m-%d_%H-%M-%S")
ZIP_FILE="$BACKUP_DIR/palworld_save_$DATE.zip"
MAX_BYTES=$((49 * 1024 * 1024))
API="https://api.telegram.org/bot$BOT_TOKEN"
mkdir -p "$BACKUP_DIR"
log() { echo "[$(date '+%F %T')] $*" >> "$LOG"; }
cd "$PAL_DIR" || { log "ERROR: cannot cd to $PAL_DIR"; exit 1; }
if [ ! -d Saved ]; then log "ERROR: Saved/ not found"; exit 1; fi
zip -rq "$ZIP_FILE" Saved -x '*.log' '*.tmp'
if [ ! -s "$ZIP_FILE" ]; then log "ERROR: zip missing/empty"; exit 1; fi
SIZE=$(stat -c%s "$ZIP_FILE"); SIZE_MB=$((SIZE / 1024 / 1024))
log "created $(basename "$ZIP_FILE") (${SIZE} bytes / ~${SIZE_MB}MB)"
if [ "$SIZE" -gt "$MAX_BYTES" ]; then
    curl -s -F chat_id="$CHAT_ID" -F text="Backup $DATE = ${SIZE_MB}MB > 50MB Telegram limit. Kept at $ZIP_FILE" "$API/sendMessage" >/dev/null
    log "ERROR: too big (${SIZE_MB}MB) - kept local, sent warning"; exit 1
fi
RESP=$(curl -s -F chat_id="$CHAT_ID" -F document=@"$ZIP_FILE" -F caption="Backup Palworld Server: $DATE (${SIZE_MB}MB)" "$API/sendDocument")
if echo "$RESP" | grep -q '"ok":true'; then
    rm -f "$ZIP_FILE"; log "sent OK, local zip removed"
else
    log "ERROR: telegram send failed - kept local zip. Response: $RESP"; exit 1
fi
BKEOF
    sed -i "s|__BOT_TOKEN__|$BOT_TOKEN|; s|__CHAT_ID__|$CHAT_ID|" /home/ubuntu/backup_palworld.sh
    chmod 700 /home/ubuntu/backup_palworld.sh
    ( crontab -l 2>/dev/null | grep -v 'backup_palworld.sh' || true ; \
      echo "$BACKUP_CRON /home/ubuntu/backup_palworld.sh" ) | crontab -
    echo "    backup cron installed: $BACKUP_CRON"
fi

echo ""
echo "=========================================================="
echo " DONE. Server container '$CONTAINER_NAME' is starting."
echo "   Logs:     sudo docker logs -f $CONTAINER_NAME"
echo "   Join UDP: <public-ip>:$GAME_PORT   (open it in the AWS Security Group!)"
echo "   Data dir: $DATA_DIR"
echo "=========================================================="

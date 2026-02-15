#!/bin/bash
# Interactive admin menu for Intelligent Sump Pump Controller
SERVICE_NAME="intelligent-sump.service"
CONFIG_FILE="$HOME/sump_config.json"
CONTROLLER_SCRIPT="$HOME/intelligent_sump_controller.py"
function require_sudo() {
  if ! command -v sudo >/dev/null 2>&1; then
    echo "This menu requires sudo to manage the service."
    read -rp "Press Enter to return to menu..." _
    return 1
  fi
  return 0
}
function view_logs() {
  require_sudo || return
  echo "\nShowing logs from the past 24 hours. Use q to exit less.\n"
  read -rp "Press Enter to continue..." _
  sudo journalctl -u "$SERVICE_NAME" --since "24 hours ago" --output=short-iso --no-hostname | less
}
function restart_service() {
  require_sudo || return
  echo "Restarting $SERVICE_NAME..."
  if sudo systemctl restart "$SERVICE_NAME"; then
    echo "Restart successful. Fetching latest status...\n"
    sudo systemctl status "$SERVICE_NAME" --no-pager
  else
    echo "Failed to restart $SERVICE_NAME." >&2
  fi
  read -rp "Press Enter to return to the menu..." _
}
function update_cycle_time() {
  local minutes seconds
  echo "\nEnter the desired OFF time in minutes (minimum 5)."
  read -rp "New wait time (minutes): " minutes
  if ! [[ $minutes =~ ^[0-9]+$ ]]; then
    echo "Invalid entry: please enter a whole number."
    read -rp "Press Enter to return to the menu..." _
    return
  fi
  if (( minutes < 5 )); then
    echo "Value too low. The controller enforces a 5 minute minimum."
    read -rp "Press Enter to return to the menu..." _
    return
  fi
  seconds=$((minutes * 60))
  python3 - "$CONFIG_FILE" "$seconds" <<'PY'
import json
import sys
from pathlib import Path
from datetime import datetime
config_path = Path(sys.argv[1])
seconds = int(sys.argv[2])
if config_path.exists():
    with config_path.open() as fh:
        try:
            data = json.load(fh)
        except json.JSONDecodeError:
            data = {}
else:
    data = {}
data['current_off_time'] = seconds
data['last_updated'] = datetime.now().isoformat()
with config_path.open('w') as fh:
    json.dump(data, fh, indent=2)
print(f"Updated {config_path} with current_off_time={seconds} seconds")
PY
  if [[ -f "$CONTROLLER_SCRIPT" ]]; then
    echo "\nWould you like to apply this wait time immediately using the controller's override?"
    read -rp "Apply override now? (y/N): " apply_override
    if [[ ${apply_override,,} == "y" ]]; then
      python3 "$CONTROLLER_SCRIPT" "wait $minutes"
    fi
  else
    echo "\nController script not found at $CONTROLLER_SCRIPT; skipped immediate override."
  fi
  echo "\nWait time updated to $minutes minutes ($seconds seconds)."
  echo "Restart the service or wait for the next cycle for the change to take effect."
  read -rp "Press Enter to return to the menu..." _
}
function show_menu() {
  clear
  echo "==============================="
  echo " Intelligent Sump Controller"
  echo "==============================="
  echo "1) View logs from past 24 hours"
  echo "2) Restart controller service"
  echo "3) Set pump wait time"
  echo "4) Exit"
  echo
}
while true; do
  show_menu
  read -rp "Select an option: " choice
  case "$choice" in
    1) view_logs ;;
    2) restart_service ;;
    3) update_cycle_time ;;
    4) echo "Goodbye."; break ;;
    *) echo "Invalid choice. Please select 1-4."; sleep 1 ;;
  esac
done

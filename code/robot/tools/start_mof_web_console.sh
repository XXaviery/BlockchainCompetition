#!/usr/bin/env bash
set -Eeuo pipefail

script_dir=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)
project_root=$(cd -- "$script_dir/.." && pwd)
pi_target=${MOF_PI_TARGET:?set MOF_PI_TARGET to the deployment target}
ros_setup=${MOF_ROS_SETUP:?MOF_ROS_SETUP is required}
ros_workspace=${MOF_ROS_WS:?MOF_ROS_WS is required}
serial_port=${MOF_SERIAL_PORT:-/dev/mof_esp32}
local_port=${MOF_WEB_PORT:-4173}
remote_port=${MOF_WEB_REMOTE_PORT:-4173}
socket_path=${MOF_SSH_SOCKET:-/tmp/mof_web_console_${UID}.sock}
# Points library: every subdirectory with metadata.yaml and at least one
# .mcap is staged to the Pi under its own directory name, which becomes the
# name shown in the web console bag list.
points_root=${MOF_POINTS_DIR:?MOF_POINTS_DIR is required}
stamp=$(date +%Y%m%d_%H%M%S)_$$
remote_stage="/tmp/mof_web_console_$stamp"
master_created=0
forward_created=0
remote_started=0

ssh_base=(ssh -S "$socket_path" -o ConnectTimeout=8 -o StrictHostKeyChecking=yes "$pi_target")
scp_base=(scp -o ControlPath="$socket_path" -o ConnectTimeout=8 -o StrictHostKeyChecking=yes)

cleanup() {
  local exit_code=$?
  trap - INT TERM HUP EXIT
  trap '' INT HUP
  if [[ "$remote_started" == 1 ]]; then
    "${ssh_base[@]}" "stage='$remote_stage'; if test -r \"\$stage/run/runner.pid\"; then pid=\$(tr -cd '0-9' < \"\$stage/run/runner.pid\"); case \"\$pid\" in ''|*[!0-9]*) ;; *) kill -TERM \"\$pid\" 2>/dev/null || true ;; esac; fi" || true
    for _ in {1..100}; do
      if ! "${ssh_base[@]}" "test -r '$remote_stage/run/runner.pid' && pid=\$(tr -cd '0-9' < '$remote_stage/run/runner.pid') && kill -0 \"\$pid\" 2>/dev/null" >/dev/null 2>&1; then
        break
      fi
      sleep 0.1
    done
  fi
  if [[ "$forward_created" == 1 ]]; then
    ssh -S "$socket_path" -O cancel -L "$local_port:127.0.0.1:$remote_port" "$pi_target" >/dev/null 2>&1 || true
  fi
  if [[ "$master_created" == 1 ]]; then
    ssh -S "$socket_path" -O exit "$pi_target" >/dev/null 2>&1 || true
  fi
  printf '\nMOF Web console stopped. Robot command was returned to zero.\n'
  exit "$exit_code"
}
trap cleanup INT TERM HUP EXIT

for required in \
  "$project_root/web/server.py" \
  "$project_root/web/main.js" \
  "$project_root/web/index.html" \
  "$project_root/web/styles.css" \
  "$project_root/ros2_ws/src/mof_esp32_bridge/config/mof_manual_open_field_params.yaml" \
  "$script_dir/run_mof_web_console_remote.sh"; do
  if [[ ! -f "$required" ]]; then
    printf 'ERROR: required file is missing: %s\n' "$required" >&2
    exit 10
  fi
done

point_dirs=()
for candidate in "$points_root"/*/; do
  [[ -d "$candidate" ]] || continue
  if [[ -f "${candidate}metadata.yaml" ]] && compgen -G "${candidate}*.mcap" >/dev/null; then
    point_dirs+=("${candidate%/}")
  fi
done
if [[ ${#point_dirs[@]} -eq 0 ]]; then
  printf 'ERROR: no point bags found under %s (each point needs metadata.yaml and at least one .mcap)\n' "$points_root" >&2
  exit 11
fi
printf 'Staging points: %s\n' "${point_dirs[*]##*/}"

check_local_port_free() {
  if ss -tln 2>/dev/null | awk '{print $4}' | grep -qE "(^|:)$local_port$"; then
    printf 'ERROR: local port %s is already in use; cannot forward the web console.\n' "$local_port" >&2
    printf 'Process holding the port:\n' >&2
    ss -tlnp 2>/dev/null | grep ":$local_port" >&2 || true
    printf 'Hint: it is often a leftover ssh ControlMaster from an earlier run; check with: ps aux | grep "ssh -M"\n' >&2
    exit 16
  fi
}

check_local_port_free

if ssh -S "$socket_path" -O check "$pi_target" >/dev/null 2>&1; then
  printf 'Reusing SSH connection: %s\n' "$socket_path"
else
  if [[ -e "$socket_path" ]]; then
    printf 'WARN: removing stale SSH socket (no live master): %s\n' "$socket_path" >&2
    rm -f -- "$socket_path"
  fi
  printf 'Connecting to %s (SSH may request authentication once)\n' "$pi_target"
  ssh -M -S "$socket_path" -o ControlPersist=120m -o ConnectTimeout=8 \
    -o ServerAliveInterval=15 -o ServerAliveCountMax=4 \
    -o StrictHostKeyChecking=yes -Nf "$pi_target"
  master_created=1
fi

"${ssh_base[@]}" "test -e '$serial_port' && ! fuser '$serial_port' >/dev/null 2>&1 && mkdir -p '$remote_stage/web' '$remote_stage/bags'"
"${scp_base[@]}" \
  "$project_root/web/server.py" "$project_root/web/main.js" \
  "$project_root/web/index.html" "$project_root/web/styles.css" \
  "$pi_target:$remote_stage/web/"
"${scp_base[@]}" \
  "$project_root/ros2_ws/src/mof_esp32_bridge/config/mof_manual_open_field_params.yaml" \
  "$script_dir/run_mof_web_console_remote.sh" \
  "$pi_target:$remote_stage/"
"${scp_base[@]}" -r "${point_dirs[@]}" "$pi_target:$remote_stage/bags/"

remote_ros_setup=$(printf '%q' "$ros_setup")
remote_ros_workspace=$(printf '%q' "$ros_workspace")
"${ssh_base[@]}" "chmod +x '$remote_stage/run_mof_web_console_remote.sh'; MOF_ROS_SETUP=$remote_ros_setup MOF_ROS_WS=$remote_ros_workspace nohup setsid '$remote_stage/run_mof_web_console_remote.sh' '$remote_stage' '$serial_port' '$remote_port' >'$remote_stage/run_supervisor.log' 2>&1 </dev/null &"
remote_started=1

ready=0
for _ in {1..200}; do
  if "${ssh_base[@]}" "grep -qx 'READY=1' '$remote_stage/run/status.env' 2>/dev/null"; then
    ready=1
    break
  fi
  if "${ssh_base[@]}" "test -f '$remote_stage/run/runner.pid' && pid=\$(tr -cd '0-9' < '$remote_stage/run/runner.pid') && ! kill -0 \"\$pid\" 2>/dev/null"; then
    "${ssh_base[@]}" "tail -n 100 '$remote_stage/run_supervisor.log'; test ! -f '$remote_stage/run/web_server.log' || tail -n 100 '$remote_stage/run/web_server.log'; test ! -f '$remote_stage/run/ros_launch.log' || tail -n 100 '$remote_stage/run/ros_launch.log'" >&2
    exit 13
  fi
  sleep 0.15
done
if [[ "$ready" != 1 ]]; then
  printf 'ERROR: remote console startup timed out\n' >&2
  exit 14
fi

check_local_port_free
ssh -S "$socket_path" -O forward -L "$local_port:127.0.0.1:$remote_port" "$pi_target"
forward_created=1

printf '\nREADY: open http://127.0.0.1:%s\n' "$local_port"
printf 'The page can drive the robot and replay the staged bag.\n'
printf 'StopZone is bypassed. Playback is isolated and cannot overlap non-zero motion.\n'
printf 'Keep this terminal open; press Ctrl+C once to stop everything safely.\n\n'

while "${ssh_base[@]}" "test -r '$remote_stage/run/runner.pid' && pid=\$(tr -cd '0-9' < '$remote_stage/run/runner.pid') && kill -0 \"\$pid\" 2>/dev/null"; do
  sleep 2
done

printf 'ERROR: remote console stopped unexpectedly. Logs remain in %s\n' "$remote_stage" >&2
exit 15

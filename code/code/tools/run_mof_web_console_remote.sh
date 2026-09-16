#!/usr/bin/env bash
set -Eeuo pipefail

if [[ $# -lt 1 ]]; then
  printf 'This is the Pi-side helper for the web console.\nRun tools/start_mof_web_console.sh on the PC instead.\n' >&2
  exit 2
fi
stage_dir=$1
serial_port=${2:-/dev/mof_esp32}
web_port=${3:-4173}
ros_workspace=${MOF_ROS_WS:-$HOME/ros2_ws}
run_dir="$stage_dir/run"
launch_pid=""
launch_pgid=""
web_pid=""
web_pgid=""

mkdir -p "$run_dir"
printf '%s\n' "$$" > "$run_dir/runner.pid"

stop_group() {
  local pid=${1:-}
  local pgid=${2:-}
  local label=${3:-process}
  [[ -n "$pid" && -n "$pgid" ]] || return 0
  kill -0 "$pid" 2>/dev/null || return 0
  kill -INT -- "-$pgid" 2>/dev/null || true
  for _ in {1..80}; do
    kill -0 "$pid" 2>/dev/null || return 0
    sleep 0.1
  done
  printf 'WARN: %s ignored SIGINT; sending SIGTERM to owned PGID %s\n' "$label" "$pgid" >&2
  kill -TERM -- "-$pgid" 2>/dev/null || true
  for _ in {1..30}; do
    kill -0 "$pid" 2>/dev/null || return 0
    sleep 0.1
  done
  printf 'ERROR: %s owned PGID %s did not stop\n' "$label" "$pgid" >&2
  return 1
}

stop_orphaned_player() {
  # Backstop for a bag player orphaned by a hard-killed web server: the
  # player runs in its own session, so stopping the server PGID does not
  # reach it. The server records the player pid/pgid in its evidence dir.
  local record="$run_dir/bag_evidence/bag_play_process.json"
  [[ -r "$record" ]] || return 0
  local pid="" pgid=""
  pid=$(grep -o '"pid": [0-9]*' "$record" | head -1 | tr -cd '0-9') || true
  pgid=$(grep -o '"pgid": [0-9]*' "$record" | head -1 | tr -cd '0-9') || true
  stop_group "$pid" "$pgid" rosbag-player || true
}

cleanup() {
  trap - INT TERM EXIT
  stop_group "$web_pid" "$web_pgid" web-console || true
  stop_orphaned_player
  stop_group "$launch_pid" "$launch_pgid" ros-launch || true
  printf 'STOPPED_AT=%s\n' "$(date --iso-8601=seconds)" >> "$run_dir/status.env"
}
trap cleanup INT TERM EXIT

# ROS setup scripts are not nounset-clean; restore strict mode immediately.
set +u
source /opt/ros/jazzy/setup.bash
source "$ros_workspace/install/setup.bash"
set -u

if [[ ! -e "$serial_port" ]]; then
  printf 'ERROR: stable serial device is missing: %s\n' "$serial_port" >&2
  exit 20
fi
if fuser "$serial_port" >/dev/null 2>&1; then
  printf 'ERROR: serial device is already owned; stop the other robot process first\n' >&2
  fuser -v "$serial_port" >&2 || true
  exit 21
fi

setsid ros2 launch mof_esp32_bridge manual_open_field.launch.py \
  port:="$serial_port" \
  params_file:="$stage_dir/mof_manual_open_field_params.yaml" \
  >"$run_dir/ros_launch.log" 2>&1 &
launch_pid=$!
launch_pgid=$(ps -o pgid= -p "$launch_pid" | tr -d ' ')
printf 'LAUNCH_PID=%s\nLAUNCH_PGID=%s\n' "$launch_pid" "$launch_pgid" >> "$run_dir/status.env"

for _ in {1..100}; do
  kill -0 "$launch_pid" 2>/dev/null || {
    printf 'ERROR: ROS launch exited during startup\n' >&2
    tail -n 80 "$run_dir/ros_launch.log" >&2 || true
    exit 22
  }
  if fuser "$serial_port" >/dev/null 2>&1; then
    break
  fi
  sleep 0.1
done
if ! fuser "$serial_port" >/dev/null 2>&1; then
  printf 'ERROR: bridge did not acquire the serial device\n' >&2
  exit 23
fi

setsid python3 "$stage_dir/Web/server.py" \
  --console --host 127.0.0.1 --port "$web_port" \
  --bag-root "$stage_dir/bags" --evidence-dir "$run_dir/bag_evidence" \
  >"$run_dir/web_server.log" 2>&1 &
web_pid=$!
web_pgid=$(ps -o pgid= -p "$web_pid" | tr -d ' ')
printf 'WEB_PID=%s\nWEB_PGID=%s\n' "$web_pid" "$web_pgid" >> "$run_dir/status.env"

ready=0
for _ in {1..150}; do
  kill -0 "$web_pid" 2>/dev/null || {
    printf 'ERROR: Web server exited during startup\n' >&2
    tail -n 80 "$run_dir/web_server.log" >&2 || true
    exit 24
  }
  if curl -fsS "http://127.0.0.1:$web_port/api/status" \
    | grep -q '"ros_available": true'; then
    ready=1
    break
  fi
  sleep 0.1
done
if [[ "$ready" != 1 ]]; then
  printf 'ERROR: Web/ROS console did not become ready\n' >&2
  exit 25
fi

printf 'READY=1\nREADY_AT=%s\n' "$(date --iso-8601=seconds)" >> "$run_dir/status.env"
printf 'READY: Web console and open-field robot chain are running\n'
wait "$web_pid"

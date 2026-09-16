# MOF Web console safety modes

`web/` is located under the hardware source root `code/robot/`. Run every source command below from that root; no fixed drive letter or user directory is required.

The source paths are relative to that root. SSH host, ROS workspace, serial device, map/bag roots, evidence directory and HTTP ports are deployment parameters. Set them in the deployment shell, for example:

```bash
export MOF_ROS_WS="${MOF_ROS_WS:-$HOME/ros2_ws}"
export MOF_SERIAL_PORT="${MOF_SERIAL_PORT:-/dev/mof_esp32}"
export MOF_WEB_PORT="${MOF_WEB_PORT:-4173}"
```

The normal deployment entry point is one PC/WSL command:

```bash
bash tools/start_mof_web_console.sh
```

It stages the `web/` files and the default bag, starts the open-field ROS chain (Collision Monitor/StopZone bypassed), starts the combined console, and creates the local SSH tunnel. Open `http://127.0.0.1:4173`, then keep the script terminal open. Press `Ctrl+C` there to publish zero and stop only the processes owned by the launcher.

The SSH host, remote ROS workspace, serial device, bag roots, evidence directory and HTTP port are deployment/runtime parameters. They are not project source paths. For a portable source-only check that needs no ROS or bag data, use the static preview below.

`web/server.py` also has four explicit modes. Operational modes bind only to loopback and are intended to be reached through an SSH local port forward.

```bash
# Static preview: no ROS motion and no rosbag subprocess permission
python3 web/server.py --host 127.0.0.1 --port 4173

# Manual motion only: publishes only /cmd_vel_nav
python3 web/server.py --motion-only --host 127.0.0.1 --port 4173

# Rosbag telemetry playback only: forces ROS_DOMAIN_ID=97
python3 web/server.py --playback-only --host 127.0.0.1 --port 4173 \
  --bag-root /path/to/bag/root --evidence-dir /path/to/evidence

# Combined console: both panels, with motion/playback runtime interlock
python3 web/server.py --console --host 127.0.0.1 --port 4173 \
  --bag-root /path/to/bag/root --evidence-dir /path/to/evidence
```

## 污染态势仿真演示

该模式是独立的软件在环只读演示，不需要 ROS、串口、rosbag 或设备：

```bash
python3 web/server.py --pollution-demo --host 127.0.0.1 --port 4173
```

页面面板包含 A/B/C/D 区域热力图、PM2.5、VOC、CO₂、温度、湿度、当前风险、预测风险、图例、场景切换、时间步播放和区域详情。页面固定显示：

```text
仿真演示数据 · 非实机传感器读数
SIMULATION / SOFTWARE-IN-THE-LOOP
```

只读接口为：

```text
GET /api/pollution/catalog
GET /api/pollution/snapshot?scenario_id=scenario_1_pm25_spike&step=0&metric=current_risk
```

场景文件固定映射到 `assets/pollution/scenarios/`，种子为 `20260999`；接口拒绝未知场景、路径穿越、非法步号、非白名单指标、非有限数值和超大查询。污染模块不调用 `MotionPublisher`，不初始化 ROS，不启动子进程，不发布 Twist，不写 rosbag，并且没有污染相关 POST 接口。

The combined console never permits physical motion and playback at the same time. Starting playback requires a zero motion command; non-zero motion is rejected until replay stops.

## Motion interlocks

- Browser motion and Stop requests carry an ephemeral browser session ID and a strictly increasing sequence number.
- The server rejects stale and repeated sequences. A late motion request cannot overwrite a newer Stop.
- A reloaded browser can take ownership only by sending a new Stop first; this avoids the former permanent HTTP 409 lock.
- Held controls refresh at 20 Hz. The server watchdog forces zero after 300 ms without an accepted refresh.
- Pointer release/cancel/lost capture, key release, window blur, page hiding, page exit and closing the console all send a newer Stop.
- Process shutdown explicitly publishes zero for at least 500 ms and prints a timestamped zero-publication report.
- Non-zero motion is refused unless `/cmd_vel_nav` has a safety-chain subscriber.

## HTTP protection

Each server process generates an ephemeral control token. The browser obtains it from the same-origin session endpoint and supplies it only in the `X-MOF-Control-Token` header on POST. POST requests without an exact same origin or without the token are rejected. The token is not printed, committed or written to evidence.

## Playback isolation

Telemetry-only playback always uses `ROS_DOMAIN_ID=97` and only the following telemetry allowlist:

- `/chassis/debug`
- `/tf`
- `/wheel/odom`

All velocity, command, action and other topics are excluded by construction. The evidence directory records the exact command, PID, PGID, domain, topic list, process events, stdout and stderr. Stop confirms process-group exit with bounded `SIGINT`, then `SIGTERM`, and finally cleans only the process group created by this server if escalation is required.

## Live motion replay

A bag whose `metadata.yaml` contains the exact topic `/cmd_vel` is classified as a motion bag (`has_motion`). Pressing Play on it drives the real robot along the recorded path:

```bash
ros2 bag play <bag> --topics /cmd_vel --remap /cmd_vel:=/cmd_vel_nav
```

It runs in the default ROS domain (no `ROS_DOMAIN_ID` override) so the command flows through the normal velocity_smoother → bridge chain. Telemetry topics are never replayed in this mode. `--playback-only` mode cannot start motion replays (no ROS motion publisher).

Safety model:

- The server suspends its own 20 Hz `/cmd_vel_nav` publisher for the duration of the replay (otherwise its zero frames would interleave with the replayed command), and resumes it with a 0.5 s zero burst when the replay stops, ends naturally, or is stopped by the dead-man.
- Pause is rejected for motion replays (`SIGSTOP` mid-path would freeze a non-zero command).
- Dead-man: while a motion replay is playing, the browser posts `/api/bags/heartbeat` every 200 ms. The server watchdog stops the replay within `MOTION_REPLAY_DEADMAN_SECONDS` (2.5 s) without heartbeats. Closing the tab, losing the network, or browser background-tab throttling stops the robot.
- Start requires a zero command, a live safety-chain subscriber on `/cmd_vel_nav`, and no other active replay. Manual motion remains rejected while a replay is active.
- Evidence records `replay_mode`, `ros_domain_id: null`, the `--remap` rule, and a `motion-replay-deadman-expired` event when the dead-man fires. The launcher's remote cleanup also stops an orphaned player if the server is hard-killed.
- If the smoother does not receive replayed messages (QoS mismatch between the recorded `/cmd_vel` profile and the smoother's `/cmd_vel_nav` subscription), add a `--qos-profile-overrides-path` override matching the smoother (reliable/volatile). The physical e-stop remains the final backstop.

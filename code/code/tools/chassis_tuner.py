#!/usr/bin/env python3
import argparse
import math
import struct
import sys
import time

import serial


BAUD_DEFAULT = 921600
TUNE_HEAD = b"\xAA\x67"
TUNE_RESPONSE_HEAD = b"\xAA\x78"
CHASSIS_HEAD = b"\xAA\x77"
IMU_HEAD = b"\xAA\x88"
CHASSIS_PAYLOAD_LEN = 54
IMU_PAYLOAD_LEN = 64
TUNE_RESPONSE_PAYLOAD_LEN = 14

GROUPS = {
    "start-pos": 2,
    "start-neg": 3,
    "run-pos": 4,
    "run-neg": 5,
    "ff-pos": 6,
    "ff-neg": 7,
    "pid": 8,
    "pid-rotation": 9,
}


def build_tune_frame(command, values=(0.0, 0.0, 0.0)):
    payload = struct.pack("<Bfff", command, *values)
    return TUNE_HEAD + bytes([len(payload)]) + payload + bytes([sum(payload) & 0xFF])


class TelemetryParser:
    def __init__(self):
        self.buffer = bytearray()
        self.bad_checksum = 0

    def feed(self, data):
        self.buffer.extend(data)
        frames = []
        while True:
            marker = self.buffer.find(b"\xAA")
            if marker < 0:
                self.buffer.clear()
                break
            if marker:
                del self.buffer[:marker]
            if len(self.buffer) < 3:
                break
            head = bytes(self.buffer[:2])
            length = self.buffer[2]
            expected = {
                CHASSIS_HEAD: CHASSIS_PAYLOAD_LEN,
                IMU_HEAD: IMU_PAYLOAD_LEN,
                TUNE_RESPONSE_HEAD: TUNE_RESPONSE_PAYLOAD_LEN,
            }.get(head)
            if expected is None or length != expected:
                del self.buffer[0]
                continue
            total = length + 4
            if len(self.buffer) < total:
                break
            payload = bytes(self.buffer[3:3 + length])
            checksum = self.buffer[3 + length]
            del self.buffer[:total]
            if (sum(payload) & 0xFF) != checksum:
                self.bad_checksum += 1
                continue
            if head == CHASSIS_HEAD:
                values = struct.unpack("<ffffffhhhiiifff", payload)
                frames.append(("chassis", {
                    "target": values[0:3],
                    "measured": values[3:6],
                    "pwm": values[6:9],
                    "encoder": values[9:12],
                    "distance": values[12:15],
                }))
            elif head == IMU_HEAD:
                values = struct.unpack("<BBIH14f", payload)
                frames.append(("imu", values))
            else:
                command, ok, v1, v2, v3 = struct.unpack("<BBfff", payload)
                frames.append(("response", (command, bool(ok), (v1, v2, v3))))
        return frames


class ChassisTuner:
    def __init__(self, port, baud):
        self.serial = serial.Serial(port, baud, timeout=0.01, write_timeout=0.2)
        self.serial.dtr = False
        self.serial.rts = False
        self.parser = TelemetryParser()
        self.latest_chassis = None
        # CP210x open/reset can restart the ESP32-S3. Wait for setup() to finish
        # before sending the first request, then establish a known stopped state.
        # Opening CP210x can reset the ESP32. IMU calibration in setup() takes
        # roughly two seconds, so do not send AA67 commands until it completes.
        time.sleep(5.0)
        self.serial.reset_input_buffer()
        self.stop()

    def close(self):
        try:
            self.stop()
        finally:
            self.serial.close()

    def send(self, command, values=(0.0, 0.0, 0.0)):
        self.serial.write(build_tune_frame(command, values))

    def stop(self):
        for _ in range(12):
            self.send(0)
            time.sleep(0.02)

    def poll(self):
        waiting = self.serial.in_waiting
        data = self.serial.read(waiting if waiting else 1)
        frames = self.parser.feed(data)
        for kind, value in frames:
            if kind == "chassis":
                self.latest_chassis = value
        return frames

    def wait_response(self, command, timeout=1.0):
        end = time.monotonic() + timeout
        while time.monotonic() < end:
            for kind, value in self.poll():
                if kind == "response" and value[0] == command:
                    return value
        raise TimeoutError(f"AA78 response timeout for command {command}")

    def command(self, command, values=(0.0, 0.0, 0.0)):
        self.send(command, values)
        return self.wait_response(command)

    def run_manual(self, pwm, duration, collect=True):
        start = time.monotonic()
        end = start + duration
        next_send = 0.0
        samples = []
        try:
            while time.monotonic() < end:
                now = time.monotonic()
                if now >= next_send:
                    self.send(1, pwm)
                    next_send = now + 0.05
                for kind, value in self.poll():
                    if collect and kind == "chassis":
                        samples.append(value)
        finally:
            self.stop()
        return samples

    def reset_encoders(self):
        return self.command(13)

    def scan_motor_direction(self, motor_index, direction, start=50, stop=220,
                             step=10, duration=0.40, count_threshold=10):
        for magnitude in range(start, stop + 1, step):
            self.reset_encoders()
            pwm = [0.0, 0.0, 0.0]
            pwm[motor_index] = float(direction * magnitude)
            samples = self.run_manual(tuple(pwm), duration)
            if not samples:
                continue
            delta = samples[-1]["encoder"][motor_index] - samples[0]["encoder"][motor_index]
            peak_speed = max(abs(x["measured"][motor_index]) for x in samples)
            print(
                f"M{motor_index + 1} {'+' if direction > 0 else '-'} "
                f"PWM={magnitude:3d} count={delta:+6d} peak={peak_speed:.4f} m/s"
            )
            if abs(delta) >= count_threshold and peak_speed >= 0.02:
                return magnitude
            time.sleep(0.20)
        return None


def print_chassis(sample):
    print("wheel  target(m/s) measured(m/s)   pwm    encoder   distance(m)")
    for i in range(3):
        print(
            f"M{i + 1}    {sample['target'][i]:+9.4f}   "
            f"{sample['measured'][i]:+10.4f}  {sample['pwm'][i]:+5d}  "
            f"{sample['encoder'][i]:+9d}   {sample['distance'][i]:+10.5f}"
        )


def main():
    parser = argparse.ArgumentParser(description="MOF-Robot binary serial chassis tuner")
    parser.add_argument("--port", default="COM8")
    parser.add_argument("--baud", type=int, default=BAUD_DEFAULT)
    sub = parser.add_subparsers(dest="action", required=True)

    monitor = sub.add_parser("monitor")
    monitor.add_argument("--seconds", type=float, default=10.0)

    manual = sub.add_parser("manual")
    manual.add_argument("pwm1", type=float)
    manual.add_argument("pwm2", type=float)
    manual.add_argument("pwm3", type=float)
    manual.add_argument("--seconds", type=float, default=1.0)

    map_rotation = sub.add_parser("map-rotation-pwm")
    map_rotation.add_argument("values", type=float, nargs="+")
    map_rotation.add_argument("--seconds", type=float, default=1.5)

    scan = sub.add_parser("scan")
    scan.add_argument("--start", type=int, default=50)
    scan.add_argument("--stop", type=int, default=220)
    scan.add_argument("--step", type=int, default=10)

    query = sub.add_parser("query")
    query.add_argument("group", choices=GROUPS)

    sub.add_parser("query-all")

    set_cmd = sub.add_parser("set")
    set_cmd.add_argument("group", choices=GROUPS)
    set_cmd.add_argument("values", type=float, nargs=3)

    configure_negative = sub.add_parser("configure-negative")
    configure_negative.add_argument("start_pwm", type=float)
    configure_negative.add_argument("running_pwm", type=float)

    configure_rotation_pid = sub.add_parser("configure-rotation-pid")
    configure_rotation_pid.add_argument("kp", type=float)
    configure_rotation_pid.add_argument("ki", type=float)
    configure_rotation_pid.add_argument("kd", type=float)

    configure_ff_negative = sub.add_parser("configure-ff-negative")
    configure_ff_negative.add_argument("gain", type=float)

    configure_positive = sub.add_parser("configure-positive")
    configure_positive.add_argument("start_pwm", type=float)
    configure_positive.add_argument("running_pwm", type=float)
    configure_positive.add_argument("feedforward", type=float)

    configure_running = sub.add_parser("configure-running")
    configure_running.add_argument("positive", type=float)
    configure_running.add_argument("negative", type=float)

    sub.add_parser("save")
    sub.add_parser("load")
    sub.add_parser("defaults")
    sub.add_parser("reset-encoders")
    sub.add_parser("encoder-config")

    args = parser.parse_args()
    tuner = ChassisTuner(args.port, args.baud)
    try:
        if args.action == "monitor":
            end = time.monotonic() + args.seconds
            next_print = 0.0
            while time.monotonic() < end:
                tuner.poll()
                if tuner.latest_chassis is not None and time.monotonic() >= next_print:
                    print_chassis(tuner.latest_chassis)
                    print()
                    next_print = time.monotonic() + 0.5
        elif args.action == "manual":
            samples = tuner.run_manual((args.pwm1, args.pwm2, args.pwm3), args.seconds)
            if samples:
                print_chassis(samples[-1])
                start_encoder = samples[0]["encoder"]
                end_encoder = samples[-1]["encoder"]
                delta = tuple(end_encoder[i] - start_encoder[i] for i in range(3))
                peak = tuple(
                    max(abs(sample["measured"][i]) for sample in samples)
                    for i in range(3)
                )
                print("encoder delta:", delta)
                print("peak speed (m/s):", tuple(round(value, 4) for value in peak))
        elif args.action == "map-rotation-pwm":
            for pwm in args.values:
                samples = tuner.run_manual((pwm, pwm, pwm), args.seconds)
                if not samples:
                    print(f"PWM {pwm:+.0f}: no telemetry")
                    continue
                steady = samples[len(samples) // 2:]
                delta = tuple(
                    samples[-1]["encoder"][i] - samples[0]["encoder"][i]
                    for i in range(3)
                )
                average_speed = tuple(
                    sum(sample["measured"][i] for sample in steady) / len(steady)
                    for i in range(3)
                )
                print(
                    f"PWM {pwm:+.0f}: delta={delta} steady_mps="
                    + str(tuple(round(value, 4) for value in average_speed))
                )
                time.sleep(0.75)
        elif args.action == "scan":
            results = {"positive": [], "negative": []}
            for direction, name in ((1, "positive"), (-1, "negative")):
                for motor in range(3):
                    threshold = tuner.scan_motor_direction(
                        motor, direction, args.start, args.stop, args.step
                    )
                    results[name].append(threshold)
            print("scan result:", results)
        elif args.action == "query":
            print(tuner.command(14, (float(GROUPS[args.group]), 0.0, 0.0)))
        elif args.action == "query-all":
            for name, group in GROUPS.items():
                print(
                    f"{name}:",
                    tuner.command(14, (float(group), 0.0, 0.0)),
                )
            print("encoder-config:", tuner.command(15))
        elif args.action == "set":
            print(tuner.command(GROUPS[args.group], tuple(args.values)))
        elif args.action == "configure-negative":
            start_values = (args.start_pwm,) * 3
            running_values = (args.running_pwm,) * 3
            print("start-neg:", tuner.command(GROUPS["start-neg"], start_values))
            print("run-neg:", tuner.command(GROUPS["run-neg"], running_values))
            print("save:", tuner.command(10))
            print(
                "verify start-neg:",
                tuner.command(14, (float(GROUPS["start-neg"]), 0.0, 0.0)),
            )
            print(
                "verify run-neg:",
                tuner.command(14, (float(GROUPS["run-neg"]), 0.0, 0.0)),
            )
        elif args.action == "configure-rotation-pid":
            values = (args.kp, args.ki, args.kd)
            print("pid-rotation:", tuner.command(GROUPS["pid-rotation"], values))
            print("save:", tuner.command(10))
            print(
                "verify pid-rotation:",
                tuner.command(14, (float(GROUPS["pid-rotation"]), 0.0, 0.0)),
            )
        elif args.action == "configure-ff-negative":
            values = (args.gain,) * 3
            print("ff-neg:", tuner.command(GROUPS["ff-neg"], values))
            print("save:", tuner.command(10))
            print(
                "verify ff-neg:",
                tuner.command(14, (float(GROUPS["ff-neg"]), 0.0, 0.0)),
            )
        elif args.action == "configure-positive":
            start_values = (args.start_pwm,) * 3
            running_values = (args.running_pwm,) * 3
            feedforward_values = (args.feedforward,) * 3
            print("start-pos:", tuner.command(GROUPS["start-pos"], start_values))
            print("run-pos:", tuner.command(GROUPS["run-pos"], running_values))
            print("ff-pos:", tuner.command(GROUPS["ff-pos"], feedforward_values))
            print("save:", tuner.command(10))
            for name in ("start-pos", "run-pos", "ff-pos"):
                print(
                    f"verify {name}:",
                    tuner.command(14, (float(GROUPS[name]), 0.0, 0.0)),
                )
        elif args.action == "configure-running":
            positive_values = (args.positive,) * 3
            negative_values = (args.negative,) * 3
            print("run-pos:", tuner.command(GROUPS["run-pos"], positive_values))
            print("run-neg:", tuner.command(GROUPS["run-neg"], negative_values))
            print("save:", tuner.command(10))
            for name in ("run-pos", "run-neg"):
                print(
                    f"verify {name}:",
                    tuner.command(14, (float(GROUPS[name]), 0.0, 0.0)),
                )
        elif args.action == "save":
            print(tuner.command(10))
        elif args.action == "load":
            print(tuner.command(11))
        elif args.action == "defaults":
            print(tuner.command(12))
        elif args.action == "reset-encoders":
            print(tuner.command(13))
        elif args.action == "encoder-config":
            print(tuner.command(15))
    except KeyboardInterrupt:
        pass
    finally:
        tuner.close()


if __name__ == "__main__":
    main()

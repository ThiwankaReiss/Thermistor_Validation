import argparse
import csv
import datetime as dt
import math
from collections import deque
from typing import Optional

import matplotlib.pyplot as plt
import serial
from serial.tools import list_ports


# Thermistor and circuit constants from user requirements
R_FIXED_OHM = 10000.0
R0_OHM = 10000.0
BETA_K = 3380.0
T0_K = 25.0 + 273.15
ADC_MAX = 1023.0
VCC = 5.0


def find_default_port() -> Optional[str]:
    ports = list(list_ports.comports())
    if not ports:
        return None
    return ports[0].device


def parse_line(line: str):
    parts = line.strip().split(',')
    if len(parts) != 4:
        return None

    if parts[0] == "tick_ms":
        return None

    try:
        tick_ms = int(parts[0])
        a0 = int(parts[1])
        a1 = int(parts[2])
        a2 = int(parts[3])
        return tick_ms, a0, a1, a2
    except ValueError:
        return None


def raw_to_resistance(raw: int) -> float:
    # Divider: 5V -> Thermistor -> ADC pin -> 10k -> GND
    # Vout = Vcc * R_fixed / (R_therm + R_fixed)
    # Rearranged using ADC ratio r = Vout/Vcc = raw/1023:
    # R_therm = R_fixed * (1/r - 1) = R_fixed * (1023 - raw)/raw
    if raw <= 0:
        return float('inf')
    if raw >= ADC_MAX:
        return 0.0
    return R_FIXED_OHM * (ADC_MAX - raw) / raw


def resistance_to_temp_c(res_ohm: float) -> float:
    if res_ohm <= 0.0 or not math.isfinite(res_ohm):
        return float('nan')

    inv_t = (1.0 / T0_K) + (1.0 / BETA_K) * math.log(res_ohm / R0_OHM)
    t_k = 1.0 / inv_t
    return t_k - 273.15


def raw_to_temp_c(raw: int) -> float:
    r = raw_to_resistance(raw)
    return resistance_to_temp_c(r)


def build_parser():
    parser = argparse.ArgumentParser(
        description="Read A0/A1/A2 from Arduino, convert to temperatures, save CSV, and plot live."
    )
    parser.add_argument("--port", type=str, default=None, help="COM port, e.g., COM5")
    parser.add_argument("--baud", type=int, default=115200, help="Serial baud rate")
    parser.add_argument("--csv", type=str, default="temperature_readings.csv", help="Output CSV file")
    parser.add_argument("--window", type=float, default=120.0, help="Plot window in seconds")
    return parser


def main():
    parser = build_parser()
    args = parser.parse_args()

    port = args.port or find_default_port()
    if not port:
        raise SystemExit("No serial ports found. Connect Arduino and pass --port COMx")

    print(f"Opening serial port: {port} @ {args.baud}")
    ser = serial.Serial(port=port, baudrate=args.baud, timeout=1)

    # Live data buffers
    t_buf = deque()
    t0_buf = deque()
    t1_buf = deque()
    t2_buf = deque()
    dt_buf = deque()

    # Set up live plots
    plt.ion()
    fig, (ax_temp, ax_delta) = plt.subplots(2, 1, figsize=(10, 8), sharex=True)

    line_t0, = ax_temp.plot([], [], label="A0 Temp (C)")
    line_t1, = ax_temp.plot([], [], label="A1 Temp (C)")
    line_t2, = ax_temp.plot([], [], label="A2 Temp (C)")
    ax_temp.set_ylabel("Temperature (C)")
    ax_temp.set_title("Thermistor Temperatures vs Time")
    ax_temp.grid(True, alpha=0.3)
    ax_temp.legend(loc="upper right")

    line_delta, = ax_delta.plot([], [], label="Delta T = A0 - A1 (C)", color="tab:red")
    ax_delta.set_xlabel("Time (s)")
    ax_delta.set_ylabel("Delta T (C)")
    ax_delta.set_title("Temperature Difference vs Time")
    ax_delta.grid(True, alpha=0.3)
    ax_delta.legend(loc="upper right")

    fig.tight_layout()

    start_tick = None

    with open(args.csv, mode="w", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        writer.writerow([
            "pc_timestamp",
            "tick_ms",
            "time_s",
            "a0_raw",
            "a1_raw",
            "a2_raw",
            "a0_temp_c",
            "a1_temp_c",
            "a2_temp_c",
            "delta_t_a0_minus_a1_c",
        ])

        print("Logging started. Press Ctrl+C to stop.")

        try:
            while True:
                raw_line = ser.readline().decode(errors="ignore").strip()
                if not raw_line:
                    plt.pause(0.001)
                    continue

                parsed = parse_line(raw_line)
                if parsed is None:
                    continue

                tick_ms, a0_raw, a1_raw, a2_raw = parsed
                if start_tick is None:
                    start_tick = tick_ms

                time_s = (tick_ms - start_tick) / 1000.0

                t0_c = raw_to_temp_c(a0_raw)
                t1_c = raw_to_temp_c(a1_raw)
                t2_c = raw_to_temp_c(a2_raw)
                delta_c = t0_c - t1_c

                # CSV log row
                writer.writerow([
                    dt.datetime.now().isoformat(timespec="milliseconds"),
                    tick_ms,
                    round(time_s, 3),
                    a0_raw,
                    a1_raw,
                    a2_raw,
                    round(t0_c, 4) if math.isfinite(t0_c) else "nan",
                    round(t1_c, 4) if math.isfinite(t1_c) else "nan",
                    round(t2_c, 4) if math.isfinite(t2_c) else "nan",
                    round(delta_c, 4) if math.isfinite(delta_c) else "nan",
                ])
                f.flush()

                # Update buffers
                t_buf.append(time_s)
                t0_buf.append(t0_c)
                t1_buf.append(t1_c)
                t2_buf.append(t2_c)
                dt_buf.append(delta_c)

                # Keep only last window seconds
                while t_buf and (time_s - t_buf[0] > args.window):
                    t_buf.popleft()
                    t0_buf.popleft()
                    t1_buf.popleft()
                    t2_buf.popleft()
                    dt_buf.popleft()

                # Update lines
                x = list(t_buf)
                line_t0.set_data(x, list(t0_buf))
                line_t1.set_data(x, list(t1_buf))
                line_t2.set_data(x, list(t2_buf))
                line_delta.set_data(x, list(dt_buf))

                # Rescale axes
                if x:
                    xmin = max(0.0, x[-1] - args.window)
                    xmax = x[-1] if x[-1] > args.window else args.window
                    ax_temp.set_xlim(xmin, xmax)
                    ax_delta.set_xlim(xmin, xmax)

                    y_temp_vals = [v for v in (list(t0_buf) + list(t1_buf) + list(t2_buf)) if math.isfinite(v)]
                    if y_temp_vals:
                        y_min = min(y_temp_vals)
                        y_max = max(y_temp_vals)
                        if y_min == y_max:
                            y_min -= 0.5
                            y_max += 0.5
                        pad = max(0.2, 0.05 * (y_max - y_min))
                        ax_temp.set_ylim(y_min - pad, y_max + pad)

                    y_delta_vals = [v for v in dt_buf if math.isfinite(v)]
                    if y_delta_vals:
                        d_min = min(y_delta_vals)
                        d_max = max(y_delta_vals)
                        if d_min == d_max:
                            d_min -= 0.2
                            d_max += 0.2
                        d_pad = max(0.1, 0.1 * (d_max - d_min))
                        ax_delta.set_ylim(d_min - d_pad, d_max + d_pad)

                plt.pause(0.001)

        except KeyboardInterrupt:
            print("Stopping logger...")
        finally:
            ser.close()
            plt.ioff()
            plt.show()
            print(f"Saved CSV: {args.csv}")


if __name__ == "__main__":
    main()

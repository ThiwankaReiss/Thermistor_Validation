import argparse
import csv
import math
from pathlib import Path
from statistics import median


CELL_LIMIT_C = 60.0
STEADY_WINDOW_S = 5.0
STEADY_SLOPE_THRESH_C_PER_S = 0.08
STEADY_STD_THRESH_C = 0.35
MIN_STEADY_DURATION_S = 15.0


def percentile(values, q):
    if not values:
        return math.nan
    sorted_vals = sorted(values)
    if len(sorted_vals) == 1:
        return sorted_vals[0]
    pos = (len(sorted_vals) - 1) * q
    lower = int(math.floor(pos))
    upper = int(math.ceil(pos))
    if lower == upper:
        return sorted_vals[lower]
    frac = pos - lower
    return sorted_vals[lower] * (1 - frac) + sorted_vals[upper] * frac


def window_std(values):
    if len(values) < 2:
        return 0.0
    mean_val = sum(values) / len(values)
    variance = sum((v - mean_val) ** 2 for v in values) / (len(values) - 1)
    return math.sqrt(variance)


def read_rows(csv_path):
    rows = []
    with csv_path.open("r", newline="", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        for r in reader:
            rows.append(
                {
                    **r,
                    "time_s": float(r["time_s"]),
                    "a0_temp_c": float(r["a0_temp_c"]),
                    "a1_temp_c": float(r["a1_temp_c"]),
                }
            )
    return rows


def find_steady_state(rows):
    times = [r["time_s"] for r in rows]
    a0 = [r["a0_temp_c"] for r in rows]
    a1 = [r["a1_temp_c"] for r in rows]
    delta = [x - y for x, y in zip(a0, a1)]

    dts = [times[i] - times[i - 1] for i in range(1, len(times)) if times[i] > times[i - 1]]
    dt = median(dts) if dts else 0.1

    window_n = max(2, int(round(STEADY_WINDOW_S / dt)))
    min_steady_n = max(window_n, int(round(MIN_STEADY_DURATION_S / dt)))

    raw_steady = [False] * len(rows)
    for i in range(window_n, len(rows)):
        dt_window = times[i] - times[i - window_n]
        if dt_window <= 0:
            continue

        slope_a0 = (a0[i] - a0[i - window_n]) / dt_window
        slope_a1 = (a1[i] - a1[i - window_n]) / dt_window
        delta_std = window_std(delta[i - window_n : i + 1])

        if (
            abs(slope_a0) <= STEADY_SLOPE_THRESH_C_PER_S
            and abs(slope_a1) <= STEADY_SLOPE_THRESH_C_PER_S
            and delta_std <= STEADY_STD_THRESH_C
        ):
            raw_steady[i] = True

    segments = []
    start = None
    for i, flag in enumerate(raw_steady):
        if flag and start is None:
            start = i
        if not flag and start is not None:
            if i - start >= min_steady_n:
                segments.append((start, i - 1))
            start = None
    if start is not None and len(rows) - start >= min_steady_n:
        segments.append((start, len(rows) - 1))

    valid_steady = [False] * len(rows)
    for s, e in segments:
        for i in range(s, e + 1):
            valid_steady[i] = True

    return {
        "delta": delta,
        "dt": dt,
        "window_n": window_n,
        "min_steady_n": min_steady_n,
        "steady_flags": valid_steady,
        "segments": segments,
    }


def label_process_step(idx, segments):
    if not segments:
        return "transient_no_steady_detected", "none", False

    for s_idx, (s, e) in enumerate(segments, start=1):
        if s <= idx <= e:
            return f"steady_state_{s_idx}", f"steady_{s_idx}", True

    if idx < segments[0][0]:
        return "warmup_transient", "warmup", False
    if idx > segments[-1][1]:
        return "post_steady_transient", "post_steady", False
    return "between_steady_transient", "transition", False


def write_outputs(rows, analysis, processed_csv, step_summary_csv, overall_summary_csv):
    delta = analysis["delta"]
    steady = analysis["steady_flags"]
    segments = analysis["segments"]

    fieldnames = list(rows[0].keys())
    if "delta_t_a0_minus_a1_c" not in fieldnames:
        fieldnames.append("delta_t_a0_minus_a1_c")
    fieldnames += [
        "process_step",
        "is_steady_state",
    ]

    with processed_csv.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        for i, r in enumerate(rows):
            process_step, _process_group, is_steady = label_process_step(i, segments)
            out = dict(r)
            out["delta_t_a0_minus_a1_c"] = round(delta[i], 4)
            out["process_step"] = process_step
            out["is_steady_state"] = int(is_steady)
            writer.writerow(out)

    step_fields = [
        "step_id",
        "start_time_s",
        "end_time_s",
        "duration_s",
        "sample_count",
        "a0_mean_c",
        "a1_mean_c",
        "offset_mean_c",
        "offset_p95_c",
        "offset_max_c",
        "derated_shutdown_from_max_c",
    ]

    all_steady_offsets = []
    with step_summary_csv.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=step_fields)
        writer.writeheader()
        for step_id, (s, e) in enumerate(segments, start=1):
            span = rows[s : e + 1]
            offsets = [delta[i] for i in range(s, e + 1)]
            all_steady_offsets.extend(offsets)
            a0_vals = [r["a0_temp_c"] for r in span]
            a1_vals = [r["a1_temp_c"] for r in span]
            offset_max = max(offsets)
            writer.writerow(
                {
                    "step_id": step_id,
                    "start_time_s": round(rows[s]["time_s"], 3),
                    "end_time_s": round(rows[e]["time_s"], 3),
                    "duration_s": round(rows[e]["time_s"] - rows[s]["time_s"], 3),
                    "sample_count": len(span),
                    "a0_mean_c": round(sum(a0_vals) / len(a0_vals), 4),
                    "a1_mean_c": round(sum(a1_vals) / len(a1_vals), 4),
                    "offset_mean_c": round(sum(offsets) / len(offsets), 4),
                    "offset_p95_c": round(percentile(offsets, 0.95), 4),
                    "offset_max_c": round(offset_max, 4),
                    "derated_shutdown_from_max_c": round(CELL_LIMIT_C - offset_max, 4),
                }
            )

    overall_fields = [
        "cell_limit_c",
        "steady_state_detected",
        "steady_state_samples",
        "steady_state_duration_s",
        "steady_offset_mean_c",
        "steady_offset_p95_c",
        "steady_offset_max_c",
        "derated_shutdown_temp_c_from_max",
        "derated_shutdown_temp_c_from_p95",
        "steady_window_s",
        "steady_slope_thresh_c_per_s",
        "steady_std_thresh_c",
        "min_steady_duration_s",
    ]

    if all_steady_offsets:
        mean_offset = sum(all_steady_offsets) / len(all_steady_offsets)
        p95_offset = percentile(all_steady_offsets, 0.95)
        max_offset = max(all_steady_offsets)
        steady_duration = len(all_steady_offsets) * analysis["dt"]
        derated_from_max = CELL_LIMIT_C - max_offset
        derated_from_p95 = CELL_LIMIT_C - p95_offset
        detected = 1
    else:
        mean_offset = math.nan
        p95_offset = math.nan
        max_offset = math.nan
        steady_duration = 0.0
        derated_from_max = math.nan
        derated_from_p95 = math.nan
        detected = 0

    with overall_summary_csv.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=overall_fields)
        writer.writeheader()
        writer.writerow(
            {
                "cell_limit_c": CELL_LIMIT_C,
                "steady_state_detected": detected,
                "steady_state_samples": int(sum(steady)),
                "steady_state_duration_s": round(steady_duration, 3),
                "steady_offset_mean_c": round(mean_offset, 4) if not math.isnan(mean_offset) else "",
                "steady_offset_p95_c": round(p95_offset, 4) if not math.isnan(p95_offset) else "",
                "steady_offset_max_c": round(max_offset, 4) if not math.isnan(max_offset) else "",
                "derated_shutdown_temp_c_from_max": round(derated_from_max, 4)
                if not math.isnan(derated_from_max)
                else "",
                "derated_shutdown_temp_c_from_p95": round(derated_from_p95, 4)
                if not math.isnan(derated_from_p95)
                else "",
                "steady_window_s": STEADY_WINDOW_S,
                "steady_slope_thresh_c_per_s": STEADY_SLOPE_THRESH_C_PER_S,
                "steady_std_thresh_c": STEADY_STD_THRESH_C,
                "min_steady_duration_s": MIN_STEADY_DURATION_S,
            }
        )

    return {
        "detected": bool(all_steady_offsets),
        "steady_samples": int(sum(steady)),
        "steady_duration_s": steady_duration,
        "offset_mean_c": mean_offset,
        "offset_p95_c": p95_offset,
        "offset_max_c": max_offset,
        "derated_from_max_c": derated_from_max,
        "derated_from_p95_c": derated_from_p95,
        "segment_count": len(segments),
    }


def main():
    parser = argparse.ArgumentParser(description="Derive AMS derated shutdown temperature from dual-sensor test CSV.")
    parser.add_argument("--input", default="temperature_readings5.csv", help="Input CSV file path")
    parser.add_argument(
        "--processed-output",
        default="temperature_readings5_processed_with_steps.csv",
        help="Output CSV with process-step columns",
    )
    parser.add_argument(
        "--step-summary-output",
        default="temperature_readings5_step_summary.csv",
        help="Output CSV with one row per detected steady-state step",
    )
    parser.add_argument(
        "--overall-summary-output",
        default="temperature_readings5_derating_summary.csv",
        help="Output CSV with final derated-temperature result",
    )
    args = parser.parse_args()

    input_csv = Path(args.input)
    processed_csv = Path(args.processed_output)
    step_summary_csv = Path(args.step_summary_output)
    overall_summary_csv = Path(args.overall_summary_output)

    rows = read_rows(input_csv)
    if not rows:
        raise RuntimeError("Input CSV has no data rows.")

    analysis = find_steady_state(rows)
    result = write_outputs(rows, analysis, processed_csv, step_summary_csv, overall_summary_csv)

    print("=== Derating Result (steady-state filtered) ===")
    print(f"Input file: {input_csv}")
    print(f"Steady-state segments detected: {result['segment_count']}")
    print(f"Steady-state samples: {result['steady_samples']}")
    print(f"Steady-state duration: {result['steady_duration_s']:.2f} s")
    if result["detected"]:
        print(f"Mean offset (a0 - a1): {result['offset_mean_c']:.4f} C")
        print(f"P95 offset (a0 - a1): {result['offset_p95_c']:.4f} C")
        print(f"Max offset (a0 - a1): {result['offset_max_c']:.4f} C")
        print(f"Derated shutdown (conservative, max): {result['derated_from_max_c']:.4f} C")
        print(f"Derated shutdown (robust, p95): {result['derated_from_p95_c']:.4f} C")
    else:
        print("No valid steady-state segment was detected. Please adjust thresholds.")

    print(f"Processed CSV: {processed_csv}")
    print(f"Step summary CSV: {step_summary_csv}")
    print(f"Overall summary CSV: {overall_summary_csv}")


if __name__ == "__main__":
    main()

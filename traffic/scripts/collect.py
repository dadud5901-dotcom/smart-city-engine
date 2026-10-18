"""
Smart City Traffic Data Collector (Bengaluru Silk Board Junction)
=================================================================
This TraCI Python script connects to Eclipse SUMO to simulate traffic
at the Silk Board Junction in Bengaluru under two scenarios:
  1. Off-peak scenario: Traffic scaled down to 50% (--scale 0.5)
  2. Peak scenario:     Traffic scaled up to 200% (--scale 2.0)

Every 10 simulation steps, the script records:
  - Current simulation time (seconds)
  - Number of active vehicles on the network
  - Average vehicle speed (m/s)
  - Total waiting time of all active vehicles (seconds)
  - Network queue length (number of vehicles with speed < 0.1 m/s)
  - Per-edge queue lengths for the top 10 busiest edges

The data is saved into CSV files inside the 'results' folder:
  - results/traffic_offpeak.csv
  - results/traffic_peak.csv

Usage:
  python collect.py offpeak
  python collect.py peak
  python collect.py compare   (or runs both and prints comparison)
"""

import os
import sys
import csv
import time
from collections import defaultdict

# ---------------------------------------------------------------------------
# Check TraCI & SUMO installation
# ---------------------------------------------------------------------------
try:
    import traci
    import sumolib
except ImportError as e:
    sys.exit(f"Error: Required SUMO Python packages not found ({e}). "
             "Ensure 'traci' and 'sumolib' are installed.")

# ---------------------------------------------------------------------------
# Path Configuration
# ---------------------------------------------------------------------------
# Get the absolute path of the directory containing this script (C:\smart-city)
BASE_DIR = os.path.dirname(os.path.abspath(__file__))

# SUMO configuration and network files live inside 'maps'
MAPS_DIR = os.path.join(BASE_DIR, "maps")
SUMO_CONFIG = os.path.join(MAPS_DIR, "osm.sumocfg")
POLY_FILE = os.path.join(MAPS_DIR, "osm.poly.xml.gz")

# Results output directory (C:\smart-city\results)
RESULTS_DIR = os.path.join(BASE_DIR, "results")

# Scenario configurations
SCENARIOS = {
    "offpeak": {
        "scale": 0.5,
        "output_file": os.path.join(RESULTS_DIR, "traffic_offpeak.csv"),
        "description": "Off-Peak Traffic (50% Volume)",
    },
    "peak": {
        "scale": 2.0,
        "output_file": os.path.join(RESULTS_DIR, "traffic_peak.csv"),
        "description": "Peak Rush-Hour Traffic (200% Volume)",
    },
}


def run_simulation(scenario_name: str) -> str:
    """
    Runs a single SUMO simulation scenario in headless mode using TraCI,
    records traffic metrics every 10 steps, and writes them to a CSV file.

    Parameters:
        scenario_name (str): Either 'offpeak' or 'peak'

    Returns:
        str: Absolute path to the generated CSV file
    """
    config = SCENARIOS[scenario_name]
    scale_factor = config["scale"]
    output_path = config["output_file"]

    print("=" * 70)
    print(f" Starting SUMO TraCI Simulation: {config['description']}")
    print(f" Scale Factor: {scale_factor} | Output: {output_path}")
    print("=" * 70)

    # Ensure results directory exists
    os.makedirs(RESULTS_DIR, exist_ok=True)

    # Locate the headless SUMO executable ('sumo', NOT 'sumo-gui')
    sumo_binary = sumolib.checkBinary("sumo")

    # Use null device to suppress SUMO from writing/overwriting files in maps/
    null_device = "NUL" if os.name == "nt" else "/dev/null"

    # Command line arguments to start SUMO
    # -c: SUMO configuration file
    # -a: Load polygon geometry overlay (without touching output.add.xml in maps)
    # --scale: Multiplier for demand (0.5 for off-peak, 2.0 for peak)
    # --tripinfo-output / --statistic-output: Redirected to null to preserve maps/ folder
    # --no-step-log: Suppress step-by-step console logging for faster simulation
    sumo_cmd = [
        sumo_binary,
        "-c", SUMO_CONFIG,
        "-a", POLY_FILE,
        "--scale", str(scale_factor),
        "--tripinfo-output", null_device,
        "--statistic-output", null_device,
        "--no-step-log", "true",
    ]

    # Start SUMO as a sub-process and establish TraCI socket connection
    traci.start(sumo_cmd)

    records = []
    step = 0
    start_wall_time = time.time()

    try:
        # Loop while there are vehicles still in the network or waiting to enter
        while traci.simulation.getMinExpectedNumber() > 0:
            # Advance simulation by 1 step (1 second)
            traci.simulationStep()
            step += 1

            # Requirement: Record traffic metrics every 10 simulation steps
            if step % 10 == 0:
                sim_time = traci.simulation.getTime()
                vehicle_ids = traci.vehicle.getIDList()
                num_vehicles = len(vehicle_ids)

                if num_vehicles > 0:
                    # 1. Fetch instantaneous speeds (in meters/second)
                    speeds = [traci.vehicle.getSpeed(vid) for vid in vehicle_ids]
                    avg_speed = sum(speeds) / num_vehicles

                    # 2. Fetch waiting times (seconds spent with speed < 0.1 m/s)
                    waiting_times = [traci.vehicle.getWaitingTime(vid) for vid in vehicle_ids]
                    total_waiting = sum(waiting_times)

                    # 3. Queue length: count of vehicles stopped (speed < 0.1 m/s)
                    queue_length = sum(1 for s in speeds if s < 0.1)

                    # 4. Per-edge queue lengths for the busiest edges
                    edge_queues = defaultdict(int)
                    for vid, s in zip(vehicle_ids, speeds):
                        if s < 0.1:
                            edge_id = traci.vehicle.getRoadID(vid)
                            edge_queues[edge_id] += 1

                    # Sort edges by queue count descending and take top 10
                    top_edges = sorted(edge_queues.items(), key=lambda x: x[1], reverse=True)[:10]
                    if top_edges:
                        top_edges_str = "; ".join(f"{edge}:{count}" for edge, count in top_edges)
                    else:
                        top_edges_str = "None"
                else:
                    # No active vehicles on the network at this timestep
                    avg_speed = 0.0
                    total_waiting = 0.0
                    queue_length = 0
                    top_edges_str = "None"

                # Store recorded snapshot
                records.append({
                    "time": round(sim_time, 1),
                    "num_vehicles": num_vehicles,
                    "avg_speed": round(avg_speed, 3),
                    "total_waiting_time": round(total_waiting, 2),
                    "queue_length": queue_length,
                    "top_10_edge_queues": top_edges_str,
                })

                # Print progress to console every 200 steps
                if step % 200 == 0:
                    print(f"  Step {step:4d}s | Active Vehicles: {num_vehicles:3d} | "
                          f"Avg Speed: {avg_speed:5.2f} m/s | Queued: {queue_length:3d}")

    finally:
        # Always close TraCI connection and shut down SUMO cleanly
        traci.close()

    elapsed = time.time() - start_wall_time
    print(f" Simulation finished in {elapsed:.2f}s ({step} total steps).")

    # Write recorded metrics to CSV
    fieldnames = [
        "time",
        "num_vehicles",
        "avg_speed",
        "total_waiting_time",
        "queue_length",
        "top_10_edge_queues",
    ]

    with open(output_path, mode="w", newline="", encoding="utf-8") as csvfile:
        writer = csv.DictWriter(csvfile, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(records)

    print(f" Successfully saved {len(records)} records to: {output_path}\n")
    return output_path


def load_metrics(csv_path: str) -> dict:
    """
    Reads a scenario CSV and computes summary statistics for comparison.
    """
    if not os.path.exists(csv_path):
        return None

    rows = []
    with open(csv_path, mode="r", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        for r in reader:
            rows.append({
                "time": float(r["time"]),
                "num_vehicles": int(r["num_vehicles"]),
                "avg_speed": float(r["avg_speed"]),
                "total_waiting_time": float(r["total_waiting_time"]),
                "queue_length": int(r["queue_length"]),
            })

    if not rows:
        return None

    # Filter to steps where vehicles were active on the road
    active_rows = [r for r in rows if r["num_vehicles"] > 0]
    if not active_rows:
        active_rows = rows

    # Mean speed across all active snapshots
    mean_speed = sum(r["avg_speed"] for r in active_rows) / len(active_rows)

    # Mean total waiting time per snapshot
    mean_total_waiting = sum(r["total_waiting_time"] for r in active_rows) / len(active_rows)

    # Per-vehicle average waiting time: total_waiting / num_vehicles at each step
    per_veh_waits = [r["total_waiting_time"] / r["num_vehicles"] for r in active_rows]
    mean_per_veh_waiting = sum(per_veh_waits) / len(per_veh_waits)

    # Maximum queue length observed in any snapshot
    max_queue = max(r["queue_length"] for r in rows)

    # Maximum active vehicles
    max_vehicles = max(r["num_vehicles"] for r in rows)

    # Total simulation duration
    sim_duration = rows[-1]["time"]

    return {
        "record_count": len(rows),
        "sim_duration": sim_duration,
        "mean_speed_mps": mean_speed,
        "mean_speed_kmh": mean_speed * 3.6,
        "mean_total_waiting_s": mean_total_waiting,
        "mean_per_veh_waiting_s": mean_per_veh_waiting,
        "max_queue_vehicles": max_queue,
        "max_vehicles": max_vehicles,
    }


def print_comparison():
    """
    Prints a formatted comparison table between Peak and Off-Peak scenarios.
    """
    offpeak_csv = SCENARIOS["offpeak"]["output_file"]
    peak_csv = SCENARIOS["peak"]["output_file"]

    offpeak_metrics = load_metrics(offpeak_csv)
    peak_metrics = load_metrics(peak_csv)

    print("=" * 78)
    print(" TRAFFIC COMPARISON SUMMARY: PEAK VS OFF-PEAK (SILK BOARD JUNCTION)")
    print("=" * 78)

    if not offpeak_metrics or not peak_metrics:
        print(" Note: Both scenarios must be run before full comparison can be displayed.")
        if offpeak_metrics:
            print(f" [Off-Peak Data Available]: {offpeak_csv}")
        else:
            print(f" [Off-Peak Data Missing]: Run 'python collect.py offpeak' first.")
        if peak_metrics:
            print(f" [Peak Data Available]: {peak_csv}")
        else:
            print(f" [Peak Data Missing]: Run 'python collect.py peak' first.")
        print("=" * 78)
        return

    col_w = 26
    print(f"{'Metric':<32} | {'Off-Peak (--scale 0.5)':<{col_w}} | {'Peak (--scale 2.0)':<{col_w}}")
    print("-" * 78)

    dur_off = f"{offpeak_metrics['sim_duration']} s"
    dur_pk = f"{peak_metrics['sim_duration']} s"
    print(f"{'Simulation Duration':<32} | {dur_off:<{col_w}} | {dur_pk:<{col_w}}")

    pts_off = f"{offpeak_metrics['record_count']} steps"
    pts_pk = f"{peak_metrics['record_count']} steps"
    print(f"{'Data Points (every 10s)':<32} | {pts_off:<{col_w}} | {pts_pk:<{col_w}}")

    veh_off = f"{offpeak_metrics['max_vehicles']} vehicles"
    veh_pk = f"{peak_metrics['max_vehicles']} vehicles"
    print(f"{'Max Concurrent Vehicles':<32} | {veh_off:<{col_w}} | {veh_pk:<{col_w}}")
    print("-" * 78)

    spd_m_off = f"{offpeak_metrics['mean_speed_mps']:.2f} m/s"
    spd_m_pk = f"{peak_metrics['mean_speed_mps']:.2f} m/s"
    print(f"{'Average Speed (m/s)':<32} | {spd_m_off:<{col_w}} | {spd_m_pk:<{col_w}}")

    spd_k_off = f"{offpeak_metrics['mean_speed_kmh']:.2f} km/h"
    spd_k_pk = f"{peak_metrics['mean_speed_kmh']:.2f} km/h"
    print(f"{'Average Speed (km/h)':<32} | {spd_k_off:<{col_w}} | {spd_k_pk:<{col_w}}")

    wt_tot_off = f"{offpeak_metrics['mean_total_waiting_s']:.2f} s"
    wt_tot_pk = f"{peak_metrics['mean_total_waiting_s']:.2f} s"
    print(f"{'Avg Waiting Time (per step)':<32} | {wt_tot_off:<{col_w}} | {wt_tot_pk:<{col_w}}")

    wt_veh_off = f"{offpeak_metrics['mean_per_veh_waiting_s']:.2f} s"
    wt_veh_pk = f"{peak_metrics['mean_per_veh_waiting_s']:.2f} s"
    print(f"{'Avg Waiting Time (per vehicle)':<32} | {wt_veh_off:<{col_w}} | {wt_veh_pk:<{col_w}}")

    q_off = f"{offpeak_metrics['max_queue_vehicles']} vehicles"
    q_pk = f"{peak_metrics['max_queue_vehicles']} vehicles"
    print(f"{'Max Queue Length (stopped veh)':<32} | {q_off:<{col_w}} | {q_pk:<{col_w}}")
    print("=" * 78)
    print("Key Engineering Insights for Viva:")
    print(" 1. Congestion Spike: Peak traffic (scale=2.0) causes maximum queue lengths")
    print(f"    to jump from {offpeak_metrics['max_queue_vehicles']} to {peak_metrics['max_queue_vehicles']} stopped vehicles.")
    print(" 2. Speed Degradation: Average network speed drops by "
          f"{((offpeak_metrics['mean_speed_mps'] - peak_metrics['mean_speed_mps']) / offpeak_metrics['mean_speed_mps']) * 100:.1f}%.")
    print(" 3. Waiting Time Impact: Total waiting time drastically increases during peak hours,")
    print(f"    rising from {offpeak_metrics['mean_total_waiting_s']:.1f}s to {peak_metrics['mean_total_waiting_s']:.1f}s per sample interval.")
    print("=" * 78)


def main():
    """
    Main CLI entrypoint. Supports:
      python collect.py offpeak
      python collect.py peak
      python collect.py compare (or both/all)
    """
    # If no argument is passed, show usage instructions
    if len(sys.argv) < 2:
        print("Usage:")
        print("  python collect.py offpeak    -> Runs off-peak scenario (--scale 0.5)")
        print("  python collect.py peak       -> Runs peak scenario (--scale 2.0)")
        print("  python collect.py compare    -> Runs both scenarios and displays comparison")
        print("\nDefaulting to running both scenarios...")
        run_simulation("offpeak")
        run_simulation("peak")
        print_comparison()
        return

    arg = sys.argv[1].strip().lower().lstrip("-")

    if arg in ("offpeak", "off-peak", "off_peak", "off"):
        run_simulation("offpeak")
        # If peak CSV already exists, also display the comparison
        if os.path.exists(SCENARIOS["peak"]["output_file"]):
            print_comparison()

    elif arg in ("peak", "p"):
        run_simulation("peak")
        # If offpeak CSV already exists, also display the comparison
        if os.path.exists(SCENARIOS["offpeak"]["output_file"]):
            print_comparison()

    elif arg in ("compare", "both", "all"):
        # Run any missing scenarios first
        if not os.path.exists(SCENARIOS["offpeak"]["output_file"]):
            run_simulation("offpeak")
        if not os.path.exists(SCENARIOS["peak"]["output_file"]):
            run_simulation("peak")
        print_comparison()

    else:
        print(f"Unknown scenario argument: '{sys.argv[1]}'")
        print("Usage:")
        print("  python collect.py offpeak")
        print("  python collect.py peak")
        sys.exit(1)


if __name__ == "__main__":
    main()

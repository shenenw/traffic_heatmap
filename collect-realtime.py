#!/usr/bin/env python3
# File: collect-realtime.py (cron + email)
# Description: Collects one bus position sample, maintains a cyclic JSON buffer,
#              generates an HTML heatmap (index.html), and emails it.
#              Designed to be run hourly from cron.

import os
import json
import time
import smtplib
import requests
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from email.mime.base import MIMEBase
from email import encoders
from datetime import datetime
from google.transit import gtfs_realtime_pb2
from collections import OrderedDict
import folium
from folium.plugins import HeatMapWithTime

# ---------- CONFIGURATION ----------
DATA_LOG_FILE = "bus_realtime_history.json"
OUTPUT_HTML_FILE = "index.html"

TOTAL_SAMPLES = 24            # Keep last 24 hourly snapshots (1 day)
SUB_SAMPLE_INTERVAL = 10      # Seconds between two API fetches (for Delta‑T)
MAX_ACCELERATION = 1.8        # Max plausible acceleration (m/s²)

# Email settings – set these as environment variables (or replace with hardcoded values)
EMAIL_FROM = os.environ.get("EMAIL_FROM")           # e.g., "your@gmail.com"
EMAIL_TO = os.environ.get("EMAIL_TO")               # e.g., "you@example.com"
EMAIL_PASSWORD = os.environ.get("EMAIL_PASSWORD")   # App password or SMTP password
SMTP_SERVER = os.environ.get("SMTP_SERVER", "smtp.gmail.com")
SMTP_PORT = int(os.environ.get("SMTP_PORT", 587))

# -----------------------------------

def fetch_live_bus_positions():
    url = "http://gtfs.edmonton.ca/TMGTFSRealTimeWebService/Vehicle/VehiclePositions.pb"
    try:
        response = requests.get(url, timeout=8)
        response.raise_for_status()
        feed = gtfs_realtime_pb2.FeedMessage()
        feed.ParseFromString(response.content)
        return feed.entity
    except Exception as e:
        print(f"[{datetime.now().strftime('%H:%M:%S')}] API Fetch Error: {e}")
        return None

def extract_base_speeds(entities):
    speeds = {}
    if not entities:
        return speeds
    for entity in entities:
        if not entity.HasField('vehicle') or not entity.vehicle.HasField('position'):
            continue
        veh = entity.vehicle
        vehicle_id = veh.vehicle.id if veh.HasField('vehicle') and veh.vehicle.HasField('id') else None
        if vehicle_id:
            speeds[vehicle_id] = veh.position.speed if veh.position.HasField('speed') else 0.0
    return speeds

def parse_and_filter_snapshot(entities, previous_speeds):
    valid_points = []
    if not entities:
        return valid_points

    for entity in entities:
        if not entity.HasField('vehicle') or not entity.vehicle.HasField('position'):
            continue
            
        veh = entity.vehicle
        pos = veh.position
        vehicle_id = veh.vehicle.id if veh.HasField('vehicle') and veh.vehicle.HasField('id') else None
        
        if not vehicle_id:
            continue

        speed_ms = pos.speed if pos.HasField('speed') else 0.0
        speed_kmh = speed_ms * 3.6
        lat = pos.latitude
        lon = pos.longitude
        
        # Edmonton Boundary Speed Clamps
        is_on_freeway = (53.45 < lat < 53.49) or (53.57 < lat < 53.62) or (lon < -113.62) or (lon > -113.38)
        if is_on_freeway:
            if speed_kmh > 90.0 or speed_kmh < 7.0:
                continue
        else:
            if speed_kmh > 70.0 or speed_kmh < 7.0:
                continue

        # Delta-T Check
        if previous_speeds and vehicle_id in previous_speeds:
            prev_speed_ms = previous_speeds[vehicle_id]
            if (abs(speed_ms - prev_speed_ms) / SUB_SAMPLE_INTERVAL) > MAX_ACCELERATION:
                continue

        intensity = 1.0 if speed_kmh < 20 else (0.6 if speed_kmh < 40 else 0.2)
        
        valid_points.append({
            "lat": lat, 
            "lon": lon, 
            "weight": intensity, 
            "speed_kmh": round(speed_kmh, 1)
        })
        
    return valid_points

def load_existing_history():
    if not os.path.exists(DATA_LOG_FILE):
        return OrderedDict()
    try:
        with open(DATA_LOG_FILE, "r") as f:
            data = json.load(f)
        return OrderedDict(sorted(data.items()))
    except (json.JSONDecodeError, IOError):
        print(f"Warning: Could not read {DATA_LOG_FILE}, starting fresh.")
        return OrderedDict()

def save_history(history):
    with open(DATA_LOG_FILE, "w") as f:
        json.dump(dict(history), f, indent=2)

def generate_heatmap(history, output_file):
    """Creates an interactive map with a time slider using the collected snapshots."""
    if not history:
        print("No data to generate heatmap.")
        return False

    # Center on Edmonton
    center_lat, center_lon = 53.5461, -113.4938
    m = folium.Map(location=[center_lat, center_lon], zoom_start=12)

    # Prepare data for HeatMapWithTime: list of lists of [lat, lon, weight]
    # Also extract timestamps for the slider labels
    time_data = []
    time_index = []
    for timestamp, points in history.items():
        if points:
            # Each point: [lat, lon, weight]
            heat_points = [[p["lat"], p["lon"], p["weight"]] for p in points]
            time_data.append(heat_points)
            time_index.append(timestamp)
        else:
            # Empty snapshot – still need an entry to keep indices aligned
            time_data.append([])
            time_index.append(timestamp)

    if time_data:
        HeatMapWithTime(time_data, index=time_index, auto_play=False, radius=10).add_to(m)

    m.save(output_file)
    print(f"Heatmap saved to {output_file}")
    return True

def send_email_with_attachment(attachment_path):
    """Send an email with the HTML file attached."""
    if not all([EMAIL_FROM, EMAIL_TO, EMAIL_PASSWORD, SMTP_SERVER]):
        print("Email credentials not set. Skipping email.")
        return False

    subject = f"Bus Data Snapshot - {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}"
    body = f"Attached is the latest bus heatmap (hourly snapshot).\nBuffer contains up to {TOTAL_SAMPLES} snapshots."

    msg = MIMEMultipart()
    msg['From'] = EMAIL_FROM
    msg['To'] = EMAIL_TO
    msg['Subject'] = subject
    msg.attach(MIMEText(body, 'plain'))

    # Attach the HTML file
    try:
        with open(attachment_path, "rb") as attachment:
            part = MIMEBase('application', 'octet-stream')
            part.set_payload(attachment.read())
            encoders.encode_base64(part)
            part.add_header('Content-Disposition', f'attachment; filename={os.path.basename(attachment_path)}')
            msg.attach(part)
    except Exception as e:
        print(f"Failed to attach file: {e}")
        return False

    # Send email
    try:
        with smtplib.SMTP(SMTP_SERVER, SMTP_PORT) as server:
            server.starttls()
            server.login(EMAIL_FROM, EMAIL_PASSWORD)
            server.send_message(msg)
        print(f"Email sent to {EMAIL_TO}")
        return True
    except Exception as e:
        print(f"Email sending failed: {e}")
        return False

def collect_one_sample():
    """Perform one complete sample (two fetches + Delta‑T filtering)."""
    entities_a = fetch_live_bus_positions()
    if entities_a is None:
        print("First fetch failed, aborting sample.")
        return None, None
    
    base_speeds = extract_base_speeds(entities_a)
    time.sleep(SUB_SAMPLE_INTERVAL)
    
    entities_b = fetch_live_bus_positions()
    if entities_b is None:
        print("Second fetch failed, aborting sample.")
        return None, None
    
    clean_points = parse_and_filter_snapshot(entities_b, base_speeds)
    timestamp_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    return timestamp_str, clean_points

def main():
    print(f"[{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}] Starting collection...")

    # Load existing buffer
    history = load_existing_history()
    print(f"Loaded {len(history)} existing snapshots (max {TOTAL_SAMPLES}).")

    # Collect one new sample
    ts, points = collect_one_sample()
    if ts is None:
        print("Sample collection failed. Exiting without changes.")
        return 1

    print(f"Collected {len(points)} valid bus positions at {ts}")

    # Append and maintain cyclic buffer
    history[ts] = points
    while len(history) > TOTAL_SAMPLES:
        oldest = next(iter(history))
        del history[oldest]
        print(f"Removed oldest snapshot: {oldest}")

    # Save JSON
    save_history(history)
    print(f"Saved JSON. Buffer now has {len(history)} snapshots.")

    # Generate HTML heatmap
    if generate_heatmap(history, OUTPUT_HTML_FILE):
        # Send email with attachment
        send_email_with_attachment(OUTPUT_HTML_FILE)
    else:
        print("Heatmap generation failed, skipping email.")

    return 0

if __name__ == "__main__":
    exit(main())

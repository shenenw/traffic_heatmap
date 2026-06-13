#!/usr/bin/env python3
# File: collect-realtime.py
# Date: 2026-06-12
# Description: Collects bus position samples, maintains a cyclic JSON buffer,
#              generates an HTML heatmap (index.html) with a time slider and
#              a bulletproof floating speed color legend, and emails the live link.
#              Optimized for low browser processing.

import os
import json
import time
import smtplib
import requests
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
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

        # Low Processing Processing: Convert raw speed directly into categorical weights
        # Slow is highest weight (1.0) for red visibility, Fast is lowest weight (0.2) for blue
        intensity = 1.0 if speed_kmh < 20 else (0.6 if speed_kmh < 40 else 0.2)
        
        valid_points.append({
            "lat": lat, 
            "lon": lon, 
            "weight": intensity
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
    """Generates an optimized time-sliding heatmap relying strictly on gradient color mapping."""
    if not history:
        print("No data to generate heatmap.")
        return False

    # Center on Edmonton
    center_lat, center_lon = 53.5461, -113.4938
    m = folium.Map(location=[center_lat, center_lon], zoom_start=12)

    # Format historical frames for HeatMapWithTime
    time_data = []
    time_index = []
    for timestamp, points in history.items():
        if points:
            # Drop speed values entirely to minimize output HTML weight
            heat_points = [[p["lat"], p["lon"], p["weight"]] for p in points]
            time_data.append(heat_points)
            time_index.append(timestamp)
        else:
            time_data.append([])
            time_index.append(timestamp)

    if time_data:
        # Maps weights exactly to colors: 0.2->Blue (>40km/h), 0.6->Lime (20-40km/h), 1.0->Red (<20km/h)
        speed_range_gradient = {0.2: 'blue', 0.6: 'lime', 1.0: 'red'}
        
        HeatMapWithTime(
            time_data, 
            index=time_index, 
            auto_play=False, 
            radius=12,
            gradient=speed_range_gradient,
            min_opacity=0.4,
            max_opacity=0.85
        ).add_to(m)

    # Inject the floating legend directly into the HTML root body
    legend_html = '''
    <div style="
        position: fixed; 
        bottom: 50px; 
        left: 50px; 
        width: 150px; 
        height: 110px; 
        background-color: rgba(255, 255, 255, 0.9); 
        border: 2px solid #999; 
        z-index: 9999; 
        font-size: 12px;
        font-family: Arial, sans-serif;
        padding: 10px;
        border-radius: 5px;
        box-shadow: 2px 2px 5px rgba(0,0,0,0.2);
        pointer-events: none;
        ">
        <b style="display: block; margin-bottom: 5px;">Bus Speed Range</b>
        <div style="margin-bottom: 3px;">
            <span style="background: red; width: 20px; height: 12px; display: inline-block; vertical-align: middle; margin-right: 5px; border-radius: 2px;"></span>
            &lt; 20 km/h (Slow)
        </div>
        <div style="margin-bottom: 3px;">
            <span style="background: lime; width: 20px; height: 12px; display: inline-block; vertical-align: middle; margin-right: 5px; border-radius: 2px;"></span>
            20 - 40 km/h
        </div>
        <div>
            <span style="background: blue; width: 20px; height: 12px; display: inline-block; vertical-align: middle; margin-right: 5px; border-radius: 2px;"></span>
            &gt; 40 km/h (Fast)
        </div>
    </div>
    '''
    m.get_root().html.add_child(folium.Element(legend_html))

    m.save(output_file)
    print(f"Low-overhead heatmap saved to {output_file}")
    return True

def send_email_with_link():
    """Send an hourly email notification containing the live link instead of an attachment."""
    if not all([EMAIL_FROM, EMAIL_TO, EMAIL_PASSWORD, SMTP_SERVER]):
        print("Email credentials not set. Skipping email.")
        return False

    cloudflare_url = "https://traffic-heatmap.shenenwang.workers.dev/"
    subject = f"Bus Traffic Heatmap Updated - {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}"
    
    body = f"""Hello,

The latest bus traffic data snapshot has been successfully collected and compiled.

You can view the real-time interactive heatmap on your Cloudflare dashboard here:
{cloudflare_url}

The rolling tracking buffer currently retains history for up to the last {TOTAL_SAMPLES} hours."""

    msg = MIMEMultipart()
    msg['From'] = EMAIL_FROM
    msg['To'] = EMAIL_TO
    msg['Subject'] = subject
    msg.attach(MIMEText(body, 'plain'))

    # Send email via SMTP
    try:
        with smtplib.SMTP(SMTP_SERVER, SMTP_PORT) as server:
            server.starttls()
            server.login(EMAIL_FROM, EMAIL_PASSWORD)
            server.send_message(msg)
        print(f"Notification email sent to {EMAIL_TO}")
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

    # Save JSON history
    save_history(history)
    print(f"Saved JSON. Buffer now has {len(history)} snapshots.")

    # Generate HTML heatmap locally for Git to track
    if generate_heatmap(history, OUTPUT_HTML_FILE):
        # Send clean email link notification
        send_email_with_link()
    else:
        print("Heatmap generation failed, skipping email notification.")

    return 0

if __name__ == "__main__":
    exit(main())

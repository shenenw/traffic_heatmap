#!/usr/bin/env python3
# File: collect-realtime.py
# Date: 2026-06-12
# Description: Collects bus position samples, maintains a cyclic JSON buffer,
#              generates an HTML heatmap (index.html) with a time slider,
#              a floating speed color legend, a scaled-up looping player bar,
#              and emails the live link. Optimized for low browser processing.

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
EMAIL_FROM = os.environ.get("EMAIL_FROM")           
EMAIL_TO = os.environ.get("EMAIL_TO")               
EMAIL_PASSWORD = os.environ.get("EMAIL_PASSWORD")   
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
    """Generates an optimized heatmap with a scaled UI and auto-looping JavaScript injected."""
    if not history:
        print("No data to generate heatmap.")
        return False

    # Center on Edmonton
    center_lat, center_lon = 53.5461, -113.4938
    m = folium.Map(location=[center_lat, center_lon], zoom_start=12)

    time_data = []
    time_index = []
    for timestamp, points in history.items():
        if points:
            heat_points = [[p["lat"], p["lon"], p["weight"]] for p in points]
            time_data.append(heat_points)
            time_index.append(timestamp)
        else:
            time_data.append([])
            time_index.append(timestamp)

    if time_data:
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

    # 1. Legend Box (remains the same)
    legend_html = '''
    <div style="position: fixed; bottom: 120px; left: 30px; width: 150px; height: 110px; background-color: rgba(255, 255, 255, 0.9); border: 2px solid #999; z-index: 9999; font-size: 14px; font-family: Arial, sans-serif; padding: 10px; border-radius: 5px; box-shadow: 2px 2px 5px rgba(0,0,0,0.2); pointer-events: none;">
        <b style="display: block; margin-bottom: 5px;">Bus Speed Range</b>
        <div style="margin-bottom: 3px;"><span style="background: red; width: 20px; height: 12px; display: inline-block; vertical-align: middle; margin-right: 5px; border-radius: 2px;"></span> &lt; 20 km/h</div>
        <div style="margin-bottom: 3px;"><span style="background: lime; width: 20px; height: 12px; display: inline-block; vertical-align: middle; margin-right: 5px; border-radius: 2px;"></span> 20 - 40 km/h</div>
        <div><span style="background: blue; width: 20px; height: 12px; display: inline-block; vertical-align: middle; margin-right: 5px; border-radius: 2px;"></span> &gt; 40 km/h</div>
    </div>
    '''
    
    # 2. UI Scaler and Force-Loop Script
    # Increased font sizes and force-set looping on load
    custom_ui_html = '''
    <style>
        .leaflet-control-timecontrol { transform: scale(1.4); transform-origin: bottom left; margin-bottom: 40px !important; margin-left: 30px !important; }
        .timecontrol-date, .timecontrol-speed, .leaflet-bar-timecontrol a { font-size: 14px !important; font-weight: bold !important; color: #000 !important; }
    </style>
    <script>
        window.addEventListener('load', function() {
            setTimeout(function() {
                // Find all time dimension players and force loop
                var players = document.querySelectorAll('.timecontrol-loop');
                if (players.length > 0) {
                    players.forEach(function(p) { p.click(); });
                }
            }, 1000);
        });
    </script>
    '''
    
    m.get_root().html.add_child(folium.Element(legend_html))
    m.get_root().html.add_child(folium.Element(custom_ui_html))

    m.save(output_file)
    return True

def send_email_with_link():
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
    entities_a = fetch_live_bus_positions()
    if entities_a is None:
        return None, None
    
    base_speeds = extract_base_speeds(entities_a)
    time.sleep(SUB_SAMPLE_INTERVAL)
    
    entities_b = fetch_live_bus_positions()
    if entities_b is None:
        return None, None
    
    clean_points = parse_and_filter_snapshot(entities_b, base_speeds)
    timestamp_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    return timestamp_str, clean_points
    
def fix_heatmap_ui(html_file):
    """Post-processes the Folium HTML to fix the control bar size and labels."""
    try:
        with open(html_file, 'r', encoding='utf-8') as f:
            html = f.read()
            
        # 1. Change the confusing 'fps' label to 'x Speed'
        html = html.replace(" + 'fps'", " + 'x Speed'")
        html = html.replace(" + \"fps\"", " + \"x Speed\"")
        
        # 2. Inject custom CSS to fix the massive empty area
        custom_css = """
        <style>
            /* Compact the HeatMapWithTime control bar */
            .leaflet-control.timecontrol {
                background-color: rgba(255, 255, 255, 0.9) !important;
                padding: 10px 20px !important;
                border-radius: 8px !important;
                box-shadow: 0 1px 5px rgba(0,0,0,0.4) !important;
                max-width: 450px !important; /* Constrains the width to remove empty space */
                margin-left: auto !important;
                margin-right: auto !important;
                margin-bottom: 20px !important;
            }
            .timecontrol svg {
                max-height: 45px !important; /* Prevents vertical stretching */
            }
        </style>
        """
        html = html.replace("</head>", f"{custom_css}\n</head>")
        
        with open(html_file, 'w', encoding='utf-8') as f:
            f.write(html)
            
    except Exception as e:
        print(f"Could not apply UI fixes: {e}")
        
def main():
    print(f"[{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}] Starting collection...")

    history = load_existing_history()
    ts, points = collect_one_sample()
    if ts is None:
        return 1

    history[ts] = points
    while len(history) > TOTAL_SAMPLES:
        oldest = next(iter(history))
        del history[oldest]

    save_history(history)
    
    if generate_heatmap(history, OUTPUT_HTML_FILE):
        # Apply the UI fixes here!
        fix_heatmap_ui(OUTPUT_HTML_FILE)
        send_email_with_link()

    return 0

if __name__ == "__main__":
    exit(main())

#!/usr/bin/env python3
# File: collect-realtime.py
# Date: 2026-06-13
# Author: Gemini
# Description: Collects real-time bus positions, computes speed-based discrete colors,
#              maintains a cyclic history, and generates a time-slid marker map (index.html).
#              Features auto-migration for old 'weight' formats and a highly resilient,
#              non-wrapping responsive mobile control bar layout.

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
from folium.plugins import TimestampedGeoJson

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

        # Delta-T Acceleration Check
        if previous_speeds and vehicle_id in previous_speeds:
            prev_speed_ms = previous_speeds[vehicle_id]
            if (abs(speed_ms - prev_speed_ms) / SUB_SAMPLE_INTERVAL) > MAX_ACCELERATION:
                continue

        # Map speed thresholds explicitly to hex colors
        if speed_kmh < 20.0:
            color_hex = "#FF0000"  # Red
        elif speed_kmh < 40.0:
            color_hex = "#00FF00"  # Lime
        else:
            color_hex = "#0000FF"  # Blue
        
        valid_points.append({
            "lat": lat, 
            "lon": lon, 
            "color": color_hex
        })
        
    return valid_points

def load_existing_history():
    if not os.path.exists(DATA_LOG_FILE):
        return OrderedDict()
    try:
        with open(DATA_LOG_FILE, "r") as f:
            data = json.load(f)
        
        sorted_data = OrderedDict(sorted(data.items()))
        
        # On-the-fly migration for old history entries containing 'weight' logs
        for timestamp, points in sorted_data.items():
            migrated_points = []
            for p in points:
                if "color" not in p and "weight" in p:
                    if p["weight"] == 1.0:
                        p["color"] = "#FF0000"
                    elif p["weight"] == 0.6:
                        p["color"] = "#00FF00"
                    else:
                        p["color"] = "#0000FF"
                migrated_points.append(p)
            sorted_data[timestamp] = migrated_points
            
        return sorted_data
    except (json.JSONDecodeError, IOError):
        print(f"Warning: Could not read {DATA_LOG_FILE}, starting fresh.")
        return OrderedDict()

def save_history(history):
    with open(DATA_LOG_FILE, "w") as f:
        json.dump(dict(history), f, indent=2)

def generate_heatmap(history, output_file):
    if not history:
        print("No historical coordinates available to plot.")
        return False

    center_lat, center_lon = 53.5461, -113.4938
    m = folium.Map(location=[center_lat, center_lon], zoom_start=12)

    features = []
    for timestamp, points in history.items():
        formatted_time = timestamp.replace(" ", "T")
        for p in points:
            features.append({
                'type': 'Feature',
                'geometry': {
                    'type': 'Point',
                    'coordinates': [p['lon'], p['lat']]
                },
                'properties': {
                    'time': formatted_time,
                    'style': {
                        'color': p['color'],
                        'fillColor': p['color'],
                        'fillOpacity': 0.85,
                        'radius': 5,
                        'weight': 1,
                        'clickable': True
                    },
                    'icon': 'circle'
                }
            })

    feature_collection = {
        'type': 'FeatureCollection',
        'features': features
    }

    # Use TimestampedGeoJson with default 1fps (1000ms transition) and auto-play
    TimestampedGeoJson(
        feature_collection,
        period='PT1H',
        duration='PT1H',
        add_last_point=True,
        auto_play=True,
        loop=True,
        max_speed=5,
        loop_button=True,
        date_options='YYYY-MM-DD HH:mm:ss',
        time_slider_drag_update=True,
        transition_time=1000 # 1000ms = 1 frame per second
    ).add_to(m)

    # Flexbox-aligned Speed range Legend Box layout
    legend_html = '''
    <div class="custom-speed-legend" style="position: fixed; bottom: 155px; left: 12px; width: 140px; background-color: rgba(255, 255, 255, 0.95); border: 1px solid #bbb; z-index: 9999; font-size: 13px; font-family: -apple-system, BlinkMacSystemFont, Arial, sans-serif; padding: 10px; border-radius: 6px; box-shadow: 0 2px 6px rgba(0,0,0,0.2); pointer-events: none; box-sizing: border-box;">
        <b style="display: block; margin-bottom: 6px; font-size: 13px; color: #222;">Bus Speed</b>
        <div style="display: flex; align-items: center; margin-bottom: 5px; line-height: 1;">
            <span style="background: #FF0000; width: 16px; height: 10px; display: block; border-radius: 2px; margin-right: 6px; flex-shrink: 0;"></span>
            <span style="display: inline-block; line-height: 1;">&lt; 20 km/h</span>
        </div>
        <div style="display: flex; align-items: center; margin-bottom: 5px; line-height: 1;">
            <span style="background: #00FF00; width: 16px; height: 10px; display: block; border-radius: 2px; margin-right: 6px; flex-shrink: 0;"></span>
            <span style="display: inline-block; line-height: 1;">20 - 40 km/h</span>
        </div>
        <div style="display: flex; align-items: center; line-height: 1;">
            <span style="background: #0000FF; width: 16px; height: 10px; display: block; border-radius: 2px; margin-right: 6px; flex-shrink: 0;"></span>
            <span style="display: inline-block; line-height: 1;">&gt; 40 km/h</span>
        </div>
    </div>
    '''
    
    # Execution safety fallback script to assert play state and loop state
    custom_ui_html = '''
    <script>
        window.addEventListener('load', function() {
            setTimeout(function() {
                var loopBtn = document.querySelector('.timecontrol-loop');
                if (loopBtn && !loopBtn.classList.contains('active')) {
                    loopBtn.click();
                }
                var playBtn = document.querySelector('.timecontrol-play');
                if (playBtn && !playBtn.classList.contains('pause')) {
                    playBtn.click();
                }
            }, 1200);
        });
    </script>
    '''
    
    m.get_root().html.add_child(folium.Element(legend_html))
    m.get_root().html.add_child(folium.Element(custom_ui_html))

    m.save(output_file)
    return True

def send_email_with_link():
    if not all([EMAIL_FROM, EMAIL_TO, EMAIL_PASSWORD, SMTP_SERVER]):
        print("Email configurations omitted. Skipping transfer.")
        return False

    cloudflare_url = "https://traffic-heatmap.shenenwang.workers.dev/"
    subject = f"Bus Traffic Dashboard Updated - {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}"
    
    body = f"""Hello,

The latest real-time discrete bus fleet markers have been processed.

You can view the interactive map showing exact vehicle locations and speed coloring here:
{cloudflare_url}

Rolling telemetry tracks the last {TOTAL_SAMPLES} active sampling frames."""

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
        print(f"Notification updates dispatched to {EMAIL_TO}")
        return True
    except Exception as e:
        print(f"SMTP Notification failure: {e}")
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
    try:
        with open(html_file, "r", encoding="utf-8") as f:
            html = f.read()

        html = html.replace(" + 'fps'", " + ' x Speed'")
        html = html.replace(" + \"fps\"", " + \" x Speed\"")

        # Refactored CSS specifically targeted at zero inner borders, unified background, perfect date centering and scaled layout
        custom_css = """
        <style>
            :root {
                --ui-font-size: 18px;       /* Scaled up 15% from 16px */
                --ctrl-height: 54px;        /* Scaled up 15% from 46px */
            }

            /* Reset all inner elements to transparent background, remove borders, radii, shadows, and margins */
            .leaflet-control.timecontrol * {
                background-color: transparent !important;
                border: none !important;
                border-radius: 0 !important;
                box-shadow: none !important;
                margin: 0 !important;
                padding: 0 !important;
                box-sizing: border-box !important;
            }

            /* Main Unified Layout Container (Desktop, Scaled 15% larger) */
            .leaflet-control.timecontrol,
            .leaflet-bar.timecontrol {
                background-color: #ffffff !important; /* Unified solid background */
                border: 1px solid #777777 !important; /* Unified high-contrast outer border */
                border-radius: 10px !important;
                box-shadow: 0 4px 15px rgba(0,0,0,0.3) !important;
                
                display: flex !important;
                flex-direction: row !important;
                flex-wrap: nowrap !important;        
                align-items: center !important;      /* Perfect vertical center alignment */
                justify-content: flex-start !important;
                gap: 16px !important;                /* Increased spacing */
                box-sizing: border-box !important;
                height: var(--ctrl-height) !important;
                padding: 0 16px !important;          /* Increased padding */
                font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, Arial, sans-serif !important;
                font-size: var(--ui-font-size) !important;
            }
            
            /* Container for playback buttons */
            .leaflet-control.timecontrol .leaflet-bar-timecontrol {
                display: inline-flex !important;
                flex-direction: row !important;
                align-items: center !important;
                height: 100% !important;
                gap: 8px !important;
            }
            
            /* Playback Button Alignment & Scaling */
            .leaflet-control.timecontrol a.leaflet-bar-timecontrol,
            .leaflet-control.timecontrol .leaflet-bar-timecontrol a,
            .leaflet-control.timecontrol .timecontrol-loop {
                width: 42px !important;     /* Scaled up from 35px */
                height: 100% !important;    /* Full height of the parent container to keep them vertically centered */
                display: inline-flex !important;
                align-items: center !important;
                justify-content: center !important;
                color: #222 !important;
                background: transparent !important;
                text-decoration: none !important;
                float: none !important;      /* Eliminate float offsets */
            }

            /* Center glyph/font icons inside the buttons */
            .leaflet-control.timecontrol a.leaflet-bar-timecontrol::before,
            .leaflet-control.timecontrol .leaflet-bar-timecontrol a::before,
            .leaflet-control.timecontrol .timecontrol-loop::before {
                display: inline-flex !important;
                align-items: center !important;
                justify-content: center !important;
                height: 100% !important;
                line-height: 1 !important;
                font-size: 19px !important;  /* Scaled up from 16px */
            }

            /* Absolute Vertical Centering & Scaling for Date & Time Text */
            .leaflet-control.timecontrol .timecontrol-date,
            .leaflet-control.timecontrol .timecontrol-date * {
                font-size: var(--ui-font-size) !important;
                font-family: -apple-system, Arial, sans-serif !important;
                color: #222 !important;
                white-space: nowrap !important;
                display: inline-flex !important;
                align-items: center !important;
                justify-content: center !important;
                height: 100% !important;
                line-height: 1 !important;
                position: static !important; /* Overrides absolute position shifts */
                transform: none !important;
            }

            /* Speed text container and indicator alignment & Scaling */
            .leaflet-control.timecontrol .timecontrol-speed,
            .leaflet-control.timecontrol .timecontrol-speed * {
                font-size: var(--ui-font-size) !important;
                font-family: -apple-system, Arial, sans-serif !important;
                color: #222 !important;
                white-space: nowrap !important;
                display: inline-flex !important;
                align-items: center !important;
                justify-content: center !important;
                height: 100% !important;
                line-height: 1 !important;
            }

            .leaflet-control.timecontrol .timecontrol-speed {
                padding-left: 32px !important; /* Scaled up from 28px */
                background-position: left center !important;
                background-size: 21px 21px !important; /* Scaled up from 18px */
            }

            /* Slider Alignment Wrappers */
            .leaflet-control.timecontrol .timecontrol-slider {
                display: inline-flex !important;
                align-items: center !important;
                flex-grow: 2 !important;     /* Assign priority space for main time slider */
                height: 100% !important;
                margin: 0 4px !important;
            }

            /* Cross-browser range input resets to restore track color and height */
            .timecontrol input[type="range"] {
                -webkit-appearance: none !important;
                -moz-appearance: none !important;
                appearance: none !important;
                background: #ccc !important;
                border: none !important;
                border-radius: 4px !important;
                height: 8px !important;      /* Scaled up from 6px */
                outline: none !important;
                vertical-align: middle !important;
                flex-grow: 1 !important;
            }

            /* Precise FPS speed slider on desktop (360px wide) */
            .leaflet-control.timecontrol .timecontrol-speed input[type="range"] {
                width: 360px !important;      /* Maintain 360px for high precision speed tuning */
                min-width: 360px !important;
                flex-grow: 0 !important;      /* Maintain exact dimension */
            }

            /* Slider track resets for Webkit & Firefox */
            .timecontrol input[type="range"]::-webkit-slider-runnable-track {
                background: transparent !important;
                border: none !important;
                box-shadow: none !important;
            }
            .timecontrol input[type="range"]::-moz-range-track {
                background: transparent !important;
                border: none !important;
                box-shadow: none !important;
            }

            /* Consistent circular slider thumbs */
            .timecontrol input[type="range"]::-webkit-slider-thumb {
                -webkit-appearance: none !important;
                appearance: none !important;
                width: 18px !important;     /* Scaled up from 14px */
                height: 18px !important;    /* Scaled up from 14px */
                border-radius: 50% !important;
                background: #444 !important;
                border: none !important;
                cursor: pointer !important;
                margin-top: 0 !important;
            }
            
            .timecontrol input[type="range"]::-moz-range-thumb {
                width: 18px !important;     /* Scaled up from 14px */
                height: 18px !important;    /* Scaled up from 14px */
                border: none !important;
                border-radius: 50% !important;
                background: #444 !important;
                cursor: pointer !important;
            }

            /* LIQUID GRID RECONSTRUCTION SHIFTS CONTROLS BELOW FLOATING LEGEND ON MOBILE */
            @media (max-width: 860px) {
                :root {
                    --ui-font-size: 12px !important;
                }
                .leaflet-bottom.leaflet-left {
                    position: fixed !important;
                    bottom: 8px !important;
                    left: 3vw !important;
                    width: 94vw !important;
                    margin: 0 !important;
                    padding: 0 !important;
                    z-index: 10000 !important;
                }
                
                .leaflet-control.timecontrol {
                    width: 100% !important;
                    max-width: 100% !important;
                    height: 38px !important;
                    padding: 6px !important;
                    gap: 4px !important;
                }

                .leaflet-bar-timecontrol a,
                .leaflet-control-timecontrol a {
                    width: 26px !important;
                    height: 26px !important;
                    font-size: 11px !important;
                }

                .timecontrol input[type="range"] {
                    width: 18vw !important;
                    height: 4px !important;
                }

                .timecontrol-speed input[type="range"] {
                    width: 15vw !important;
                    min-width: 50px !important;
                }

                .timecontrol input[type="range"]::-webkit-slider-thumb {
                    width: 12px !important;
                    height: 12px !important;
                }
                
                .timecontrol-date,
                .timecontrol-date * { 
                    font-size: 12px !important; 
                    font-weight: bold !important; 
                }
                .timecontrol-speed,
                .timecontrol-speed * { 
                    font-size: 11px !important;
                }
                .timecontrol-speed {
                    padding-left: 18px !important;
                    background-size: 14px 14px !important;
                }
                
                .custom-speed-legend {
                    bottom: 110px !important;
                    left: 12px !important;
                    transform: scale(0.9);
                    transform-origin: bottom left;
                }
            }
        </style>
        """
        html = html.replace("</head>", f"{custom_css}\n</head>")

        with open(html_file, "w", encoding="utf-8") as f:
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
        fix_heatmap_ui(OUTPUT_HTML_FILE)
        send_email_with_link()

    return 0

if __name__ == "__main__":
    exit(main())

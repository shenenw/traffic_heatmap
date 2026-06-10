# File: bus-api.py
# Date: June 10, 2026
# Name: pkd
# Description: Fetches real-time ETS bus positions, filters stopped/errant data, 
#              and generates an interactive HTML traffic intensity heatmap.

import os
import requests
import folium
from folium.plugins import HeatMap

def fetch_live_bus_positions():
    # Target: Edmonton Real-Time Transit Vehicle Positions API
    url = "https://data.edmonton.ca/resource/tm6k-66g3.json"
    MY_EDMONTON_APP_TOKEN = os.environ.get("EDMONTON_APP_TOKEN")
    
    if not MY_EDMONTON_APP_TOKEN:
        print("Error: EDMONTON_APP_TOKEN environment variable is not set.")
        return None
        
    headers = {
        "User-Agent": "Mozilla/5.0 (X11; Linux x86_64)",
        "X-App-Token": MY_EDMONTON_APP_TOKEN,
        "Accept": "application/json"
    }
    
    params = {
        "$limit": 1500  # Pull large batch to sweep entire active transit grid
    }
    
    try:
        response = requests.get(url, headers=headers, params=params, timeout=12)
        response.raise_for_status()
        return response.json()
    except requests.exceptions.RequestException as e:
        print(f"API Fetch Error: {e}")
        return None

def process_and_map(bus_data):
    if not bus_data:
        print("No telemetry data retrieved.")
        return

    heatmap_points = []
    
    for bus in bus_data:
        try:
            # Extract position and vector speed (API values are typically strings)
            lat = float(bus.get("latitude"))
            lon = float(bus.get("longitude"))
            speed = float(bus.get("speed", 0)) 
            
            # --- TELEMETRY FILTERS ---
            # 1. GPS Bounce / Anomaly Filter (Ignore impossible transit speeds)
            if speed > 110: 
                continue
                
            # 2. Bus Stop / Dwell Filter (Ignore values under ~7 km/h to isolate actual road congestion)
            if speed < 7.0:
                continue
                
            # 3. Congestion Weight Calculation (Slower moving vehicles get a heavier red heat profile)
            if speed < 20:
                intensity = 1.0  # Slow crawling traffic bottleneck
            elif speed < 40:
                intensity = 0.6  # Delayed movement
            else:
                intensity = 0.2  # Nominal velocity profile
                
            heatmap_points.append([lat, lon, intensity])
            
        except (ValueError, TypeError):
            continue

    # Center map coordinates over Edmonton
    edmonton_map = folium.Map(location=[53.5461, -113.4938], zoom_start=11, tiles="cartodbpositron")
    
    # Render HeatMap Layer matching the attached template styles
    if heatmap_points:
        HeatMap(
            data=heatmap_points,
            radius=15,
            max_zoom=13,
            blur=10,
            gradient={0.2: 'blue', 0.4: 'lime', 0.6: 'orange', 1.0: 'red'}
        ).add_to(edmonton_map)
    
    # Save output directly to index.html
    edmonton_map.save("index.html")
    print(f"Success: Mapped {len(heatmap_points)} actively rolling vehicles to index.html.")

if __name__ == "__main__":
    raw_positions = fetch_live_bus_positions()
    process_and_map(raw_positions)

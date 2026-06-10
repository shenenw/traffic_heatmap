# File: bus-api.py
# Date: June 10, 2026
# Name: pmd
# Description: Fetches real-time ETS bus positions, filters out stopped/errant data, 
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
    
    # Let's request the maximum block of active buses
    params = {
        "$limit": 1000
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
        print("No data to map.")
        return

    heatmap_points = []
    
    for bus in bus_data:
        try:
            # Safely grab metrics
            lat = float(bus.get("latitude"))
            lon = float(bus.get("longitude"))
            speed = float(bus.get("speed", 0))  # Provided typically in m/s or km/h depending on source schema
            
            # --- FILTERS ---
            # 1. Filter out GPS Bounce (Physically impossible city transit speeds)
            if speed > 110: 
                continue
                
            # 2. Filter out Bus Stops / Dwell Times
            # If speed is practically zero, it's likely boarding passengers rather than trapped in traffic
            if speed < 2.0:
                continue
                
            # 3. Congestion Weight Calculation
            # Slow speeds (but moving) indicate heavy traffic. 
            # We give higher weight intensity to moving buses that are crawling.
            if speed < 15:
                intensity = 1.0  # Heavy traffic congestion
            elif speed < 30:
                intensity = 0.6  # Moderate delays
            else:
                intensity = 0.2  # Free flowing
                
            heatmap_points.append([lat, lon, intensity])
            
        except (ValueError, TypeError):
            # Skip rows missing clean coordinates or speed values
            continue

    # Center map on Edmonton coordinates
    edmonton_map = folium.Map(location=[53.5461, -113.4938], zoom_start=11, tiles="cartodbpositron")
    
    # Generate HeatMap Layer
    HeatMap(
        data=heatmap_points,
        radius=15,
        max_zoom=13,
        blur=10,
        gradient={0.2: 'blue', 0.4: 'lime', 0.6: 'orange', 1.0: 'red'}
    ).add_to(edmonton_map)
    
    # Save output to index.html
    edmonton_map.save("index.html")
    print(f"Success: Processed {len(heatmap_points)} valid data probes into index.html heatmap.")

if __name__ == "__main__":
    raw_data = fetch_live_bus_positions()
    process_and_map(raw_data)

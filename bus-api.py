# File: bus-api.py
# Date: June 10, 2026
# Name: pkd
# Description: Fetches GTFS-RT bus positions, filters stopped/errant data, 
#              and generates an interactive HTML traffic intensity heatmap.

import requests
import folium
from folium.plugins import HeatMap
from google.transit import gtfs_realtime_pb2

def fetch_live_bus_positions():
    # Edmonton's official GTFS-Realtime endpoint (No API key required)
    url = "http://gtfs.edmonton.ca/TMGTFSRealTimeWebService/Vehicle/VehiclePositions.pb"
    
    try:
        response = requests.get(url, timeout=15)
        response.raise_for_status()
        
        # Parse the binary Protocol Buffer data into a readable feed
        feed = gtfs_realtime_pb2.FeedMessage()
        feed.ParseFromString(response.content)
        return feed.entity
    except Exception as e:
        print(f"GTFS API Fetch Error: {e}")
        return None

def process_and_map(entities):
    if not entities:
        print("No telemetry data retrieved.")
        return

    heatmap_points = []
    
    for entity in entities:
        # Ensure the data point is a vehicle and has GPS coordinates
        if not entity.HasField('vehicle') or not entity.vehicle.HasField('position'):
            continue
            
        pos = entity.vehicle.position
        
        # GTFS-RT speed is natively in meters per second. Convert to km/h.
        speed_ms = pos.speed if pos.HasField('speed') else 0
        speed_kmh = speed_ms * 3.6
        
        lat = pos.latitude
        lon = pos.longitude
        
        # --- TELEMETRY FILTERS ---
        # 1. GPS Bounce / Anomaly Filter (Ignore impossible speeds)
        if speed_kmh > 110:
            continue
            
        # 2. Bus Stop / Dwell Filter (Ignore values under 7 km/h)
        if speed_kmh < 7.0:
            continue
            
        # 3. Congestion Weight Calculation
        if speed_kmh < 20:
            intensity = 1.0  # Slow crawling traffic bottleneck
        elif speed_kmh < 40:
            intensity = 0.6  # Delayed movement
        else:
            intensity = 0.2  # Nominal velocity profile
            
        heatmap_points.append([lat, lon, intensity])

    # Center map coordinates over Edmonton
    edmonton_map = folium.Map(location=[53.5461, -113.4938], zoom_start=11, tiles="cartodbpositron")
    
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

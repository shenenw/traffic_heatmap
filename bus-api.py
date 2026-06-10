# File: bus-api.py
# Date: June 10, 2026
# Name: pkd
# Description: Connects to Edmonton Open Data portal utilizing a registered
#              developer App Token via environment variables to stream active traffic delays.

import os
import requests

def fetch_traffic_with_token():
    # Target: Traffic Disruptions API Dataset ID
    url = "https://data.edmonton.ca/resource/k4tx-5k8p.json"
    
    # Safely fetch the token from environment variables
    MY_EDMONTON_APP_TOKEN = os.environ.get("EDMONTON_APP_TOKEN")
    
    if not MY_EDMONTON_APP_TOKEN:
        print("Error: EDMONTON_APP_TOKEN environment variable is not set.")
        return None
    
    headers = {
        "User-Agent": "Mozilla/5.0 (X11; Linux x86_64)",
        "X-App-Token": MY_EDMONTON_APP_TOKEN,  # The official header for Socrata auth
        "Accept": "application/json"
    }
    
    params = {
        "$where": "status = 'Current'",
        "$limit": 500
    }
    
    try:
        response = requests.get(url, headers=headers, params=params, timeout=10)
        response.raise_for_status()
        return response.json()
    except requests.exceptions.RequestException as e:
        print(f"Authentication or grid fetch failure: {e}")
        return None

if __name__ == "__main__":
    data = fetch_traffic_with_token()
    if data:
        print(f"Success! Authenticated and pulled {len(data)} active city disruptions.")

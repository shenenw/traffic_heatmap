# File: bus-api.py
# Date: June 10, 2026
# Name: pkd
# Description: Connects to Edmonton Open Data portal, streams active traffic 
#              disruptions, and outputs the results into a formatted HTML file.

import os
import requests
from datetime import datetime

def fetch_traffic_with_token():
    url = "https://data.edmonton.ca/resource/k4tx-5k8p.json"
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

def generate_html(data):
    current_time = datetime.now().strftime("%Y-%m-%d %H:%M:%S MDT")
    
    html_content = f"""<!DOCTYPE html>
<html>
<head>
    <title>Edmonton Traffic Disruptions</title>
    <style>
        body {{ font-family: Arial, sans-serif; margin: 20px; background-color: #f4f4f9; }}
        h1 {{ color: #333; }}
        .timestamp {{ color: #666; font-style: italic; margin-bottom: 20px; }}
        table {{ width: 100%; border-collapse: collapse; background: white; }}
        th, td {{ padding: 12px; text-align: left; border-bottom: 1px solid #ddd; }}
        th {{ background-color: #007acc; color: white; }}
        tr:hover {{ background-color: #f1f1f1; }}
    </style>
</head>
<body>
    <h1>Active Edmonton Traffic Disruptions</h1>
    <div class="timestamp">Last Updated: {current_time}</div>
    <table>
        <tr>
            <th>Description</th>
            <th>Location</th>
            <th>Status</th>
        </tr>
"""
    
    if data:
        for item in data:
            description = item.get("description", "N/A")
            location = item.get("location_description", "N/A")
            status = item.get("status", "N/A")
            html_content += f"""        <tr>
            <td>{description}</td>
            <td>{location}</td>
            <td>{status}</td>
        </tr>\n"""
    else:
        html_content += "        <tr><td colspan='3'>No active disruptions found or failed to fetch data.</td></tr>\n"
        
    html_content += """    </table>
</body>
</html>"""

    with open("index.html", "w") as file:
        file.write(html_content)
    print("Success: index.html generated.")

if __name__ == "__main__":
    traffic_data = fetch_traffic_with_token()
    generate_html(traffic_data)

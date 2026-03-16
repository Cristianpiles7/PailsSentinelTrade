
import requests

url = "http://127.0.0.1:8000/api/matrix"
headers = {"X-PST-Token": "PstAdmin01"} # Default token from main.py

try:
    response = requests.get(url, headers=headers)
    if response.status_code == 200:
        data = response.json()
        for item in data:
            sym = item.get('symbol')
            factors = item.get('factors_map', {})
            mr = factors.get('PST-Mean-Reversion', {})
            print(f"Símbolo: {sym} | Mean Reversion IsActive: {mr.get('is_active')} (Type: {type(mr.get('is_active'))})")
    else:
        print(f"Error: {response.status_code}")
except Exception as e:
    print(f"Error: {e}")

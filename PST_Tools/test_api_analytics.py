import requests
import json

URL = "http://127.0.0.1:8000/api/performance/analytics"

def test_api():
    try:
        # Intentar sin token primero (si la ruta no está protegida o el token es opcional para GET)
        # O intentar leer el token de .env si existe
        headers = {}
        if os.path.exists(".env"):
            with open(".env", "r") as f:
                for line in f:
                    if "WEB_PASSWORD" in line:
                        headers["X-PST-Token"] = line.split("=")[1].strip()

        resp = requests.get(URL, headers=headers)
        print(f"Status Code: {resp.status_code}")
        if resp.status_code == 200:
            data = resp.json()
            print("API Response:")
            print(json.dumps(data, indent=2))
        else:
            print(f"Error: {resp.text}")
    except Exception as e:
        print(f"Failed to connect: {e}")

if __name__ == "__main__":
    import os
    test_api()

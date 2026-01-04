from openalgo import api as openalgo_api
from config import config

print("Inspecting OpenAlgo API Client...")
try:
    client = openalgo_api(
        api_key=config.get("api.api_key"),
        host=config.get("api.host")
    )
    
    print("\nAvailable Attributes/Methods:")
    for attr in dir(client):
        if not attr.startswith("__"):
            print(f"- {attr}")
            
except Exception as e:
    print(f"Error: {e}")

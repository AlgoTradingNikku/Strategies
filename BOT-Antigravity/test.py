from openalgo import api

# Replace 'your_api_key_here' with your actual API key
# Specify the host URL with your hosted domain or ngrok domain. 
# If running locally in windows then use the default host value. 
client = api(api_key='0ea69e6165a81731f9a37d4ab47778f7649ead857a80ac777b727863d1cf780c', host='http://127.0.0.1:5000')

response = client.placeorder(
    strategy="Python",
    symbol="RPOWER",
    action="BUY",
    exchange="NSE",
    price_type="MARKET",
    product="CNC",
    quantity=1
)
print(response)

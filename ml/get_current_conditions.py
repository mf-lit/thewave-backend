import bs4 as bs
import requests
import html5lib
import json
import re
from datetime import datetime

headers = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; WOW64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/109.0.0.0 Safari/537.36"
}

# Weather API
api_key = "f71eaabb484349bda53195132232309"
lat = "51.540859"
lon = "-2.620890"
api_url = f"https://api.weatherapi.com/v1/forecast.json?key={api_key}&q={lat},{lon}&days=1&aqi=no&alerts=no"
response = requests.get(api_url)
data = json.loads(response.text)
max_temp = data["forecast"]["forecastday"][0]["day"]["maxtemp_c"]
min_temp = data["forecast"]["forecastday"][0]["day"]["mintemp_c"]
api_url = f"https://api.weatherapi.com/v1/current.json?key={api_key}&q={lat},{lon}&aqi=no&alerts=no"
response = requests.get(api_url)
data = json.loads(response.text)
current_temp = data["current"]["temp_c"]
feelslike_temp = data["current"]["feelslike_c"]

# Wave Water Temperature
url = "https://www.thewave.com/"
response = requests.get(url, headers=headers)
soup = bs.BeautifulSoup(response.content, "html5lib")

marker = soup.find("p", string=re.compile("Water:.*"))
water_temp = float(re.sub("[^0-9.]", "", marker.text.strip()))
air_temp = marker.find_previous("p")
conditions = air_temp.find_previous("p")
air_temp = float(re.sub("[^0-9.]", "", air_temp.text.strip()))
conditions = conditions.text.strip().rstrip(" &")

# Time now
datetime = datetime.now().isoformat()

print(
    f"{datetime},{water_temp},{air_temp},{conditions},{max_temp},{min_temp},{current_temp},{feelslike_temp}"
)
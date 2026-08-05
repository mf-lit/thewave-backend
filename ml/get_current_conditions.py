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
# The site's own weather endpoint, referenced by the homepage's client-side
# state. Returns the same values the page renders, without the markup.
weather_api_url = "https://www.thewave.com/wp-json/wave/v1/weather"


def parse_temp(value):
    match = re.search(r"-?\d+(?:\.\d+)?", value or "")
    if not match:
        raise ValueError(f"Could not parse temperature from {value!r}")
    return float(match.group())


try:
    response = requests.get(weather_api_url, headers=headers)
    response.raise_for_status()
    payload = response.json()
    water_temp = parse_temp(payload["waterTemp"])
    air_temp = parse_temp(payload["current"]["temp"])
    conditions = payload["current"]["description"].strip().rstrip(" &")
except Exception:
    # Fallback: scrape the homepage. The values sit in a block of sibling
    # <p> tags with numbers wrapped in <span data-wp-text="..."> bindings, so
    # the <p> tags have mixed content and must be matched on their full text
    # rather than BeautifulSoup's `string=` filter, which only matches
    # single-string tags.
    url = "https://www.thewave.com/"
    response = requests.get(url, headers=headers)
    soup = bs.BeautifulSoup(response.content, "html5lib")

    marker = soup.find(
        lambda tag: tag.name == "p" and tag.get_text().strip().startswith("Water:")
    )
    water_temp = parse_temp(marker.get_text())
    air_temp = marker.find_previous("p")
    conditions = air_temp.find_previous("p")
    air_temp = parse_temp(air_temp.get_text())
    conditions = conditions.get_text().strip().rstrip(" &")

# Time now
datetime = datetime.now().isoformat()

print(
    f"{datetime},{water_temp},{air_temp},{conditions},{max_temp},{min_temp},{current_temp},{feelslike_temp}"
)
import bs4 as bs
import requests
import re


url = "https://www.thewave.com/"
headers = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; WOW64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/109.0.0.0 Safari/537.36"
}

response = requests.get(url, headers=headers)
soup = bs.BeautifulSoup(response.content, "html5lib")

marker = soup.find("p", string=re.compile("Water:.*"))
water_temp = float(re.sub("[^0-9.]", "", marker.text.strip()))


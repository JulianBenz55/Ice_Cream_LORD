import requests
import cloudscraper
from bs4 import BeautifulSoup

URL = "https://aniworld.to/anime/stream/the-100-girlfriends-who-really-really-really-really-really-love-you/staffel-3/episode-2"
HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/126.0.0.0 Safari/537.36",
    "Referer": "https://aniworld.to/",
}

print("=== Test 1: Normales requests ===")
try:
    resp = requests.get(URL, headers=HEADERS, timeout=30)
    print(f"Status: {resp.status_code}")
    print(f"Length: {len(resp.text)}")
    print(f"'VOE' drin: {'VOE' in resp.text}")
    print(f"'Cloudflare' drin: {'cloudflare' in resp.text.lower()}")
except Exception as e:
    print(f"Fehler: {e}")

print("\n=== Test 2: Mit cloudscraper ===")
try:
    scraper = cloudscraper.create_scraper()
    resp2 = scraper.get(URL, headers=HEADERS, timeout=30)
    print(f"Status: {resp2.status_code}")
    print(f"Length: {len(resp2.text)}")
    print(f"'VOE' drin: {'VOE' in resp2.text}")
    soup = BeautifulSoup(resp2.text, "html.parser")
    for a in soup.find_all("a", href=True):
        text = a.get_text(strip=True)
        if any(p in text.lower() for p in ["voe", "vidoza", "stream"]):
            print(f"  -> {text}: {a['href']}")
except Exception as e:
    print(f"Fehler: {e}")

import requests
from bs4 import BeautifulSoup
import re
import base64
import json
import subprocess
import sys
import os

# Headers, damit AniWorld uns nicht blockt
HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "de-DE,de;q=0.9,en;q=0.8",
}

DOWNLOAD_DIR = "/sdcard/Download/VideoApp"

def find_hoster_urls(aniworld_url):
    """Lade AniWorld-Seite und finde alle Hoster-Links."""
    print(f"[+] Lade AniWorld-Seite: {aniworld_url}")
    
    try:
        resp = requests.get(aniworld_url, headers=HEADERS, timeout=15)
        resp.raise_for_status()
    except Exception as e:
        print(f"[-] Fehler beim Laden: {e}")
        return []
    
    soup = BeautifulSoup(resp.text, "html.parser")
    hoster_urls = []
    
    # Muster 1: Direkte iframes
    for iframe in soup.find_all("iframe"):
        src = iframe.get("src") or iframe.get("data-src")
        if src and any(h in src.lower() for h in ["voe", "streamtape", "dood", "vidoza", "speedfiles"]):
            if src.startswith("//"):
                src = "https:" + src
            hoster_urls.append(src)
            print(f"[+] Gefunden (iframe): {src}")
    
    # Muster 2: Links in data-src Attributen
    for tag in soup.find_all(attrs={"data-src": True}):
        src = tag["data-src"]
        if any(h in src.lower() for h in ["voe", "streamtape", "dood", "vidoza", "speedfiles"]):
            if src.startswith("//"):
                src = "https:" + src
            hoster_urls.append(src)
            print(f"[+] Gefunden (data-src): {src}")
    
    # Muster 3: JavaScript-Variablen mit Hoster-URLs
    js_text = soup.find_all("script")
    for script in js_text:
        if script.string:
            # Suche nach VOE/Streamtape URLs in JS
            matches = re.findall(r'https?://(?:voe\.sx|streamtape\.com|dood\.|vidoza\.net|speedfiles\.co)[^\s"\']+', script.string)
            for m in matches:
                hoster_urls.append(m)
                print(f"[+] Gefunden (JS): {m}")
    
    # Muster 4: Hoster-Redirect-Links auf AniWorld selbst
    for a in soup.find_all("a", href=True):
        href = a["href"]
        if any(h in href.lower() for h in ["/redirect/", "/out/", "/stream/"]):
            if href.startswith("/"):
                href = "https://aniworld.to" + href
            # Manchmal ist der Link direkt der Hoster
            if any(h in href.lower() for h in ["voe", "streamtape", "dood"]):
                hoster_urls.append(href)
                print(f"[+] Gefunden (redirect): {href}")
    
    # Duplikate entfernen
    hoster_urls = list(dict.fromkeys(hoster_urls))
    return hoster_urls


def extract_voe_direct_url(voe_url):
    """Versuche, direkte MP4/m3u8 aus VOE zu extrahieren."""
    print(f"[+] Analysiere VOE: {voe_url}")
    
    try:
        resp = requests.get(voe_url, headers=HEADERS, timeout=15)
        resp.raise_for_status()
    except Exception as e:
        print(f"[-] VOE nicht erreichbar: {e}")
        return None
    
    text = resp.text
    
    # Muster A: window.location.href Redirect
    redirect_match = re.search(r'window\.location\.href\s*=\s*["\']([^"\']+)["\']', text)
    if redirect_match:
        new_url = redirect_match.group(1)
        if new_url.startswith("//"):
            new_url = "https:" + new_url
        print(f"[+] VOE Redirect gefunden: {new_url}")
        return new_url
    
    # Muster B: MP4-URL direkt im JS
    mp4_match = re.search(r'(https?://[^\s"\']+\.mp4[^\s"\']*)', text)
    if mp4_match:
        print(f"[+] Direkte MP4 gefunden: {mp4_match.group(1)}")
        return mp4_match.group(1)
    
    # Muster C: HLS/m3u8
    hls_match = re.search(r'(https?://[^\s"\']+\.m3u8[^\s"\']*)', text)
    if hls_match:
        print(f"[+] HLS-Stream gefunden: {hls_match.group(1)}")
        return hls_match.group(1)
    
    # Muster D: Base64-codierte URL
    b64_match = re.search(r'atob\s*\(\s*["\']([A-Za-z0-9+/=]+)["\']', text)
    if b64_match:
        try:
            decoded = base64.b64decode(b64_match.group(1)).decode("utf-8")
            if decoded.startswith("http"):
                print(f"[+] Base64-URL dekodiert: {decoded}")
                return decoded
        except:
            pass
    
    # Muster E: "sources" JSON
    sources_match = re.search(r'var\s+sources\s*=\s*(\{[^;]+\});', text)
    if sources_match:
        try:
            sources = json.loads(sources_match.group(1))
            if "mp4" in sources:
                print(f"[+] MP4 aus sources: {sources['mp4']}")
                return sources["mp4"]
            if "hls" in sources:
                print(f"[+] HLS aus sources: {sources['hls']}")
                return sources["hls"]
        except:
            pass
    
    print("[-] Kein direktes Video in VOE gefunden.")
    return None


def extract_streamtape_direct_url(st_url):
    """Streamtape-Direktlink extrahieren."""
    print(f"[+] Analysiere Streamtape: {st_url}")
    try:
        resp = requests.get(st_url, headers=HEADERS, timeout=15)
        text = resp.text
        
        # Muster: token im JS
        token_match = re.search(r"document\.getElementById\('[^']+'\)\.innerHTML\s*=\s*['\"]([^'\"]+)['\"]", text)
        if token_match:
            # Direkter Download-Link ist oft im JS versteckt
            pass
        
        # Manchmal ist der direkte Link in einem Meta-Tag oder JS
        direct = re.search(r'(https?://[^\s"\']+\.streamtape\.com/get_video[^\s"\']*)', text)
        if direct:
            return direct.group(1)
    except Exception as e:
        print(f"[-] Streamtape Fehler: {e}")
    return None


def download_with_ytdlp(url, output_dir=DOWNLOAD_DIR):
    """Download via yt-dlp."""
    os.makedirs(output_dir, exist_ok=True)
    
    cmd = [
        "yt-dlp",
        "-o", f"{output_dir}/%(title)s.%(ext)s",
        "--no-warnings",
        "--newline",
        "--progress",
        url
    ]
    
    print(f"[+] Starte yt-dlp: {url}")
    result = subprocess.run(cmd)
    return result.returncode == 0


def download_aniworld(aniworld_url):
    """Hauptfunktion: AniWorld-URL -> Download."""
    hoster_urls = find_hoster_urls(aniworld_url)
    
    if not hoster_urls:
        print("[-] Keine Hoster-URLs auf AniWorld gefunden.")
        print("[!] Mögliche Ursachen: Cloudflare-Blockade, Login erforderlich, oder Seitenstruktur geändert.")
        return False
    
    for hoster_url in hoster_urls:
        print(f"\n[→] Versuche Hoster: {hoster_url}")
        
        # Versuche direkte Extraktion je nach Hoster
        direct_url = None
        
        if "voe" in hoster_url.lower():
            direct_url = extract_voe_direct_url(hoster_url)
        elif "streamtape" in hoster_url.lower():
            direct_url = extract_streamtape_direct_url(hoster_url)
        
        # Wenn direkte URL gefunden, nutze die
        target = direct_url or hoster_url
        
        # Versuche yt-dlp
        if download_with_ytdlp(target):
            print("[✓] Download erfolgreich!")
            return True
        else:
            print("[-] yt-dlp hat mit diesem Hoster nicht funktioniert.")
    
    print("[-] Alle Hoster fehlgeschlagen.")
    return False


if __name__ == "__main__":
    if len(sys.argv) > 1:
        url = sys.argv[1]
    else:
        url = input("AniWorld-URL: ").strip()
    
    if not url:
        print("Bitte eine URL angeben.")
        sys.exit(1)
    
    download_aniworld(url)

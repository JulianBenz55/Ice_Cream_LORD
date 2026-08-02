#!/usr/bin/env python3
import os
import sys
import subprocess
import json
import urllib.request

RENDER_URL = "https://DEIN-RENDER-APP.onrender.com"  # <-- Hier deine Render-URL eintragen!
DOWNLOAD_DIR = "/sdcard/Download/IceCream"

def get_direct_url(video_url):
    """Fragt deinen Render-Server nach der Direkt-URL."""
    try:
        data = json.dumps({"url": video_url}).encode()
        req = urllib.request.Request(
            f"{RENDER_URL}/api/resolve",
            data=data,
            headers={"Content-Type": "application/json"},
            method="POST"
        )
        with urllib.request.urlopen(req, timeout=30) as resp:
            result = json.loads(resp.read().decode())
            if result.get("qualities"):
                return result["qualities"][0]["url"]
    except Exception as e:
        print(f"[!] Server-Fehler: {e}")
    return None

def download_with_ytdlp(url):
    """Lokal mit yt-dlp herunterladen."""
    os.makedirs(DOWNLOAD_DIR, exist_ok=True)
    cmd = [
        "yt-dlp",
        "-o", f"{DOWNLOAD_DIR}/%(title)s.%(ext)s",
        "-f", "best[height<=1080]",
        "--no-playlist",
        "--progress",
        url
    ]
    subprocess.run(cmd)

def main():
    print("╔══════════════════════════════════════╗")
    print("║     🍦 ICE CREAM DOWNLOADER           ║")
    print("╚══════════════════════════════════════╝")
    print()
    
    url = input("Video-URL (YouTube/AniWorld): ").strip()
    if not url:
        return
    
    # YouTube direkt
    if "youtube" in url or "youtu.be" in url:
        print("[+] YouTube erkannt, starte Download...")
        download_with_ytdlp(url)
        return
    
    # AniWorld: Erst Render-Server fragen, dann lokal downloaden
    print("[+] Frage Render-Server nach Direkt-Link...")
    direct_url = get_direct_url(url)
    
    if direct_url:
        print(f"[+] Direkt-URL gefunden: {direct_url[:60]}...")
        print("[+] Starte lokalen Download...")
        download_with_ytdlp(direct_url)
    else:
        print("[-] Kein Direkt-Link gefunden.")
        print("[!] Versuche yt-dlp direkt mit AniWorld-URL...")
        download_with_ytdlp(url)

if __name__ == "__main__":
    main()

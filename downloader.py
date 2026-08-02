#!/usr/bin/env python3
import os
import sys
import subprocess
import json
import urllib.request
import urllib.error

RENDER_URL = "https://ice-cream-lord.onrender.com"
DOWNLOAD_DIR = "/sdcard/Download/IceCream"

def clear():
    os.system("clear")

def print_header():
    clear()
    print("╔══════════════════════════════════════╗")
    print("║     🍦 ICE CREAM DOWNLOADER          ║")
    print("║     Render + Termux Edition          ║")
    print("╚══════════════════════════════════════╝")
    print()

def server_resolve(url):
    """Fragt deinen Render-Server nach Direkt-URL."""
    try:
        data = json.dumps({"url": url}).encode("utf-8")
        req = urllib.request.Request(
            f"{RENDER_URL}/api/resolve",
            data=data,
            headers={"Content-Type": "application/json"},
            method="POST"
        )
        with urllib.request.urlopen(req, timeout=45) as resp:
            result = json.loads(resp.read().decode())
            if result.get("qualities") and len(result["qualities"]) > 0:
                q = result["qualities"][0]
                return q.get("url"), q.get("provider", "unknown")
    except urllib.error.HTTPError as e:
        err = e.read().decode()
        print(f"[!] Server-Fehler {e.code}: {err[:200]}")
    except Exception as e:
        print(f"[!] Verbindungsfehler: {e}")
    return None, None

def download_ytdlp(url):
    """Lokaler Download mit yt-dlp."""
    os.makedirs(DOWNLOAD_DIR, exist_ok=True)
    cmd = [
        "yt-dlp",
        "-o", f"{DOWNLOAD_DIR}/%(title)s.%(ext)s",
        "-f", "best[height<=1080]",
        "--no-playlist",
        "--progress",
        "--no-warnings",
        url
    ]
    print(f"\n[+] Starte Download nach {DOWNLOAD_DIR}...\n")
    subprocess.run(cmd)

def download_youtube_audio(url):
    """Nur Audio als MP3."""
    os.makedirs(DOWNLOAD_DIR, exist_ok=True)
    cmd = [
        "yt-dlp",
        "-o", f"{DOWNLOAD_DIR}/%(title)s.%(ext)s",
        "-x", "--audio-format", "mp3",
        "--audio-quality", "0",
        "--progress",
        "--no-warnings",
        url
    ]
    print(f"\n[+] Starte Audio-Download nach {DOWNLOAD_DIR}...\n")
    subprocess.run(cmd)

def update_from_github():
    """Code-Updates von GitHub holen."""
    print("\n🔄 Prüfe auf Updates...")
    os.chdir(os.path.dirname(os.path.abspath(__file__)))
    result = subprocess.run(["git", "pull"], capture_output=True, text=True)
    print(result.stdout)
    if result.returncode != 0:
        print("Fehler:", result.stderr)
    input("\nEnter zum Fortfahren...")

def show_downloads():
    print(f"\n📁 Downloads in {DOWNLOAD_DIR}:")
    print("-" * 45)
    try:
        files = sorted(os.listdir(DOWNLOAD_DIR))
        if not files:
            print("Noch keine Dateien.")
        else:
            total = 0
            for i, f in enumerate(files, 1):
                path = os.path.join(DOWNLOAD_DIR, f)
                size = os.path.getsize(path)
                size_mb = size / (1024*1024)
                total += size_mb
                print(f"  {i}. {f}")
                print(f"     {size_mb:.1f} MB")
            print("-" * 45)
            print(f"  Gesamt: {total:.1f} MB")
    except Exception as e:
        print(f"Fehler: {e}")
    print("-" * 45)
    input("\nEnter zum Fortfahren...")

def main():
    while True:
        print_header()
        print("  1️⃣  YouTube Video (MP4)")
        print("  2️⃣  YouTube Audio (MP3)")
        print("  3️⃣  AniWorld Episode (via Server)")
        print("  4️⃣  Direkte URL (YouTube/AniWorld/sonstige)")
        print("  5️⃣  Meine Downloads anzeigen")
        print("  6️⃣  Update von GitHub")
        print("  0️⃣  Beenden")
        print()
        
        choice = input("Wähle (0-6): ").strip()
        
        if choice == "1":
            url = input("\nYouTube-URL: ").strip()
            if url:
                download_ytdlp(url)
            input("\nEnter zum Fortfahren...")
        
        elif choice == "2":
            url = input("\nYouTube-URL: ").strip()
            if url:
                download_youtube_audio(url)
            input("\nEnter zum Fortfahren...")
        
        elif choice == "3":
            url = input("\nAniWorld-Episoden-URL: ").strip()
            if not url:
                continue
            print(f"\n[+] Frage Server: {RENDER_URL}")
            direct_url, provider = server_resolve(url)
            if direct_url:
                print(f"[✓] Provider: {provider}")
                print(f"[+] Direkt-Link erhalten")
                download_ytdlp(direct_url)
            else:
                print("[-] Server konnte keinen Link finden.")
                print("[!] Mögliche Ursachen:")
                print("    • AniWorld blockt die Server-IP (Cloudflare)")
                print("    • Die Episode hat keinen erkannten Hoster")
                print("    • Der Server ist im Cold-Start (warte 30s)")
            input("\nEnter zum Fortfahren...")
        
        elif choice == "4":
            url = input("\nBeliebige URL: ").strip()
            if url:
                download_ytdlp(url)
            input("\nEnter zum Fortfahren...")
        
        elif choice == "5":
            show_downloads()
        
        elif choice == "6":
            update_from_github()
        
        elif choice == "0":
            print("\n👋 Bis zum nächsten Mal!")
            break

if __name__ == "__main__":
    main()

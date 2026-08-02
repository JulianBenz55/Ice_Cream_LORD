from flask import Flask, render_template, request, jsonify
import subprocess
import os
import sys
import threading

# Füge das Verzeichnis zum Pfad hinzu, damit wir aniworld.py importieren können
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

try:
    from aniworld import download_aniworld
except ImportError:
    download_aniworld = None

app = Flask(__name__)
DOWNLOAD_DIR = "/sdcard/Download/VideoApp"

def download_youtube(url):
    os.makedirs(DOWNLOAD_DIR, exist_ok=True)
    cmd = [
        "yt-dlp",
        "-o", f"{DOWNLOAD_DIR}/%(title)s.%(ext)s",
        "-f", "best[height<=1080]",
        "--no-playlist",
        "--newline",
        "--progress",
        url
    ]
    return subprocess.run(cmd, capture_output=True, text=True)

@app.route("/")
def index():
    return render_template("index.html")

@app.route("/download", methods=["POST"])
def download():
    data = request.json
    url = data.get("url")
    platform = data.get("platform")
    
    if not url:
        return jsonify({"error": "Keine URL"}), 400
    
    if platform == "aniworld":
        if download_aniworld is None:
            return jsonify({"success": False, "error": "AniWorld-Modul nicht geladen"}), 500
        
        # AniWorld läuft synchron und blockiert kurz, aber das ist OK für Termux
        success = download_aniworld(url)
        return jsonify({
            "success": success,
            "output": "AniWorld-Download abgeschlossen." if success else "Fehlgeschlagen."
        })
    
    elif platform == "youtube":
        result = download_youtube(url)
        return jsonify({
            "success": result.returncode == 0,
            "output": result.stdout[-800:] if result.returncode == 0 else result.stderr[-800:]
        })
    
    return jsonify({"error": "Unbekannte Plattform"}), 400

@app.route("/files")
def list_files():
    try:
        files = os.listdir(DOWNLOAD_DIR)
        return jsonify({"files": files})
    except:
        return jsonify({"files": []})

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=8080, debug=False)

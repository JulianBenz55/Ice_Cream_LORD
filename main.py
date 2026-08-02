from fastapi import FastAPI, HTTPException, Request, Query
from fastapi.responses import StreamingResponse, FileResponse
from fastapi.staticfiles import StaticFiles
from fastapi.middleware.cors import CORSMiddleware
from pathlib import Path
import httpx
import re
import base64
import subprocess
import json
import os
import tempfile
import urllib.parse
import requests
import sys
from bs4 import BeautifulSoup

FRONTEND_DIR = Path(__file__).resolve().parent / "frontend"

app = FastAPI(title="Video Downloader")
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/126.0.0.0 Safari/537.36",
    "Accept-Language": "de-DE,de;q=0.9,en-US;q=0.8,en;q=0.7",
    "Referer": "https://aniworld.to/",
}

PROVIDERS = ["VOE", "Vidoza", "Vidmoly", "Filemoon"]


def log(msg):
    print(f"[DOWNLOADER] {msg}", file=sys.stderr)


def sanitize_filename(name):
    if not name:
        return "video.mp4"
    name = name.replace('\x00', '').strip()
    name = re.sub(r'[\\/*:?"<>|\n\r\t]+', '_', name)
    if len(name) > 200:
        name = name[:200]
    return name


def save_cookies(cookies_b64):
    if not cookies_b64:
        return None
    try:
        raw = base64.b64decode(cookies_b64).decode("utf-8")
        fd, path = tempfile.mkstemp(prefix="cookies_", suffix=".txt")
        os.close(fd)
        with open(path, "w", encoding="utf-8") as f:
            f.write("# Netscape HTTP Cookie File\n")
            for line in raw.splitlines():
                f.write(line.rstrip('\n') + "\n")
        return path
    except Exception as e:
        log("Cookie save error: " + str(e))
        return None


def run_ytdlp_info(url, cookie_file=None):
    cmd = [
        "yt-dlp",
        "--no-warnings",
        "--dump-single-json",
        "--no-download",
        "--flat-playlist",
    ]
    if cookie_file:
        cmd.extend(["--cookies", cookie_file])
    cmd.append(url)
    result = subprocess.run(cmd, capture_output=True, text=True, timeout=90)
    if result.returncode != 0:
        raise ValueError("yt-dlp Fehler: " + (result.stderr or result.stdout)[:1000])
    return json.loads(result.stdout)


def extract_entries(data):
    entries = []
    if "entries" in data and data["entries"]:
        for entry in data["entries"]:
            if entry is None:
                continue
            entries.append({
                "id": entry.get("id", ""),
                "title": entry.get("title", "Unbekannt"),
                "url": entry.get("webpage_url") or entry.get("url", ""),
                "duration": entry.get("duration"),
                "thumbnail": entry.get("thumbnail", ""),
                "uploader": entry.get("uploader", ""),
            })
    else:
        formats = []
        for fmt in data.get("formats", []):
            formats.append({
                "format_id": fmt.get("format_id", ""),
                "ext": fmt.get("ext", "mp4"),
                "quality": fmt.get("quality", 0),
                "resolution": fmt.get("resolution", "?"),
                "filesize": fmt.get("filesize") or fmt.get("filesize_approx"),
                "url": fmt.get("url", ""),
            })
        entries.append({
            "id": data.get("id", ""),
            "title": data.get("title", "Unbekannt"),
            "url": data.get("webpage_url") or "",
            "duration": data.get("duration"),
            "thumbnail": data.get("thumbnail", ""),
            "uploader": data.get("uploader", ""),
            "formats": formats,
        })
    return entries


def get_hoster_link(episode_url, provider):
    log("Lade Episode: " + episode_url)
    session = requests.Session()
    resp = session.get(episode_url, headers=HEADERS, timeout=30)
    log("Status: " + str(resp.status_code))
    if resp.status_code != 200:
        raise ValueError("Status " + str(resp.status_code))
    soup = BeautifulSoup(resp.text, "lxml")
    for a in soup.find_all("a", href=True):
        text = a.get_text(strip=True).lower()
        if provider.lower() in text:
            link = a["href"]
            if link.startswith("/redirect/"):
                return session, "https://aniworld.to" + link
            if link.startswith("http"):
                return session, link
    for iframe in soup.find_all("iframe", src=True):
        src = iframe["src"]
        if provider.lower() in src.lower():
            return session, src
    for div in soup.find_all(attrs={"data-link": True}):
        if provider.lower() in div.get_text(strip=True).lower():
            return session, div["data-link"]
    raise ValueError("Kein " + provider + "-Link gefunden.")


def resolve_voe(session, redirect_url):
    log("Löse VOE auf: " + redirect_url)
    resp = session.get(redirect_url, headers=HEADERS, timeout=30, allow_redirects=True)
    text = resp.text
    m = re.search(r'"hls":\s*"([^"]+)"', text)
    if m:
        return m.group(1)
    m = re.search(r'"mp4":\s*"([^"]+)"', text)
    if m:
        return m.group(1)
    m = re.search(r'let\s+\w+\s*=\s*"([A-Za-z0-9+/=]{50,})"', text)
    if m:
        try:
            decoded = base64.b64decode(m.group(1)).decode("utf-8")
            if decoded.startswith("http"):
                return decoded
        except Exception:
            pass
    idx = text.find(".m3u8")
    if idx != -1:
        start = text.rfind("http", 0, idx)
        if start != -1:
            end = idx + 5
            while end < len(text) and text[end] not in ' "\'\n\r\t<>':
                end += 1
            return text[start:end]
    raise ValueError("VOE konnte nicht aufgeloest werden.")


def resolve_ytdlp(url, cookie_file=None):
    cmd = ["yt-dlp", "--no-warnings", "-g"]
    if cookie_file:
        cmd.extend(["--cookies", cookie_file])
    cmd.append(url)
    result = subprocess.run(cmd, capture_output=True, text=True, timeout=30)
    if result.returncode == 0 and result.stdout.strip():
        return result.stdout.strip().split("\n")[0]
    raise ValueError("yt-dlp fehlgeschlagen: " + (result.stderr or result.stdout))


def resolve_episode(episode_url, provider, cookie_file=None):
    provider = provider.lower()
    try:
        session, hoster_url = get_hoster_link(episode_url, provider)
        if provider == "voe":
            return resolve_voe(session, hoster_url)
    except Exception as e:
        log("VOE Resolver fehlgeschlagen: " + str(e))
    try:
        return resolve_ytdlp(episode_url, cookie_file)
    except Exception as e:
        log("yt-dlp Episode fehlgeschlagen: " + str(e))
    try:
        session, hoster_url = get_hoster_link(episode_url, provider)
        return resolve_ytdlp(hoster_url, cookie_file)
    except Exception as e:
        log("yt-dlp Hoster fehlgeschlagen: " + str(e))
    raise ValueError("Alle Methoden fehlgeschlagen.")


def resolve_with_fallback(url, cookie_file=None):
    last_error = None
    for provider in PROVIDERS:
        try:
            return resolve_episode(url, provider, cookie_file), provider
        except Exception as e:
            last_error = e
            log(provider + " fehlgeschlagen: " + str(e))
    raise last_error or RuntimeError("Kein Provider verfuegbar.")


async def stream_generator(video_url):
    async with httpx.AsyncClient() as client:
        async with client.stream(
            "GET",
            video_url,
            timeout=60,
            headers={
                "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/126.0.0.0 Safari/537.36",
                "Referer": "https://aniworld.to/",
            },
        ) as response:
            response.raise_for_status()
            async for chunk in response.aiter_bytes(chunk_size=8192):
                if chunk:
                    yield chunk


@app.get("/")
def index():
    return FileResponse(FRONTEND_DIR / "index.html")


@app.post("/api/resolve")
async def resolve(request: Request):
    try:
        body = await request.json()
        url = body.get("url")
        cookies_b64 = body.get("cookies_b64")
        if not url:
            raise HTTPException(status_code=400, detail="URL fehlt")
        cookie_file = save_cookies(cookies_b64)
        direct_url, provider = resolve_with_fallback(url, cookie_file)
        label = "Original (" + provider + ")"
        qualities = [
            {
                "url": direct_url,
                "label": label,
                "format_id": None,
                "original_url": url,
                "via_ytdlp": False,
                "provider": provider,
            }
        ]
        return {"qualities": qualities}
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))
    finally:
        if cookie_file and os.path.exists(cookie_file):
            try:
                os.remove(cookie_file)
            except Exception:
                pass


@app.post("/api/extract")
async def extract(request: Request):
    try:
        body = await request.json()
        url = body.get("url")
        cookies_b64 = body.get("cookies_b64")
        if not url:
            raise HTTPException(status_code=400, detail="URL fehlt")
        cookie_file = save_cookies(cookies_b64)
        data = run_ytdlp_info(url, cookie_file)
        entries = extract_entries(data)
        return {
            "success": True,
            "source": data.get("extractor", "unknown"),
            "title": data.get("title", ""),
            "entries": entries,
            "count": len(entries),
        }
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))
    finally:
        if cookie_file and os.path.exists(cookie_file):
            try:
                os.remove(cookie_file)
            except Exception:
                pass


def ytdlp_stream_generator(url, cookie_file=None):
    """
    NEU: Streamt Video direkt durch yt-dlp.
    yt-dlp lädt das Video und wir leiten es direkt zum Client weiter.
    Das ist der einzige Weg, der für ARD, YouTube (mit Cookies) etc. funktioniert.
    """
    cmd = ["yt-dlp", "-o", "-", "--no-warnings", "--no-progress"]
    if cookie_file:
        cmd.extend(["--cookies", cookie_file])
    cmd.append(url)
    log("Starte yt-dlp Stream: " + " ".join(cmd[:5]) + "...")
    process = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    try:
        while True:
            chunk = process.stdout.read(65536)
            if not chunk:
                break
            yield chunk
    finally:
        process.stdout.close()
        process.stderr.close()
        process.wait()
        if process.returncode != 0:
            log("yt-dlp exit code: " + str(process.returncode))


@app.get("/api/download")
async def download(
    url: str = Query(...),
    quality: str = Query("original"),
    format_id: str = Query(None),
    original_url: str = Query(None),
    via_ytdlp: bool = Query(False),
    cookies_b64: str = Query(None),
):
    try:
        cookie_file = save_cookies(cookies_b64)
        
        # Versuche zuerst, den Dateinamen zu ermitteln
        filename = "video.mp4"
        try:
            info_cmd = ["yt-dlp", "--no-warnings", "--print", "%(title)s.%(ext)s", "--no-download"]
            if cookie_file:
                info_cmd.extend(["--cookies", cookie_file])
            info_cmd.append(url)
            info_result = subprocess.run(info_cmd, capture_output=True, text=True, timeout=30)
            if info_result.returncode == 0 and info_result.stdout.strip():
                filename = sanitize_filename(info_result.stdout.strip().split("\n")[0])
        except Exception:
            pass
        
        # Stream direkt durch yt-dlp
        generator = ytdlp_stream_generator(url, cookie_file)
        disp = 'attachment; filename="' + filename + '"'
        headers = {"Content-Disposition": disp}
        return StreamingResponse(generator, media_type="video/mp4", headers=headers)
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))
    finally:
        if cookie_file and os.path.exists(cookie_file):
            try:
                os.remove(cookie_file)
            except Exception:
                pass


@app.get("/api/quickdownload")
async def quickdownload(url: str = Query(...), cookies_b64: str = Query(None)):
    try:
        cookie_file = save_cookies(cookies_b64)
        direct_url, provider = resolve_with_fallback(url, cookie_file)
        
        # Dateinamen ermitteln
        filename = "video.mp4"
        try:
            info_cmd = ["yt-dlp", "--no-warnings", "--print", "%(title)s.%(ext)s", "--no-download"]
            if cookie_file:
                info_cmd.extend(["--cookies", cookie_file])
            info_cmd.append(url)
            info_result = subprocess.run(info_cmd, capture_output=True, text=True, timeout=30)
            if info_result.returncode == 0 and info_result.stdout.strip():
                filename = sanitize_filename(info_result.stdout.strip().split("\n")[0])
        except Exception:
            pass
        
        # Stream direkt durch yt-dlp
        generator = ytdlp_stream_generator(direct_url, cookie_file)
        disp = 'attachment; filename="' + filename + '"'
        headers = {"Content-Disposition": disp}
        return StreamingResponse(generator, media_type="video/mp4", headers=headers)
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))
    finally:
        if cookie_file and os.path.exists(cookie_file):
            try:
                os.remove(cookie_file)
            except Exception:
                pass


app.mount("/static", StaticFiles(directory=str(FRONTEND_DIR)), name="static")

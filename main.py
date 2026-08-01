from fastapi import FastAPI, HTTPException, Query
from fastapi.responses import StreamingResponse, FileResponse
from fastapi.staticfiles import StaticFiles
from fastapi.middleware.cors import CORSMiddleware
from pathlib import Path
import httpx
import re
import base64
import subprocess
import json
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


def run_ytdlp_info(url):
    cmd = [
        "yt-dlp",
        "--no-warnings",
        "--dump-single-json",
        "--no-download",
        "--flat-playlist",
        url,
    ]
    result = subprocess.run(cmd, capture_output=True, text=True, timeout=60)
    if result.returncode != 0:
        raise ValueError("yt-dlp Fehler: " + result.stderr[:500])
    data = json.loads(result.stdout)
    return data


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
    import requests
    session = requests.Session()
    resp = session.get(episode_url, headers=HEADERS, timeout=30)
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
    resp = session.get(redirect_url, headers=HEADERS, timeout=30, allow_redirects=True)
    text = resp.text
    patterns = [
        '"hls":\\s*"([^"]+)"',
        '"mp4":\\s*"([^"]+)"',
        'let\\s+\\w+\\s*=\\s*"([A-Za-z0-9+/=]{50,})"',
    ]
    for pat in patterns:
        matches = re.findall(pat, text)
        for m in matches:
            try:
                decoded = base64.b64decode(m).decode("utf-8")
                if decoded.startswith("http"):
                    return decoded
            except Exception:
                pass
            if m.startswith("http"):
                return m
    idx = text.find(".m3u8")
    if idx != -1:
        start = text.rfind("http", 0, idx)
        if start != -1:
            end = idx + 5
            while end < len(text) and text[end] not in ' "\'\n\r\t<>':
                end += 1
            return text[start:end]
    raise ValueError("VOE konnte nicht aufgeloest werden.")


def resolve_ytdlp(url):
    result = subprocess.run(
        ["yt-dlp", "--no-warnings", "-g", url],
        capture_output=True,
        text=True,
        timeout=30
    )
    if result.returncode == 0 and result.stdout.strip():
        return result.stdout.strip().split("\n")[0]
    raise ValueError("yt-dlp fehlgeschlagen: " + result.stderr)


def resolve_episode(episode_url, provider):
    provider = provider.lower()
    try:
        session, hoster_url = get_hoster_link(episode_url, provider)
        if provider == "voe":
            return resolve_voe(session, hoster_url)
    except Exception:
        pass
    try:
        return resolve_ytdlp(episode_url)
    except Exception:
        pass
    try:
        session, hoster_url = get_hoster_link(episode_url, provider)
        return resolve_ytdlp(hoster_url)
    except Exception:
        pass
    raise ValueError("Alle Methoden fehlgeschlagen.")


def resolve_with_fallback(url):
    last_error = None
    for provider in PROVIDERS:
        try:
            return resolve_episode(url, provider), provider
        except Exception as e:
            last_error = e
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
async def resolve(url: str = Query(...)):
    try:
        direct_url, provider = resolve_with_fallback(url)
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


@app.get("/api/extract")
async def extract(url: str = Query(...)):
    try:
        data = run_ytdlp_info(url)
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


@app.get("/api/download")
async def download(
    url: str = Query(...),
    quality: str = Query("original"),
    format_id: str = Query(None),
    original_url: str = Query(None),
    via_ytdlp: bool = Query(False),
):
    try:
        async with httpx.AsyncClient() as client:
            head = await client.head(
                url,
                timeout=15,
                follow_redirects=True,
                headers={
                    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/126.0.0.0 Safari/537.36"
                },
            )
        media_type = head.headers.get("Content-Type", "video/mp4")
        filename = "video.mp4"
        cd = head.headers.get("Content-Disposition", "")
        if "filename=" in cd:
            filename = cd.split("filename=")[1].strip('"')
        generator = stream_generator(url)
        disp = 'attachment; filename="' + filename + '"'
        headers = {"Content-Disposition": disp}
        return StreamingResponse(generator, media_type=media_type, headers=headers)
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))


@app.get("/api/quickdownload")
async def quickdownload(url: str = Query(...)):
    try:
        direct_url, provider = resolve_with_fallback(url)
        async with httpx.AsyncClient() as client:
            head = await client.head(
                direct_url,
                timeout=15,
                follow_redirects=True,
                headers={
                    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/126.0.0.0 Safari/537.36"
                },
            )
        media_type = head.headers.get("Content-Type", "video/mp4")
        filename = "video.mp4"
        cd = head.headers.get("Content-Disposition", "")
        if "filename=" in cd:
            filename = cd.split("filename=")[1].strip('"')
        generator = stream_generator(direct_url)
        disp = 'attachment; filename="' + filename + '"'
        headers = {"Content-Disposition": disp}
        return StreamingResponse(generator, media_type=media_type, headers=headers)
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))


app.mount("/static", StaticFiles(directory=str(FRONTEND_DIR)), name="static")

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
import os
import urllib.parse
import requests
import tempfile

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


def sanitize_filename(name: str) -> str:
    if not name:
        return "video.mp4"
    # Remove null bytes and surrounding whitespace
    name = name.replace('\x00', '').strip()
    # Keep unicode letters/numbers and a few safe punctuation characters
    name = re.sub(r'[\\/*:?"<>|\n\r\t]+', '_', name)
    # Limit length
    if len(name) > 200:
        name = name[:200]
    return name


def _write_cookies_netscape(session, target_domain="aniworld.to"):
    jar = session.cookies
    fd, path = tempfile.mkstemp(prefix="cookies_", suffix=".txt")
    os.close(fd)
    try:
        with open(path, "w", encoding="utf-8") as f:
            f.write("# Netscape HTTP Cookie File\n")
            for c in jar:
                domain = c.domain or target_domain
                include_subdomains = "TRUE" if domain.startswith(".") else "FALSE"
                path_val = c.path or "/"
                secure = "TRUE" if c.secure else "FALSE"
                expires = str(int(c.expires)) if c.expires else "0"
                name = c.name
                value = c.value
                f.write("\t".join([domain, include_subdomains, path_val, secure, expires, name, value]) + "\n")
        return path
    except Exception:
        if os.path.exists(path):
            os.remove(path)
        raise


def run_ytdlp_info(url):
    # Try to pre-fetch the page to obtain cookies (helps with sites requiring a session)
    cookie_file = None
    try:
        session = requests.Session()
        session.get(url, headers=HEADERS, timeout=15, allow_redirects=True)
        cookie_file = _write_cookies_netscape(session, target_domain=urllib.parse.urlparse(url).hostname or "aniworld.to")
    except Exception:
        cookie_file = None

    cmd = [
        "yt-dlp",
        "--no-warnings",
        "--dump-single-json",
        "--no-download",
        "--flat-playlist",
        url,
    ]
    # add headers
    for k, v in HEADERS.items():
        cmd.insert(-1, "--add-header")
        cmd.insert(-1, f"{k}: {v}")
    if cookie_file:
        cmd.insert(-1, "--cookies")
        cmd.insert(-1, cookie_file)

    try:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=90)
        if result.returncode != 0:
            raise ValueError("yt-dlp Fehler: " + (result.stderr or result.stdout)[:1000])
        data = json.loads(result.stdout)
        return data
    finally:
        if cookie_file and os.path.exists(cookie_file):
            try:
                os.remove(cookie_file)
            except Exception:
                pass


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
        '"hls":\\s*"([^\"]+)"',
        '"mp4":\\s*"([^\"]+)"',
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
    # Use yt-dlp -g to get direct URL. Add headers which some sites require (Referer/User-Agent)
    cookie_file = None
    try:
        session = requests.Session()
        session.get(url, headers=HEADERS, timeout=15, allow_redirects=True)
        cookie_file = _write_cookies_netscape(session, target_domain=urllib.parse.urlparse(url).hostname or "aniworld.to")
    except Exception:
        cookie_file = None

    cmd = [
        "yt-dlp",
        "--no-warnings",
        "-g",
        url,
    ]
    for k, v in HEADERS.items():
        cmd.insert(-1, "--add-header")
        cmd.insert(-1, f"{k}: {v}")
    if cookie_file:
        cmd.insert(-1, "--cookies")
        cmd.insert(-1, cookie_file)

    try:
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=30
        )
        if result.returncode == 0 and result.stdout.strip():
            return result.stdout.strip().split("\n")[0]
        raise ValueError("yt-dlp fehlgeschlagen: " + (result.stderr or result.stdout))
    finally:
        if cookie_file and os.path.exists(cookie_file):
            try:
                os.remove(cookie_file)
            except Exception:
                pass


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


async def infer_filename_from_ytdlp(url, format_id=None):
    try:
        data = run_ytdlp_info(url)
        # If playlist, try first entry
        if isinstance(data, dict) and data.get("entries"):
            first = None
            for e in data["entries"]:
                if e:
                    first = e
                    break
            data = first or data
        title = data.get("title") or data.get("id")
        ext = None
        if format_id and data.get("formats"):
            for f in data.get("formats", []):
                if f.get("format_id") == format_id:
                    ext = f.get("ext")
                    break
        if not ext:
            ext = data.get("ext") or "mp4"
        filename = f"{title}.{ext}" if title else None
        return sanitize_filename(filename) if filename else None
    except Exception:
        return None


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
        # Try to determine filename via yt-dlp metadata if possible
        filename = None
        # First attempt: if original_url or via_ytdlp, ask yt-dlp for title
        if original_url or via_ytdlp or format_id:
            meta_source = original_url or url
            filename = await infer_filename_from_ytdlp(meta_source, format_id=format_id)
        # Next: try a HEAD request to get Content-Disposition / fallback media type
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
        if not filename:
            cd = head.headers.get("Content-Disposition", "")
            if "filename=" in cd:
                filename = cd.split("filename=")[1].strip('"')
        if not filename:
            # try to infer from URL path
            path = urllib.parse.urlparse(url).path
            name = os.path.basename(path)
            if name:
                filename = urllib.parse.unquote(name)
        if not filename:
            filename = "video.mp4"
        filename = sanitize_filename(filename)
        generator = stream_generator(url)
        disp = 'attachment; filename="' + filename + '"'
        headers = {"Content-Disposition": disp}
        return StreamingResponse(generator, media_type=media_type, headers=headers)
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))


@app.get("/api/quickdownload")
async def quickdownload(url: str = Query(...)):
    try:
        # resolve page -> direct url
        direct_url, provider = resolve_with_fallback(url)
        # Try to infer filename from the original page via yt-dlp
        filename = await infer_filename_from_ytdlp(url)
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
        if not filename:
            cd = head.headers.get("Content-Disposition", "")
            if "filename=" in cd:
                filename = cd.split("filename=")[1].strip('"')
        if not filename:
            path = urllib.parse.urlparse(direct_url).path
            name = os.path.basename(path)
            if name:
                filename = urllib.parse.unquote(name)
        if not filename:
            filename = "video.mp4"
        filename = sanitize_filename(filename)
        generator = stream_generator(direct_url)
        disp = 'attachment; filename="' + filename + '"'
        headers = {"Content-Disposition": disp}
        return StreamingResponse(generator, media_type=media_type, headers=headers)
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))


app.mount("/static", StaticFiles(directory=str(FRONTEND_DIR)), name="static")

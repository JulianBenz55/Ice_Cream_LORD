from fastapi import FastAPI, HTTPException, Query, Request
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


def _write_cookies_netscape(session=None, cookies_txt_content: str = None, target_domain="aniworld.to"):
    fd, path = tempfile.mkstemp(prefix="cookies_", suffix=".txt")
    os.close(fd)
    try:
        with open(path, "w", encoding="utf-8") as f:
            f.write("# Netscape HTTP Cookie File\n")
            if cookies_txt_content is not None:
                # write provided cookies.txt content directly
                # ensure lines are valid
                for line in cookies_txt_content.splitlines():
                    f.write(line.rstrip('\n') + "\n")
            elif session is not None:
                jar = session.cookies
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


def cookies_b64_to_header(b64str: str):
    try:
        raw = base64.b64decode(b64str).decode("utf-8")
    except Exception:
        return None
    parts = []
    for line in raw.splitlines():
        line = line.strip()
        if not line or line.startswith('#'):
            continue
        cols = line.split('\t')
        if len(cols) >= 7:
            # Netscape format: domain, flag, path, secure, expires, name, value
            name = cols[5]
            value = cols[6]
            parts.append(f"{name}={value}")
        else:
            # fallback: try cookie in 'name=value' form
            if '=' in line:
                parts.append(line)
    if not parts:
        return None
    return "; ".join(parts)


def run_ytdlp_info(url, cookie_file: str = None):
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

    result = subprocess.run(cmd, capture_output=True, text=True, timeout=90)
    if result.returncode != 0:
        raise ValueError("yt-dlp Fehler: " + (result.stderr or result.stdout)[:1000])
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


def resolve_ytdlp(url, cookie_file: str = None):
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

    result = subprocess.run(
        cmd,
        capture_output=True,
        text=True,
        timeout=30
    )
    if result.returncode == 0 and result.stdout.strip():
        return result.stdout.strip().split("\n")[0]
    raise ValueError("yt-dlp fehlgeschlagen: " + (result.stderr or result.stdout))


def resolve_episode(episode_url, provider, cookie_file: str = None):
    provider = provider.lower()
    try:
        session, hoster_url = get_hoster_link(episode_url, provider)
        if provider == "voe":
            return resolve_voe(session, hoster_url)
    except Exception:
        pass
    try:
        return resolve_ytdlp(episode_url, cookie_file=cookie_file)
    except Exception:
        pass
    try:
        session, hoster_url = get_hoster_link(episode_url, provider)
        return resolve_ytdlp(hoster_url, cookie_file=cookie_file)
    except Exception:
        pass
    raise ValueError("Alle Methoden fehlgeschlagen.")


def resolve_with_fallback(url, cookie_file: str = None):
    last_error = None
    for provider in PROVIDERS:
        try:
            return resolve_episode(url, provider, cookie_file=cookie_file), provider
        except Exception as e:
            last_error = e
    raise last_error or RuntimeError("Kein Provider verfuegbar.")


async def stream_generator(video_url, cookie_header: str = None):
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/126.0.0.0 Safari/537.36",
        "Referer": "https://aniworld.to/",
    }
    if cookie_header:
        headers["Cookie"] = cookie_header
    async with httpx.AsyncClient() as client:
        async with client.stream(
            "GET",
            video_url,
            timeout=60,
            headers=headers,
        ) as response:
            response.raise_for_status()
            async for chunk in response.aiter_bytes(chunk_size=8192):
                if chunk:
                    yield chunk


async def infer_filename_from_ytdlp(url, format_id=None, cookie_file: str = None):
    try:
        data = run_ytdlp_info(url, cookie_file=cookie_file)
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
async def resolve(request: Request, url: str = Query(...)):
    body = None
    try:
        body = await request.json()
    except Exception:
        body = None
    cookies_b64 = None
    if body and isinstance(body, dict):
        cookies_b64 = body.get("cookies_b64")
    # also allow cookies_b64 as query param
    q = request.query_params.get("cookies_b64")
    if q:
        cookies_b64 = q

    cookie_file = None
    cookie_header = None
    try:
        if cookies_b64:
            # write provided cookies content to temp file
            try:
                cookie_txt = base64.b64decode(cookies_b64).decode("utf-8")
                cookie_file = _write_cookies_netscape(cookies_txt_content=cookie_txt)
                cookie_header = cookies_b64_to_header(cookies_b64)
            except Exception:
                cookie_file = None
                cookie_header = None
        else:
            # attempt prefetch to obtain cookies automatically
            try:
                session = requests.Session()
                session.get(url, headers=HEADERS, timeout=15, allow_redirects=True)
                cookie_file = _write_cookies_netscape(session=session, target_domain=urllib.parse.urlparse(url).hostname or "aniworld.to")
            except Exception:
                cookie_file = None
    except Exception:
        cookie_file = None
        cookie_header = None

    try:
        direct_url, provider = resolve_with_fallback(url, cookie_file=cookie_file)
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
async def extract(request: Request, url: str = Query(None)):
    body = None
    try:
        body = await request.json()
    except Exception:
        body = None
    if not url and body and isinstance(body, dict):
        url = body.get("url")
    cookies_b64 = None
    if body and isinstance(body, dict):
        cookies_b64 = body.get("cookies_b64")
    q = request.query_params.get("cookies_b64")
    if q:
        cookies_b64 = q

    cookie_file = None
    try:
        if cookies_b64:
            try:
                cookie_txt = base64.b64decode(cookies_b64).decode("utf-8")
                cookie_file = _write_cookies_netscape(cookies_txt_content=cookie_txt)
            except Exception:
                cookie_file = None
        else:
            try:
                session = requests.Session()
                session.get(url, headers=HEADERS, timeout=15, allow_redirects=True)
                cookie_file = _write_cookies_netscape(session=session, target_domain=urllib.parse.urlparse(url).hostname or "aniworld.to")
            except Exception:
                cookie_file = None

        data = run_ytdlp_info(url, cookie_file=cookie_file)
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
        cookie_file = None
        cookie_header = None
        if cookies_b64:
            try:
                cookie_txt = base64.b64decode(cookies_b64).decode("utf-8")
                cookie_file = _write_cookies_netscape(cookies_txt_content=cookie_txt)
                cookie_header = cookies_b64_to_header(cookies_b64)
            except Exception:
                cookie_file = None
                cookie_header = None

        # Try to determine filename via yt-dlp metadata if possible
        filename = None
        if original_url or via_ytdlp or format_id:
            meta_source = original_url or url
            filename = await infer_filename_from_ytdlp(meta_source, format_id=format_id, cookie_file=cookie_file)
        async with httpx.AsyncClient() as client:
            head = await client.head(
                url,
                timeout=15,
                follow_redirects=True,
                headers={
                    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/126.0.0.0 Safari/537.36",
                    **({"Cookie": cookie_header} if cookie_header else {}),
                },
            )
        media_type = head.headers.get("Content-Type", "video/mp4")
        if not filename:
            cd = head.headers.get("Content-Disposition", "")
            if "filename=" in cd:
                filename = cd.split("filename=")[1].strip('"')
        if not filename:
            path = urllib.parse.urlparse(url).path
            name = os.path.basename(path)
            if name:
                filename = urllib.parse.unquote(name)
        if not filename:
            filename = "video.mp4"
        filename = sanitize_filename(filename)
        generator = stream_generator(url, cookie_header=cookie_header)
        disp = 'attachment; filename="' + filename + '"'
        headers = {"Content-Disposition": disp}
        return StreamingResponse(generator, media_type=media_type, headers=headers)
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
        cookie_file = None
        cookie_header = None
        if cookies_b64:
            try:
                cookie_txt = base64.b64decode(cookies_b64).decode("utf-8")
                cookie_file = _write_cookies_netscape(cookies_txt_content=cookie_txt)
                cookie_header = cookies_b64_to_header(cookies_b64)
            except Exception:
                cookie_file = None
                cookie_header = None

        direct_url, provider = resolve_with_fallback(url, cookie_file=cookie_file)
        filename = await infer_filename_from_ytdlp(url, cookie_file=cookie_file)
        async with httpx.AsyncClient() as client:
            head = await client.head(
                direct_url,
                timeout=15,
                follow_redirects=True,
                headers={
                    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/126.0.0.0 Safari/537.36",
                    **({"Cookie": cookie_header} if cookie_header else {}),
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
        generator = stream_generator(direct_url, cookie_header=cookie_header)
        disp = 'attachment; filename="' + filename + '"'
        headers = {"Content-Disposition": disp}
        return StreamingResponse(generator, media_type=media_type, headers=headers)
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))
    finally:
        if cookie_file and os.path.exists(cookie_file):
            try:
                os.remove(cookie_file)
            except Exception:
                pass


app.mount("/static", StaticFiles(directory=str(FRONTEND_DIR)), name="static")

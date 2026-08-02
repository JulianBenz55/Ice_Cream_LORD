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
import logging

FRONTEND_DIR = Path(__file__).resolve().parent / "frontend"

logger = logging.getLogger("uvicorn.error")

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
    name = re.sub(r'[\\/*:?\
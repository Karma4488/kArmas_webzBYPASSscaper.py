#!/usr/bin/env python3
"""
Ultimate Professional Web Scraper (requests + BeautifulSoup)
Use responsibly. 
Made in l0v3 bY kArmasec
"""
import requests
from requests.auth import HTTPBasicAuth
from bs4 import BeautifulSoup
from urllib.parse import urljoin, urlparse
import time
import logging
import sys
import os

# ============================
# CONFIGURATION
# ============================

BASE_URL = "https://target.com"
OUTPUT_DIR = "output.html"              # All files go here
USER_AGENT = (
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/129.0.0.0 Safari/537.36"
)
RATE_DELAY_SECONDS = 2.0                 # Be respectful
MAX_PAGES = 20
MAX_RETRIES = 3
RETRY_BACKOFF = 2                        # Exponential backoff

# Set to True only if you have explicit permission from the site owner
IGNORE_ROBOTS_TXT = False                # <<<--- CHANGE THIS IF YOU HAVE PERMISSION

# Optional auth (set via environment variables for security)
SCRAPE_USER = os.getenv("SCRAPE_USER")
SCRAPE_PASS = os.getenv("SCRAPE_PASS")
SCRAPE_BEARER = os.getenv("SCRAPE_BEARER")

# ============================
# LOGGING SETUP
# ============================

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%H:%M:%S"
)
log = logging.getLogger(__name__)

log.info("kArmasec Ultimate Web Scraper v1.3 — Starting up")

# ============================
# REQUESTS SESSION
# ============================

session = requests.Session()
session.headers.update({
    "User-Agent": USER_AGENT,
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.9",
    "Accept-Encoding": "gzip, deflate, br",
    "Connection": "keep-alive",
    "Upgrade-Insecure-Requests": "1",
})

if SCRAPE_USER and SCRAPE_PASS:
    session.auth = HTTPBasicAuth(SCRAPE_USER, SCRAPE_PASS)
    log.info("Basic auth loaded from environment")

if SCRAPE_BEARER:
    session.headers.update({"Authorization": f"Bearer {SCRAPE_BEARER}"})
    log.info("Bearer token loaded")

# ============================
# ROBOTS.TXT CHECK
# ============================

def allowed_by_robots(base_url: str) -> bool:
    """Return True if crawling is allowed according to robots.txt"""
    if IGNORE_ROBOTS_TXT:
        log.warning("IGNORING robots.txt — YOU enabled IGNORE_ROBOTS_TXT = True")
        return True

    robots_url = urljoin(base_url, "/robots.txt")
    try:
        r = session.get(robots_url, timeout=10)
        if r.status_code != 200:
            log.warning("robots.txt not found (%s). Assuming allowed.", r.status_code)
            return True

        txt = r.text.lower()
        user_agent_section = False
        disallowed_paths = []

        for line in txt.splitlines():
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            if line.lower().startswith("user-agent:"):
                ua = line.split(":", 1)[1].strip()
                user_agent_section = ("*" in ua or "googlebot" in ua or "bot" in ua)
            elif user_agent_section and line.lower().startswith("disallow:"):
                path = line.split(":", 1)[1].strip()
                if path:
                    disallowed_paths.append(path)

        # If there's a Disallow: / → everything is blocked
        if any(p == "/" or p == "/*" for p in disallowed_paths):
            log.error("robots.txt contains 'Disallow: /' → Crawling is FORBIDDEN")
            log.error("To proceed anyway, set IGNORE_ROBOTS_TXT = True (only with permission!)")
            return False

        log.info("robots.txt checked → crawling allowed")
        return True

    except Exception as e:
        log.warning("Failed to fetch robots.txt: %s → assuming allowed", e)
        return True

# ============================
# FETCH PAGE
# ============================

def fetch(url: str) -> str | None:
    """Fetch a URL with retry logic and proper error handling"""
    for attempt in range(1, MAX_RETRIES + 1):
        try:
            r = session.get(url, timeout=20, allow_redirects=True)
            
            if r.status_code == 401:
                log.error("401 Unauthorized → check credentials")
                return None
            if r.status_code == 403:
                log.error("403 Forbidden → access denied by server")
                return None
            if 400 <= r.status_code < 500:
                log.error("Client error %s → skipping %s", r.status_code, url)
                return None
            if 500 <= r.status_code < 600:
                log.warning("Server error %s → retry %d/%d", r.status_code, attempt, MAX_RETRIES)
                time.sleep(RETRY_BACKOFF ** attempt)
                continue

            r.raise_for_status()
            return r.text

        except requests.RequestException as e:
            log.warning("Request failed (%s) → attempt %d/%d: %s", url, attempt, MAX_RETRIES, e)
            if attempt < MAX_RETRIES:
                time.sleep(RETRY_BACKOFF ** attempt)
            else:
                log.error("Failed to fetch %s after %d attempts", url, MAX_RETRIES)
                return None

# ============================
# SAVE HTML
# ============================

def save_html(url: str, html: str):
    parsed = urlparse(url)
    path = parsed.path.strip("/") or "index"
    filename = path.replace("/", "_").rstrip("_") + ".html"
    
    os.makedirs(OUTPUT_DIR, exist_ok=True)
    filepath = os.path.join(OUTPUT_DIR, filename)

    header = f"<!-- Scraped by kArmasec Ultimate Scraper v1.3 | {time.strftime('%Y-%m-%d %H:%M:%S')} -->\n"
    with open(filepath, "w", encoding="utf-8") as f:
        f.write(header + html)
    
    log.info("Saved → %s", filepath)

# ============================
# EXTRACT LINKS
# ============================

def extract_links(base_url: str, html: str):
    soup = BeautifulSoup(html, "html.parser")
    links = set()

    for a in soup.find_all("a", href=True):
        href = a["href"].strip()
        if href.startswith(("mailto:", "tel:", "javascript:", "#")):
            continue
        full_url = urljoin(base_url, href).split("#")[0]
        if urlparse(full_url).netloc == urlparse(base_url).netloc:
            links.add(full_url)
    
    return links

# ============================
# MAIN
# ============================

def main():
    if not allowed_by_robots(BASE_URL):
        log.error("robots.txt blocks crawling. Exiting.")
        log.error("If you have permission, edit the script and set IGNORE_ROBOTS_TXT = True")
        sys.exit(1)

    to_visit = [BASE_URL]
    visited = set()
    scraped_count = 0

    log.info("Starting crawl of %s → max %d pages", BASE_URL, MAX_PAGES)

    while to_visit and scraped_count < MAX_PAGES:
        url = to_visit.pop(0)
        if url in visited:
            continue

        log.info("Fetching [%d/%d] %s", scraped_count + 1, MAX_PAGES, url)
        html = fetch(url)
        if not html:
            visited.add(url)
            continue

        save_html(url, html)
        visited.add(url)
        scraped_count += 1

        new_links = extract_links(BASE_URL, html)
        for link in sorted(new_links):
            if link not in visited and link not in to_visit:
                if len(to_visit) + scraped_count < MAX_PAGES:
                    to_visit.append(link)

        time.sleep(RATE_DELAY_SECONDS)

    log.info("Done! Scraped %d pages → saved in '%s'", scraped_count, OUTPUT_DIR)
    log.info("Made with love by kArmasec")

if __name__ == "__main__":
    main()

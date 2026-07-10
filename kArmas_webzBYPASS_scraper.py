#!/usr/bin/env python3
"""
Ultimate Professional Web Scraper (requests + BeautifulSoup)
Made in l0v3 bY kArmasec

Updates:
- Default OUTPUT_DIR is now a directory ('output') instead of a file.
- Added a small CLI (argparse) so BASE_URL, OUTPUT_DIR, MAX_PAGES, RATE_DELAY_SECONDS, and IGNORE_ROBOTS_TXT can be set at runtime.
- Improved robots.txt parsing to respect Disallow and Crawl-delay (if present); Crawl-delay will adjust the rate delay.
- Sanitized saved filenames and included a short hash when query strings exist to avoid collisions.
- Minor logging and error handling improvements.
"""
from __future__ import annotations

import argparse
import hashlib
import logging
import os
import sys
import time
from typing import Optional, Tuple
from urllib.parse import urljoin, urlparse, urlunparse

import requests
from requests.auth import HTTPBasicAuth
from bs4 import BeautifulSoup

# ============================
# CONFIGURATION (defaults)
# ============================

BASE_URL = "https://target.com"
OUTPUT_DIR = "output"              # directory to place scraped files
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
    datefmt="%H:%M:%S",
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

def _parse_robots(txt: str, our_agent: str = "*") -> Tuple[bool, Optional[float]]:
    """Parse robots.txt text and return (allowed, crawl_delay).
    allowed is False only if Disallow: / applies to us.
    crawl_delay is None or a positive float if found.
    """
    txt = txt.splitlines()
    user_agents = []  # list of (agents_set, directives dict)
    current_agents = None
    current_directives = {}

    for line in txt:
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        if ":" not in line:
            continue
        key, val = line.split(":", 1)
        key = key.strip().lower()
        val = val.strip()

        if key == "user-agent":
            if current_agents is not None:
                user_agents.append((current_agents, current_directives))
            current_agents = {a.strip().lower() for a in val.split()} if val else {val.lower()}
            current_directives = {}
        elif current_agents is None:
            # directive before any user-agent — ignore
            continue
        else:
            # store directive for current group
            current_directives.setdefault(key, []).append(val)

    if current_agents is not None:
        user_agents.append((current_agents, current_directives))

    # Find most specific matching group: exact agent match first, then '*' fallback
    our = our_agent.lower()
    matched = None
    for agents, directives in user_agents:
        if our in agents:
            matched = directives
            break
    if matched is None:
        for agents, directives in user_agents:
            if "*" in agents:
                matched = directives
                break

    crawl_delay: Optional[float] = None
    if matched:
        # check crawl-delay
        if "crawl-delay" in matched:
            try:
                crawl_delay = float(matched["crawl-delay"][0])
            except Exception:
                crawl_delay = None
        # check disallow
        if "disallow" in matched:
            for p in matched["disallow"]:
                if p.strip() in ("/", "/*"):
                    return False, crawl_delay
    return True, crawl_delay


def allowed_by_robots(base_url: str) -> Tuple[bool, Optional[float]]:
    """Return (allowed, crawl_delay) where allowed is True if crawling is allowed.
    If IGNORE_ROBOTS_TXT is True, returns (True, None) but logs a warning.
    """
    global IGNORE_ROBOTS_TXT
    if IGNORE_ROBOTS_TXT:
        log.warning("IGNORING robots.txt — YOU enabled IGNORE_ROBOTS_TXT = True")
        return True, None

    robots_url = urljoin(base_url, "/robots.txt")
    try:
        r = session.get(robots_url, timeout=10)
        if r.status_code != 200:
            log.warning("robots.txt not found (%s). Assuming allowed.", r.status_code)
            return True, None

        allowed, crawl_delay = _parse_robots(r.text, our_agent="*")
        if not allowed:
            log.error("robots.txt contains 'Disallow: /' → Crawling is FORBIDDEN")
            log.error("To proceed anyway, set IGNORE_ROBOTS_TXT = True (only with permission!)")
            return False, crawl_delay

        log.info("robots.txt checked → crawling allowed")
        if crawl_delay is not None:
            log.info("robots.txt specifies Crawl-delay=%s seconds", crawl_delay)
        return True, crawl_delay

    except Exception as e:
        log.warning("Failed to fetch robots.txt: %s → assuming allowed", e)
        return True, None

# ============================
# FETCH PAGE
# ============================

def fetch(url: str) -> Optional[str]:
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

def _sanitize_filename_from_url(url: str) -> str:
    """Create a filesystem-safe filename for a URL. Keeps path structure as underscores and
    appends a short hash if query string exists to avoid collisions.
    Examples:
      https://example.com/ -> index.html
      https://example.com/foo/bar -> foo_bar.html
      https://example.com/search?q=1 -> search_5f3a.html (hash of query)
    """
    parsed = urlparse(url)
    path = parsed.path.strip("/") or "index"
    safe = path.replace("/", "_")
    if parsed.query:
        short = hashlib.md5(parsed.query.encode("utf-8")).hexdigest()[:6]
        safe = f"{safe}_{short}"
    # fall back filename
    filename = safe.rstrip("_") + ".html"
    return filename


def save_html(url: str, html: str, output_dir: str):
    filename = _sanitize_filename_from_url(url)
    os.makedirs(output_dir, exist_ok=True)
    filepath = os.path.join(output_dir, filename)

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

    base_netloc = urlparse(base_url).netloc
    for a in soup.find_all("a", href=True):
        href = a["href"].strip()
        if href.startswith(("mailto:", "tel:", "javascript:", "#")):
            continue
        full_url = urljoin(base_url, href).split("#")[0]
        parsed = urlparse(full_url)
        if parsed.scheme not in ("http", "https"):
            continue
        if parsed.netloc == base_netloc:
            # normalize: remove fragment, keep query
            normalized = urlunparse((parsed.scheme, parsed.netloc, parsed.path, parsed.params, parsed.query, ""))
            links.add(normalized)

    return links

# ============================
# MAIN
# ============================

def main():
    global BASE_URL, OUTPUT_DIR, MAX_PAGES, RATE_DELAY_SECONDS, IGNORE_ROBOTS_TXT

    p = argparse.ArgumentParser(description="kArmasec Ultimate Web Scraper")
    p.add_argument("base_url", nargs="?", default=BASE_URL, help="Base URL to start crawling")
    p.add_argument("--output", "-o", default=OUTPUT_DIR, help="Output directory to save HTML files")
    p.add_argument("--max-pages", "-n", type=int, default=MAX_PAGES, help="Maximum number of pages to scrape")
    p.add_argument("--delay", "-d", type=float, default=RATE_DELAY_SECONDS, help="Seconds to wait between requests")
    p.add_argument("--ignore-robots", action="store_true", help="Ignore robots.txt (DANGEROUS unless you have permission)")
    args = p.parse_args()

    BASE_URL = args.base_url
    OUTPUT_DIR = args.output
    MAX_PAGES = args.max_pages
    RATE_DELAY_SECONDS = args.delay
    if args.ignore_robots:
        IGNORE_ROBOTS_TXT = True

    allowed, crawl_delay = allowed_by_robots(BASE_URL)
    if not allowed:
        log.error("robots.txt blocks crawling. Exiting.")
        log.error("If you have permission, run with --ignore-robots or edit the script and set IGNORE_ROBOTS_TXT = True")
        sys.exit(1)

    if crawl_delay is not None:
        # respect crawl-delay if it's larger than our configured delay
        try:
            if crawl_delay > RATE_DELAY_SECONDS:
                log.info("Adjusting rate delay from %s to robots 'Crawl-delay'=%s", RATE_DELAY_SECONDS, crawl_delay)
                RATE_DELAY_SECONDS = crawl_delay
        except Exception:
            pass

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

        save_html(url, html, OUTPUT_DIR)
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

import requests
import hashlib
import json
import os
import re
import difflib
from datetime import datetime
from bs4 import BeautifulSoup
from dotenv import load_dotenv

load_dotenv()

DATA_FILE = "monitoring_data.json"

WEBSITES = [
    {
        "url": "https://www.liverpoolfc.com/tickets/tickets-availability",
        "name": "Liverpool FC Tickets",
        "discover_links": True,
        "link_pattern": "/tickets/tickets-availability/",
    },
    {"url": "https://www.liverpoolfc.com/tickets/ticket-forwarding", "name": "Ticket Forwarding"},
    {"url": "https://legacy.liverpoolfc.com/tickets/premier-league-sale-dates", "name": "Premier League Sale Dates"},
    {"url": "https://legacy.liverpoolfc.com/tickets/ballots", "name": "Ticket Ballots"},
]


def clean_match_title_from_slug(slug: str) -> str:
    slug = slug.strip("/").split("/")[-1]
    slug = re.sub(r"-\d+$", "", slug)
    parts = slug.split("-")
    month_set = {"jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "oct", "nov", "dec"}

    date_idx = None
    for i in range(len(parts) - 2):
        if parts[i].isdigit() and parts[i + 1].lower() in month_set and parts[i + 2].isdigit() and len(parts[i + 2]) == 4:
            date_idx = i
            break
    if date_idx is None:
        return slug.replace("-", " ").title()

    teams_part = parts[:date_idx]
    day = parts[date_idx]
    mon = parts[date_idx + 1].title()
    time_part = parts[date_idx + 3] if len(parts) > date_idx + 3 else ""

    teams_str = " ".join(teams_part).replace(" v ", " vs ").replace(" V ", " vs ")
    teams_str = re.sub(r"\bliverpool\s+fc\b", "LFC", teams_str, flags=re.IGNORECASE)
    teams_str = teams_str.title().replace("Lfc", "LFC")

    cleaned_time = time_part.lower()
    m = re.match(r"^(\d{2})(\d{2})(am|pm)$", cleaned_time)
    if m:
        hh = int(m.group(1))
        mm = m.group(2)
        ampm = m.group(3)
        cleaned_time = f"{hh}:{mm}{ampm}".lstrip("0")

    date_str = f"{day} {mon}"
    return f"{teams_str} — {date_str} — {cleaned_time}" if cleaned_time else f"{teams_str} — {date_str}"


def load_previous_data():
    if os.path.exists(DATA_FILE):
        with open(DATA_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    return {}


def save_data(data):
    with open(DATA_FILE, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2)


def normalize_text(html: str, selector: str | None = None) -> str:
    soup = BeautifulSoup(html, "html.parser")
    for tag in soup(["script", "style", "noscript"]):
        tag.decompose()

    if selector:
        el = soup.select_one(selector)
        text = el.get_text("\n", strip=True) if el else soup.get_text("\n", strip=True)
    else:
        text = soup.get_text("\n", strip=True)

    lines = [ln.strip() for ln in text.splitlines() if ln.strip()]
    return "\n".join(lines)


def get_page_text(url, selector=None):
    try:
        headers = {"User-Agent": "Mozilla/5.0"}
        r = requests.get(url, headers=headers, timeout=30)
        r.raise_for_status()
        return normalize_text(r.text, selector)
    except Exception as e:
        print(f"Error fetching {url}: {e}")
        return None


def sha256(s: str) -> str:
    return hashlib.sha256(s.encode("utf-8")).hexdigest()


def discover_links(url, link_pattern):
    try:
        headers = {"User-Agent": "Mozilla/5.0"}
        r = requests.get(url, headers=headers, timeout=30)
        r.raise_for_status()
        soup = BeautifulSoup(r.text, "html.parser")

        from urllib.parse import urljoin

        links = []
        for a in soup.find_all("a", href=True):
            href = a["href"]
            if href.startswith("/"):
                href = urljoin(url, href)
            if link_pattern in href and href not in links:
                links.append(href)
        print(f"  Found {len(links)} matching links")
        return links
    except Exception as e:
        print(f"Error discovering links from {url}: {e}")
        return []


def send_discord_notification(message: str):
    webhook_url = os.environ.get("DISCORD_WEBHOOK_URL")
    if not webhook_url:
        print("Discord webhook URL not set")
        return

    payload = {
        "embeds": [{
            "title": "🎟️ TicketHelpLFC — Changes Detected",
            "description": message[:4096],
            "color": 0x1E88E5,
            "timestamp": datetime.utcnow().isoformat(),
            "footer": {"text": "Website Monitor"},
        }]
    }

    try:
        r = requests.post(webhook_url, json=payload, timeout=20)
        r.raise_for_status()
        print("Discord notification sent")
    except Exception as e:
        print(f"Failed to send Discord notification: {e}")


def diff_preview(old_text: str, new_text: str, max_lines: int = 200) -> str:
    diff = list(difflib.unified_diff(
        old_text.splitlines(),
        new_text.splitlines(),
        fromfile="before",
        tofile="after",
        lineterm=""
    ))
    if not diff:
        return ""
    return "\n".join(diff[:max_lines])


def monitor_websites():
    previous = load_previous_data()
    current = {}
    changes = []

    urls_to_check = []
    for site in WEBSITES:
        if site.get("discover_links"):
            print(f"Discovering pages from {site['name']}...")
            for u in discover_links(site["url"], site.get("link_pattern", "")):
                urls_to_check.append({
                    "url": u,
                    "name": f"{site['name']} - {u.split('/')[-1][:50]}",
                    "selector": site.get("selector"),
                })
        else:
            urls_to_check.append({
                "url": site["url"],
                "name": site["name"],
                "selector": site.get("selector"),
            })

    print(f"\nMonitoring {len(urls_to_check)} total pages...\n")

    for site in urls_to_check:
        url = site["url"]
        name = site["name"]
        selector = site.get("selector")

        print(f"Checking {name}...")
        text = get_page_text(url, selector)
        if text is None:
            continue

        text_snap = text[:12000]
        h = sha256(text_snap)

        current[url] = {
            "name": name,
            "hash": h,
            "last_checked": datetime.now().isoformat(),
            "selector": selector,
            "text": text_snap,
        }

        if url in previous:
            if previous[url].get("hash") != h:
                old_text = previous[url].get("text", "")
                changes.append({
                    "name": name,
                    "url": url,
                    "previous_check": previous[url].get("last_checked", "Unknown"),
                    "diff": diff_preview(old_text, text_snap),
                })
                print(f"✓ Change detected on {name}!")
            else:
                print(f"  No changes on {name}")
        else:
            print(f"  First time monitoring {name}")

    if changes:
        msg = ""
        for c in changes:
            url = c["url"]
            display = c["name"]
            if "/tickets/tickets-availability/" in url:
                display = clean_match_title_from_slug(url.rstrip("/").split("/")[-1])

            msg += f"**{display}**\n🔗 {url}\n🕐 Prev: {c['previous_check']}\n"
            if c["diff"]:
                snippet = c["diff"][:3200]
                msg += f"```diff\n{snippet}\n```\n"
            msg += "\n"

        send_discord_notification(msg)
        print(f"\n{len(changes)} change(s) detected and notification sent!")
    else:
        print("\nNo changes detected on any monitored websites")

    save_data(current)


if __name__ == "__main__":
    monitor_websites()

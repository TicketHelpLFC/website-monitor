import requests
import hashlib
import json
import os
from datetime import datetime
from bs4 import BeautifulSoup
import re

def clean_match_title_from_slug(slug: str) -> str:
    """
    Ultra-short format:
    'marseille-v-liverpool-fc-21-jan-2026-0800pm-524'
      -> 'Marseille vs LFC — 21 Jan — 8:00pm'
    """
    slug = slug.strip("/").split("/")[-1]  # ensure it's just the last path chunk

    # Remove trailing numeric id (e.g. -524)
    slug = re.sub(r"-\d+$", "", slug)

    parts = slug.split("-")

    # Find the date pattern in the slug: dd-mon-yyyy
    month_set = {"jan","feb","mar","apr","may","jun","jul","aug","sep","oct","nov","dec"}
    date_idx = None
    for i in range(len(parts) - 2):
        if parts[i].isdigit() and parts[i+1].lower() in month_set and parts[i+2].isdigit() and len(parts[i+2]) == 4:
            date_idx = i
            break

    if date_idx is None:
        return slug.replace("-", " ").title()

    teams_part = parts[:date_idx]
    day = parts[date_idx]
    mon = parts[date_idx + 1].title()
    time_part = parts[date_idx + 3] if len(parts) > date_idx + 3 else ""

    teams_str = " ".join(teams_part)
    teams_str = teams_str.replace(" v ", " vs ").replace(" V ", " vs ")
    teams_str = re.sub(r"\bliverpool\s+fc\b", "LFC", teams_str, flags=re.IGNORECASE)
    teams_str = teams_str.title().replace("Lfc", "LFC")

    cleaned_time = time_part.lower()
    m = re.match(r"^(\d{2})(\d{2})(am|pm)$", cleaned_time)
    if m:
        hh = int(m.group(1))
        mm = m.group(2)
        ampm = m.group(3)
        cleaned_time = f"{hh}:{mm}{ampm}" if hh != 0 else f"12:{mm}{ampm}"
        cleaned_time = cleaned_time.lstrip("0")

    date_str = f"{day} {mon}"
    if cleaned_time:
        return f"{teams_str} — {date_str} — {cleaned_time}"
    return f"{teams_str} — {date_str}"

# Configuration
WEBSITES = [
    {
        "url": "https://www.liverpoolfc.com/tickets/tickets-availability",
        "name": "Liverpool FC Tickets",
        "discover_links": True,
        "link_pattern": "/tickets/tickets-availability/"
    },
    {
        "url": "https://www.liverpoolfc.com/tickets/ticket-forwarding",
        "name": "Ticket Forwarding"
    },
    {
        "url": "https://legacy.liverpoolfc.com/tickets/premier-league-sale-dates",
        "name": "Premier League Sale Dates"
    },
    {
        "url": "https://legacy.liverpoolfc.com/tickets/ballots",
        "name": "Ticket Ballots"
    }
]

DATA_FILE = "monitoring_data.json"


def load_previous_data():
    """Load previous monitoring data"""
    if os.path.exists(DATA_FILE):
        with open(DATA_FILE, 'r') as f:
            return json.load(f)
    return {}

def save_data(data):
    """Save monitoring data"""
    with open(DATA_FILE, 'w') as f:
        json.dump(data, f, indent=2)

def get_page_content(url, selector=None):
    """Fetch and extract content from webpage"""
    try:
        headers = {
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36'
        }
        response = requests.get(url, headers=headers, timeout=30)
        response.raise_for_status()
        
        # If no selector provided, monitor entire page
        if not selector:
            return response.text
        
        # If selector provided, monitor only that part
        soup = BeautifulSoup(response.text, 'html.parser')
        element = soup.select_one(selector)
        
        if element:
            return element.get_text(strip=True)
        
        # Fallback to entire page if selector not found
        print(f"Warning: Selector '{selector}' not found, monitoring entire page")
        return response.text
    except Exception as e:
        print(f"Error fetching {url}: {e}")
        return None

def get_content_hash(content):
    """Generate hash of content for comparison"""
    return hashlib.sha256(content.encode('utf-8')).hexdigest()

def discover_links(url, link_pattern):
    """Discover all links on a page matching a pattern"""
    try:
        headers = {
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36'
        }
        response = requests.get(url, headers=headers, timeout=30)
        response.raise_for_status()
        
        soup = BeautifulSoup(response.text, 'html.parser')
        links = []
        
        for link in soup.find_all('a', href=True):
            href = link['href']
            
            # Convert relative URLs to absolute
            if href.startswith('/'):
                from urllib.parse import urljoin
                href = urljoin(url, href)
            
            # Check if link matches pattern
            if link_pattern in href and href not in links:
                links.append(href)
        
        print(f"  Found {len(links)} matching links")
        return links
    except Exception as e:
        print(f"Error discovering links from {url}: {e}")
        return []

def send_discord_notification(message):
    """Send notification via Discord webhook"""
    webhook_url = os.environ.get('DISCORD_WEBHOOK_URL')
    
    if not webhook_url:
        print("Discord webhook URL not set")
        return
    
    # Create embed for richer formatting
    embed = {
        "embeds": [{
            "title": "🔔 Website Changes Detected!",
            "description": message,
            "color": 5814783,  # Blue color
            "timestamp": datetime.now().isoformat(),
            "footer": {
                "text": "Website Monitor"
            }
        }]
    }
    
    try:
        response = requests.post(webhook_url, json=embed)
        response.raise_for_status()
        print("Discord notification sent")
    except Exception as e:
        print(f"Failed to send Discord notification: {e}")

def monitor_websites():
    """Main monitoring function"""
    previous_data = load_previous_data()
    current_data = {}
    changes_detected = []
    
    # Build list of all URLs to monitor
    urls_to_check = []
    
    for site in WEBSITES:
        if site.get('discover_links'):
            # Discover linked pages automatically
            print(f"Discovering pages from {site['name']}...")
            discovered_urls = discover_links(site['url'], site.get('link_pattern', ''))
            
            for discovered_url in discovered_urls:
                urls_to_check.append({
                    'url': discovered_url,
                    'name': f"{site['name']} - {discovered_url.split('/')[-1][:50]}",
                    'selector': site.get('selector')
                })
        else:
            # Single URL to monitor
            urls_to_check.append({
                'url': site['url'],
                'name': site['name'],
                'selector': site.get('selector')
            })
    
    print(f"\nMonitoring {len(urls_to_check)} total pages...\n")
    
    # Check all URLs
    for site in urls_to_check:
        url = site['url']
        name = site['name']
        selector = site.get('selector')
        
        print(f"Checking {name}...")
        
        content = get_page_content(url, selector)
        if content is None:
            continue
            
        current_hash = get_content_hash(content)
        current_data[url] = {
            "name": name,
            "hash": current_hash,
            "last_checked": datetime.now().isoformat(),
            "selector": selector
        }
        
        # Check for changes
        if url in previous_data:
            if previous_data[url]['hash'] != current_hash:
                changes_detected.append({
                    "name": name,
                    "url": url,
                    "previous_check": previous_data[url].get('last_checked', 'Unknown')
                })
                print(f"✓ Change detected on {name}!")
            else:
                print(f"  No changes on {name}")
        else:
            print(f"  First time monitoring {name}")
    
      for change in changes_detected:
        slug = change["url"].rstrip("/").split("/")[-1]
        pretty = clean_match_title_from_slug(slug)
        display_name = pretty if "/tickets/tickets-availability/" in change["url"] else change["name"]

        message += f"**{display_name}**\n"
        message += f"🔗 {change['url']}\n"
        message += f"🕐 Last check: {change['previous_check']}\n\n"


        
        send_discord_notification(message)
        print(f"\n{len(changes_detected)} change(s) detected and notification sent!")
    else:
        print("\nNo changes detected on any monitored websites")
    
    # Save current state
    save_data(current_data)

if __name__ == "__main__":
    monitor_websites()

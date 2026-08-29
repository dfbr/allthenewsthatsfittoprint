import os
import re
import sys
import datetime
from typing import List, Dict, Optional, Tuple
import requests
import feedparser
from bs4 import BeautifulSoup
import yaml

# Configuration
DOCS_DIR = "./docs"
COVERS_DIR = os.path.join(DOCS_DIR, "covers")
DATA_DIR = os.path.join(DOCS_DIR, "_data")
USER_AGENT = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"

# Primary and topic-specific RSS feeds from BBC
BBC_RSS_URLS = [
    "http://feeds.bbci.co.uk/news/uk/rss.xml",
    "http://feeds.bbci.co.uk/news/rss.xml"
]

KNOWN_PAPERS = [
    "Daily Mail", "The Daily Telegraph", "Telegraph", "The Times", "The Guardian",
    "i paper", "i", "The Sun", "Daily Mirror", "Mirror", "Daily Express", "Express",
    "Financial Times", "FT", "The Independent", "Metro", "Daily Star", "The Scotsman",
    "The Herald", "Morning Star"
]

def clean_filename(name: str) -> str:
    cleaned = re.sub(r'[\\/*?:"<>|]', "", name)
    return cleaned.strip().replace(" ", "_")

def extract_paper_name(alt_text: str) -> str:
    for paper in KNOWN_PAPERS:
        pattern = rf"\b{re.escape(paper)}\b"
        if re.search(pattern, alt_text, re.IGNORECASE):
            return clean_filename(paper)
    cleaned_alt = re.sub(r'(?i)front page of|front page|cover of|the cover|picture of', '', alt_text).strip()
    cleaned_alt = clean_filename(cleaned_alt)
    return cleaned_alt if cleaned_alt else "Unknown_Paper"

def upgrade_bbc_image_url(url: str, target_width: int = 1024) -> str:
    """Upgrades BBC dynamic iChef resolution parameter to high quality."""
    return re.sub(r'/news/\d+/', f'/news/{target_width}/', url)

def get_paper_roundup_articles() -> List[Tuple[str, str]]:
    """
    Searches multiple RSS feeds for paper roundup entries.
    Returns a list of tuples: [(article_url, date_str), ...]
    """
    session = requests.Session()
    session.headers.update({"User-Agent": USER_AGENT})
    
    found_articles = []
    seen_links = set()

    for rss_url in BBC_RSS_URLS:
        try:
            response = session.get(rss_url, timeout=15)
            feed = feedparser.parse(response.content)
            print(f"[*] Fetched RSS feed ({rss_url}) containing {len(feed.entries)} entries.")
        except Exception as e:
            print(f"[-] RSS Fetch Error ({rss_url}): {e}", file=sys.stderr)
            continue

        for entry in feed.entries:
            title = entry.get("title", "")
            link = entry.get("link", "")
            
            if link in seen_links:
                continue

            # Broadened regex matching for BBC paper roundup headlines
            if re.search(r"What the papers say|Newspaper headlines|national papers|paper review|front pages|headlines:", title, re.IGNORECASE):
                published_parsed = entry.get("published_parsed")
                if published_parsed:
                    date_str = datetime.date(*published_parsed[:3]).isoformat()
                else:
                    date_str = datetime.date.today().isoformat()
                    
                print(f"[+] Found Article Candidate: '{title}' ({date_str}) -> {link}")
                found_articles.append((link, date_str))
                seen_links.add(link)

    if not found_articles:
        print("[-] No matching paper articles found in checked RSS feeds.")

    return found_articles

def extract_images_from_article(article_url: str) -> List[Dict[str, str]]:
    """Extracts front page images from BBC article DOM."""
    session = requests.Session()
    session.headers.update({"User-Agent": USER_AGENT})
    
    try:
        resp = session.get(article_url, timeout=15)
        resp.raise_for_status()
    except Exception as e:
        print(f"[-] Article HTTP Error: {e}", file=sys.stderr)
        return []

    soup = BeautifulSoup(resp.text, "html.parser")
    images_data = []

    article = soup.find("article") or soup
    
    # Parse <figure> elements
    figures = article.find_all("figure")
    for fig in figures:
        img = fig.find("img")
        if not img:
            continue
            
        src = img.get("src") or img.get("data-src")
        if not src:
            srcset = img.get("srcset")
            if srcset:
                src = srcset.split(",")[0].split(" ")[0]
                
        if not src or "ichef.bbci.co.uk" not in src:
            continue

        figcaption = fig.find("figcaption")
        alt_text = figcaption.get_text(strip=True) if figcaption else img.get("alt", "")
        
        if re.search(r"front|page|paper|cover|headline|mail|times|guardian|telegraph|express|sun|mirror|ft", alt_text, re.IGNORECASE):
            paper_name = extract_paper_name(alt_text)
            high_res_url = upgrade_bbc_image_url(src, target_width=1024)
            images_data.append({"paper": paper_name, "url": high_res_url, "alt": alt_text})

    # Fallback to general <img> tags
    if not images_data:
        for img in article.find_all("img"):
            src = img.get("src") or img.get("data-src")
            alt_text = img.get("alt", "")
            if src and "ichef.bbci.co.uk" in src and re.search(r"front|page|paper|cover", alt_text, re.IGNORECASE):
                paper_name = extract_paper_name(alt_text)
                images_data.append({
                    "paper": paper_name, 
                    "url": upgrade_bbc_image_url(src, target_width=1024), 
                    "alt": alt_text
                })

    return images_data

def download_image(url: str, output_path: str) -> bool:
    """Downloads an image file. Returns True if a new file was saved."""
    if os.path.exists(output_path):
        return False

    os.makedirs(os.path.dirname(output_path), exist_ok=True)
    session = requests.Session()
    session.headers.update({"User-Agent": USER_AGENT})
    
    try:
        with session.get(url, stream=True, timeout=15) as r:
            r.raise_for_status()
            with open(output_path, 'wb') as f:
                for chunk in r.iter_content(chunk_size=8192):
                    f.write(chunk)
        print(f"    [+] Saved new cover: {output_path}")
        return True
    except Exception as e:
        print(f"    [-] Failed to download image {url}: {e}", file=sys.stderr)
        return False

def build_jekyll_yaml():
    """Scans all collected images and regenerates docs/_data/papers.yml."""
    os.makedirs(DATA_DIR, exist_ok=True)
    all_covers = []

    if os.path.exists(COVERS_DIR):
        for paper_folder in os.listdir(COVERS_DIR):
            folder_path = os.path.join(COVERS_DIR, paper_folder)
            if os.path.isdir(folder_path):
                for file in os.listdir(folder_path):
                    if file.endswith(".jpg"):
                        date_str = file.replace(".jpg", "")
                        all_covers.append({
                            "paper": paper_folder,
                            "paper_display": paper_folder.replace("_", " "),
                            "date": date_str,
                            "img_url": f"/covers/{paper_folder}/{file}"
                        })

    all_covers.sort(key=lambda x: (x["date"], x["paper_display"]), reverse=True)

    yaml_file = os.path.join(DATA_DIR, "papers.yml")
    with open(yaml_file, "w") as f:
        yaml.dump(all_covers, f, default_flow_style=False)
    print(f"[*] Regenerated YAML data index with {len(all_covers)} total paper covers.")

def main():
    print("[*] Starting UK Front Page Collector...")
    articles = get_paper_roundup_articles()
    
    new_images_downloaded = 0

    for article_url, issue_date in articles:
        print(f"[*] Processing article for {issue_date}...")
        images = extract_images_from_article(article_url)
        print(f"    Found {len(images)} front page images in article.")
        
        for img in images:
            output_file = os.path.join(COVERS_DIR, img["paper"], f"{issue_date}.jpg")
            if download_image(img["url"], output_file):
                new_images_downloaded += 1

    build_jekyll_yaml()

    print(f"\n[+] Execution complete. New downloads added: {new_images_downloaded}")
    
    # Modern GitHub Actions output mechanism using GITHUB_OUTPUT environment file
    github_output = os.getenv("GITHUB_OUTPUT")
    has_new = "true" if new_images_downloaded > 0 else "false"
    if github_output:
        with open(github_output, "a") as f:
            f.write(f"has_new_data={has_new}\n")

if __name__ == "__main__":
    main()
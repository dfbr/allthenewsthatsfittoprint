import os
import re
import datetime
from typing import List, Dict, Optional
import requests
import feedparser
from bs4 import BeautifulSoup
import yaml

# Configuration
DOCS_DIR = "./docs"
COVERS_DIR = os.path.join(DOCS_DIR, "covers")
DATA_DIR = os.path.join(DOCS_DIR, "_data")
USER_AGENT = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"
BBC_RSS_URL = "http://feeds.bbci.co.uk/news/uk/rss.xml"

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
    return re.sub(r'/news/\d+/', f'/news/{target_width}/', url)

def get_paper_roundup_url() -> Optional[tuple[str, str]]:
    session = requests.Session()
    session.headers.update({"User-Agent": USER_AGENT})
    try:
        response = session.get(BBC_RSS_URL, timeout=10)
        feed = feedparser.parse(response.content)
    except Exception as e:
        print(f"[-] RSS Error: {e}")
        return None

    for entry in feed.entries:
        title = entry.get("title", "")
        if re.search(r"What the papers say|Newspaper headlines", title, re.IGNORECASE):
            link = entry.get("link", "")
            published_parsed = entry.get("published_parsed")
            if published_parsed:
                date_str = datetime.date(*published_parsed[:3]).isoformat()
            else:
                date_str = datetime.date.today().isoformat()
            return link, date_str
    return None

def extract_images_from_article(article_url: str) -> List[Dict[str, str]]:
    session = requests.Session()
    session.headers.update({"User-Agent": USER_AGENT})
    try:
        resp = session.get(article_url, timeout=10)
        resp.raise_for_status()
    except Exception as e:
        print(f"[-] Article fetch error: {e}")
        return []

    soup = BeautifulSoup(resp.text, "html.parser")
    images_data = []

    article = soup.find("article") or soup
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

    return images_data

def download_image(url: str, output_path: str) -> bool:
    os.makedirs(os.path.dirname(output_path), exist_ok=True)
    if os.path.exists(output_path):
        return True

    session = requests.Session()
    session.headers.update({"User-Agent": USER_AGENT})
    try:
        with session.get(url, stream=True, timeout=15) as r:
            r.raise_for_status()
            with open(output_path, 'wb') as f:
                for chunk in r.iter_content(chunk_size=8192):
                    f.write(chunk)
        return True
    except Exception as e:
        print(f"    [-] Download failed {url}: {e}")
        return False

def build_jekyll_yaml():
    """Scans downloaded images and builds docs/_data/papers.yml."""
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

    # Sort descending by date
    all_covers.sort(key=lambda x: x["date"], reverse=True)

    yaml_file = os.path.join(DATA_DIR, "papers.yml")
    with open(yaml_file, "w") as f:
        yaml.dump(all_covers, f, default_flow_style=False)
    print(f"[+] Updated YAML data at {yaml_file}")

def main():
    print("[*] Fetching BBC roundups...")
    roundup = get_paper_roundup_url()
    if roundup:
        article_url, issue_date = roundup
        images = extract_images_from_article(article_url)
        for img in images:
            output_file = os.path.join(COVERS_DIR, img["paper"], f"{issue_date}.jpg")
            download_image(img["url"], output_file)
    
    print("[*] Generating Jekyll Data file...")
    build_jekyll_yaml()

if __name__ == "__main__":
    main()
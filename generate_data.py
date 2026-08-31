import hashlib
import os
import re
import sys
import tempfile
import datetime
from typing import List, Dict, Optional, Tuple
import requests
import feedparser
from bs4 import BeautifulSoup
from PIL import Image
import yaml

# Configuration
DOCS_DIR = "./docs"
COVERS_DIR = os.path.join(DOCS_DIR, "covers")
DATA_DIR = os.path.join(DOCS_DIR, "_data")
USER_AGENT = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"

BBC_RSS_URLS = [
    "http://feeds.bbci.co.uk/news/uk/rss.xml",
    "http://feeds.bbci.co.uk/news/rss.xml"
]

# Primary list of UK print newspapers
KNOWN_PAPERS = [
    "Daily Mail", "The Daily Telegraph", "Telegraph", "The Times", "The Guardian",
    "i paper", "i", "The Sun", "Daily Mirror", "Mirror", "Daily Express", "Express",
    "Financial Times", "FT", "The Independent", "Metro", "Daily Star", "The Scotsman",
    "The Herald", "Morning Star"
]

# Generic terms that indicate a newspaper front page
PAPER_KEYWORDS = [
    "front page", "front cover", "newspaper", "headline", "the paper", "papers", "Papers"
]

def clean_filename(name: str) -> str:
    cleaned = re.sub(r'[\\/*?:"<>|]', "", name)
    return cleaned.strip().replace(" ", "_")

def extract_paper_name(alt_text: str) -> Optional[str]:
    """
    Extracts a valid newspaper name ONLY if the text explicitly names a known paper.
    Returns None if no known paper is identified.
    """
    for paper in KNOWN_PAPERS:
        pattern = rf"\b{re.escape(paper)}\b"
        if re.search(pattern, alt_text, re.IGNORECASE):
            return clean_filename(paper)
    return None

def is_valid_newspaper_image(src: str, alt_text: str) -> bool:
    """
    Strict filter to exclude non-newspaper graphics, ads, logos, and teasers.
    """
    url_lower = src.lower()
    text_lower = alt_text.lower()

    # 1. Exclude known BBC UI graphics, logos, avatars, and social sharing banners
    excluded_path_terms = [
        "social_sharing", "bbc_news", "logo", "avatar", "promo",
        "icon", "advert", "banner", "line_break"
    ]
    if any(term in url_lower for term in excluded_path_terms):
        return False

    # 2. Exclude common non-paper caption descriptions
    excluded_text_terms = [
        "bbc news", "getty images", "reuters", "afp", "stock photo",
        "author", "reporter", "file photo", "file picture"
    ]
    if any(term in text_lower for term in excluded_text_terms):
        # Allow if it explicitly says 'front page'
        if not any(kw in text_lower for kw in PAPER_KEYWORDS):
            return False

    # 3. MUST match a known paper or explicitly mention "front page/newspaper"
    paper_matched = extract_paper_name(alt_text) is not None
    has_paper_keyword = any(kw in text_lower for kw in PAPER_KEYWORDS)

    return paper_matched or has_paper_keyword

def upgrade_bbc_image_url(url: str, target_width: int = 1024) -> str:
    """Upgrades BBC dynamic iChef resolution parameter to high quality."""
    # Standard iChef pattern: .../news/240/... -> .../news/1024/...
    url = re.sub(r'/news/\d+/', f'/news/{target_width}/', url)
    # ACE image pattern: .../standard/240/... -> .../standard/1024/...
    url = re.sub(r'/standard/\d+/', f'/standard/{target_width}/', url)
    return url

def get_paper_roundup_articles() -> List[Tuple[str, str]]:
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
            description = entry.get("summary", "") or entry.get("description", "")
            link = entry.get("link", "")
            
            if link in seen_links:
                continue

            combined_text = f"{title} {description}"
            pattern = r"What the papers say|Newspaper headlines|national papers|paper review|front pages|headline|papers focus|papers report|Papers"
            
            if re.search(pattern, combined_text, re.IGNORECASE):
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
    """Extracts ONLY verified front page images from BBC article DOM."""
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
    seen_urls = set()

    article = soup.find("article") or soup
    
    # Locate all image containers (<figure>, <picture>, or standalone <img>)
    figures = article.find_all(["figure", "picture"])
    
    for fig in figures:
        img = fig.find("img")
        if not img:
            continue
            
        src = img.get("src") or img.get("data-src")
        if not src:
            srcset = img.get("srcset")
            if srcset:
                src = srcset.split(",")[0].split(" ")[0]
                
        if not src or ("ichef.bbci.co.uk" not in src and "bbc.co.uk" not in src):
            continue

        # Collect caption text from figcaption, alt tag, or parent elements
        figcaption = fig.find(["figcaption", "caption"])
        alt_text = figcaption.get_text(strip=True) if figcaption else img.get("alt", "")
        if not alt_text and fig.parent:
            alt_text = fig.parent.get_text(strip=True)

        # STAGE 1: Strict image verification check
        if not is_valid_newspaper_image(src, alt_text):
            print(f"    [-] Skipped non-paper graphic: '{alt_text[:40]}...'")
            continue

        paper_name = extract_paper_name(alt_text) or "UK_National_Paper"
        high_res_url = upgrade_bbc_image_url(src, target_width=1024)

        if high_res_url not in seen_urls:
            images_data.append({
                "paper": paper_name,
                "url": high_res_url,
                "alt": alt_text
            })
            seen_urls.add(high_res_url)

    return images_data

# Matches archived version filenames, e.g. "2024-01-01_v2.jpg"
VERSION_SUFFIX_RE = re.compile(r'^(?P<base>.+)_v(?P<version>\d+)(?P<ext>\.[^.]+)$')

def _file_hash(path: str) -> str:
    hasher = hashlib.sha256()
    with open(path, 'rb') as f:
        for chunk in iter(lambda: f.read(8192), b""):
            hasher.update(chunk)
    return hasher.hexdigest()

# Perceptual hash size (dHash): an (N+1)xN grid of gradient comparisons,
# giving an N*N-bit fingerprint of the image's visual content.
PHASH_SIZE = 8
# Hamming-distance (out of PHASH_SIZE*PHASH_SIZE bits) below which two
# images are considered visually the same front page, allowing for
# incidental re-encoding/re-compression/resizing noise from the source
# CDN rather than a genuine change to the printed page.
PHASH_MATCH_THRESHOLD = 6

def _perceptual_hash(path: str) -> Optional[int]:
    """
    Computes a difference hash (dHash) of the image, which is robust to
    re-compression, minor resizing, and other encoding differences that
    don't change what the image actually shows. Returns None if the file
    can't be decoded as an image.
    """
    try:
        with Image.open(path) as img:
            gray = img.convert("L").resize(
                (PHASH_SIZE + 1, PHASH_SIZE), Image.LANCZOS
            )
            pixels = list(gray.getdata())
    except Exception as e:
        print(f"    [-] Could not decode image for perceptual hash: {e}", file=sys.stderr)
        return None

    bits = 0
    for row in range(PHASH_SIZE):
        offset = row * (PHASH_SIZE + 1)
        for col in range(PHASH_SIZE):
            bits <<= 1
            if pixels[offset + col] > pixels[offset + col + 1]:
                bits |= 1
    return bits

def _hamming_distance(a: int, b: int) -> int:
    return bin(a ^ b).count("1")

def _images_visually_match(path_a: str, path_b: str) -> bool:
    """
    Returns True if two image files should be treated as the same front
    page: either byte-identical, or perceptually indistinguishable within
    PHASH_MATCH_THRESHOLD. If either image can't be decoded for perceptual
    hashing, only the (already-failed) byte comparison applies, so they're
    treated as different.
    """
    if _file_hash(path_a) == _file_hash(path_b):
        return True

    hash_a = _perceptual_hash(path_a)
    hash_b = _perceptual_hash(path_b)
    if hash_a is None or hash_b is None:
        return False

    return _hamming_distance(hash_a, hash_b) <= PHASH_MATCH_THRESHOLD

def _next_version_path(output_path: str) -> str:
    """
    Finds the next free "_vN" archive path for a given canonical output path,
    e.g. "2024-01-01.jpg" -> "2024-01-01_v1.jpg" (or the next free N).
    """
    base, ext = os.path.splitext(output_path)
    directory = os.path.dirname(output_path)
    highest = 0
    if os.path.isdir(directory):
        for existing in os.listdir(directory):
            match = VERSION_SUFFIX_RE.match(existing)
            if match and os.path.join(directory, match.group("base") + match.group("ext")) == base + ext:
                highest = max(highest, int(match.group("version")))
    return f"{base}_v{highest + 1}{ext}"

def download_image(url: str, output_path: str) -> bool:
    os.makedirs(os.path.dirname(output_path), exist_ok=True)
    session = requests.Session()
    session.headers.update({"User-Agent": USER_AGENT})

    directory = os.path.dirname(output_path)
    tmp_fd, tmp_path = tempfile.mkstemp(dir=directory or ".", suffix=".tmp")
    try:
        try:
            with session.get(url, stream=True, timeout=15) as r:
                r.raise_for_status()
                with os.fdopen(tmp_fd, 'wb') as f:
                    for chunk in r.iter_content(chunk_size=8192):
                        f.write(chunk)
        except Exception as e:
            print(f"    [-] Failed to download image {url}: {e}", file=sys.stderr)
            return False

        if os.path.exists(output_path):
            if _images_visually_match(output_path, tmp_path):
                return False

            # The front page has changed since we last checked: archive the
            # previous version before writing the new one to the canonical
            # path, so the day's page can show every version published so far.
            archive_path = _next_version_path(output_path)
            os.rename(output_path, archive_path)
            print(f"    [~] Front page changed, archived previous version: {archive_path}")

        os.replace(tmp_path, output_path)
        print(f"    [+] Saved new cover: {output_path}")
        return True
    finally:
        if os.path.exists(tmp_path):
            os.remove(tmp_path)

def build_jekyll_yaml():
    os.makedirs(DATA_DIR, exist_ok=True)
    all_covers = []

    if os.path.exists(COVERS_DIR):
        for paper_folder in os.listdir(COVERS_DIR):
            folder_path = os.path.join(COVERS_DIR, paper_folder)
            if not os.path.isdir(folder_path):
                continue

            # Group every file for this paper by the date it was published,
            # separating out any archived earlier versions ("_vN" suffix)
            # from the canonical (most recent) image for that date.
            by_date: Dict[str, Dict[str, object]] = {}
            for file in os.listdir(folder_path):
                if not file.endswith(".jpg"):
                    continue

                version_match = VERSION_SUFFIX_RE.match(file)
                if version_match:
                    date_str = version_match.group("base")
                    version_num = int(version_match.group("version"))
                else:
                    date_str = file[:-len(".jpg")]
                    version_num = None

                entry = by_date.setdefault(date_str, {"canonical": None, "archived": []})
                if version_num is None:
                    entry["canonical"] = file
                else:
                    entry["archived"].append((version_num, file))

            for date_str, entry in by_date.items():
                if not entry["canonical"]:
                    # Only archived versions exist with no current canonical
                    # image (shouldn't normally happen); skip incomplete data.
                    continue

                archived_urls = [
                    f"/covers/{paper_folder}/{file}"
                    for _, file in sorted(entry["archived"], key=lambda pair: pair[0])
                ]
                canonical_url = f"/covers/{paper_folder}/{entry['canonical']}"

                all_covers.append({
                    "paper": paper_folder,
                    "paper_display": paper_folder.replace("_", " "),
                    "date": date_str,
                    "img_url": canonical_url,
                    "versions": archived_urls + [canonical_url]
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
        print(f"    Found {len(images)} valid front page images in article.")
        
        for img in images:
            output_file = os.path.join(COVERS_DIR, img["paper"], f"{issue_date}.jpg")
            if download_image(img["url"], output_file):
                new_images_downloaded += 1

    build_jekyll_yaml()

    print(f"\n[+] Execution complete. New downloads added: {new_images_downloaded}")
    
    github_output = os.getenv("GITHUB_OUTPUT")
    has_new = "true" if new_images_downloaded > 0 else "false"
    if github_output:
        with open(github_output, "a") as f:
            f.write(f"has_new_data={has_new}\n")

if __name__ == "__main__":
    main()

from __future__ import annotations

import csv
import io
import threading
import time
import uuid
from collections import deque
from urllib.parse import urljoin, urlparse, urldefrag, urlunparse
from urllib.robotparser import RobotFileParser

import requests
from bs4 import BeautifulSoup
from flask import Flask, jsonify, render_template, request, send_file, abort

app = Flask(__name__)
app.config["MAX_CONTENT_LENGTH"] = 2 * 1024 * 1024

USER_AGENT = "EducationalWebCrawler/1.0 (+local student project)"
REQUEST_TIMEOUT = 15
REQUEST_DELAY = 0.8

PRESETS = {
    "quotes": "https://quotes.toscrape.com/",
    "books": "https://books.toscrape.com/",
    "itats": "https://itats.ac.id/",
}

jobs = {}
jobs_lock = threading.Lock()
robots_cache = {}


def normalize_url(url: str) -> str | None:
    url = urldefrag(url.strip())[0]
    parsed = urlparse(url)
    if parsed.scheme not in ("http", "https") or not parsed.netloc:
        return None
    # Remove common tracking parameters while preserving meaningful query strings.
    parsed = parsed._replace(fragment="")
    return urlunparse(parsed)


def same_domain(url: str, root_netloc: str) -> bool:
    host = (urlparse(url).hostname or "").lower()
    root = (urlparse(root_netloc).hostname or root_netloc).lower()
    return host == root or host.endswith("." + root)


def allowed_by_robots(url: str) -> bool:
    parsed = urlparse(url)
    origin = f"{parsed.scheme}://{parsed.netloc}"
    robots_url = origin + "/robots.txt"
    if robots_url not in robots_cache:
        parser = RobotFileParser()
        parser.set_url(robots_url)
        try:
            response = requests.get(
                robots_url, headers={"User-Agent": USER_AGENT},
                timeout=REQUEST_TIMEOUT
            )
            if response.status_code == 200:
                parser.parse(response.text.splitlines())
            else:
                # If robots.txt is unavailable, do not treat that as a crawl prohibition.
                parser.parse(["User-agent: *", "Allow: /"])
        except requests.RequestException:
            parser.parse(["User-agent: *", "Disallow: /"])
        robots_cache[robots_url] = parser
    return robots_cache[robots_url].can_fetch(USER_AGENT, url)


def fetch_page(url: str):
    response = requests.get(
        url,
        headers={"User-Agent": USER_AGENT},
        timeout=REQUEST_TIMEOUT,
    )
    response.raise_for_status()
    content_type = response.headers.get("Content-Type", "").lower()
    if "text/html" not in content_type and content_type:
        return None, response.url
    response.encoding = response.apparent_encoding or response.encoding
    return BeautifulSoup(response.text, "html.parser"), response.url


def get_site_type(url: str) -> str:
    host = (urlparse(url).hostname or "").lower()
    if host.endswith("quotes.toscrape.com"):
        return "quotes"
    if host.endswith("books.toscrape.com"):
        return "books"
    return "generic"


def clean_text(node) -> str:
    return " ".join(node.stripped_strings) if node else ""


def scrape_quotes(soup: BeautifulSoup, page_url: str):
    rows = []
    for item in soup.select("div.quote"):
        quote = clean_text(item.select_one("span.text"))
        author = clean_text(item.select_one("small.author"))
        tags = [clean_text(tag) for tag in item.select(".tags a.tag")]
        author_link = item.select_one("span a")
        author_url = urljoin(page_url, author_link.get("href", "")) if author_link else ""
        rows.append({
            "quote": quote,
            "author": author,
            "tags": " | ".join(tags),
            "author_url": author_url,
            "page_url": page_url,
        })
    return rows


def scrape_books(soup: BeautifulSoup, page_url: str):
    rows = []
    category = ""
    crumbs = soup.select("ul.breadcrumb li")
    if len(crumbs) >= 3:
        category = clean_text(crumbs[-2])

    cards = soup.select("article.product_pod")
    for card in cards:
        title_node = card.select_one("h3 a")
        price_node = card.select_one(".price_color")
        stock_node = card.select_one(".availability")
        rating_node = card.select_one("p.star-rating")
        image_node = card.select_one("div.image_container img")
        href = title_node.get("href", "") if title_node else ""
        product_url = urljoin(page_url, href) if href else page_url
        rating = ""
        if rating_node:
            classes = rating_node.get("class", [])
            rating = next((c for c in classes if c != "star-rating"), "")
        rows.append({
            "title": title_node.get("title", clean_text(title_node)) if title_node else "",
            "price": clean_text(price_node),
            "rating": rating,
            "availability": clean_text(stock_node),
            "category": category,
            "product_url": product_url,
            "image_url": urljoin(page_url, image_node.get("src", "")) if image_node else "",
            "source_page": page_url,
        })

    # A product detail page has no product_pod card, so extract its detail fields.
    if not rows and soup.select_one(".product_main"):
        main = soup.select_one(".product_main")
        title = clean_text(main.select_one("h1"))
        price = clean_text(main.select_one(".price_color"))
        availability = clean_text(main.select_one(".availability"))
        rating_node = main.select_one("p.star-rating")
        rating = ""
        if rating_node:
            rating = next((c for c in rating_node.get("class", []) if c != "star-rating"), "")
        image_node = soup.select_one(".thumbnail img")
        rows.append({
            "title": title,
            "price": price,
            "rating": rating,
            "availability": availability,
            "category": category,
            "product_url": page_url,
            "image_url": urljoin(page_url, image_node.get("src", "")) if image_node else "",
            "source_page": page_url,
        })
    return rows


def scrape_generic(soup: BeautifulSoup, page_url: str):
    title = clean_text(soup.title)
    description_node = soup.select_one('meta[name="description"]')
    description = description_node.get("content", "").strip() if description_node else ""
    headings = [clean_text(node) for node in soup.select("h1, h2, h3") if clean_text(node)]
    main = soup.select_one("main") or soup.select_one("article") or soup.body
    content = clean_text(main)
    return [{
        "title": title,
        "url": page_url,
        "description": description,
        "headings": " | ".join(headings),
        "content": content[:12000],
    }]


def extract_links(soup: BeautifulSoup, page_url: str, root_netloc: str):
    found = []
    for anchor in soup.select("a[href]"):
        href = anchor.get("href", "").strip()
        if not href or href.startswith(("#", "mailto:", "tel:", "javascript:")):
            continue
        candidate = normalize_url(urljoin(page_url, href))
        if not candidate:
            continue
        parsed = urlparse(candidate)
        if parsed.path.lower().endswith((
            ".jpg", ".jpeg", ".png", ".gif", ".svg", ".webp",
            ".pdf", ".zip", ".css", ".js", ".xml"
        )):
            continue
        if same_domain(candidate, root_netloc):
            found.append(candidate)
    return found


def set_job(job_id, **values):
    with jobs_lock:
        jobs[job_id].update(values)


def run_crawler(job_id: str, start_url: str, max_pages: int, max_depth: int):
    root_netloc = urlparse(start_url).netloc
    site_type = get_site_type(start_url)
    queue = deque([(start_url, 0)])
    visited = set()
    rows = []
    errors = []
    preview = []
    page_count = 0
    data_count = 0
    last_request = 0.0

    set_job(job_id, status="running", message="Crawling dimulai")

    while queue and page_count < max_pages:
        url, depth = queue.popleft()
        url = normalize_url(url)
        if not url or url in visited or depth > max_depth:
            continue
        visited.add(url)

        if not allowed_by_robots(url):
            errors.append({"url": url, "error": "Dilewati: tidak diizinkan oleh robots.txt"})
            continue

        wait_for = REQUEST_DELAY - (time.time() - last_request)
        if wait_for > 0:
            time.sleep(wait_for)

        try:
            soup, final_url = fetch_page(url)
            last_request = time.time()
            if soup is None:
                continue
            page_count += 1

            if site_type == "quotes":
                extracted = scrape_quotes(soup, final_url)
            elif site_type == "books":
                extracted = scrape_books(soup, final_url)
            else:
                extracted = scrape_generic(soup, final_url)

            rows.extend(extracted)
            data_count += len(extracted)
            for row in extracted[:3]:
                if len(preview) < 12:
                    preview.append(row)

            set_job(
                job_id,
                pages_crawled=page_count,
                data_extracted=data_count,
                current_url=final_url,
                depth=depth,
                preview=preview[-12:],
                rows=list(rows),
                errors=list(errors),
                message=f"Memproses halaman pada kedalaman {depth}",
            )

            if depth < max_depth:
                for link in extract_links(soup, final_url, root_netloc):
                    if link not in visited:
                        queue.append((link, depth + 1))

        except requests.RequestException as exc:
            last_request = time.time()
            errors.append({"url": url, "error": str(exc)[:250]})
        except Exception as exc:
            errors.append({"url": url, "error": str(exc)[:250]})

    with jobs_lock:
        job = jobs[job_id]
        job.update({
            "status": "completed",
            "pages_crawled": page_count,
            "data_extracted": data_count,
            "preview": preview[-12:],
            "rows": rows,
            "errors": errors,
            "message": "Proses selesai",
            "current_url": "",
        })


@app.get("/")
def index():
    return render_template("index.html", presets=PRESETS)


@app.post("/start")
def start():
    payload = request.get_json(silent=True) or {}
    url = normalize_url(payload.get("url", ""))
    if not url:
        return jsonify({"error": "Masukkan URL http atau https yang valid."}), 400

    try:
        max_pages = int(payload.get("max_pages", 30))
        max_depth = int(payload.get("max_depth", 2))
    except (TypeError, ValueError):
        return jsonify({"error": "Batas halaman dan kedalaman harus berupa angka."}), 400

    if not 1 <= max_pages <= 500:
        return jsonify({"error": "Maksimal halaman harus antara 1 sampai 500."}), 400
    if not 0 <= max_depth <= 10:
        return jsonify({"error": "Kedalaman harus antara 0 sampai 10."}), 400

    job_id = uuid.uuid4().hex
    with jobs_lock:
        jobs[job_id] = {
            "status": "queued",
            "pages_crawled": 0,
            "data_extracted": 0,
            "max_pages": max_pages,
            "max_depth": max_depth,
            "depth": 0,
            "current_url": "",
            "message": "Menunggu proses",
            "preview": [],
            "rows": [],
            "errors": [],
        }

    thread = threading.Thread(
        target=run_crawler,
        args=(job_id, url, max_pages, max_depth),
        daemon=True,
    )
    thread.start()
    return jsonify({"job_id": job_id})


@app.get("/status/<job_id>")
def status(job_id):
    with jobs_lock:
        job = jobs.get(job_id)
        if not job:
            abort(404)
        response = {k: v for k, v in job.items() if k != "rows"}
    return jsonify(response)


@app.get("/data/<job_id>")
def get_data(job_id):
    """Return one page of extracted rows for the results table."""
    try:
        page = max(1, int(request.args.get("page", 1)))
        per_page = int(request.args.get("per_page", 10))
    except ValueError:
        return jsonify({"error": "Parameter halaman tidak valid."}), 400

    if per_page not in (10, 20, 50):
        return jsonify({"error": "Jumlah baris per halaman harus 10, 20, atau 50."}), 400

    with jobs_lock:
        job = jobs.get(job_id)
        if not job:
            abort(404)
        rows = list(job.get("rows", []))

    total = len(rows)
    total_pages = max(1, (total + per_page - 1) // per_page)
    page = min(page, total_pages)
    start_index = (page - 1) * per_page
    page_rows = rows[start_index:start_index + per_page]
    fields = list(rows[0].keys()) if rows else []

    return jsonify({
        "rows": page_rows,
        "fields": fields,
        "total": total,
        "page": page,
        "per_page": per_page,
        "total_pages": total_pages,
        "start": start_index + 1 if total else 0,
        "end": min(start_index + per_page, total),
    })


@app.get("/download/<job_id>")
def download(job_id):
    with jobs_lock:
        job = jobs.get(job_id)
        if not job or job.get("status") != "completed":
            abort(404)
        rows = job.get("rows", [])

    if not rows:
        return "Tidak ada data yang berhasil diekstrak.", 404

    fieldnames = list(rows[0].keys())
    output = io.StringIO(newline="")
    writer = csv.DictWriter(output, fieldnames=fieldnames, extrasaction="ignore")
    writer.writeheader()
    writer.writerows(rows)

    memory_file = io.BytesIO(output.getvalue().encode("utf-8-sig"))
    memory_file.seek(0)
    return send_file(
        memory_file,
        mimetype="text/csv",
        as_attachment=True,
        download_name="hasil_crawling.csv",
    )


if __name__ == "__main__":
    app.run(debug=True, threaded=True)

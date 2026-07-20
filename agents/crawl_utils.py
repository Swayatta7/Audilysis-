import json
import re
import time
from collections import deque
from html.parser import HTMLParser
from urllib.parse import urldefrag, urljoin, urlparse

import requests


DEFAULT_TIMEOUT = 45
DEFAULT_HEADERS = {"User-Agent": "AudylysisBot/1.0 (+https://localhost)"}


def ensure_url(value: str) -> str:
    value = (value or "").strip()
    if not value:
        return ""
    if not value.startswith(("http://", "https://")):
        value = f"https://{value}"
    return urldefrag(value)[0]


def same_domain(url_a: str, url_b: str) -> bool:
    return urlparse(url_a).netloc.lower() == urlparse(url_b).netloc.lower()


class SimpleHTMLAuditParser(HTMLParser):
    def __init__(self, base_url: str):
        super().__init__()
        self.base_url = base_url
        self.title = ""
        self.meta_description = ""
        self.meta_robots = ""
        self.canonical = ""
        self.h1 = []
        self.h2 = []
        self.internal_links = []
        self.external_links = []
        self.images = []
        self.images_missing_alt = []
        self.structured_data = []
        self.faq_markers = []
        self.current_tag = None
        self.current_attrs = {}
        self.current_text = []

    def handle_starttag(self, tag, attrs):
        attrs_dict = dict(attrs)
        self.current_tag = tag
        self.current_attrs = attrs_dict
        self.current_text = []

        if tag == "meta":
            name = attrs_dict.get("name", "").lower()
            prop = attrs_dict.get("property", "").lower()
            if name == "description":
                self.meta_description = attrs_dict.get("content", "")
            elif name == "robots":
                self.meta_robots = attrs_dict.get("content", "")
            elif prop == "og:description" and not self.meta_description:
                self.meta_description = attrs_dict.get("content", "")
        elif tag == "link" and attrs_dict.get("rel") == "canonical":
            self.canonical = attrs_dict.get("href", "")
        elif tag == "a":
            href = attrs_dict.get("href", "").strip()
            if href:
                absolute = urljoin(self.base_url, href)
                if absolute.startswith(("http://", "https://")):
                    absolute = urldefrag(absolute)[0]
                    if same_domain(self.base_url, absolute):
                        self.internal_links.append(absolute)
                    else:
                        self.external_links.append(absolute)
        elif tag == "img":
            src = attrs_dict.get("src", "").strip()
            alt = attrs_dict.get("alt")
            if src:
                absolute_src = urljoin(self.base_url, src)
                self.images.append(absolute_src)
                if alt is None or not alt.strip():
                    self.images_missing_alt.append(absolute_src)

    def handle_endtag(self, tag):
        text = " ".join(self.current_text).strip()
        if tag == "title":
            self.title = text
        elif tag == "h1" and text:
            self.h1.append(text)
        elif tag == "h2" and text:
            self.h2.append(text)
        elif tag == "script" and self.current_attrs.get("type") == "application/ld+json":
            if text:
                try:
                    self.structured_data.append(json.loads(text))
                except json.JSONDecodeError:
                    self.structured_data.append({"raw": text})
        self.current_tag = None
        self.current_attrs = {}
        self.current_text = []

    def handle_data(self, data):
        if self.current_tag:
            self.current_text.append(data)
        lowered = data.lower()
        if "faq" in lowered or "frequently asked" in lowered:
            self.faq_markers.append(data.strip())


def fetch_url(url: str, allow_redirects: bool = True, timeout: int = DEFAULT_TIMEOUT, retries: int = 1) -> dict:
    target = ensure_url(url)
    last_error = None
    for attempt in range(retries + 1):
        started = time.perf_counter()
        try:
            response = requests.get(target, headers=DEFAULT_HEADERS, timeout=timeout, allow_redirects=allow_redirects)
            elapsed = round((time.perf_counter() - started) * 1000, 2)
            body = response.text or ""
            return {
                "url": target,
                "final_url": response.url,
                "status_code": response.status_code,
                "headers": dict(response.headers),
                "text": body,
                "content": response.content,
                "page_size_bytes": len(response.content),
                "response_time_ms": elapsed,
                "history": [{"url": item.url, "status_code": item.status_code} for item in response.history],
            }
        except (requests.exceptions.Timeout, requests.exceptions.ConnectionError, requests.exceptions.SSLError, requests.exceptions.RequestException) as exc:
            last_error = exc
            if attempt == retries:
                raise
    raise last_error


def audit_single_page(url: str, timeout: int = DEFAULT_TIMEOUT, retries: int = 1) -> dict:
    fetched = fetch_url(url, timeout=timeout, retries=retries)
    parser = SimpleHTMLAuditParser(fetched["final_url"])
    parser.feed(fetched["text"])
    text = re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", fetched["text"]))
    words = [word for word in text.split(" ") if word]
    robots_url = urljoin(fetched["final_url"], "/robots.txt")
    sitemap_url = urljoin(fetched["final_url"], "/sitemap.xml")

    robots_status = safe_status(robots_url, timeout=timeout)
    sitemap_status = safe_status(sitemap_url, timeout=timeout)

    return {
        "url": fetched["final_url"],
        "requested_url": fetched["url"],
        "http_status": fetched["status_code"],
        "https": urlparse(fetched["final_url"]).scheme == "https",
        "ssl_valid": urlparse(fetched["final_url"]).scheme == "https",
        "title": parser.title,
        "meta_description": parser.meta_description,
        "h1": parser.h1,
        "h2": parser.h2,
        "canonical": parser.canonical,
        "meta_robots": parser.meta_robots,
        "internal_links": sorted(set(parser.internal_links)),
        "external_links": sorted(set(parser.external_links)),
        "images": sorted(set(parser.images)),
        "images_missing_alt": sorted(set(parser.images_missing_alt)),
        "structured_data": parser.structured_data,
        "faq_markers": [item for item in parser.faq_markers if item][:10],
        "page_size_bytes": fetched["page_size_bytes"],
        "response_time_ms": fetched["response_time_ms"],
        "word_count": len(words),
        "redirect_chain": fetched["history"],
        "robots_txt": {"url": robots_url, "status_code": robots_status},
        "sitemap_xml": {"url": sitemap_url, "status_code": sitemap_status},
    }


def safe_status(url: str, timeout: int = DEFAULT_TIMEOUT) -> int | None:
    try:
        response = requests.get(url, headers=DEFAULT_HEADERS, timeout=timeout, allow_redirects=True)
        return response.status_code
    except requests.RequestException:
        return None


def crawl_site(
    start_url: str,
    depth: int = 1,
    limit: int = 10,
    broken_link_scope: str = "all",
    max_link_checks_per_page: int = 25,
    audit_timeout: int = DEFAULT_TIMEOUT,
    audit_retries: int = 1,
    link_check_timeout: int | None = None,
) -> dict:
    normalized = ensure_url(start_url)
    visited = set()
    queue = deque([(normalized, 0)])
    pages = []
    broken_links = []

    while queue and len(visited) < limit:
        current_url, current_depth = queue.popleft()
        if current_url in visited:
            continue
        visited.add(current_url)
        try:
            page = audit_single_page(current_url, timeout=audit_timeout, retries=audit_retries)
            pages.append(page)
            for link in page["internal_links"]:
                if current_depth < depth and same_domain(normalized, link) and link not in visited:
                    queue.append((link, current_depth + 1))
            links_to_check = list(page["internal_links"])
            if broken_link_scope == "all":
                links_to_check.extend(page["external_links"])
            elif broken_link_scope == "external":
                links_to_check = list(page["external_links"])

            for link in links_to_check[:max_link_checks_per_page]:
                status = safe_status(link, timeout=link_check_timeout or audit_timeout)
                if status and status >= 400:
                    broken_links.append({"url": link, "status_code": status, "source_url": current_url})
        except requests.RequestException as exc:
            pages.append({"url": current_url, "error": str(exc)})

    return {
        "start_url": normalized,
        "pages": pages,
        "crawled_urls": [page["url"] for page in pages if page.get("url")],
        "broken_links": broken_links,
    }

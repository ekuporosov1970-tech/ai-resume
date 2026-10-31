#!/usr/bin/env python3
"""Read-only public-search access probe. No authentication or integration writes."""
import json
import re
import urllib.error
import urllib.parse
import urllib.request
from html.parser import HTMLParser
from pathlib import Path

SEARCH = "https://russia.gorodrabot.ru/" + urllib.parse.quote("автор_текстов")
class Reader(HTMLParser):
    def __init__(self):
        super().__init__()
        self.links = []
        self.href = None
        self.parts = []
        self.in_title = False
        self.title_parts = []
    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        if tag == "a":
            self.href = attrs.get("href")
            self.parts = []
        if tag == "title":
            self.in_title = True
    def handle_data(self, data):
        if self.href is not None:
            self.parts.append(data)
        if self.in_title:
            self.title_parts.append(data)
    def handle_endtag(self, tag):
        if tag == "a" and self.href is not None:
            label = re.sub(r"\\s+", " ", " ".join(self.parts)).strip()
            if label and self.href:
                self.links.append({"text": label[:180], "href": urllib.parse.urljoin(SEARCH, self.href)})
            self.href = None
            self.parts = []
        if tag == "title":
            self.in_title = False

req = urllib.request.Request(SEARCH, headers={
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    "Accept-Language": "ru-RU,ru;q=0.9",
    "Accept": "text/html,application/xhtml+xml",
})
result = {"search_url": SEARCH, "success": False}
try:
    with urllib.request.urlopen(req, timeout=30) as response:
        payload = response.read(1800000)
        result.update({"http_status": response.status, "final_url": response.url,
                       "content_type": response.headers.get("Content-Type", ""),
                       "bytes_read": len(payload)})
except urllib.error.HTTPError as error:
    payload = error.read(10000)
    result.update({"http_status": error.code, "final_url": error.url,
                   "error": "HTTP Error "+str(error.code), "bytes_read": len(payload)})
except Exception as error:
    payload = b""
    result["error"] = type(error).__name__+": "+str(error)[:300]
html = payload.decode("utf-8", "replace")
reader = Reader()
reader.feed(html)
result["page_title"] = " ".join(reader.title_parts).strip()[:300]
result["anchors_total"] = len(reader.links)
result["contains_query_text"] = "автор текстов" in html.lower().replace("_", " ")
result["bot_challenge_indicators"] = bool(re.search(r"captcha|too many requests|cloudflare challenge", html, re.I))
candidates = [a for a in reader.links if re.search(r"ваканси|vacanc|/job/|/jobs/", a["href"], re.I)]
result["candidate_links_count"] = len(candidates)
result["candidate_links_sample"] = candidates[:20]
result["other_links_sample"] = reader.links[:8] if not candidates else []
result["success"] = result.get("http_status") == 200 and bool(candidates)
Path("gorodrabot-probe-report.json").write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
print(json.dumps(result, ensure_ascii=False, indent=2))

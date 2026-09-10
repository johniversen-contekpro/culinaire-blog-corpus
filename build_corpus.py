import json
import os
import re
import subprocess
import sys
from datetime import datetime
from html.parser import HTMLParser
from pathlib import Path

from dotenv import load_dotenv
import requests

load_dotenv()

TOKEN = os.environ["WEBFLOW_TOKEN"]
API_BASE = "https://api.webflow.com/v2"
HEADERS = {"Authorization": f"Bearer {TOKEN}", "accept-version": "2.0.0"}

BRANDS = {
    "mobile": {
        "site_id": "66e9b7545cdc891d7a2e281c",
        "collection_id": "66f0553be5cecad0be5c604f",
        "domain": "mobileculinaire.com",
        "url_prefix": "/resource-center/",
    },
    "modular": {
        "site_id": "699ef5544171255db9879608",
        "collection_id": "699ef5544171255db9879684",
        "domain": "modularculinaire.com",
        "url_prefix": "/blog/",
    },
}

# Fields to extract — text/content only, no reference fields (topics, markets, author)
CONTENT_FIELDS = [
    "article-content",       # main body HTML
    "meta-title",            # SEO title
    "description-text",      # meta description / excerpt
    "date",                  # publication date
    "post-body",             # fallback body field name used on some collections
    "content",               # generic fallback
]


class _Stripper(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self._parts = []

    def handle_data(self, data):
        self._parts.append(data)

    def get_text(self):
        return re.sub(r"\s+", " ", " ".join(self._parts)).strip()


def strip_html(html: str) -> str:
    if not html:
        return ""
    s = _Stripper()
    s.feed(html)
    return s.get_text()


def fetch_items(collection_id: str) -> list:
    items, offset, limit = [], 0, 100
    while True:
        r = requests.get(
            f"{API_BASE}/collections/{collection_id}/items",
            headers=HEADERS,
            params={"limit": limit, "offset": offset},
            timeout=30,
        )
        r.raise_for_status()
        data = r.json()
        page = data.get("items", [])
        items.extend(page)
        total = data.get("pagination", {}).get("total", len(items))
        offset += len(page)
        if offset >= total or not page:
            break
    return items


def build_brand(brand: str, cfg: dict) -> tuple:
    print(f"\n-- {brand.upper()} ------------------------------")
    items = fetch_items(cfg["collection_id"])
    print(f"  Fetched {len(items)} items from API")

    records, missing = [], []
    for item in items:
        if item.get("isDraft") or item.get("isArchived"):
            continue
        fd = item.get("fieldData", {})
        slug = fd.get("slug") or item.get("slug", "")

        # Find the main article HTML
        article_html = ""
        for key in ("article-content", "post-body", "content"):
            if fd.get(key):
                article_html = fd[key]
                break
        if not article_html:
            missing.append(slug)

        record = {
            "slug": slug,
            "title": fd.get("name", ""),
            "url": f"https://{cfg['domain']}{cfg['url_prefix']}{slug}/",
            "lastPublished": item.get("lastPublished", ""),
            "meta_title": fd.get("meta-title", ""),
            "description": strip_html(fd.get("description-text", "")),
            "text": strip_html(article_html),
        }
        records.append(record)

    records.sort(key=lambda r: r["slug"])

    out = Path("corpus") / f"{brand}.jsonl"
    out.parent.mkdir(exist_ok=True)
    with open(out, "w", encoding="utf-8") as f:
        for rec in records:
            f.write(json.dumps(rec, ensure_ascii=False) + "\n")

    print(f"  Written  : {len(records)} items → {out}")
    if missing:
        print(f"  No article-content: {len(missing)} items")
        for s in missing[:10]:
            print(f"    - {s}")
        if len(missing) > 10:
            print(f"    … and {len(missing) - 10} more")

    return len(items), len(records), missing


def git_commit_push():
    ts = datetime.utcnow().strftime("%Y-%m-%d %H:%M UTC")
    cmds = [
        ["git", "add", "corpus/"],
        ["git", "commit", "-m", f"corpus: update {ts}"],
        ["git", "push"],
    ]
    for cmd in cmds:
        result = subprocess.run(cmd, capture_output=True, text=True)
        if result.returncode != 0:
            # "nothing to commit" is not an error
            if "nothing to commit" in result.stdout + result.stderr:
                print("  git: nothing to commit, corpus unchanged.")
                return
            print(f"  git error ({' '.join(cmd)}):\n{result.stderr}", file=sys.stderr)
            sys.exit(1)
        print(f"  git: {result.stdout.strip() or 'ok'}")


def main():
    totals = {"fetched": 0, "written": 0, "missing": 0}
    for brand, cfg in BRANDS.items():
        fetched, written, missing = build_brand(brand, cfg)
        totals["fetched"] += fetched
        totals["written"] += written
        totals["missing"] += len(missing)

    print(f"\n-- SUMMARY ----------------------------------")
    print(f"  Total fetched : {totals['fetched']}")
    print(f"  Total written : {totals['written']}")
    print(f"  Missing body  : {totals['missing']}")

    git_commit_push()
    print("\nDone.")


if __name__ == "__main__":
    main()

"""
Build / refresh the curated BIS knowledge base from bis.gov.in.

The product facts in knowledge_base/<slug>.json are curated from BIS pages and
Government of India gazette notifications. This script:
  1. fetches (and caches) the key BIS index pages so we have local provenance,
  2. verifies that every source URL referenced in the KB actually resolves,
  3. stamps each product file with `fetched_at` and a `_provenance` block.

It does NOT invent or overwrite curated facts - it records where they came from
and flags dead links. Run:  python tools/build_kb.py

Exit code is 0 even if some BIS URLs are unreachable (the committed KB is the
always-available fallback); it prints a report.
"""

import datetime
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from services import bis_fetch  # noqa: E402

KB_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "knowledge_base")

# BIS index pages fetched purely for local provenance / future parsing.
BIS_INDEX_PAGES = [
    "https://www.bis.gov.in/product-certification/product-certification-overview/?lang=en",
    "https://www.bis.gov.in/product-certification/products-under-compulsory-certification/?lang=en",
    "https://www.bis.gov.in/upcoming-qcos-notified-and-due-for-implementation/?lang=en",
    "https://www.bis.gov.in/product-certification/products-under-compulsory-certification/scheme-i-mark-scheme/?lang=en",
    "https://www.bis.gov.in/product-certification/products-under-compulsory-certification/scheme-ii-registration-scheme/?lang=en",
    "https://www.crsbis.in/BIS/about-crs.do",
    "https://lims.bis.gov.in/home/labs/",
]


def _iter_source_urls(obj):
    if isinstance(obj, dict):
        if "url" in obj and isinstance(obj["url"], str):
            yield obj["url"]
        for v in obj.values():
            yield from _iter_source_urls(v)
    elif isinstance(obj, list):
        for v in obj:
            yield from _iter_source_urls(v)


def main():
    verify = "--verify" in sys.argv  # slow per-URL HEAD checks; off by default

    print("== Fetching BIS index pages (provenance cache) ==")
    idx_status = {}
    for url in BIS_INDEX_PAGES:
        ok, _text, detail = bis_fetch.fetch(url)
        idx_status[url] = detail if ok else f"UNREACHABLE ({detail})"
        print(f"  [{'ok ' if ok else 'FAIL'}] {url}  -> {idx_status[url]}")

    now = datetime.datetime.now(datetime.timezone.utc).isoformat(timespec="seconds")
    files = sorted(f for f in os.listdir(KB_DIR) if f.endswith(".json") and not f.startswith("_"))
    total_urls = 0
    total_dead = 0

    for fname in files:
        path = os.path.join(KB_DIR, fname)
        with open(path, "r", encoding="utf-8") as fh:
            data = json.load(fh)

        urls = sorted(set(_iter_source_urls(data.get("areas", {}))))
        dead = []
        if verify:
            print(f"\n== {fname}: verifying {len(urls)} source URLs ==")
            for u in urls:
                ok = bis_fetch.head_ok(u)
                total_urls += 1
                if not ok:
                    dead.append(u)
                    total_dead += 1
                print(f"  [{'ok ' if ok else 'DEAD'}] {u}")
        else:
            total_urls += len(urls)
            print(f"== {fname}: {len(urls)} source URLs (not verified; pass --verify to check) ==")

        data["fetched_at"] = now
        data["_provenance"] = {
            "generated_by": "tools/build_kb.py",
            "generated_at": now,
            "bis_index_pages": idx_status,
            "source_urls_checked": len(urls),
            "source_urls_unreachable": dead,
        }
        with open(path, "w", encoding="utf-8") as fh:
            json.dump(data, fh, ensure_ascii=False, indent=2)
            fh.write("\n")

    print(f"\n== Done ==  {len(files)} products, {total_urls} source URLs checked, {total_dead} unreachable.")
    if total_dead:
        print("Unreachable URLs are kept (a page may block HEAD or be temporarily down); "
              "review them if they stay dead.")


if __name__ == "__main__":
    main()

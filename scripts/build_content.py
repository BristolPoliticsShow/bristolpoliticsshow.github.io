#!/usr/bin/env python3
"""Fetch the show's feeds and write the website's content JSON files.

Runs server-side (locally or in GitHub Actions) so the browser never has to
fetch the feeds directly, which avoids their missing CORS headers.

Sources:
  - WordPress feed (politicsthisweek) -> rich write-ups: Stories + "this week" banner
  - Radio4All podcast feed             -> ALL audio (full shows + interviews/clips)

Outputs:
  audio.json     - every recent audio item, grouped into weeks (full show + interviews)
  thisweek.json  - the latest show title/date and whether a new show is imminent
  stories.json   - individual stories (text + image/video) from each show's write-up
"""

import datetime as dt
import html
import json
import re
import sys
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET

from bs4 import BeautifulSoup

WP_FEED_URL = "https://politicsthisweek.gn.apc.org/feed/"
R4A_FEED_URL = "https://www.radio4all.net/podcast.xml?series=State+Of+The+City+reports"

AUDIO_OUTPUT = "audio.json"
THISWEEK_OUTPUT = "thisweek.json"
STORIES_OUTPUT = "stories.json"

ARCHIVE_DAYS = 183  # ~6 months, matching the mobile app
MAX_STORIES = 150

CONTENT_NS = "{http://purl.org/rss/1.0/modules/content/}encoded"

TITLE_SUFFIX = re.compile(
    r"\s*[–—-]\s*The Bristol Politics Show,\s*presented by Tony Gosling\s*$",
    re.IGNORECASE,
)
READ_MORE_SUFFIX = re.compile(r"\s*Read More\s*[»»]?\s*$", re.IGNORECASE)
URL_RE = re.compile(r"https?://\S+")
FULL_SHOW_DATE_RE = re.compile(r"full[- ]show[- ]?(\d{1,2})([A-Za-z]{3})(\d{2})", re.IGNORECASE)
LEADING_TAG_RE = re.compile(r"^(COMPLETE|EXCLUSIVE|UPDATE)\s+", re.IGNORECASE)

MONTHS = {m.lower(): i for i, m in enumerate(
    ["", "Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"])}

PROMO_IMAGE_HINTS = ("download-listen", "not-the-bcfm-politics-show-app", "cultureshop-450x187")
INTERNAL_LINK_HINTS = (
    "politicsthisweek", "bristolpoliticsshow.github.io", "apps.apple.com", "apkpure.com",
    "aurora-store", "play.google.com", "internet-radio.com/station",
    "radio4all.net/index.php/contributor",
)
STORY_END_MARKERS = (
    "radio4all download pages", "complete show and full interviews",
    "not the bcfm politics show cancelled",
)


def fetch(url: str) -> bytes:
    req = urllib.request.Request(url, headers={"User-Agent": "BristolPoliticsShow-site/1.0"})
    with urllib.request.urlopen(req, timeout=120) as resp:
        return resp.read()


def strip_html(text: str) -> str:
    text = re.sub(r"<[^>]+>", "", text or "")
    text = html.unescape(text)
    return re.sub(r"\s+", " ", text).strip()


def to_https(url: str) -> str:
    return "https://" + url[len("http://"):] if url.startswith("http://") else url


def parse_rss_date(s: str) -> dt.datetime | None:
    s = (s or "").strip()
    for fmt in ("%a, %d %b %Y %H:%M:%S %z", "%a, %d %b %Y %H:%M:%S %Z", "%a, %d %b %Y %H:%M:%S"):
        try:
            d = dt.datetime.strptime(s[:31], fmt)
            return d.replace(tzinfo=None)
        except ValueError:
            continue
    return None


def format_date(d: dt.datetime) -> str:
    return d.strftime("%A %-d %B %Y")


def clean_title(raw: str) -> str:
    return TITLE_SUFFIX.sub("", strip_html(raw)).strip()


# ---------- Radio4All audio archive ----------

def nice_name(url: str) -> str:
    fn = urllib.parse.unquote(url.rsplit("/", 1)[-1])
    fn = re.sub(r"\.mp3$", "", fn, flags=re.IGNORECASE)
    fn = fn.replace("_", " ").strip()
    return fn


def is_full_show(name: str) -> bool:
    n = name.lower()
    return "full-show" in n or "full show" in n or "ntbcfmps-full" in n


def show_date_from_name(name: str) -> dt.datetime | None:
    m = FULL_SHOW_DATE_RE.search(name)
    if not m:
        return None
    day, mon, yy = m.group(1), m.group(2).lower(), m.group(3)
    if mon not in MONTHS:
        return None
    try:
        return dt.datetime(2000 + int(yy), MONTHS[mon], int(day))
    except ValueError:
        return None


def week_start(d: dt.datetime) -> dt.datetime:
    monday = d - dt.timedelta(days=(d.weekday()))
    return dt.datetime(monday.year, monday.month, monday.day)


def parse_radio4all(xml_bytes: bytes) -> list[dict]:
    root = ET.fromstring(xml_bytes)
    items = root.findall(".//item")
    cutoff = dt.datetime.now() - dt.timedelta(days=ARCHIVE_DAYS)

    seen: set[str] = set()
    episodes: list[dict] = []
    for item in items:
        enc = item.find("enclosure")
        url = enc.get("url") if enc is not None else None
        if not url or url in seen:
            continue
        seen.add(url)
        pub = parse_rss_date(item.findtext("pubDate", default="")) or dt.datetime.now()
        if pub < cutoff:
            continue
        raw = nice_name(url)
        full = is_full_show(raw)
        name = "Full show" if full else LEADING_TAG_RE.sub("", raw).strip()
        episodes.append({
            "name": name or raw,
            "url": to_https(url),
            "isFullShow": full,
            "pubDate": pub,
        })

    # Group into Monday-start weeks.
    groups: dict[dt.date, list[dict]] = {}
    for ep in episodes:
        key = week_start(ep["pubDate"]).date()
        groups.setdefault(key, []).append(ep)

    weeks = []
    for key in sorted(groups, reverse=True):
        eps = groups[key]
        # Label the week from the full show's filename date if we have one.
        label_date = None
        for ep in eps:
            d = show_date_from_name(nice_name(ep["url"]))
            if d:
                label_date = d
                break
        if label_date is None:
            label_date = max(ep["pubDate"] for ep in eps)
        # Full show first, then interviews alphabetically.
        eps.sort(key=lambda e: (not e["isFullShow"], e["name"].lower()))
        weeks.append({
            "date": format_date(label_date),
            "items": [{"name": e["name"], "url": e["url"], "isFullShow": e["isFullShow"]}
                      for e in eps],
        })
    return weeks


# ---------- WordPress stories ----------

def youtube_id(url: str) -> str | None:
    if not url:
        return None
    for pat in (r"[?&]v=([A-Za-z0-9_\-]{6,})", r"youtu\.be/([A-Za-z0-9_\-]{6,})",
                r"youtube(?:-nocookie)?\.com/embed/([A-Za-z0-9_\-]{6,})"):
        m = re.search(pat, url)
        if m:
            return m.group(1)
    return None


def split_sentences(text: str) -> list[str]:
    return [s.strip() for s in re.findall(r"[^.!?]+[.!?]+[\"'’”\)]*|\S[^.!?]*$", text or "") if s.strip()]


def truncate(text: str, limit: int) -> str:
    text = text.strip()
    return (text[:limit].rstrip() + "…") if len(text) > limit else text


def title_and_subtitle(text: str) -> tuple[str, str]:
    sents = split_sentences(text)
    if not sents:
        return truncate(text, 110), ""
    title = truncate(sents[0], 120)
    subtitle = truncate(" ".join(sents[1:3]), 170)
    return title, subtitle


def parse_stories_from_content(content_html: str, date_display: str) -> list[dict]:
    soup = BeautifulSoup(content_html, "html.parser")
    blocks = [n for n in soup.children if getattr(n, "name", None)]
    stories: list[dict] = []
    started = False
    pending_imgs: list[str] = []
    pending_vids: list[str] = []

    def take_media(el):
        imgs, vids = [], []
        for im in el.find_all("img"):
            src = (im.get("src") or im.get("data-src") or "").strip()
            if src and not any(h in src.lower() for h in PROMO_IMAGE_HINTS):
                imgs.append(to_https(src))
        for fr in el.find_all(["iframe", "embed"]):
            vid = youtube_id(fr.get("src") or fr.get("data-src") or "")
            if vid:
                vids.append(vid)
        for a in el.find_all("a", href=True):
            vid = youtube_id(a["href"])
            if vid:
                vids.append(vid)
        for m in URL_RE.findall(el.get_text(" ")):
            vid = youtube_id(m)
            if vid:
                vids.append(vid)
        return imgs, vids

    for el in blocks:
        name = el.name
        heading = el.get_text(" ").strip().lower() if name in ("h1", "h2", "h3", "h4") else ""
        if not started:
            if name == "h3" and "part one" in heading:
                started = True
            continue
        if heading and any(m in heading for m in STORY_END_MARKERS):
            break
        if name in ("h1", "h2", "h3", "h4"):
            continue

        imgs, vids = take_media(el)
        raw_text = re.sub(r"\s+", " ", el.get_text(" ")).strip()
        text = re.sub(r"\s+", " ", URL_RE.sub("", raw_text)).strip()
        if len(text) >= 40:
            all_imgs = pending_imgs + imgs
            all_vids = pending_vids + vids
            links, seen = [], set()
            for a in el.find_all("a", href=True):
                href = a["href"].strip()
                if (not href.startswith("http") or youtube_id(href)
                        or href in seen
                        or any(h in href.lower() for h in INTERNAL_LINK_HINTS)):
                    continue
                label = re.sub(r"\s+", " ", a.get_text()).strip()
                # Tony sometimes wraps a whole paragraph in a link; don't show that
                # as link text — fall back to the domain for a tidy reference.
                if len(label) < 2 or len(label) > 80:
                    label = urllib.parse.urlparse(href).netloc.replace("www.", "")
                if label:
                    seen.add(href)
                    links.append({"label": label, "url": href})
            title, subtitle = title_and_subtitle(text)
            stories.append({
                "date": date_display,
                "title": title,
                "subtitle": subtitle,
                "text": text,
                "image": all_imgs[0] if all_imgs else None,
                "videoId": all_vids[0] if all_vids else None,
                "links": links[:8],
            })
            pending_imgs, pending_vids = [], []
        else:
            pending_imgs += imgs
            pending_vids += vids
    return stories


def build_wordpress(now: str) -> bool:
    """Stories + this-week banner from the WordPress feed. Returns success."""
    try:
        root = ET.fromstring(fetch(WP_FEED_URL))
    except Exception as exc:  # noqa: BLE001
        print(f"WARN: WordPress feed failed: {exc}", file=sys.stderr)
        return False

    channel = root.find("channel")
    items = channel.findall("item") if channel is not None else root.findall(".//item")
    stories: list[dict] = []
    latest_show = None
    has_upcoming = False

    for idx, item in enumerate(items):
        enc = item.find("enclosure")
        audio_url = enc.get("url") if enc is not None else None
        title = clean_title(item.findtext("title", default="").strip())
        pub = parse_rss_date(item.findtext("pubDate", default=""))
        date_display = format_date(pub) if pub else ""
        if audio_url:
            if latest_show is None:
                latest_show = {"title": title or "The Bristol Politics Show", "date": date_display}
            content_html = item.findtext(CONTENT_NS) or item.findtext("description") or ""
            stories.extend(parse_stories_from_content(content_html, date_display))
        elif idx == 0:
            has_upcoming = True

    with open(THISWEEK_OUTPUT, "w", encoding="utf-8") as fh:
        json.dump({"updated": now, "hasUpcoming": has_upcoming, "latestShow": latest_show},
                  fh, ensure_ascii=False, indent=2)
        fh.write("\n")
    with open(STORIES_OUTPUT, "w", encoding="utf-8") as fh:
        json.dump({"updated": now, "count": len(stories[:MAX_STORIES]),
                   "stories": stories[:MAX_STORIES]}, fh, ensure_ascii=False, indent=2)
        fh.write("\n")
    print(f"Wrote {THISWEEK_OUTPUT} (upcoming={has_upcoming}), {STORIES_OUTPUT} "
          f"({len(stories)} stories)")
    return True


def build_audio(now: str) -> bool:
    try:
        weeks = parse_radio4all(fetch(R4A_FEED_URL))
    except Exception as exc:  # noqa: BLE001
        print(f"WARN: Radio4All feed failed: {exc}", file=sys.stderr)
        return False
    if not weeks:
        print("WARN: Radio4All feed produced no audio", file=sys.stderr)
        return False
    total = sum(len(w["items"]) for w in weeks)
    with open(AUDIO_OUTPUT, "w", encoding="utf-8") as fh:
        json.dump({"updated": now, "weekCount": len(weeks), "itemCount": total, "weeks": weeks},
                  fh, ensure_ascii=False, indent=2)
        fh.write("\n")
    print(f"Wrote {AUDIO_OUTPUT} ({len(weeks)} weeks, {total} audio items)")
    return True


def main() -> int:
    now = dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    wp_ok = build_wordpress(now)
    audio_ok = build_audio(now)
    # Succeed if at least one source updated; fail only if both failed.
    return 0 if (wp_ok or audio_ok) else 1


if __name__ == "__main__":
    raise SystemExit(main())

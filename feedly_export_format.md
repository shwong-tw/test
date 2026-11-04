# Feedly Export: JSON Format

This document describes the JSON files produced by exporting a Feedly account
(free plan) through Feedly's API (`https://api.feedly.com/v3`) from the
browser console. It is meant for a program or session that **imports** this
data elsewhere.

There are two kinds of files:

| File | Contents | Produced from |
|---|---|---|
| `feedly-subscriptions.json` | List of all subscribed feeds (129 at export time) | `GET /v3/subscriptions` (raw Feedly response, unmodified) |
| `NNN_<FeedTitle>.json` (one per feed) | All articles Feedly has stored for that feed | `GET /v3/streams/contents` (paged; **reshaped** by the export script) |

All files are UTF-8 JSON.

---

## 1. `feedly-subscriptions.json`

**Top level:** a JSON **array** of subscription objects. This is Feedly's raw
API response, so fields vary between feeds and Feedly may add others. An
importer must treat every field except `id` as optional.

### Example

```json
[
  {
    "id": "feed/https://example.com/rss.xml",
    "title": "Example Blog",
    "website": "https://example.com",
    "description": "A blog about examples",
    "categories": [
      { "id": "user/1234abcd-0000-0000-0000-000000000000/category/5678ef00-0000-0000-0000-000000000000", "label": "Tech" }
    ],
    "added": 1420070400000,
    "updated": 1730700000000,
    "subscribers": 5321,
    "velocity": 3.5,
    "language": "en",
    "iconUrl": "https://example.com/icon.png",
    "visualUrl": "https://example.com/visual.png",
    "topics": ["tech", "programming"]
  }
]
```

### Fields

| Field | Type | Always present | Meaning |
|---|---|---|---|
| `id` | string | yes | Feedly stream ID: the literal prefix `feed/` followed by the RSS/Atom feed URL. **Strip `feed/` to get the feed URL.** |
| `title` | string | usually | Feed title (the user's custom name if they renamed it) |
| `website` | string | often | URL of the site's home page |
| `description` | string | sometimes | Feed description |
| `categories` | array of `{id, label}` | usually (can be empty `[]`) | The user's Feedly categories (folders) containing this feed. `label` is the display name. A feed may be in several categories. |
| `added` | number | usually | When the user subscribed, in **Unix epoch milliseconds** |
| `updated` | number | sometimes | When the feed last updated, in epoch milliseconds |
| `subscribers` | number | sometimes | Feedly-wide follower count (not user data) |
| `velocity` | number | sometimes | Average articles per week (not user data) |
| `language` | string | sometimes | Language code, e.g. `"en"` |
| `iconUrl`, `visualUrl`, `coverUrl` | string | sometimes | Feed images |
| `topics` | array of string | sometimes | Feedly's topic tags (not user data) |

Other fields (e.g. `sortid`, `contentType`, `partial`, `state`) may also
appear; they can be ignored.

---

## 2. Per-feed article files: `NNN_<FeedTitle>.json`

### File naming

- `NNN` is the zero-padded index of the feed in the subscriptions array
  (`000`, `001`, … `128`), followed by `_` and the feed title.
- In the title, every run of characters outside `[A-Za-z0-9_.-]` is replaced
  with `_`, and the base name is cut to 80 characters before `.json`.
- **Don't rely on the file name** to identify the feed. Use the `feedUrl`
  field inside the file.
- If the browser saved a file under a name that already existed, it may have
  added a suffix like `(1)`.

### Top-level structure

A JSON **object** (not an array):

```json
{
  "feed": "Example Blog",
  "feedUrl": "https://example.com/rss.xml",
  "website": "https://example.com",
  "categories": ["Tech"],
  "count": 2,
  "items": [
    {
      "title": "Some article title",
      "published": "2016-03-14T09:30:00.000Z",
      "url": "https://example.com/2016/03/some-article",
      "author": "Jane Doe",
      "summary": "<p>Short HTML excerpt…</p>",
      "content": "<p>Full HTML article text…</p>"
    }
  ]
}
```

| Field | Type | Meaning |
|---|---|---|
| `feed` | string or absent | Feed title (copied from the subscription's `title`) |
| `feedUrl` | string | RSS/Atom URL of the feed (the subscription `id` with `feed/` removed). **Use this as the key to join with `feedly-subscriptions.json`** (`"feed/" + feedUrl === subscription.id`). |
| `website` | string or absent | Site home page |
| `categories` | array of string | Category **labels** (names only, no IDs). May be `[]`. |
| `count` | number | Number of entries in `items` |
| `items` | array | Articles, **newest first** (Feedly's default order) |

### Article objects (`items[]`)

| Field | Type | Meaning |
|---|---|---|
| `title` | string or absent | Article title (plain text, may contain HTML entities) |
| `published` | string or `null` | Publication time as an **ISO 8601 UTC string** (e.g. `2016-03-14T09:30:00.000Z`). `null` if Feedly had no date. |
| `url` | string or absent | Link to the original article. Taken from Feedly's `canonicalUrl`, or else the first `alternate[].href`. |
| `author` | string or absent | Author name |
| `summary` | string or absent | Article excerpt, as an **HTML string** |
| `content` | string or absent | Full article body, as an **HTML string**. Absent if the feed only publishes summaries, or if the export ran with `INCLUDE_CONTENT = false`. |

**Notes for importers**

- **Missing fields:** they are **omitted** from the JSON, not set to `null`
  (except `published`). `JSON.stringify` drops `undefined` values. Handle
  missing keys.
- **Often only one of `summary` / `content` is present.** For an article
  body, use `content`, then fall back to `summary`.
- **HTML:** `summary` and `content` are raw HTML from the publisher. They may
  contain relative links, `<img>` tags pointing to external hosts, scripts or
  iframes. **Sanitize them before rendering.**
- **No unique article ID was exported.** To de-duplicate, use `url`; if it's
  missing, use `title` + `published`.
- **History depth varies by feed.** Some files go back about 10 years; small
  or dead feeds may have few or zero items (`count: 0`, `items: []`).
- **Possible gaps:** if the export was interrupted and resumed with a later
  `START` index, some feed indexes may be missing or duplicated. Check the
  `NNN` indexes against the 129 subscriptions.
- **Not included:** read/unread state, saved/"read later" items, user tags,
  and images (`visual`).

---

## 3. Suggested import procedure

1. Load `feedly-subscriptions.json`. Build a map from `id.replace(/^feed\//, "")`
   to the subscription. This gives the list of feeds and the category tree
   (`categories[].label`).
2. For each `NNN_*.json` file, parse it, look up the subscription by
   `feedUrl`, and import its `items`.
3. Convert `published` (ISO string) to the target system's datetime type.
   Convert `added` / `updated` in the subscriptions file from epoch
   milliseconds.
4. De-duplicate articles by `url` within each feed.

### Minimal Python reader

```python
import json, glob, os
from datetime import datetime

export_dir = "path/to/downloads"

with open(os.path.join(export_dir, "feedly-subscriptions.json"), encoding="utf-8") as f:
    subs = {s["id"].removeprefix("feed/"): s for s in json.load(f)}

for path in sorted(glob.glob(os.path.join(export_dir, "[0-9][0-9][0-9]_*.json"))):
    with open(path, encoding="utf-8") as f:
        data = json.load(f)
    sub = subs.get(data["feedUrl"], {})
    categories = data.get("categories", [])
    for item in data["items"]:
        published = (datetime.fromisoformat(item["published"].replace("Z", "+00:00"))
                     if item.get("published") else None)
        body = item.get("content") or item.get("summary") or ""
        # ... insert (data["feedUrl"], categories, item.get("title"), item.get("url"),
        #             item.get("author"), published, body) into the target store
```

#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Daily AI Builders Digest: fetch feeds -> build material pack -> DeepSeek remix
-> save digests/YYYY-MM-DD.md -> push to Feishu webhook as interactive card(s).

Run inside GitHub Actions; requires env DEEPSEEK_API_KEY and FEISHU_WEBHOOK.
Digest date convention (same as the retro pipeline): the feed committed on day X
covers mostly day X-1 content, so the digest produced from a run on day X is
dated X-1.
"""
import io
import json
import os
import sys
import urllib.request
import urllib.error
from datetime import datetime, timedelta, timezone

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
FEEDS = os.path.join(BASE, "feeds")
DIGESTS = os.path.join(BASE, "digests")
PROMPTS = os.path.join(BASE, "prompts")
STATE_PATH = os.path.join(BASE, "state.json")

FOOTER = "Generated through the Follow Builders skill: https://github.com/zarazhangrui/follow-builders"

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")


def load_json(path, default):
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return default


def load_state():
    st = load_json(STATE_PATH, {})
    st.setdefault("podcasts", [])   # processed podcast guids
    st.setdefault("blogs", [])      # processed blog urls
    st.setdefault("last_digest", None)
    return st


def save_state(st):
    with open(STATE_PATH, "w", encoding="utf-8") as f:
        json.dump(st, f, ensure_ascii=False, indent=1)


def http_post(url, payload, timeout=300):
    data = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(url, data=data, headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read().decode("utf-8"))


# ---------------------------------------------------------------- material ---

def build_material(digest_date, x_feed, pod_feed, blog_feed, state):
    parts = []
    tweets = []
    for b in x_feed.get("x", []):
        kept = [t for t in b.get("tweets", []) if t.get("url")]
        if kept:
            tweets.append({
                "name": b.get("name", ""),
                "handle": b.get("handle", ""),
                "bio": b.get("bio", ""),
                "tweets": [{"createdAt": t.get("createdAt"), "url": t.get("url"),
                            "likes": t.get("likes"), "text": t.get("text", "")} for t in kept],
            })

    # podcasts: only episodes not yet processed (guid = unique id)
    pods = []
    for p in pod_feed.get("podcasts", []):
        key = p.get("guid") or (p.get("name", "") + "|" + p.get("title", ""))
        if key in state["podcasts"] or not p.get("url"):
            continue
        pods.append(p)

    # blogs: only entries not yet processed (url = unique id)
    blogs = []
    for bl in blog_feed.get("blogs", []):
        key = bl.get("url") or bl.get("title", "")
        if key in state["blogs"] or not bl.get("url"):
            continue
        blogs.append(bl)

    parts.append("=== TWEETS (per builder; bio describes their role) ===")
    parts.append(json.dumps(tweets, ensure_ascii=False, indent=1))

    if pods:
        parts.append("\n=== PODCAST EPISODES (full transcript follows each) ===")
        for p in pods:
            tr = (p.get("transcript") or "")[:60000]
            parts.append(json.dumps({
                "name": p.get("name"), "title": p.get("title"), "url": p.get("url"),
                "publishedAt": p.get("publishedAt"), "transcript": tr,
            }, ensure_ascii=False))

    if blogs:
        parts.append("\n=== OFFICIAL BLOG POSTS ===")
        for bl in blogs:
            parts.append(json.dumps({
                "source": bl.get("source"), "name": bl.get("name"), "title": bl.get("title"),
                "url": bl.get("url"), "author": bl.get("author"),
                "content": (bl.get("content") or bl.get("description") or "")[:10000],
            }, ensure_ascii=False))

    return tweets, pods, blogs, "\n".join(parts)


# ------------------------------------------------------------------- remix ---

def call_deepseek(api_key, system_prompt, user_content):
    payload = {
        "model": "deepseek-chat",
        "messages": [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_content},
        ],
        "temperature": 0.7,
        "max_tokens": 8192,
    }
    last_err = None
    for attempt in range(3):
        try:
            resp = http_post(
                "https://api.deepseek.com/chat/completions",
                {**payload, **({"stream": False})},
                timeout=420,
            )
            return resp["choices"][0]["message"]["content"]
        except (urllib.error.URLError, KeyError, OSError) as e:
            last_err = e
            print(f"deepseek attempt {attempt+1} failed: {e}", file=sys.stderr)
    raise RuntimeError(f"DeepSeek failed after retries: {last_err}")


def load_prompt(name):
    with open(os.path.join(PROMPTS, name), encoding="utf-8") as f:
        return f.read()


def build_system_prompt():
    return "\n\n".join([
        load_prompt("digest-intro.md"),
        load_prompt("summarize-tweets.md"),
        load_prompt("summarize-podcast.md"),
        load_prompt("translate.md"),
        f"""OUTPUT FORMAT (strict):
- First line exactly: # AI Builders Digest — {DIGEST_DATE}  (the em dash appears ONLY in this header line; never use an em dash anywhere in the body)
- Then sections in order: "## X / TWITTER", "## OFFICIAL BLOGS" (only if blogs exist), "## PODCASTS" (only if podcasts exist). Omit empty sections entirely.
- Bilingual, interleaved: every English paragraph is immediately followed by its Chinese translation, then the next item. Keep terms like AI/LLM/GPU/RAG/agent/benchmark/token/context window in English inside Chinese text.
- NEVER invent content. Every item MUST carry its URL from the material. No URL = do not include the item.
- Authors: full name + role derived from the bio field. Never write @handles in the text.
- Threads of multiple tweets = one summarized piece with one URL.
- Skip non-substantive tweets (memes, promos, politics, one-line banter).
- Podcast entries: 200-400 English words starting with "The Takeaway:", include one verbatim quote from the transcript, use the exact title and exact URL given in the material.
- The LAST line of the output must be exactly: {FOOTER}
- Output ONLY the markdown digest, no explanations before or after.""",
    ])


# ------------------------------------------------------------------ feishu ---

def feishu_send_md(webhook, title, md_body):
    """Send markdown as Feishu interactive card(s), chunked if needed."""
    CHUNK = 24000
    chunks = []
    rest = md_body
    while rest:
        if len(rest) <= CHUNK:
            chunks.append(rest)
            break
        cut = rest.rfind("\n", 0, CHUNK)
        if cut < CHUNK // 2:
            cut = CHUNK
        chunks.append(rest[:cut])
        rest = rest[cut:].lstrip("\n")

    for i, chunk in enumerate(chunks):
        elements = [{"tag": "div", "text": {"tag": "lark_md", "content": chunk}}]
        card = {
            "config": {"wide_screen_mode": True},
            "elements": elements + [{"tag": "hr"}, {
                "tag": "note",
                "elements": [{"tag": "plain_text", "content": FOOTER}],
            }],
        }
        if i == 0:
            card["header"] = {"template": "blue",
                              "title": {"tag": "plain_text", "content": title}}
        else:
            elements.insert(0, {"tag": "div", "text": {
                "tag": "lark_md", "content": f"**{title} ({i+1}/{len(chunks)})**"}})
        resp = http_post(webhook, {"msg_type": "interactive", "card": card}, timeout=60)
        code = resp.get("code", resp.get("StatusCode", "?"))
        print(f"feishu part {i+1}/{len(chunks)} -> code {code}")
        if str(code) not in ("0", "200", "Success"):
            raise RuntimeError(f"Feishu rejected message: {resp}")


# --------------------------------------------------------------------- main ---

if __name__ == "__main__":
    now_utc = datetime.now(timezone.utc)
    DIGEST_DATE = (now_utc - timedelta(days=1)).strftime("%Y-%m-%d")

    x_feed = load_json(os.path.join(FEEDS, "feed-x.json"), {})
    pod_feed = load_json(os.path.join(FEEDS, "feed-podcasts.json"), {})
    blog_feed = load_json(os.path.join(FEEDS, "feed-blogs.json"), {})
    state = load_state()

    tweets, pods, blogs, material = build_material(
        DIGEST_DATE, x_feed, pod_feed, blog_feed, state)
    print(f"digest {DIGEST_DATE}: builders={len(tweets)} pods={len(pods)} blogs={len(blogs)}")

    os.makedirs(DIGESTS, exist_ok=True)
    out_path = os.path.join(DIGESTS, f"{DIGEST_DATE}.md")

    if not tweets and not pods and not blogs:
        digest = (f"# AI Builders Digest — {DIGEST_DATE}\n\n"
                  f"当日无实质内容更新 / No substantive updates today.\n\n{FOOTER}\n")
    else:
        digest = call_deepseek(
            os.environ["DEEPSEEK_API_KEY"],
            build_system_prompt(),
            f"Digest date: {DIGEST_DATE}\n\nMATERIAL JSON:\n{material}",
        ).strip()
        if not digest.startswith("# AI Builders Digest"):
            digest = f"# AI Builders Digest — {DIGEST_DATE}\n\n{digest}"
        if FOOTER not in digest.splitlines()[-1]:
            digest += "\n\n" + FOOTER

    with open(out_path, "w", encoding="utf-8", newline="\n") as f:
        f.write(digest + "\n")
    print(f"written: {out_path} ({len(digest)} chars)")

    # mark processed
    state["podcasts"] += [p.get("guid") or (p.get("name", "") + "|" + p.get("title", "")) for p in pods]
    state["blogs"] += [bl.get("url") or bl.get("title", "") for bl in blogs]
    state["last_digest"] = DIGEST_DATE
    # keep state bounded
    state["podcasts"] = state["podcasts"][-400:]
    state["blogs"] = state["blogs"][-400:]
    save_state(state)

    webhook = os.environ.get("FEISHU_WEBHOOK", "")
    if webhook:
        body = "\n".join(digest.splitlines()[1:]).strip()  # drop H1 (card header carries it)
        feishu_send_md(webhook, f"AI Builders Digest — {DIGEST_DATE}", body)
    else:
        print("FEISHU_WEBHOOK not set; skipped delivery")

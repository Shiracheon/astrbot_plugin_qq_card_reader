"""Parse share metadata locally; never fetch links or infer media contents."""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from itertools import chain
from urllib.parse import parse_qs, parse_qsl, urlencode, urlsplit, urlunsplit

MAX_JSON_LENGTH = 131072
MAX_FIELD_LENGTH = 600

# Match field names, not their values. Unknown fields are never guessed to be content.
USEFUL_FIELDS = {
    "来源": {"tag", "sourcename", "appname", "platformname", "sitename", "来源", "平台"},
    "标题": {"title", "headline", "subject", "caption", "maintitle", "videotitle",
           "songname", "songtitle", "articletitle", "posttitle", "标题"},
    "作者": {"author", "artist", "singer", "creator", "uploader", "upname", "byline",
           "作者", "歌手", "发布者"},
    "描述": {"desc", "description", "summary", "intro", "introduction", "abstract",
           "subtitle", "brief", "描述", "简介", "摘要", "副标题"},
    "正文节选": {"content", "text", "body", "articlecontent", "postcontent", "正文", "内容", "文本"},
    "链接": {"jumpurl", "qqdocurl", "url", "shareurl", "link", "weburl", "targeturl",
           "h5url", "detailurl", "contenturl", "链接", "分享链接"},
    "提示": {"prompt"},
}
NOISE_FIELDS = {
    "config", "extra", "ext", "headers", "request", "analytics", "tracking", "statistics",
    "stats", "log", "debug", "auth", "layout", "style", "styles", "theme", "sender",
    "preview", "cover", "coverurl", "icon", "tagicon", "avatar", "image", "images",
    "imageurl", "pic", "picurl", "musicurl", "audiourl", "videourl",
}


def _field_name(key: str) -> str:
    return re.sub(r"[\W_]+", "", key.casefold())


def _useful_role(key: str, inherited: str = "") -> str:
    name = _field_name(key)
    if inherited in ("作者", "来源") and name in ("name", "nickname", "displayname", "名称", "昵称"):
        return inherited
    return next((role for role, fields in USEFUL_FIELDS.items() if name in fields), "")


def _private_key(key: str) -> bool:
    name = re.sub(r"[^a-z0-9]", "", key.casefold())
    return any(word in name for word in ("token", "secret", "password", "passwd", "cookie",
                                        "authorization", "sessionid", "apikey", "accesskey", "credential")) or name in (
        "hosteuin", "skey", "pskey", "sessdata", "bilijct",
    )


def _generic_text(value: str) -> str:
    text = clean_text(value, 4096)
    if not text.lower().startswith(("https://", "http://")):
        return clean_text(text)
    url = safe_url(text)
    if not url:
        return "[无效链接]"
    parts = urlsplit(url)
    query = urlencode([(key, item) for key, item in parse_qsl(parts.query, keep_blank_values=True)
                       if not _private_key(key)])
    fragment = parts.fragment
    if "?" in fragment:
        route, fragment_query = fragment.split("?", 1)
        fragment = route + "?" + urlencode([
            (key, item) for key, item in parse_qsl(fragment_query, keep_blank_values=True)
            if not _private_key(key)
        ])
    return urlunsplit((parts.scheme, parts.netloc, parts.path, query, fragment))


def clean_text(value: object, limit: int = MAX_FIELD_LENGTH) -> str:
    if not isinstance(value, str):
        return ""
    # Keep metadata on one line so it cannot change the surrounding labels.
    return re.sub(r"\s+", " ", value.replace("\x00", "")).strip()[:limit]


def safe_url(value: object) -> str:
    text = clean_text(value, 4096)
    if not text:
        return ""
    try:
        parts = urlsplit(text)
        if parts.scheme not in ("https", "http") or not parts.hostname:
            return ""
        if parts.username or parts.password or any(c in text for c in '<>"\\'):
            return ""
        # Access the port to reject malformed URLs, even though we don't fetch it.
        parts.port
    except ValueError:
        return ""
    return text


def _host(url: str, domain: str) -> bool:
    host = urlsplit(url).hostname or ""
    return host == domain or host.endswith("." + domain)


def normalize_url(url: str, platform: str) -> str:
    """Remove known share tracking parameters without resolving short links."""
    if not url:
        return ""
    parts = urlsplit(url)
    if platform == "netease" and _host(url, "music.163.com"):
        # Desktop links may put the route and query after #/song?id=...
        route = urlsplit(parts.fragment) if parts.fragment.startswith("/") else parts
        song_id = parse_qs(route.query).get("id", [""])[0]
        if route.path.rstrip("/") in ("/song", "/m/song") and song_id.isdigit():
            return "https://music.163.com/song?id=" + song_id
    if platform == "qqmusic" and _host(url, "y.qq.com"):
        # Preserve songmid/songid, short-link codes, type and unknown route fields.
        tracking = {"platform", "appshare", "appversion", "hosteuin", "appsongtype", "_wv", "source", "adtag"}
        kept = [(key, value) for key, value in parse_qsl(parts.query, keep_blank_values=True)
                if key.casefold() not in tracking]
        return urlunsplit((parts.scheme, parts.netloc, parts.path, urlencode(kept), parts.fragment))
    if platform == "bilibili" and _host(url, "b23.tv"):
        return urlunsplit((parts.scheme, parts.netloc, parts.path, "", ""))
    if platform == "bilibili" and _host(url, "bilibili.com"):
        if re.fullmatch(r"/video/(?:BV[0-9A-Za-z]+|av[0-9]+)/?", parts.path):
            query = parse_qs(parts.query)
            kept = {key: query[key][0] for key in ("p", "t") if key in query}
            return urlunsplit((parts.scheme, parts.netloc, parts.path, urlencode(kept), ""))
    if platform == "heybox" and _host(url, "xiaoheihe.cn"):
        # Retain the post ID, share route and any unknown content parameters.
        # A sender's share-session/source fields are unnecessary model context.
        kept = [(key, value) for key, value in parse_qsl(parts.query, keep_blank_values=True)
                if key not in ("h_session_id", "h_src")]
        return urlunsplit((parts.scheme, parts.netloc, parts.path, urlencode(kept), parts.fragment))
    return url


@dataclass(frozen=True)
class Card:
    platform: str
    title: str
    description: str = ""
    url: str = ""
    preview: str = ""
    content: str = ""

    @property
    def identity(self) -> tuple[str, str, str, str, str]:
        return self.platform, self.title, self.description, self.url, self.content

    def render(self) -> str:
        label = {
            "netease": "网易云音乐分享卡片",
            "qqmusic": "QQ音乐分享卡片",
            "bilibili": "B站分享卡片",
            "heybox": "小黑盒分享卡片",
            "json": "JSON消息转文字",
        }.get(self.platform, "分享卡片")
        rows = [f"[{label}]", f"标题：{self.title or '卡片未提供标题'}"]
        if self.description:
            rows.append(f"{'歌手/描述' if self.platform in ('netease', 'qqmusic') else '描述'}：{self.description}")
        if self.url:
            rows.append(f"链接：{self.url}")
        # A preview URL is metadata, not a downloaded image or evidence of contents.
        if self.preview:
            rows.append(f"封面地址：{self.preview}")
        if self.content:
            rows.append("字段内容：\n" + self.content)
        return "\n".join(rows)


def parse_card(payload: object) -> Card | None:
    if isinstance(payload, str):
        if len(payload) > MAX_JSON_LENGTH:
            return None
        try:
            payload = json.loads(payload)
        except (ValueError, RecursionError):
            return None
    if not isinstance(payload, dict):
        return None
    meta = payload.get("meta")
    if not isinstance(meta, dict):
        return None
    # QQ can deliver music shares as meta.news or meta.music.
    # The native music card's musicUrl is playback metadata; jumpUrl is the share.
    for key in ("music", "news", "detail_1"):
        fields = meta.get(key)
        if not isinstance(fields, dict):
            continue
        url = safe_url(fields.get("jumpUrl") if key in ("news", "music") else fields.get("qqdocurl"))
        tag = clean_text(fields.get("tag"))
        title = clean_text(fields.get("title"))
        desc = clean_text(fields.get("desc"))
        app_id = str(fields.get("appid", ""))
        if (url and _host(url, "music.163.com")) or tag == "网易云音乐" or app_id == "100495085":
            platform = "netease"
        elif (url and _host(url, "y.qq.com")) or tag == "QQ音乐" or app_id == "100497308":
            platform = "qqmusic"
        elif (
            (url and (_host(url, "b23.tv") or _host(url, "bilibili.com")))
            or tag == "哔哩哔哩"
            or (key == "detail_1" and (app_id == "1109937557" or title == "哔哩哔哩"))
        ):
            platform = "bilibili"
        elif (url and _host(url, "xiaoheihe.cn")) or tag == "小黑盒" or app_id == "1105910806":
            platform = "heybox"
        else:
            continue
        if key == "detail_1" and platform == "bilibili":
            # miniapp.title is the app name, miniapp.desc is the shared item title.
            title, desc = desc or title, ""
        if not title and not desc and not url:
            continue
        preview = safe_url(fields.get("preview"))
        return Card(platform, title, desc, normalize_url(url, platform), preview)
    return None


def parse_generic_json(payload: object, max_chars: int = 3000, max_depth: int = 6,
                       extract_only: bool = True) -> Card:
    """Select useful fields by default; optionally retain the bounded full expansion."""
    max_chars = max(500, min(int(max_chars), 12000))
    max_depth = max(1, min(int(max_depth), 10))
    if isinstance(payload, str):
        if len(payload) > MAX_JSON_LENGTH:
            return Card("json", "超大 JSON 消息", content="JSON 超过解析大小上限，未展开内容。")
        try:
            payload = json.loads(payload)
        except (ValueError, RecursionError):
            # Unparsed raw strings cannot be reliably redacted; report their state.
            return Card("json", "无法解析的 JSON 消息", content="收到 JSON 组件，但数据格式异常，未展开原始内容。")

    rows: list[str] = []
    size = 0
    visited = 0
    truncated = False
    title = ""
    selected: dict[str, list[str]] = {}
    preferred = ("title", "headline", "meta", "news", "music", "detail_1", "author", "artist",
                 "desc", "description", "summary", "content", "text", "body", "jumpUrl",
                 "qqdocurl", "url", "data", "result", "payload", "items", "prompt")

    def add(path: str, text: str) -> None:
        nonlocal size, truncated
        line = f"{path or '$'}：{text}"
        remaining = max_chars - size - 50  # Reserve room for a truncation notice.
        if len(line) + 1 > remaining:
            truncated = True
            suffix = " [字段已截断]"
            if remaining <= len(suffix) + 1:
                return
            line = line[:remaining - len(suffix) - 1] + suffix
        rows.append(line)
        size += len(line) + 1

    def walk(value: object, path: str, depth: int, field: str = "", inherited: str = "") -> None:
        nonlocal visited, truncated, title
        if visited >= 200 or size >= max_chars - 100:
            truncated = True
            return
        visited += 1
        if _private_key(field):
            if not extract_only:
                add(path, "[已隐藏]")
            return
        if extract_only and _field_name(field) in NOISE_FIELDS:
            return
        role = _useful_role(field, inherited)
        if isinstance(value, str) and len(value) <= MAX_JSON_LENGTH and value.lstrip().startswith(("{", "[")):
            try:
                nested = json.loads(value)
            except (ValueError, RecursionError):
                nested = None
            if isinstance(nested, (dict, list)):
                if depth >= max_depth:
                    if extract_only:
                        truncated = True
                    else:
                        add(path, "[内嵌 JSON 达到展开层级上限]")
                    return
                walk(nested, path + ".JSON", depth + 1, inherited=role or inherited)
                return
        if isinstance(value, (dict, list)):
            if not value:
                if not extract_only:
                    add(path, "{}" if isinstance(value, dict) else "[]")
                return
            if depth >= max_depth:
                if extract_only:
                    truncated = True
                else:
                    add(path, "[达到展开层级上限]")
                return
            if isinstance(value, dict):
                items = chain(((key, value[key]) for key in preferred if key in value),
                              ((key, item) for key, item in value.items() if key not in preferred))
                for key, item in items:
                    if visited >= 200 or size >= max_chars - 100:
                        truncated = True
                        break
                    original_key = str(key)
                    key = clean_text(original_key, 80)
                    child_role = role or inherited
                    if _field_name(field) == "app":
                        child_role = "来源"
                    walk(item, (path + "." if path else "") + key, depth + 1, original_key, child_role)
            else:
                for index, item in enumerate(value):
                    if visited >= 200 or size >= max_chars - 100:
                        truncated = True
                        break
                    walk(item, f"{path or '$'}[{index}]", depth + 1, field, inherited)
            return
        if extract_only:
            if isinstance(value, str) and not path:
                # A JSON root string is itself content; an unnamed dictionary field is not.
                if depth == 0:
                    role = "正文节选"
            if not role or not isinstance(value, str):
                return
            text = _generic_text(value)
            if not text:
                return
            if role == "链接":
                # Do not truncate a URL into a broken address or display huge opaque paths.
                if not safe_url(text):
                    return
                if len(text) > 600:
                    truncated = True
                    return
            elif len(value) > MAX_FIELD_LENGTH and not value.lower().startswith(("https://", "http://")):
                text += " [字段已截断]"
            values = selected.setdefault(role, [])
            if text not in values:
                if len(values) < 3:
                    values.append(text)
                else:
                    truncated = True
            return
        if isinstance(value, str):
            text = _generic_text(value)
            if not title and field in ("title", "prompt") and text:
                title = clean_text(text)
            if len(value) > len(text):
                # Field-level shortening is visible; URL redaction is not truncation.
                if not value.lower().startswith(("https://", "http://")) and len(value) > MAX_FIELD_LENGTH:
                    text += " [字段已截断]"
        elif value is None:
            text = "null（空值）"
        elif isinstance(value, bool):
            text = "true（是）" if value else "false（否）"
        elif isinstance(value, (int, float)):
            text = str(value)
        else:
            text = "[非标准 JSON 值，未展开]"
        add(path, text or "[空字符串]")

    walk(payload, "", 0)
    if extract_only:
        # Real titles outrank QQ's generic '[分享]' prompt; don't repeat the title in the body.
        title = clean_text((selected.get("标题") or selected.get("提示") or [""])[0], 160)
        seen = {title} if title else set()
        limits = {"来源": 60, "作者": 120, "描述": 300, "正文节选": 600, "链接": 600}
        for role, limit in limits.items():
            values = [value for value in selected.get(role, []) if value not in seen]
            if not values:
                continue
            seen.update(values)
            text = "；".join(values)
            if len(text) > limit:
                if role == "链接":
                    text = values[0]  # Keep a complete usable share link.
                else:
                    text = text[:limit] + " [字段已截断]"
            add(role, text)
        if not title and not rows:
            rows.append("未识别到标题、作者、描述、正文或有效分享链接；未展开其他字段。")
        if truncated:
            rows.append("[部分信息未展示：达到长度、层级或字段数量上限]")
        return Card("json", title or "未提供标题的 JSON 消息", content="\n".join(rows))
    if truncated:
        rows.append("[内容已截断：达到长度或字段数量上限]")
    return Card("json", title or "未识别平台的 JSON 消息", content="\n".join(rows))


def cards_from_segments(segments: object, limit: int = 3, include_generic: bool = False,
                        generic_max_chars: int = 3000, generic_max_depth: int = 6,
                        generic_extract_only: bool = True) -> list[Card]:
    """Accept AstrBot Json components or OneBot array segments; no nested replies."""
    if not isinstance(segments, (list, tuple)):
        return []
    result: list[Card] = []
    seen = set()
    for segment in segments:
        if isinstance(segment, dict):
            if segment.get("type") != "json":
                continue
            data = segment.get("data", {})
            payload = data.get("data") if isinstance(data, dict) else None
        else:
            kind = getattr(segment, "type", "")
            if str(getattr(kind, "value", kind)).lower() != "json":
                continue
            payload = getattr(segment, "data", None)
        card = parse_card(payload)
        if card is None and include_generic:
            card = parse_generic_json(payload, generic_max_chars, generic_max_depth, generic_extract_only)
        if card and card.identity not in seen:
            result.append(card)
            seen.add(card.identity)
            if len(result) >= limit:
                break
    return result

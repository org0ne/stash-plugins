"""
jav_nfo_core.py -- metadata sources, translation backends and NFO helpers
for the jav-nfo-refresh Stash plugin.

Lifted from jav_nfo_refresh_unified.py (the standalone CLI) with the
argument parsing, directory walking, CSV reporting and interactive
prompts removed. Everything here is store-agnostic: the plugin entry
point (jav-nfo-refresh.py) decides what goes into Stash and what goes
into the .nfo sidecar.

Three metadata sources, tried in fixed priority order (first hit wins):
  javstash.org   GraphQL API, needs an API key (taken from the stash-box
                 configured in Stash when the plugin setting is blank)
  libredmm.com   JSON API, anonymous; scrapes on demand (HTTP 202
                 "processing" until it has the title -- see
                 LibredmmClient.processing_wait)
  jav321.com     plain HTML, anonymous; search form + video page

Translation backends: deep-translator (free, unofficial), Google Cloud
Translation NMT (API key) and Google Cloud Translation LLM (service
account + project). The cloud imports are lazy so a deep-translator-only
install does not need google-cloud-translate.
"""

import html
import os
import re
import time
import shutil
import logging
import xml.etree.ElementTree as ET
from datetime import datetime
from pathlib import Path
from typing import Optional, List, Dict, Any, Tuple, NamedTuple
from urllib.parse import quote

import requests

# ---------------------------------------------------------------------------
# Configuration constants
# ---------------------------------------------------------------------------

SOURCE_JAVSTASH = "javstash"
SOURCE_LIBREDMM = "libredmm"
SOURCE_JAV321 = "jav321"
# Lookup order -- the first source that hits wins; the rest aren't asked.
SOURCE_PRIORITY = [SOURCE_JAVSTASH, SOURCE_LIBREDMM, SOURCE_JAV321]
# --source shorthands. "both" predates jav321 and keeps its old meaning.
SOURCE_ALIASES = {
    "all": list(SOURCE_PRIORITY),
    "both": [SOURCE_JAVSTASH, SOURCE_LIBREDMM],
}
# javstash prefixes its housekeeping tags ("※ Delisted", ...) and their
# category ("※ Internal Javstash Tags") with this mark -- never genres.
JAVSTASH_INTERNAL_TAG_MARK = "※"

JAVSTASH_DEFAULT_ENDPOINT = "https://javstash.org/graphql"
# Short label written into <stashid endpoint="..."> -- update this if you
# ever point --endpoint at a different stash-box instance.
JAVSTASH_ENDPOINT_NAME = "javstash"
DEFAULT_SEARCH_LIMIT = 5     # how many fuzzy searchScene candidates to pull

LIBREDMM_DEFAULT_BASE_URL = "https://www.libredmm.com"
PROCESSING_WAIT_SOLE_SOURCE = 30  # --processing-wait default when libredmm is the first selected source
PROCESSING_WAIT_BOTH = 0          # ... and when javstash is ahead of it (see module docstring)
PROCESSING_POLL_INTERVAL = 10     # seconds between re-polls (each one queues a search on libredmm)

JAV321_DEFAULT_BASE_URL = "https://www.jav321.com"

DEFAULT_DELAY = 1.0          # seconds between network calls, per source (be polite)
DEFAULT_TIMEOUT = 20         # seconds per HTTP request
MAX_RETRIES = 3
GROUP_DIR_NAMES = {"lada": "Lada", "leaked": "Leaked"}

# Marker genres. One REFRESHED genre per source, written when that source's
# scrape succeeded and the file is actually being written (never on their
# own); their presence makes later runs skip that source (--rescrape
# overrides). FAILED_GENRE is written only when every queried source
# missed and nothing already refreshed the file, and is removed by any
# later successful scrape. All matched case-insensitively, written exactly.
REFRESHED_GENRES = {
    SOURCE_JAVSTASH: "nfo refreshed javstash",
    SOURCE_LIBREDMM: "nfo refreshed LibreDMM",
    SOURCE_JAV321: "nfo refreshed jav321",
}
FAILED_GENRE = "Metadata Refresh Failed"
# Where a genre goes in a file that has no <genre> block at all --
# first of these that's actually present wins (see add_genre).
GENRE_AFTER_TAGS = ["tag", "director", "releasedate", "premiered",
                    "original_plot", "plot", "originaltitle", "title"]

# DMM storefront/format tags that libredmm and jav321 return as genres but
# that don't describe the content -- skipped before translation. Matched
# against the raw Japanese text exactly. Edit freely.
GENRE_SKIP = {
    "サンプル動画",           # has sample video
    "ハイビジョン",           # HD
    "4K",
    "Blu-ray（ブルーレイ）",
    "ブルーレイ（Blu-ray）",
    "DVDトースター",
    "特典付き・セット商品",   # includes bonus / set product
    "独占配信",               # exclusive distribution
    "セール中",               # on sale
    "期間限定セール",         # limited-time sale
    "数量限定",               # limited quantity
}

SEARCH_SCENE_QUERY = """
query SearchScene($term: String!, $limit: Int) {
  searchScene(term: $term, limit: $limit) {
    id
    title
    details
    release_date
    director
    code
    studio { name }
    urls { url }
    tags { name category { name } }
  }
}
"""

# "Uncensored"-house studios like 1Pondo and Caribbeancom use a different
# code shape entirely: MMDDYY (release date) + shoot number + a fixed
# site-abbreviation suffix. Each site has its OWN fixed separator style,
# and that separator is treated as significant, structural information --
# not interchangeable punctuation -- because it's part of what makes a
# code well-formed for that site:
#   1pondo:       MMDDYY_###-1pon    (underscore before the number,
#                                      hyphen before the suffix)
#   Caribbeancom: MMDDYY-###-carib   (hyphen throughout)
# A code with the "wrong" separator for its site is deliberately NOT
# matched here and falls through to the generic code logic below (where it
# will most likely be rejected outright, surfacing as no_code rather than
# being silently reinterpreted). Add new site patterns to this dict.
UNCENSORED_CODE_PATTERNS: Dict[str, "re.Pattern[str]"] = {
    "1pon": re.compile(r"\d{6}_\d{2,4}-1pon(?![A-Za-z0-9])", re.IGNORECASE),
    "carib": re.compile(r"\d{6}-\d{2,4}-carib(?![A-Za-z0-9])", re.IGNORECASE),
}

log = logging.getLogger("jav-nfo-refresh")


# ---------------------------------------------------------------------------
# Code normalization / extraction
# ---------------------------------------------------------------------------

# Matches a trailing "-pt1", "_part2", " pt2", " Part 2", "-cd1", "-disc2"
# style multi-part indicator. A separator (hyphen, underscore or space)
# before the keyword is REQUIRED specifically to avoid false positives: a
# code like "SDCD-123" legitimately ends in letters "CD" right before its
# digit suffix, but there's no separator introducing it as a separate
# "part" segment the way "ABP-576-pt1" has, so it won't match.
PART_SUFFIX_RE = re.compile(r"[-_\s]+(?:pt|part|cd|disc|disk)\.?\s*\d+$", re.IGNORECASE)

# The canonical code shape after cleaning: LABEL-[prefix]DIGITS[suffix].
# The number may carry up to three letters in front ("MXPC-S011",
# "H4610-ORI962") and up to two behind ("KTRA-309E", "IBW-610Z",
# "GOKU-055D"); both are kept in the canonical code. Greedy LABEL keeps
# multi-hyphen labels ("FC2-PPV-2581845") whole.
CODE_SHAPE_RE = re.compile(r"^(.+)-([A-Z]{0,3})(\d+)([A-Z]{0,2})$")


def normalize_code(raw: str) -> Optional[str]:
    """
    Normalize a studio code to a canonical "LABEL-DIGITS" form (uppercase,
    single hyphen, no leading zero stripped from the digit run -- JAV codes
    keep their zero-padding, e.g. "035" not "35").

    LABEL can be any mix of letters and digits, as long as it contains at
    least one letter -- this covers plain letter labels ("GRACE-035"),
    numeric-prefixed labels ("230OREC-939", where the leading digits are
    part of the label itself), AND labels with digits embedded after a
    leading letter ("T28-442", where "T28" is the whole label) -- the
    digits in a label aren't necessarily a prefix OR a suffix, so this
    doesn't try to anchor on either position; it just splits on the LAST
    hyphen/underscore and requires whatever's left of it to contain a
    letter somewhere.

    Returns None if the input doesn't look like a code at all.

    Checked first: uncensored-house codes (1Pondo, Caribbeancom, ...) --
    see UNCENSORED_CODE_PATTERNS above. These don't fit the LETTERS+DIGITS
    shape the rest of this function assumes (they start with a 6-digit
    date and end in a letters-only site suffix), so each site's exact
    pattern is checked against the raw string first. The matched text is
    returned AS-IS (lowercased only) -- separators are NOT rewritten,
    since which separator appears where is exactly what distinguishes a
    well-formed code for one site from another.
    """
    if not raw:
        return None
    for pattern in UNCENSORED_CODE_PATTERNS.values():
        m = pattern.search(raw)
        if m:
            return m.group(0).lower()

    # A multi-part marker ("SOE-339 pt2", "MOT-139 Part 2", "ABP-576-cd1")
    # is never part of the code, wherever the string came from.
    cleaned = PART_SUFFIX_RE.sub("", raw.strip()).upper()
    # keep only letters, digits, hyphen, underscore for matching purposes
    cleaned = re.sub(r"[^A-Z0-9_-]", "", cleaned)
    # normalize underscore to hyphen so e.g. "T28_442" and "T28-442" both
    # resolve to the same canonical form
    cleaned = cleaned.replace("_", "-")
    if not cleaned:
        return None

    m = CODE_SHAPE_RE.match(cleaned)
    if m:
        label, prefix, digits, suffix = m.groups()
    else:
        # No separator at all -- fall back to splitting at the start of
        # the trailing digit run (e.g. "GRACE035" -> "GRACE" + "035").
        # The label must hold at least two letters here: a lone letter
        # followed by digits ("T28", the first half of "T28_442") is far
        # more likely to be a fragment than a real code.
        m2 = re.match(r"^([A-Z0-9]*[A-Z][A-Z0-9]*[A-Z])(\d+)([A-Z]{0,2})$", cleaned)
        if not m2:
            return None
        label, digits, suffix = m2.groups()
        prefix = ""

    if not re.search(r"[A-Z]", label):
        return None  # a purely-numeric "label" isn't a real code
    return f"{label}-{prefix}{digits}{suffix}"


def extract_code_from_nfo(root: ET.Element) -> Optional[str]:
    tagline_el = root.find("tagline")
    if tagline_el is not None and tagline_el.text and tagline_el.text.strip():
        code = normalize_code(tagline_el.text.strip())
        if code:
            return code
    return None


def extract_code_from_filename(path: Path) -> Optional[str]:
    stem = path.stem  # filename without the extension
    stem_clean = PART_SUFFIX_RE.sub("", stem)

    # Uncensored-style codes (1pondo/Caribbeancom/...) first, anywhere in
    # the stem: they contain their own underscore between the date and
    # shoot number ("092621_001-1pon"), so the before-the-underscore rule
    # below would truncate them to just the date.
    for pattern in UNCENSORED_CODE_PATTERNS.values():
        m = pattern.search(stem_clean)
        if m:
            return m.group(0).lower()

    # Everything before the first "_" -- quality/source tags hang off an
    # underscore ("ABP-576_1080p") and must not be read as a suffix.
    candidate = PART_SUFFIX_RE.sub("", stem_clean.split("_", 1)[0])
    code = normalize_code(candidate)
    if code:
        return code
    # Last resort: the whole stem ("T28_442" -> "T28-442").
    return normalize_code(stem_clean)


def get_code_for_file(root: ET.Element, path: Path) -> Optional[str]:
    code = extract_code_from_nfo(root)
    if code:
        return code
    return extract_code_from_filename(path)


def strip_numeric_prefix(code: str) -> Optional[str]:
    """
    For numeric-prefixed codes like "230OREC-939", returns the
    prefix-stripped form "OREC-939". Both sources are observed to file
    some of these labels under the prefix-less id (javstash's `code`
    field; libredmm's normalized_id), so it's used as an alternate lookup
    term. Returns None if the code has no numeric prefix.
    """
    m = re.match(r"^\d+([A-Z]+-[A-Z]{0,3}\d+[A-Z]{0,2})$", code)
    return m.group(1) if m else None


def strip_edition_suffix(code: str) -> Optional[str]:
    """
    For codes with trailing edition/variant letters ("KTRA-309E",
    "IBW-610Z", "NAAC-038B"), the bare "KTRA-309" form -- what the sources
    usually file the release under. Returns None when there is no suffix.
    """
    m = CODE_SHAPE_RE.match(code)
    if not m or not m.group(4):
        return None
    return f"{m.group(1)}-{m.group(2)}{m.group(3)}"


def uncensored_bare_code(local_code: str) -> Optional[str]:
    """
    For a well-formed local uncensored-house code (e.g. "080316-222-carib"
    or "020519_001-1pon"), returns the site-suffix-stripped form
    ("080316-222", "020519_001" -- native date/number separator kept as-is),
    tried as an additional lookup form since upstream sources don't
    consistently keep the site suffix. Returns None if `local_code` doesn't
    match a known uncensored-site pattern.
    """
    m = re.match(
        r"^(\d{6}[-_]\d{2,4})-(?:" + "|".join(UNCENSORED_CODE_PATTERNS.keys()) + r")$",
        local_code, re.IGNORECASE,
    )
    return m.group(1) if m else None


# Bare uncensored-house date+number code, no site suffix, e.g. "080316-222"
# or "020519_001" -- only used when interpreting a *code returned by a
# source*, never for parsing local .nfo tagline/filename data (which is
# still required to be the full, well-formed, suffixed form).
BARE_UNCENSORED_DATE_RE = re.compile(r"^\d{6}[-_]\d{2,4}$")


def normalize_scene_code(raw: str) -> Optional[str]:
    """
    Like normalize_code(), but additionally accepts a bare uncensored-house
    date+number code with no site suffix (see BARE_UNCENSORED_DATE_RE).
    Used only for comparing codes returned by a source against our
    locally-derived code -- not for parsing local data.
    """
    code = normalize_code(raw)
    if code:
        return code
    stripped = (raw or "").strip()
    if BARE_UNCENSORED_DATE_RE.match(stripped):
        return stripped
    return None


def code_match_key(code: Optional[str]) -> Optional[str]:
    """
    Comparison key for codes that ignores zero-padding differences in the
    trailing digit run ("ABP-0123" == "ABP-123"), since libredmm pads its
    normalized_id to its own convention. Uncensored-house codes are
    compared as-is (their digits are dates/shoot numbers, not padded ids).
    """
    if not code:
        return None
    m = CODE_SHAPE_RE.match(code)
    if m and re.search(r"[A-Z]", m.group(1)):
        label, prefix, digits, suffix = m.groups()
        return f"{label}-{prefix}{int(digits)}{suffix}"
    return code.lower()


def acceptable_code_forms(code: str) -> List[str]:
    """The local code plus every alternate form a source might file it under."""
    forms = [code]
    bare = strip_edition_suffix(code)
    for alt in (strip_numeric_prefix(code), uncensored_bare_code(code), bare,
                strip_numeric_prefix(bare) if bare else None):
        if alt and alt not in forms:
            forms.append(alt)
    return forms


KNOWN_GROUPS = list(GROUP_DIR_NAMES.values())
GROUP_IN_TITLE_RE = re.compile(
    r"\[(" + "|".join(re.escape(g) for g in KNOWN_GROUPS) + r")\]",
    re.IGNORECASE,
)


def detect_group(path: Path, existing_title: Optional[str] = None) -> Optional[str]:
    """
    Determine the {Group} for the rebuilt <title>.

    Priority:
      1. Path-based (authoritative): any path component case-insensitively
         matching a known group directory name (see GROUP_DIR_NAMES).
      2. Fallback: an existing "[Group]" bracket already present in the
         file's current <title>, in case the file lives outside its
         original Sorted/<Group> directory but the title metadata still
         reflects where it came from. Case-insensitive match, normalized
         to the canonical casing either way.
      3. Otherwise None (omit the [Group] segment entirely).
    """
    for part in path.parts:
        match = GROUP_DIR_NAMES.get(part.lower())
        if match:
            return match
    if existing_title:
        m = GROUP_IN_TITLE_RE.search(existing_title)
        if m:
            matched = m.group(1).lower()
            for canonical in KNOWN_GROUPS:
                if canonical.lower() == matched:
                    return canonical
    return None


# ---------------------------------------------------------------------------
# Common shape every source is normalized into
# ---------------------------------------------------------------------------

class SourceData(NamedTuple):
    source: str
    id: str                          # javstash scene UUID / libredmm normalized_id / jav321 (DMM) cid
    title: Optional[str]
    details: Optional[str]
    release_date: Optional[str]      # YYYY-MM-DD
    director: Optional[str]
    urls: List[str]
    genres: List[str]                # raw Japanese (javstash tags / libredmm + jav321 genres)

    def has_any_data(self) -> bool:
        return any([self.title, self.details, self.release_date,
                    self.director, self.urls, self.genres])


# ---------------------------------------------------------------------------
# Shared per-source throttling
# ---------------------------------------------------------------------------

class Throttle:
    """
    Minimum gap of `delay` seconds between consecutive requests to ONE
    source. Each client owns its own, so --delay applies to javstash,
    libredmm and jav321 independently. Called before EVERY actual HTTP
    request -- retries, alternate code forms, libredmm re-polls and
    jav321's follow-up page fetches included.
    """

    def __init__(self, delay: float):
        self.delay = delay
        self._last: Optional[float] = None

    def wait(self) -> None:
        if self.delay <= 0:
            return
        now = time.monotonic()
        if self._last is not None:
            remaining = self.delay - (now - self._last)
            if remaining > 0:
                time.sleep(remaining)
        self._last = time.monotonic()


def retry_after_seconds(resp: requests.Response) -> float:
    """Retry-After for a 429 -- seconds if it's a plain integer, else a
    conservative 30s (HTTP-date form isn't worth parsing)."""
    retry_after = resp.headers.get("Retry-After")
    return float(retry_after) if retry_after and retry_after.isdigit() else 30.0


# ---------------------------------------------------------------------------
# javstash.org API
# ---------------------------------------------------------------------------

class JavstashClient:
    def __init__(self, endpoint: str, api_key: str, timeout: int = DEFAULT_TIMEOUT,
                 delay: float = DEFAULT_DELAY):
        self.endpoint = endpoint
        self.session = requests.Session()
        self.session.headers.update({
            "Content-Type": "application/json",
            "ApiKey": api_key,
        })
        self.timeout = timeout
        self.throttle = Throttle(delay)

    def _post(self, query: str, variables: Dict[str, Any]) -> Dict[str, Any]:
        last_exc = None
        for attempt in range(1, MAX_RETRIES + 1):
            self.throttle.wait()
            try:
                resp = self.session.post(
                    self.endpoint,
                    json={"query": query, "variables": variables},
                    timeout=self.timeout,
                )
                if resp.status_code == 429:
                    wait = retry_after_seconds(resp)
                    last_exc = RuntimeError("Rate limited (429) by javstash")
                    if attempt < MAX_RETRIES:
                        log.warning(
                            "Rate limited by javstash (429) on attempt %d/%d -- "
                            "waiting %.0fs before retrying. If this keeps happening "
                            "across a large batch run, increase the Request delay setting.",
                            attempt, MAX_RETRIES, wait,
                        )
                        time.sleep(wait)
                    continue
                resp.raise_for_status()
                payload = resp.json()
                if payload.get("errors"):
                    raise RuntimeError(f"GraphQL errors: {payload['errors']}")
                return payload["data"]
            except (requests.RequestException, RuntimeError, ValueError) as exc:
                last_exc = exc
                if attempt < MAX_RETRIES:
                    backoff = 2 ** attempt
                    log.warning(
                        "javstash call failed (attempt %d/%d): %s -- retrying in %ds",
                        attempt, MAX_RETRIES, exc, backoff,
                    )
                    time.sleep(backoff)
        raise RuntimeError(f"javstash call failed after {MAX_RETRIES} attempts: {last_exc}")

    def _search_candidates(self, term: str, limit: int) -> List[Dict[str, Any]]:
        data = self._post(SEARCH_SCENE_QUERY, {"term": term, "limit": limit})
        return data.get("searchScene") or []

    def find_scene_by_code(self, code: str, limit: int) -> Tuple[Optional[Dict[str, Any]], Optional[str]]:
        """
        searchScene is a fuzzy text search -- there is no exact "code ="
        filter available on this API, so we pull a handful of candidates
        and require a normalized-code match ourselves. Every alternate
        form of the code (see acceptable_code_forms) is both a search term
        and an acceptable match, since javstash sometimes stores a code
        with its numeric prefix or uncensored site suffix stripped.

        Returns (scene, detail) -- detail is None on a successful match,
        or a human-readable explanation of the miss otherwise.
        """
        acceptable_codes = acceptable_code_forms(code)
        all_candidates: List[Dict[str, Any]] = []
        for term in acceptable_codes:
            candidates = self._search_candidates(term, limit)
            all_candidates.extend(candidates)
            for scene in candidates:
                scene_code = normalize_scene_code(scene.get("code") or "")
                if scene_code in acceptable_codes:
                    if scene_code != code:
                        log.info(
                            "%s: javstash matched via alternate code form %r "
                            "-- accepting as the same release.", code, scene_code,
                        )
                    return scene, None

        if all_candidates:
            seen_codes = [c.get("code") for c in all_candidates]
            detail = (
                f"no code match (accepted forms: {acceptable_codes}) among "
                f"{len(all_candidates)} fuzzy result(s); candidate codes: {seen_codes}"
            )
        else:
            detail = f"searchScene returned zero candidates for {acceptable_codes}"
        return None, detail


def fetch_javstash(client: JavstashClient, code: str,
                   args: Any) -> Tuple[Optional[SourceData], Optional[str]]:
    scene, detail = client.find_scene_by_code(code, args.search_limit)
    if scene is None:
        return None, detail
    genres = []
    for tag in scene.get("tags") or []:
        name = (tag.get("name") or "").strip()
        category = ((tag.get("category") or {}).get("name") or "").strip()
        if not name or name.startswith(JAVSTASH_INTERNAL_TAG_MARK) \
                or category.startswith(JAVSTASH_INTERNAL_TAG_MARK):
            continue
        genres.append(name)
    return SourceData(
        source=SOURCE_JAVSTASH,
        id=scene.get("id") or "",
        title=(scene.get("title") or "").strip() or None,
        details=(scene.get("details") or "").strip() or None,
        release_date=(scene.get("release_date") or "").strip() or None,
        director=(scene.get("director") or "").strip() or None,
        urls=[u.get("url") for u in (scene.get("urls") or []) if u.get("url")],
        genres=genres,
    ), None


# ---------------------------------------------------------------------------
# libredmm.com API
# ---------------------------------------------------------------------------

class LibredmmClient:
    def __init__(self, base_url: str, timeout: int = DEFAULT_TIMEOUT,
                 delay: float = DEFAULT_DELAY, processing_wait: float = 0):
        self.base_url = base_url.rstrip("/")
        self.session = requests.Session()
        self.session.headers.update({
            "Accept": "application/json",
            "User-Agent": "jav_nfo_refresh_unified/1.0",
        })
        self.timeout = timeout
        self.throttle = Throttle(delay)
        self.processing_wait = processing_wait

    def movie_page_url(self, movie_id: str) -> str:
        return f"{self.base_url}/movies/{quote(movie_id, safe='')}"

    AUTH_TOKEN_RE = re.compile(r'name="authenticity_token" value="([^"]+)"')

    def login(self, email: str, password: str) -> None:
        """
        Sign in through libredmm's regular sign-in form (Rails + Clearance):
        GET /sign_in for the form's CSRF authenticity_token, then POST
        /session. self.session keeps the resulting cookies, so every later
        lookup is made as this account. Clearance answers a redirect on
        success and 401 on bad credentials.

        Deliberately a single attempt with no retry loop -- bad credentials
        shouldn't hammer the login page. Raises RuntimeError on failure.
        """
        html_headers = {"Accept": "text/html"}
        self.throttle.wait()
        resp = self.session.get(f"{self.base_url}/sign_in", headers=html_headers,
                                timeout=self.timeout)
        resp.raise_for_status()
        m = self.AUTH_TOKEN_RE.search(resp.text)
        if not m:
            raise RuntimeError("couldn't find the sign-in form's authenticity_token "
                               "-- libredmm's sign-in page may have changed")

        self.throttle.wait()
        resp = self.session.post(
            f"{self.base_url}/session",
            data={
                "authenticity_token": html.unescape(m.group(1)),
                "session[email]": email,
                "session[password]": password,
            },
            headers=html_headers, timeout=self.timeout, allow_redirects=False,
        )
        if resp.status_code == 401:
            raise RuntimeError("libredmm rejected the email/password")
        if resp.status_code not in (301, 302, 303):
            raise RuntimeError(f"unexpected sign-in response (HTTP {resp.status_code})")

    def _get_movie(self, term: str) -> Tuple[str, Optional[Dict[str, Any]]]:
        """
        One lookup of /movies/<term>.json. Returns (state, movie):
          ("ok", {...})         200 with movie data
          ("processing", None)  202 -- libredmm is scraping it in the background
          ("not_found", None)   404
        Raises RuntimeError once retries are exhausted on anything else
        (429s, 5xx/Cloudflare 52x, network errors, unparseable bodies).
        """
        url = f"{self.base_url}/movies/{quote(term, safe='')}.json"
        last_exc = None
        for attempt in range(1, MAX_RETRIES + 1):
            self.throttle.wait()
            try:
                resp = self.session.get(url, timeout=self.timeout)
                if resp.status_code == 429:
                    wait = retry_after_seconds(resp)
                    last_exc = RuntimeError("Rate limited (429) by libredmm")
                    if attempt < MAX_RETRIES:
                        log.warning(
                            "Rate limited by libredmm (429) on attempt %d/%d -- "
                            "waiting %.0fs before retrying. If this keeps happening "
                            "across a large batch run, increase the Request delay setting.",
                            attempt, MAX_RETRIES, wait,
                        )
                        time.sleep(wait)
                    continue
                if resp.status_code == 404:
                    return "not_found", None
                if resp.status_code == 202:
                    return "processing", None
                resp.raise_for_status()
                payload = resp.json()
                if not isinstance(payload, dict):
                    raise ValueError(f"unexpected JSON payload type {type(payload).__name__}")
                if payload.get("err"):
                    if payload["err"] == "processing":
                        return "processing", None
                    raise RuntimeError(f"libredmm error: {payload['err']}")
                return "ok", payload
            except (requests.RequestException, RuntimeError, ValueError) as exc:
                last_exc = exc
                if attempt < MAX_RETRIES:
                    backoff = 2 ** attempt
                    log.warning(
                        "libredmm call failed for %s (attempt %d/%d): %s -- retrying in %ds",
                        term, attempt, MAX_RETRIES, exc, backoff,
                    )
                    time.sleep(backoff)
        raise RuntimeError(f"libredmm call failed after {MAX_RETRIES} attempts: {last_exc}")

    def find_movie_by_code(self, code: str) -> Tuple[Optional[Dict[str, Any]], Optional[str]]:
        """
        Look `code` up on libredmm, along with its alternate forms. Every
        form is requested each round; forms answering 202 "processing" are
        re-polled every PROCESSING_POLL_INTERVAL seconds until
        self.processing_wait runs out (0 = ask once). The first 200 whose
        normalized_id matches an accepted form wins -- libredmm resolves
        ids with its own fuzzy logic, so a movie for a different code is
        rejected rather than trusted.

        Returns (movie, detail) -- detail is None on a successful match,
        or a human-readable explanation of the miss otherwise.
        """
        acceptable_codes = acceptable_code_forms(code)
        acceptable_keys = {code_match_key(c) for c in acceptable_codes}

        deadline = time.monotonic() + self.processing_wait
        pending = list(acceptable_codes)
        not_found: List[str] = []
        mismatched: List[str] = []

        while True:
            still_pending = []
            for term in pending:
                state, movie = self._get_movie(term)
                if state == "ok":
                    returned_id = movie.get("normalized_id") or ""
                    returned_key = code_match_key(normalize_scene_code(returned_id))
                    if returned_key in acceptable_keys:
                        if returned_key != code_match_key(code):
                            log.info(
                                "%s: libredmm matched via alternate code form %r "
                                "(filed as %r) -- accepting as the same release.",
                                code, term, returned_id,
                            )
                        return movie, None
                    mismatched.append(f"{term}->{returned_id}")
                elif state == "processing":
                    still_pending.append(term)
                else:
                    not_found.append(term)
            pending = still_pending
            remaining = deadline - time.monotonic()
            if not pending or remaining <= 0:
                break
            log.info("%s: libredmm still processing %s -- re-polling in %ds "
                     "(%.0fs left)", code, pending, PROCESSING_POLL_INTERVAL, remaining)
            time.sleep(min(PROCESSING_POLL_INTERVAL, remaining))

        parts = [f"tried forms {acceptable_codes}"]
        if not_found:
            parts.append(f"not found: {not_found}")
        if mismatched:
            parts.append(f"returned a different movie: {mismatched}")
        if pending:
            parts.append(f"still processing after {self.processing_wait:.0f}s: "
                         f"{pending} -- a later run may pick it up")
        return None, "; ".join(parts)


def fetch_libredmm(client: LibredmmClient, code: str,
                   args: Any) -> Tuple[Optional[SourceData], Optional[str]]:
    movie, detail = client.find_movie_by_code(code)
    if movie is None:
        return None, detail
    movie_id = movie.get("normalized_id") or code
    urls = []
    if movie.get("url"):
        urls.append(movie["url"])
    urls.append(client.movie_page_url(movie_id))
    # libredmm dates are full ISO timestamps with an offset, e.g.
    # "2020-07-23T03:00:41.000-07:00" -- only the calendar date is kept.
    date = (movie.get("date") or "")[:10]
    if not re.match(r"^\d{4}-\d{2}-\d{2}$", date):
        date = ""
    return SourceData(
        source=SOURCE_LIBREDMM,
        id=movie_id,
        title=(movie.get("title") or "").strip() or None,
        details=(movie.get("description") or "").strip() or None,
        release_date=date or None,
        director=", ".join(d.strip() for d in (movie.get("directors") or []) if d and d.strip()) or None,
        urls=urls,
        genres=[g for g in (movie.get("genres") or []) if g],
    ), None


# ---------------------------------------------------------------------------
# jav321.com (HTML scrape -- the site has no API)
# ---------------------------------------------------------------------------

JAV321_TAG_RE = re.compile(r"<[^>]+>")
JAV321_ANCHOR_TEXT_RE = re.compile(r"<a\b[^>]*>(.*?)</a>", re.S)
# One "<b>label</b>: value<br>" line of the video page's info column.
JAV321_FIELD_RE = re.compile(r"<b>([^<]+)</b>:\s*(.*?)<br\s*/?>", re.S)
JAV321_HEADING_RE = re.compile(r'<div class="panel-heading">\s*<h3>(.*?)</h3>', re.S)
JAV321_INFO_COLUMN_RE = re.compile(r'<div class="col-md-9">(.*?)</div>', re.S)
JAV321_FULL_ROW_RE = re.compile(r'<div class="col-md-12">(.*?)</div>', re.S)
JAV321_VIDEO_PATH_RE = re.compile(r"/video/([^/?#]+)")
JAV321_NOT_FOUND_TEXT = "AVが見つかりませんでした"


def jav321_clean(fragment: str) -> str:
    """Strip tags and entities from an HTML fragment, collapse whitespace."""
    text = html.unescape(JAV321_TAG_RE.sub(" ", fragment or ""))
    return re.sub(r"\s+", " ", text).strip()


def jav321_linked_names(fragment: str) -> List[str]:
    """The texts of every <a> in a field value (people/genres are linked),
    falling back to the plain text if there are no links."""
    names = [jav321_clean(t) for t in JAV321_ANCHOR_TEXT_RE.findall(fragment or "")]
    names = [n for n in names if n]
    if names:
        return names
    plain = jav321_clean(fragment)
    return [plain] if plain else []


def parse_jav321_video_page(page: str) -> Dict[str, Any]:
    """
    Pull the metadata out of a jav321 /video/<id> page. The page is plain
    Bootstrap markup: the first "panel-info" holds an <h3> heading (title
    plus a <small> subtitle repeating the code and cast), an info column
    of "<b>label</b>: value<br>" lines (品番 product number, 配信開始日
    date, ジャンル genres, 監督 director, ...), an optional sample-video
    row, and a full-width row with the description. Everything after that
    first panel (cast filmographies, torrent table) is ignored. Missing
    pieces come back empty rather than raising -- the caller decides what
    it can live without.
    """
    info: Dict[str, Any] = {"title": "", "code": "", "date": "", "director": "",
                            "genres": [], "description": ""}
    m = JAV321_HEADING_RE.search(page)
    if m:
        heading = re.sub(r"<small>.*?</small>", "", m.group(1), flags=re.S)
        info["title"] = jav321_clean(heading)

    m = JAV321_INFO_COLUMN_RE.search(page)
    if not m:
        return info
    fields: Dict[str, str] = {}
    for label, value in JAV321_FIELD_RE.findall(m.group(1)):
        fields[jav321_clean(label)] = value
    info["code"] = jav321_clean(fields.get("品番", ""))
    info["date"] = jav321_clean(fields.get("配信開始日", ""))
    info["director"] = ", ".join(jav321_linked_names(fields.get("監督", "")))
    info["genres"] = jav321_linked_names(fields.get("ジャンル", ""))

    # Description: the first full-width row after the info column that is
    # plain text (the sample-video row and an empty red-notice row are
    # also full-width rows). Bounded by the next panel so cast thumbnails
    # further down can't be mistaken for it.
    after = page[m.end():]
    next_panel = after.find('class="panel panel-info"')
    if next_panel != -1:
        after = after[:next_panel]
    for row in JAV321_FULL_ROW_RE.findall(after):
        if "<video" in row or "<div" in row:
            continue
        text = jav321_clean(row)
        if text:
            info["description"] = text
            break
    return info


class Jav321Client:
    def __init__(self, base_url: str, timeout: int = DEFAULT_TIMEOUT,
                 delay: float = DEFAULT_DELAY):
        self.base_url = base_url.rstrip("/")
        self.session = requests.Session()
        self.session.headers.update({
            "Accept": "text/html",
            "User-Agent": "jav_nfo_refresh_unified/1.0",
        })
        self.timeout = timeout
        self.throttle = Throttle(delay)

    def video_page_url(self, video_id: str) -> str:
        return f"{self.base_url}/video/{quote(video_id, safe='')}"

    def _request(self, method: str, url: str, **kwargs: Any) -> requests.Response:
        """
        One throttled HTTP request with the same 429 / 5xx / network-error
        retry policy as the other clients. Redirects are NOT followed --
        the search endpoint's redirect is the signal we're after. A 404 is
        returned as-is, not retried: jav321 answers an empty 404 for a
        video page its own search redirects to (the entry exists in its
        index but the page was never generated), and that's a definitive
        miss. Raises RuntimeError once retries are exhausted.
        """
        last_exc = None
        for attempt in range(1, MAX_RETRIES + 1):
            self.throttle.wait()
            try:
                resp = self.session.request(method, url, timeout=self.timeout,
                                            allow_redirects=False, **kwargs)
                if resp.status_code == 429:
                    wait = retry_after_seconds(resp)
                    last_exc = RuntimeError("Rate limited (429) by jav321")
                    if attempt < MAX_RETRIES:
                        log.warning(
                            "Rate limited by jav321 (429) on attempt %d/%d -- "
                            "waiting %.0fs before retrying. If this keeps happening "
                            "across a large batch run, increase the Request delay setting.",
                            attempt, MAX_RETRIES, wait,
                        )
                        time.sleep(wait)
                    continue
                if resp.status_code != 404:
                    resp.raise_for_status()
                # The site declares charset=UTF-8; pin it so a stray header
                # change can't mojibake the Japanese text.
                resp.encoding = "utf-8"
                return resp
            except (requests.RequestException, RuntimeError) as exc:
                last_exc = exc
                if attempt < MAX_RETRIES:
                    backoff = 2 ** attempt
                    log.warning(
                        "jav321 call failed for %s (attempt %d/%d): %s -- retrying in %ds",
                        url, attempt, MAX_RETRIES, exc, backoff,
                    )
                    time.sleep(backoff)
        raise RuntimeError(f"jav321 call failed after {MAX_RETRIES} attempts: {last_exc}")

    def _search(self, term: str) -> Tuple[str, Optional[str], Optional[str]]:
        """
        Submit the site's search form for `term`. Returns (state, video_id,
        page):
          ("ok", id, html)          redirected to /video/<id> and fetched it
          ("not_found", None, None) 200 on /search with the "not found" notice
          ("dead_page", id, None)   redirected to /video/<id>, but that page
                                    is an empty 404 -- jav321 knows the code
                                    yet has nothing for it. A miss.
        Anything else (a redirect somewhere unexpected, a 200 without the
        notice, a 404 from the search form itself) is raised as
        RuntimeError so a changed site shows up as api_error rather than
        as a silent miss.
        """
        resp = self._request("POST", f"{self.base_url}/search", data={"sn": term})
        if resp.status_code in (301, 302, 303, 307, 308):
            location = resp.headers.get("Location") or ""
            m = JAV321_VIDEO_PATH_RE.search(location)
            if not m:
                raise RuntimeError(f"jav321 search redirected somewhere unexpected: {location!r}")
            video_id = m.group(1)
            page = self._request("GET", self.video_page_url(video_id))
            if page.status_code == 404:
                return "dead_page", video_id, None
            return "ok", video_id, page.text
        if resp.status_code == 200 and JAV321_NOT_FOUND_TEXT in resp.text:
            return "not_found", None, None
        raise RuntimeError(f"jav321 search answered HTTP {resp.status_code} without "
                           "redirecting or reporting 'not found' -- has the site changed?")

    def find_movie_by_code(self, code: str) -> Tuple[Optional[Dict[str, Any]], Optional[str]]:
        """
        Look `code` up on jav321, trying each alternate form in turn. The
        search is fuzzy on their side (a numeric-prefixed code lands on
        the prefix-less DMM entry, for instance), so the video page's 品番
        must match an accepted form -- a page for some other movie is
        rejected rather than trusted.

        Returns (info, detail) -- info is the parsed page plus "id" on a
        successful match (detail None), or None with a human-readable
        explanation of the miss.
        """
        acceptable_codes = acceptable_code_forms(code)
        acceptable_keys = {code_match_key(c) for c in acceptable_codes}
        not_found: List[str] = []
        dead_pages: List[str] = []
        mismatched: List[str] = []

        for term in acceptable_codes:
            state, video_id, page = self._search(term)
            if state == "not_found":
                not_found.append(term)
                continue
            if state == "dead_page":
                dead_pages.append(f"{term}->{video_id}")
                continue
            info = parse_jav321_video_page(page or "")
            returned_code = info.get("code") or ""
            returned_key = code_match_key(normalize_scene_code(returned_code))
            if returned_key in acceptable_keys:
                if returned_key != code_match_key(code):
                    log.info(
                        "%s: jav321 matched via alternate code form %r "
                        "(filed as %r, cid %s) -- accepting as the same release.",
                        code, term, returned_code, video_id,
                    )
                info["id"] = video_id
                return info, None
            mismatched.append(f"{term}->{video_id} (品番 {returned_code or 'missing'})")

        parts = [f"tried forms {acceptable_codes}"]
        if not_found:
            parts.append(f"not found: {not_found}")
        if dead_pages:
            parts.append(f"search redirected to a video page that is an empty 404 "
                         f"(jav321 lists the code but has no data): {dead_pages}")
        if mismatched:
            parts.append(f"returned a different movie: {mismatched}")
        return None, "; ".join(parts)


def fetch_jav321(client: Jav321Client, code: str,
                 args: Any) -> Tuple[Optional[SourceData], Optional[str]]:
    info, detail = client.find_movie_by_code(code)
    if info is None:
        return None, detail
    video_id = info["id"]
    date = info.get("date") or ""
    if not re.match(r"^\d{4}-\d{2}-\d{2}$", date):
        date = ""
    return SourceData(
        source=SOURCE_JAV321,
        id=video_id,
        title=info.get("title") or None,
        details=info.get("description") or None,
        release_date=date or None,
        director=info.get("director") or None,
        urls=[client.video_page_url(video_id)],
        genres=[g for g in info.get("genres") or [] if g],
    ), None


FETCHERS = {
    SOURCE_JAVSTASH: fetch_javstash,
    SOURCE_LIBREDMM: fetch_libredmm,
    SOURCE_JAV321: fetch_jav321,
}


# ---------------------------------------------------------------------------
# Translation -- two interchangeable backends, selected via --translate-backend
# ---------------------------------------------------------------------------

class BaseTranslator:
    """
    Shared chunking/error-handling so both backends behave identically from
    the rest of the script's point of view. Chunks long text so nothing
    gets silently truncated, per the "do not truncate" requirement.
    """

    CHUNK_SIZE = 4000

    def translate_ja_to_en(self, text: str) -> Optional[str]:
        if not text or not text.strip():
            return None
        try:
            if len(text) <= self.CHUNK_SIZE:
                return self._translate_single(text)
            # Split on sentence-ish boundaries so we don't cut mid-word,
            # then translate each chunk and rejoin -- no part of the
            # original text is dropped.
            chunks = self._chunk_text(text)
            translated_chunks = [self._translate_single(c) for c in chunks]
            return " ".join(tc for tc in translated_chunks if tc)
        except Exception as exc:  # anything can go wrong here, both backends
            log.warning("Translation failed (%s) -- leaving field untouched.", exc)
            return None

    def _chunk_text(self, text: str) -> List[str]:
        chunks, current = [], ""
        for piece in re.split(r"(?<=[。\n])", text):
            if len(current) + len(piece) > self.CHUNK_SIZE and current:
                chunks.append(current)
                current = piece
            else:
                current += piece
        if current:
            chunks.append(current)
        return chunks

    def _translate_single(self, text: str) -> str:
        raise NotImplementedError


class DeepTranslatorBackend(BaseTranslator):
    """Free, unofficial -- no API key needed, but quality/uptime can vary.

    The underlying free endpoint rejects more than 5 requests/second, which
    a single file's title + plot + genres can easily burst past, so calls
    are spaced by MIN_INTERVAL."""

    MIN_INTERVAL = 0.3  # seconds

    def __init__(self):
        self._last_call: Optional[float] = None
        try:
            from deep_translator import GoogleTranslator
        except ImportError as exc:
            raise SystemExit(
                "Translation backend 'deep-translator' is selected but the "
                "'deep-translator' package isn't installed.\n"
                "Run: /srv/stash/.venv/bin/pip install deep-translator (or the "
                "plugin's requirements.txt), or enable Disable translation."
            ) from exc
        # Per deep-translator's own docs: instantiate once and reuse for
        # multiple translate() calls, rather than creating a new instance
        # per call.
        self._translator = GoogleTranslator(source="ja", target="en")

    def _translate_single(self, text: str) -> str:
        if self._last_call is not None:
            remaining = self.MIN_INTERVAL - (time.monotonic() - self._last_call)
            if remaining > 0:
                time.sleep(remaining)
        try:
            return self._translator.translate(text)
        finally:
            self._last_call = time.monotonic()


class CloudTranslationBackend(BaseTranslator):
    """
    Official Google Cloud Translation API (Basic/v2), authenticated with a
    simple API key -- no service account/OAuth setup needed. Used for the
    "nmt" model (--cloud-model nmt, the default). Covered by the free tier:
    the first 500,000 characters/month (Basic + Advanced combined) are free.

    Get an API key: console.cloud.google.com -> enable "Cloud Translation
    API" on a project -> APIs & Services > Credentials > Create
    Credentials > API key.

    Note: the "translation-llm" model is NOT handled by this class -- see
    CloudTranslationLLMBackend below. On at least this project, attempting
    translation-llm through this simple API-key path fails with "LLM model
    is not enabled", because (per this project's own Model Garden page)
    that model is gated behind Application Default Credentials (the v3
    Advanced API), contradicting Google's general docs which claim it's
    reachable through Basic/v2 with just an API key. Trust your own
    project's Model Garden page over the generic docs if they disagree.
    """

    ENDPOINT = "https://translation.googleapis.com/language/translate/v2"

    def __init__(self, api_key: Optional[str], timeout: int = DEFAULT_TIMEOUT):
        if not api_key:
            raise SystemExit(
                "Translation backend 'cloud' is selected but no API key was "
                "provided.\nSet the Google Cloud API key plugin setting.\n"
                "Get one from console.cloud.google.com (enable the 'Cloud "
                "Translation API' on a project, then create an API key "
                "under APIs & Services > Credentials)."
            )
        self.api_key = api_key
        self.timeout = timeout
        self.session = requests.Session()

    def _translate_single(self, text: str) -> str:
        body = {"q": text, "source": "ja", "target": "en", "format": "text"}
        # Deliberately omit `model` entirely -- this matches the Basic
        # API's own documented NMT default rather than spelling out the
        # NMT resource path, one less thing that could be malformed.
        resp = self.session.post(
            self.ENDPOINT,
            params={"key": self.api_key},
            json=body,
            timeout=self.timeout,
        )
        if not resp.ok:
            # Google's error responses carry a much more useful message
            # than the bare HTTP status -- surface it instead of letting
            # raise_for_status() discard it.
            try:
                detail = resp.json().get("error", {}).get("message", resp.text)
            except ValueError:
                detail = resp.text
            raise RuntimeError(f"Cloud Translation API error ({resp.status_code}): {detail}")
        payload = resp.json()
        return payload["data"]["translations"][0]["translatedText"]


class CloudTranslationLLMBackend(BaseTranslator):
    """
    Official Google Cloud Translation API (Advanced/v3), used specifically
    for the "translation-llm" model (--cloud-model translation-llm).

    Unlike the "nmt" model (CloudTranslationBackend, Basic/v2 + bare API
    key), this model requires Application Default Credentials (ADC) on at
    least some projects -- confirmed via this project's own Model Garden
    sample code, which authenticates with `TranslationServiceClient()`
    and explicitly says "To authenticate to Cloud Translation, set up
    Application Default Credentials" -- no API key in sight. This
    contradicts Google's general TLLM docs, which claim Basic/v2 + API key
    works; the project-specific Model Garden page is the more trustworthy
    source when they disagree.

    IMPORTANT COST NOTE: the 500,000 character/month free tier does NOT
    apply to LLM-model translations -- those are billed per character from
    the very first one.

    SETUP:
      1. pip install google-cloud-translate
      2. Pick ONE of these credential methods:
         a) Service account (recommended for unattended/scripted runs):
            - Cloud console -> IAM & Admin -> Service Accounts -> Create
              Service Account.
            - Grant it a role that can call Cloud Translation (e.g.
              "Cloud Translation API User").
            - On that service account: Keys -> Add Key -> Create new key
              -> JSON. Download the file.
            - Either set the environment variable
              GOOGLE_APPLICATION_CREDENTIALS to that file's path, or pass
              --cloud-credentials-file /path/to/key.json.
         b) Your own user credentials (simpler for local/interactive use):
            - Run `gcloud auth application-default login` and follow the
              browser prompt. No JSON file or env var needed -- the client
              library finds these automatically.
    """

    def __init__(self, project_id: Optional[str], region: str = "us-central1",
                 credentials_file: Optional[str] = None):
        if not project_id:
            raise SystemExit(
                "Translation engine cloud-llm requires the Google Cloud project ID "
                "plugin setting.\n"
                "Find your project ID next to the project name in the Cloud "
                "console's project picker, or run "
                "`gcloud config get-value project`."
            )
        try:
            from google.cloud import translate
        except ImportError as exc:
            raise SystemExit(
                "Translation engine cloud-llm requires the "
                "'google-cloud-translate' package.\n"
                "Run: /srv/stash/.venv/bin/pip install google-cloud-translate\n"
                "You'll also need Application Default Credentials set up "
                "-- see this script's module docstring, or "
                "CloudTranslationLLMBackend's class docstring, for setup "
                "steps."
            ) from exc

        client_kwargs = {}
        if credentials_file:
            try:
                from google.oauth2 import service_account
                client_kwargs["credentials"] = (
                    service_account.Credentials.from_service_account_file(credentials_file)
                )
            except Exception as exc:
                raise SystemExit(
                    f"Failed to load service account credentials from "
                    f"{credentials_file}: {exc}"
                ) from exc

        try:
            self._client = translate.TranslationServiceClient(**client_kwargs)
        except Exception as exc:
            raise SystemExit(
                f"Failed to create a Cloud Translation v3 client: {exc}\n"
                "This usually means Application Default Credentials aren't "
                "set up. Either point the Google Cloud credentials file plugin "
                "setting at a service account JSON key file, or run "
                "`gcloud auth application-default login` as the Stash user."
            ) from exc

        self.parent = f"projects/{project_id}/locations/{region}"
        self.model_path = f"{self.parent}/models/general/translation-llm"

    def _translate_single(self, text: str) -> str:
        response = self._client.translate_text(
            request={
                "contents": [text],
                "target_language_code": "en",
                "source_language_code": "ja",
                "model": self.model_path,
                "parent": self.parent,
                "mime_type": "text/plain",
            }
        )
        return response.translations[0].translated_text


def build_translator(backend: str, cloud_api_key: Optional[str],
                      cloud_model: str = "nmt",
                      cloud_project_id: Optional[str] = None,
                      cloud_region: str = "us-central1",
                      cloud_credentials_file: Optional[str] = None) -> BaseTranslator:
    if backend == "cloud":
        if cloud_model == "translation-llm":
            return CloudTranslationLLMBackend(
                cloud_project_id, region=cloud_region,
                credentials_file=cloud_credentials_file,
            )
        return CloudTranslationBackend(cloud_api_key)
    return DeepTranslatorBackend()


# ---------------------------------------------------------------------------
# NFO read/write helpers
# ---------------------------------------------------------------------------

SELF_CLOSING_TAG_RE = re.compile(r'<([A-Za-z_][\w.-]*)((?:\s+[A-Za-z_][\w.-]*="[^"]*")*)\s*/>')


def expand_self_closing_tags(xml_text: str) -> str:
    """
    ElementTree always serializes empty elements as self-closing
    (<tag />), even if they were originally written as a full <tag></tag>
    pair -- there's no way to control this during write(). This runs as a
    post-processing pass to convert every self-closing tag back into an
    explicit open/close pair, matching the existing file convention.
    """
    return SELF_CLOSING_TAG_RE.sub(r"<\1\2></\1>", xml_text)


def write_nfo(tree: ET.ElementTree, path: Path) -> None:
    ET.indent(tree, space="  ")
    tree.write(path, encoding="utf-8", xml_declaration=True)
    text = path.read_text(encoding="utf-8")
    expanded = expand_self_closing_tags(text)
    if expanded != text:
        path.write_text(expanded, encoding="utf-8")


def existing_text(root: ET.Element, tag: str) -> Optional[str]:
    """Stripped text of the first `tag`, or None if absent/empty."""
    return (root.findtext(tag) or "").strip() or None


def _insert_index(root: ET.Element, after_tags: List[str]) -> int:
    """Index right after the first tag in `after_tags` that's present,
    else the end of <movie>."""
    children = list(root)
    for ref_tag in after_tags:
        ref_el = root.find(ref_tag)
        if ref_el is not None:
            return children.index(ref_el) + 1
    return len(children)


def set_attributed_tag_insert_after(root: ET.Element, tag: str, attrib: Dict[str, str],
                                     text: str, after_tags: List[str]) -> ET.Element:
    """
    Like set_text_insert_after, but matches/creates an element by BOTH tag
    name and a specific attribute value -- e.g. <stashid endpoint="javstash">
    -- rather than just the tag name, so multiple same-named tags can
    coexist distinguished by attribute (mirrors the existing
    <uniqueid type="dmm"> convention already used in these files).
    """
    for el in root.findall(tag):
        if all(el.get(k) == v for k, v in attrib.items()):
            el.text = text
            return el
    el = ET.Element(tag, attrib)
    root.insert(_insert_index(root, after_tags), el)
    el.text = text
    return el


def set_text_insert_after(root: ET.Element, tag: str, text: str,
                           after_tags: List[str]) -> ET.Element:
    """
    Set `tag`'s text, creating the element if it doesn't exist. When
    creating, insert it right after the first tag in `after_tags` that's
    actually present (falls back to appending at the end of <movie>).

    If MORE than one element with this tag already exists (e.g. a duplicate
    <originaltitle> written by a previous buggy run or an external tool),
    all but the first are removed before updating -- the first is kept as
    the positional anchor so the tag doesn't migrate around the file.
    """
    existing = root.findall(tag)
    if len(existing) > 1:
        log.debug("Removing %d duplicate <%s> element(s)", len(existing) - 1, tag)
        for dup in existing[1:]:
            root.remove(dup)
    el = existing[0] if existing else None
    if el is None:
        el = ET.Element(tag)
        root.insert(_insert_index(root, after_tags), el)
    el.text = text
    return el


def set_repeated_tag(root: ET.Element, tag: str, values: List[str],
                      after_tags: List[str]) -> None:
    """
    Replace ALL existing elements named `tag` with a fresh set, one per
    item in `values`, in order. On first write, the new elements are
    inserted right after the first tag in `after_tags` that's actually
    present. On later runs, they're re-inserted at the same position the
    old ones occupied, so repeated runs don't cause the block to migrate.
    """
    existing = root.findall(tag)
    insert_idx = None
    if existing:
        insert_idx = list(root).index(existing[0])
        for el in existing:
            root.remove(el)
    if insert_idx is None:
        insert_idx = _insert_index(root, after_tags)
    for offset, value in enumerate(values):
        el = ET.Element(tag)
        el.text = value
        root.insert(insert_idx + offset, el)


def has_genre(root: ET.Element, value: str) -> bool:
    return any(
        (el.text or "").strip().lower() == value.lower()
        for el in root.findall("genre")
    )


def add_genre(root: ET.Element, value: str, after_tags: List[str]) -> bool:
    """
    Append a <genre> element with `value`, unless one already exists
    (compared case-insensitively on stripped text) -- so re-running over
    an already-processed library is idempotent and never accumulates
    duplicate genres.

    Placement: right after the LAST existing <genre>, so the genre block
    stays contiguous instead of getting a stray entry elsewhere in the
    file. With no <genre> at all, falls back to the first present tag in
    `after_tags`, then to the end of <movie>.

    Returns True if an element was actually added.
    """
    if has_genre(root, value):
        return False
    existing = root.findall("genre")
    if existing:
        insert_idx = list(root).index(existing[-1]) + 1
    else:
        insert_idx = _insert_index(root, after_tags)
    el = ET.Element("genre")
    el.text = value
    root.insert(insert_idx, el)
    return True


def remove_genre(root: ET.Element, value: str) -> bool:
    """Remove every <genre> whose text matches `value` (case-insensitively).
    Returns True if anything was removed."""
    removed = False
    for el in root.findall("genre"):
        if (el.text or "").strip().lower() == value.lower():
            root.remove(el)
            removed = True
    return removed


# Per-run cache of Japanese genre -> translated English genre. The same few
# hundred DMM genres repeat across an entire library, so after the first
# handful of files most genres never hit the translator at all. Failures
# aren't cached, so a flaky translation gets retried on the next file that
# has the same genre.
_GENRE_TRANSLATION_CACHE: Dict[str, str] = {}

# Hiragana, katakana, CJK ideographs, full-width forms -- "contains any
# Japanese". A genre with none (e.g. "4K", an English tag) needs no
# translation call.
JAPANESE_RE = re.compile(r"[　-ヿ㐀-䶿一-鿿＀-￯]")


def translate_genres(translator: BaseTranslator, genres_jp: List[str]) -> Dict[str, str]:
    """
    Translate every not-yet-cached genre in ONE call (newline-joined), then
    split the result back apart. If the line count doesn't survive the
    round trip, falls back to one call per genre. Returns jp -> en for
    every genre that translated successfully (cached or fresh).
    """
    uncached = []
    for g in dict.fromkeys(genres_jp):
        if g in _GENRE_TRANSLATION_CACHE:
            continue
        if JAPANESE_RE.search(g):
            uncached.append(g)
        else:
            _GENRE_TRANSLATION_CACHE[g] = to_title_case(g)
    if uncached:
        fresh: List[Optional[str]] = []
        if len(uncached) == 1:
            fresh = [translator.translate_ja_to_en(uncached[0])]
        else:
            batch = translator.translate_ja_to_en("\n".join(uncached))
            if batch is None:
                # The call itself failed (already logged) -- don't multiply
                # the failure into one more failing call per genre.
                fresh = []
            else:
                lines = [ln.strip() for ln in batch.split("\n") if ln.strip()]
                if len(lines) == len(uncached):
                    fresh = lines
                else:
                    log.debug("Batched genre translation returned %d line(s) for %d "
                              "genre(s) -- falling back to one call per genre.",
                              len(lines), len(uncached))
                    fresh = [translator.translate_ja_to_en(g) for g in uncached]
        for genre_jp, translated in zip(uncached, fresh):
            if translated and translated.strip():
                _GENRE_TRANSLATION_CACHE[genre_jp] = to_title_case(translated.strip())
    return {g: _GENRE_TRANSLATION_CACHE[g] for g in genres_jp if g in _GENRE_TRANSLATION_CACHE}


def apply_genres(root: ET.Element, path: Path, genres_jp: List[str],
                 translator: BaseTranslator) -> List[str]:
    """
    Translate each scraped Japanese genre (minus GENRE_SKIP) and add it as
    a <genre> alongside whatever genres the file already has. Existing
    genres are never removed. Returns the list of genres actually added.
    """
    wanted = [g.strip() for g in genres_jp
              if g and g.strip() and g.strip() not in GENRE_SKIP]
    translations = translate_genres(translator, wanted)
    added = []
    for genre_jp in wanted:
        translated = translations.get(genre_jp)
        if translated is None:
            log.warning("%s: genre translation failed for %r -- skipping that genre.",
                        path.name, genre_jp)
            continue
        if add_genre(root, translated, after_tags=GENRE_AFTER_TAGS):
            added.append(translated)
    return added


def source_already_refreshed(root: ET.Element, source: str) -> bool:
    """
    True if `source` has already refreshed this file: its REFRESHED genre
    is present, or -- javstash only -- a legacy <stashid endpoint="javstash">
    written by the old jav_nfo_refresh.py, which predates the marker.
    """
    if has_genre(root, REFRESHED_GENRES[source]):
        return True
    if source == SOURCE_JAVSTASH:
        return any(el.get("endpoint") == JAVSTASH_ENDPOINT_NAME
                   for el in root.findall("stashid"))
    return False


def get_actor_names(root: ET.Element) -> List[str]:
    return [
        name_el.text.strip()
        for name_el in root.findall("./actor/name")
        if name_el.text and name_el.text.strip()
    ]


# Minor words left lowercase in headline-style title case, unless they're
# the first or last word. Deliberately NOT using str.title() -- it mangles
# apostrophes ("Don't" -> "Don'T") and capitalizes every word with no
# exceptions, which doesn't read as a real title.
TITLE_CASE_MINOR_WORDS = {
    "a", "an", "the", "and", "but", "or", "nor", "as", "at", "by", "for",
    "in", "of", "on", "per", "to", "vs", "via", "with", "from", "into",
    "onto", "over", "under", "up", "down", "off", "out",
}


def to_title_case(text: str) -> str:
    """
    Headline-style title case: capitalize each word's first letter (and
    lowercase the rest of its alphabetic run), except minor words (short
    articles/prepositions/conjunctions) stay lowercase unless they're the
    first or last word. Operates on whitespace-separated tokens, so any
    punctuation attached to a word (hyphens, brackets, slashes) is
    preserved exactly where it already is -- only the letters themselves
    get re-cased.
    """
    words = text.split(" ")
    last_idx = len(words) - 1
    result = []
    for i, word in enumerate(words):
        if not word:
            result.append(word)
            continue
        if i not in (0, last_idx) and word.lower() in TITLE_CASE_MINOR_WORDS:
            result.append(word.lower())
            continue
        for j, ch in enumerate(word):
            if ch.isalpha():
                result.append(word[:j] + ch.upper() + word[j + 1:].lower())
                break
        else:
            result.append(word)  # no alphabetic character in this token at all
    return " ".join(result)


def build_new_title(actor_str: str, group: Optional[str], translated_title: str,
                     code: str) -> str:
    parts = []
    if actor_str:
        parts.append(actor_str)
    middle = f"[{group}] {translated_title}" if group else translated_title
    parts.append(middle)
    title = " - ".join(parts) if len(parts) > 1 else middle
    return f"{title} [{code}]"


# Written unconditionally whenever title translation succeeds, regardless
# of --no-backup -- this is the marker the already-translated check reads,
# decoupled from whether <title_backup> (the actual backup text) exists.
# Holds an ISO timestamp, mostly just so the tag isn't suspiciously empty
# and so you have an audit trail of when it happened.
TRANSLATED_MARKER_TAG = "title_translated"


def was_already_translated(root: ET.Element) -> bool:
    """
    True if this file shows ANY sign of having been translated before --
    checks both the dedicated marker (TRANSLATED_MARKER_TAG, written
    regardless of --no-backup) and the older <title_backup> tag (so files
    processed before this marker existed still get recognized).
    """
    return root.find(TRANSLATED_MARKER_TAG) is not None or root.find("title_backup") is not None


def stamp_translated_marker(root: ET.Element) -> None:
    """
    Write the TRANSLATED_MARKER_TAG timestamp, refreshing it if it's
    already there -- so the marker always says when <title>/<plot> were
    LAST translated, not when they first were. Called after every
    successful title or plot translation, in every mode.
    """
    set_text_insert_after(root, TRANSLATED_MARKER_TAG,
                          datetime.now().isoformat(timespec="seconds"),
                          after_tags=["title_backup", "title"])


def apply_title_rebuild(root: ET.Element, path: Path, code: str,
                         new_originaltitle: str, translator: BaseTranslator,
                         no_backup: bool = False) -> bool:
    """Returns True if <title> was actually rewritten, False if translation
    failed and nothing was touched."""
    translated = translator.translate_ja_to_en(new_originaltitle)
    if translated is None:
        log.warning("%s: title translation failed -- leaving <title> untouched.", path.name)
        return False
    translated = to_title_case(translated)

    title_el = root.find("title")
    old_title_text = title_el.text if title_el is not None else None

    actor_str = ", ".join(get_actor_names(root))
    group = detect_group(path, existing_title=old_title_text)
    new_title = build_new_title(actor_str, group, translated, code)

    if not no_backup:
        backup_el = root.find("title_backup")
        if backup_el is None and old_title_text is not None:
            set_text_insert_after(root, "title_backup", old_title_text, after_tags=["title"])

    stamp_translated_marker(root)

    if title_el is None:
        title_el = ET.Element("title")
        root.insert(0, title_el)
    title_el.text = new_title
    return True


# ---------------------------------------------------------------------------
# Per-file processing
# ---------------------------------------------------------------------------

def backup_nfo_file(path: Path, mode: str) -> None:
    backup_path = path.with_suffix(path.suffix + ".bak")
    if mode == "skip":
        return
    if mode == "once" and backup_path.exists():
        return
    shutil.copy2(path, backup_path)


class ProcessResult(NamedTuple):
    status: str
    code: Optional[str] = None
    detail: Optional[str] = None
    tagline: Optional[str] = None
    # per-source outcome this run: "hit" / "miss" / "error" (queried
    # sources only) -- surfaced as javstash_hit, libredmm_miss, ... counters
    outcomes: Optional[Dict[str, str]] = None


def merge_field(field: str, file_tag: str, root: ET.Element,
                data: Dict[str, SourceData], protected: List[str]) -> Optional[str]:
    """
    Pick a field's value in SOURCE_PRIORITY order. The winning source
    contributes what it returned; a PROTECTED source (higher priority,
    already refreshed this file in the past) contributes whatever the file
    already holds -- its data keeps its priority, so a lower source only
    fills what's missing. First non-empty value wins.
    """
    for source in SOURCE_PRIORITY:
        if source in data:
            value = getattr(data[source], field)
            if value:
                return value
        elif source in protected:
            value = existing_text(root, file_tag)
            if value:
                return value
    return None


def parse_source_list(value: str) -> List[str]:
    """
    Source list parser: a single source name, a comma-separated subset, or
    one of SOURCE_ALIASES. Always returned in SOURCE_PRIORITY order, since
    priority is fixed no matter how the user lists them.
    """
    chosen: List[str] = []
    for name in (n.strip().lower() for n in value.split(",")):
        if not name:
            continue
        if name in SOURCE_ALIASES:
            chosen.extend(SOURCE_ALIASES[name])
        elif name in SOURCE_PRIORITY:
            chosen.append(name)
        else:
            raise ValueError(
                f"unknown source {name!r} -- choose from "
                f"{', '.join(SOURCE_PRIORITY + list(SOURCE_ALIASES))}")
    sources = [s for s in SOURCE_PRIORITY if s in chosen]
    if not sources:
        raise ValueError("at least one source is required")
    return sources


MISS_STATUSES = {"no_code", "no_match", "api_error", "parse_error", "no_changes", "no_source_text"}

# The subset of MISS_STATUSES that means "no source found this file's
# metadata" and so earns the FAILED_GENRE marker (see
# mark_metadata_refresh_failed). no_match/api_error are only ever returned
# when EVERY queried source missed and nothing already refreshed the file.
# Deliberately narrower than MISS_STATUSES:
#   parse_error     the file isn't valid XML -- can't be rewritten safely
#   no_changes      a source DID match; it just had nothing usable in it
#   no_source_text  --translate-only ran with nothing to translate
# Never populated in --translate-only mode, which does no lookups.
NOT_FOUND_STATUSES = {"no_code", "no_match", "api_error"}

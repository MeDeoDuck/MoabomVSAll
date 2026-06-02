"""
YouTube transcript fetching service.

흐름: 워커(주거용 IP) 우선 시도 → 실패 시 로컬 폴백.

로컬 폴백은 **fetch worker(services/fetch_worker/transcript_logic.py)와 동일한
yt-dlp 세팅**을 사용한다. 워커가 429 를 피하는 핵심은 특별한 옵션이 아니라
**쿠키 파일(로그인 세션)** 이다 — yt-dlp(cookiefile) 와 requests 세션
(MozillaCookieJar) 양쪽에 같은 쿠키를 붙여 "로그인 사용자"로 timedtext 를
받으면 IP 레이트리밋(429)이 사실상 해소된다. (워커 docstring 참고)

옵션 B (원본 언어 우선, 자동번역 회피):
  info['automatic_captions'] 는 번역 가능한 모든 타겟 언어 목록이라, 영어
  영상의 'ko' 항목 URL 은 manual(en) 에 &tlang=ko 를 붙인 **실시간 자동번역**
  (429 유발 + 품질 낮음)이다. 그래서 URL 에 `tlang=` 이 있으면 건너뛰어
  **원본 자막(manual + 원본 auto)만** 받는다. 모아봄 보고서 agent 는 자막
  언어를 가리지 않고(GPT-4.1) 한국어 보고서를 내므로, en/ja 원본을 받아도
  결과는 한국어다 — YouTube 번역을 시킬 이유가 없다.

쿠키 경로 해석 순서 (워커와 동일 규칙):
  1. YT_COOKIES_PATH env (워커 호환) 또는 YT_COOKIE_FILE env (스펙 호환)
  2. <repo>/.secrets/yt_cookies.txt

반환: {transcript_text, language_code, segment_count} 또는 None.
"""
from typing import Optional, Dict, Any, List
import json
import os
import time
from http.cookiejar import MozillaCookieJar

import requests
import yt_dlp


_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
_DEFAULT_COOKIES_PATH = os.path.join(_REPO_ROOT, ".secrets", "yt_cookies.txt")


def _preferred_langs() -> List[str]:
    """자막 우선 언어 큐 (호출 시점에 env 읽음).

    기본 "ko,en" = 워커와 동일. 옵션 B(원본 받아 GPT 번역)를 살리려면 en/ja 를
    포함(예: TRANSCRIPT_LANGS=ko,en,ja). 한글 원본만: TRANSCRIPT_LANGS=ko.
    """
    raw = os.getenv("TRANSCRIPT_LANGS", "ko,en")
    return [s.strip() for s in raw.split(",") if s.strip()] or ["ko", "en"]


def _resolve_cookies_path() -> Optional[str]:
    """쿠키 파일 경로 (워커와 동일 규칙). 없으면 None."""
    for env_name in ("YT_COOKIES_PATH", "YT_COOKIE_FILE"):
        p = os.environ.get(env_name)
        if p and os.path.exists(p):
            return p
    if os.path.exists(_DEFAULT_COOKIES_PATH):
        return _DEFAULT_COOKIES_PATH
    return None


def _build_session() -> requests.Session:
    """쿠키 적용된 requests 세션 (워커 _build_session 과 동일)."""
    s = requests.Session()
    p = _resolve_cookies_path()
    if p:
        jar = MozillaCookieJar(p)
        try:
            jar.load(ignore_discard=True, ignore_expires=True)
            s.cookies = jar  # type: ignore[assignment]
        except Exception:
            pass
    return s


def _parse_json3(content: str) -> Optional[str]:
    try:
        data = json.loads(content)
    except (json.JSONDecodeError, TypeError):
        return None
    parts: List[str] = []
    for event in (data.get("events") or []):
        for seg in (event.get("segs") or []):
            if "utf8" in seg:
                parts.append(seg["utf8"])
    text = " ".join(parts).strip()
    return text or None


def _parse_vtt(content: str) -> Optional[str]:
    parts: List[str] = []
    for line in content.split("\n"):
        line = line.strip()
        if line and not line.startswith("WEBVTT") and "-->" not in line:
            parts.append(line)
    text = " ".join(parts).strip()
    return text or None


def _fetch_with_backoff(session: requests.Session, url: str, max_retries: int = 3) -> Optional[str]:
    """워커 _fetch_with_backoff 와 동일 (429 지수 백오프)."""
    for attempt in range(max_retries):
        try:
            response = session.get(url, timeout=30)
            if response.status_code == 429:
                wait = 2 ** attempt
                print(f"[TRANSCRIPT] 429 Too Many Requests, retry {attempt + 1}/{max_retries} after {wait}s")
                if attempt < max_retries - 1:
                    time.sleep(wait)
                    continue
                print(f"[TRANSCRIPT] Max retries exceeded for URL")
                return None
            response.raise_for_status()
            return response.text
        except requests.exceptions.Timeout:
            if attempt < max_retries - 1:
                time.sleep(2 ** attempt)
                continue
            return None
        except requests.exceptions.RequestException as e:
            print(f"[TRANSCRIPT] Request error: {e}")
            return None
    return None


def _fetch_via_worker(video_id: str, base_url: str, token: str) -> Optional[Dict[str, Any]]:
    """POST to fetch worker /transcript. Returns dict on 200, None otherwise."""
    url = base_url.rstrip("/") + "/transcript"
    headers = {"Authorization": f"Bearer {token}"}
    payload = {"video_id": video_id}

    last_status: Optional[int] = None
    for attempt in range(3):
        try:
            resp = requests.post(url, json=payload, headers=headers, timeout=60)
            last_status = resp.status_code
            if resp.status_code == 200:
                data = resp.json()
                return {
                    "transcript_text": data["transcript_text"],
                    "language_code": data["language_code"],
                    "segment_count": data["segment_count"],
                }
            if resp.status_code == 404:
                print(f"[TRANSCRIPT] worker: no transcript for {video_id}")
                return None
            if 500 <= resp.status_code < 600:
                print(f"[TRANSCRIPT] worker 5xx ({resp.status_code}), retry {attempt + 1}/3")
                time.sleep(2 ** attempt)
                continue
            print(f"[TRANSCRIPT] worker client error {resp.status_code}: {resp.text[:200]}")
            return None
        except requests.exceptions.RequestException as e:
            print(f"[TRANSCRIPT] worker request error attempt {attempt + 1}/3: {type(e).__name__}: {e}")
            time.sleep(2 ** attempt)
            continue

    print(f"[TRANSCRIPT] worker exhausted retries (last_status={last_status})")
    return None


def _fetch_local(video_id: str, url: str) -> Optional[Dict[str, Any]]:
    """로컬 폴백 — 워커 fetch_transcript 와 동일 세팅(쿠키 + extract_info +
    requests 세션) + 옵션 B(tlang 자동번역 URL 제외)."""
    # process=False: format/n-challenge 파이프라인 회피 (워커와 동일 이유).
    ydl_opts: Dict[str, Any] = {
        "quiet": True,
        "no_warnings": True,
        "skip_download": True,
    }
    cookie_path = _resolve_cookies_path()
    if cookie_path:
        ydl_opts["cookiefile"] = cookie_path
    else:
        print("[TRANSCRIPT] [WARN] 쿠키 파일 없음 — 429 위험 (.secrets/yt_cookies.txt 또는 YT_COOKIES_PATH)")

    try:
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            print(f"[TRANSCRIPT] Extracting metadata from {url}")
            info = ydl.extract_info(url, download=False, process=False)
    except Exception as e:  # noqa: BLE001
        print(f"[TRANSCRIPT] metadata extract failed: {type(e).__name__}: {str(e)[:150]}")
        return None

    # Manual 우선, 그 다음 auto. (automatic_captions 는 번역 타겟 목록이라 항상
    # truthy → manual 이 'or' 단락으로 누락되지 않게 분리해서 합친다.)
    manual_subs = info.get("subtitles") or {}
    auto_subs = info.get("automatic_captions") or {}
    print(f"[TRANSCRIPT] manual langs: {list(manual_subs.keys())[:8]} | auto langs: {list(auto_subs.keys())[:8]}")

    session = _build_session()
    preferred_formats = ("json3", "vtt")

    for lang in _preferred_langs():
        items = (manual_subs.get(lang) or []) + (auto_subs.get(lang) or [])
        if not items:
            continue
        print(f"[TRANSCRIPT] Trying language: {lang} ({len(items)} sources)")
        for item in items:
            if not isinstance(item, dict) or "url" not in item:
                continue
            sub_url = item["url"]
            # 옵션 B: tlang= (자동번역) URL 은 건너뛴다 → 원본 자막만.
            if "tlang=" in sub_url:
                continue
            ext = item.get("ext", "")
            if ext not in preferred_formats:
                continue
            print(f"[TRANSCRIPT] Fetching {lang}/{ext}: {sub_url[:60]}...")
            content = _fetch_with_backoff(session, sub_url)
            if not content:
                continue
            text = _parse_json3(content) if ext == "json3" else _parse_vtt(content)
            if text:
                print(f"[TRANSCRIPT] SUCCESS: {len(text)} chars, language={lang}, format={ext}")
                return {
                    "transcript_text": text,
                    "language_code": lang,
                    "segment_count": len(text.split()),
                }

    print(f"[TRANSCRIPT] No transcript available")
    return None


def fetch_video_transcript(video_id: str) -> Optional[Dict[str, Any]]:
    """워커 우선 → 로컬 폴백. 반환 계약: {transcript_text, language_code, segment_count} | None."""
    print(f"[TRANSCRIPT] Fetching for video_id={video_id}")
    url = f"https://www.youtube.com/watch?v={video_id}"

    worker_url = os.environ.get("YOUTUBE_FETCH_WORKER_URL")
    worker_token = os.environ.get("YOUTUBE_FETCH_WORKER_TOKEN")
    if worker_url and worker_token:
        print(f"[TRANSCRIPT] Trying worker first: {worker_url}")
        result = _fetch_via_worker(video_id, worker_url, worker_token)
        if result is not None:
            print(f"[TRANSCRIPT] worker SUCCESS: {len(result['transcript_text'])} chars")
            return result
        print(f"[TRANSCRIPT] Falling back to local fetch")

    try:
        return _fetch_local(video_id, url)
    except Exception as e:  # noqa: BLE001
        print(f"[TRANSCRIPT] Failed: {type(e).__name__}: {str(e)[:150]}")
        import traceback
        traceback.print_exc()
        return None

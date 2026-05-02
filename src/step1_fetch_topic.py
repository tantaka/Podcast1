"""
ステップ1: Claude Code の前日リリースバージョンを取得する。
GitHub の CHANGELOG.md と Releases API を両方確認し、前日に公開されたバージョンを返す。
"""

import json
import re
import time
import urllib.request
import urllib.error
from datetime import datetime, timedelta, timezone
from pathlib import Path

from config import logger, OUTPUT_DIR, MAX_RETRIES, RETRY_DELAY_BASE


def _http_get(url: str, headers: dict = None) -> dict | str | None:
    req = urllib.request.Request(url, headers=headers or {})
    for attempt in range(MAX_RETRIES):
        try:
            with urllib.request.urlopen(req, timeout=30) as resp:
                body = resp.read().decode()
                content_type = resp.headers.get("Content-Type", "")
                if "json" in content_type:
                    return json.loads(body)
                return body
        except urllib.error.HTTPError as e:
            if e.code == 403 and "rate limit" in e.reason.lower():
                wait = RETRY_DELAY_BASE * (2 ** attempt)
                logger.warning(f"GitHub レート制限: {wait}秒待機 (試行 {attempt+1}/{MAX_RETRIES})")
                time.sleep(wait)
            else:
                logger.error(f"HTTP エラー {e.code}: {url}")
                raise
        except Exception as e:
            logger.error(f"リクエスト失敗: {e}")
            if attempt < MAX_RETRIES - 1:
                time.sleep(RETRY_DELAY_BASE)
            else:
                raise
    return None


def _get_yesterday_jst() -> str:
    jst = timezone(timedelta(hours=9))
    return (datetime.now(jst) - timedelta(days=1)).strftime("%Y-%m-%d")


def _fetch_via_releases_api(yesterday: str) -> dict | None:
    """GitHub Releases API から前日リリースを取得する。"""
    logger.info("GitHub Releases API で前日リリースを検索中...")
    url = "https://api.github.com/repos/anthropics/claude-code/releases?per_page=20"
    releases = _http_get(url, {"Accept": "application/vnd.github.v3+json", "User-Agent": "podcast-bot"})

    if not isinstance(releases, list):
        logger.warning("Releases API の応答が予期しない形式です")
        return None

    for r in releases:
        published = r.get("published_at", "")[:10]
        if published == yesterday:
            version = r.get("tag_name", "").lstrip("v")
            logger.info(f"Releases API でバージョン {version} を発見 (公開日: {published})")
            return {
                "version": version,
                "title": r.get("name", f"Claude Code {version}"),
                "body": r.get("body", ""),
                "published_at": published,
                "source": "releases_api",
            }

    logger.info(f"Releases API に {yesterday} のリリースは見つかりませんでした")
    return None


def _fetch_via_changelog(yesterday: str) -> dict | None:
    """CHANGELOG.md から前日リリースを取得する。"""
    logger.info("CHANGELOG.md から前日リリースを検索中...")
    url = "https://raw.githubusercontent.com/anthropics/claude-code/main/CHANGELOG.md"
    text = _http_get(url)

    if not isinstance(text, str):
        logger.warning("CHANGELOG.md の取得に失敗しました")
        return None

    # パターン例: ## [1.2.3] - 2025-05-01  または  ## 1.2.3 (2025-05-01)
    patterns = [
        rf"## \[(.+?)\] - {yesterday}(.*?)(?=\n## |\Z)",
        rf"## (.+?) \({yesterday}\)(.*?)(?=\n## |\Z)",
        rf"## (.+?) - {yesterday}(.*?)(?=\n## |\Z)",
    ]

    for pat in patterns:
        m = re.search(pat, text, re.DOTALL)
        if m:
            version = m.group(1).strip().lstrip("v")
            body = m.group(2).strip()
            logger.info(f"CHANGELOG.md でバージョン {version} を発見 (日付: {yesterday})")
            return {
                "version": version,
                "title": f"Claude Code {version}",
                "body": body,
                "published_at": yesterday,
                "source": "changelog",
            }

    logger.info(f"CHANGELOG.md に {yesterday} のエントリは見つかりませんでした")
    return None


def fetch_topic(date_override: str = None) -> dict | None:
    """
    前日に公開された Claude Code バージョン情報を返す。
    見つからない場合は None を返す。
    date_override: テスト用に日付を指定できる (YYYY-MM-DD)
    """
    yesterday = date_override or _get_yesterday_jst()
    logger.info(f"対象日付: {yesterday}")

    topic = _fetch_via_releases_api(yesterday)
    if not topic:
        topic = _fetch_via_changelog(yesterday)

    if topic:
        out_path = OUTPUT_DIR / "topic.json"
        out_path.write_text(json.dumps(topic, ensure_ascii=False, indent=2), encoding="utf-8")
        logger.info(f"トピック情報を保存: {out_path}")
    else:
        logger.warning(f"{yesterday} に公開された Claude Code バージョンは見つかりませんでした")

    return topic


if __name__ == "__main__":
    import sys
    date_arg = sys.argv[1] if len(sys.argv) > 1 else None
    result = fetch_topic(date_arg)
    if result:
        print(json.dumps(result, ensure_ascii=False, indent=2))
    else:
        print("トピックが見つかりませんでした")
        sys.exit(1)

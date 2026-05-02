"""
ステップ6: MP3 と成果物を Google Drive にアップロードし、
履歴 JSON を更新する。
"""

import json
import time
import urllib.request
import urllib.error
import urllib.parse
from datetime import datetime, timezone, timedelta
from pathlib import Path

from config import (
    logger, OUTPUT_DIR,
    GOOGLE_CLIENT_ID, GOOGLE_CLIENT_SECRET, GOOGLE_REFRESH_TOKEN,
    GOOGLE_TOKEN_URL, GOOGLE_DRIVE_UPLOAD_URL, GOOGLE_DRIVE_FILES_URL,
    DRIVE_FOLDER_NAME, HISTORY_FILE_NAME,
    MAX_RETRIES, RETRY_DELAY_BASE, RETRY_DELAY_MAX,
)


def _get_access_token() -> str:
    """refresh_token を使ってアクセストークンを取得する。"""
    if not all([GOOGLE_CLIENT_ID, GOOGLE_CLIENT_SECRET, GOOGLE_REFRESH_TOKEN]):
        raise ValueError(
            "Google 認証情報が不足しています。"
            "GOOGLE_CLIENT_ID / GOOGLE_CLIENT_SECRET / GOOGLE_REFRESH_TOKEN を確認してください"
        )

    data = urllib.parse.urlencode({
        "client_id": GOOGLE_CLIENT_ID,
        "client_secret": GOOGLE_CLIENT_SECRET,
        "refresh_token": GOOGLE_REFRESH_TOKEN,
        "grant_type": "refresh_token",
    }).encode()

    req = urllib.request.Request(
        GOOGLE_TOKEN_URL,
        data=data,
        headers={"Content-Type": "application/x-www-form-urlencoded"},
        method="POST",
    )

    with urllib.request.urlopen(req, timeout=30) as resp:
        token_data = json.loads(resp.read().decode())

    access_token = token_data.get("access_token")
    if not access_token:
        raise RuntimeError(f"アクセストークン取得失敗: {token_data}")

    logger.info("Google アクセストークン取得成功")
    return access_token


def _find_or_create_folder(access_token: str, folder_name: str) -> str:
    """Drive 内のフォルダを検索し、なければ作成する。"""
    query = urllib.parse.urlencode({
        "q": f"name='{folder_name}' and mimeType='application/vnd.google-apps.folder' and trashed=false",
        "fields": "files(id, name)",
    })
    req = urllib.request.Request(
        f"{GOOGLE_DRIVE_FILES_URL}?{query}",
        headers={"Authorization": f"Bearer {access_token}"},
    )
    with urllib.request.urlopen(req, timeout=30) as resp:
        files = json.loads(resp.read().decode()).get("files", [])

    if files:
        folder_id = files[0]["id"]
        logger.info(f"既存フォルダ '{folder_name}' を使用: {folder_id}")
        return folder_id

    # フォルダを作成
    meta = json.dumps({
        "name": folder_name,
        "mimeType": "application/vnd.google-apps.folder",
    }).encode()
    req = urllib.request.Request(
        GOOGLE_DRIVE_FILES_URL,
        data=meta,
        headers={
            "Authorization": f"Bearer {access_token}",
            "Content-Type": "application/json",
        },
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=30) as resp:
        folder = json.loads(resp.read().decode())

    folder_id = folder["id"]
    logger.info(f"フォルダ '{folder_name}' を作成: {folder_id}")
    return folder_id


def _upload_file(access_token: str, file_path: Path, folder_id: str, mime_type: str) -> str:
    """ファイルを multipart upload でアップロードする。"""
    file_data = file_path.read_bytes()
    meta = json.dumps({
        "name": file_path.name,
        "parents": [folder_id],
    }).encode()

    boundary = "===BOUNDARY==="
    body = (
        f"--{boundary}\r\n"
        f"Content-Type: application/json; charset=UTF-8\r\n\r\n"
    ).encode() + meta + (
        f"\r\n--{boundary}\r\n"
        f"Content-Type: {mime_type}\r\n\r\n"
    ).encode() + file_data + f"\r\n--{boundary}--".encode()

    for attempt in range(MAX_RETRIES):
        try:
            req = urllib.request.Request(
                f"{GOOGLE_DRIVE_UPLOAD_URL}?uploadType=multipart",
                data=body,
                headers={
                    "Authorization": f"Bearer {access_token}",
                    "Content-Type": f"multipart/related; boundary={boundary}",
                    "Content-Length": str(len(body)),
                },
                method="POST",
            )
            with urllib.request.urlopen(req, timeout=120) as resp:
                result = json.loads(resp.read().decode())
            file_id = result["id"]
            logger.info(f"アップロード完了: {file_path.name} → Drive ID: {file_id}")
            return file_id

        except urllib.error.HTTPError as e:
            if e.code in (429, 500, 503):
                wait = min(RETRY_DELAY_BASE * (2 ** attempt), RETRY_DELAY_MAX)
                logger.warning(f"Drive API エラー {e.code}: {wait}秒後にリトライ (試行 {attempt+1}/{MAX_RETRIES})")
                time.sleep(wait)
            else:
                raise

    raise RuntimeError(f"{file_path.name} のアップロードに失敗しました")


def _fetch_history(access_token: str, folder_id: str) -> dict:
    """Drive から履歴 JSON を取得する。なければ空の履歴を返す。"""
    query = urllib.parse.urlencode({
        "q": f"name='{HISTORY_FILE_NAME}' and '{folder_id}' in parents and trashed=false",
        "fields": "files(id, name)",
    })
    req = urllib.request.Request(
        f"{GOOGLE_DRIVE_FILES_URL}?{query}",
        headers={"Authorization": f"Bearer {access_token}"},
    )
    with urllib.request.urlopen(req, timeout=30) as resp:
        files = json.loads(resp.read().decode()).get("files", [])

    if not files:
        logger.info("履歴ファイルが存在しません。新規作成します")
        return {"episodes": []}

    file_id = files[0]["id"]
    req = urllib.request.Request(
        f"{GOOGLE_DRIVE_FILES_URL}/{file_id}?alt=media",
        headers={"Authorization": f"Bearer {access_token}"},
    )
    with urllib.request.urlopen(req, timeout=30) as resp:
        history = json.loads(resp.read().decode())
    logger.info(f"履歴を取得: {len(history.get('episodes', []))} エピソード")
    return history


def _update_history(access_token: str, folder_id: str, history: dict) -> None:
    """Drive 上の履歴ファイルを更新または作成する。"""
    history_bytes = json.dumps(history, ensure_ascii=False, indent=2).encode("utf-8")

    # 既存ファイルを検索
    query = urllib.parse.urlencode({
        "q": f"name='{HISTORY_FILE_NAME}' and '{folder_id}' in parents and trashed=false",
        "fields": "files(id)",
    })
    req = urllib.request.Request(
        f"{GOOGLE_DRIVE_FILES_URL}?{query}",
        headers={"Authorization": f"Bearer {access_token}"},
    )
    with urllib.request.urlopen(req, timeout=30) as resp:
        files = json.loads(resp.read().decode()).get("files", [])

    if files:
        # 既存ファイルを上書き
        file_id = files[0]["id"]
        req = urllib.request.Request(
            f"{GOOGLE_DRIVE_UPLOAD_URL}/{file_id}?uploadType=media",
            data=history_bytes,
            headers={
                "Authorization": f"Bearer {access_token}",
                "Content-Type": "application/json",
            },
            method="PATCH",
        )
        with urllib.request.urlopen(req, timeout=30) as resp:
            resp.read()
        logger.info(f"履歴ファイルを更新: {file_id}")
    else:
        # 新規作成
        tmp_path = OUTPUT_DIR / HISTORY_FILE_NAME
        tmp_path.write_bytes(history_bytes)
        _upload_file(access_token, tmp_path, folder_id, "application/json")
        tmp_path.unlink(missing_ok=True)


def upload_podcast(mp3_path: Path, script_data: dict, research: dict) -> dict:
    """
    MP3、台本、調査結果を Google Drive にアップロードし、
    履歴を更新して結果を返す。
    """
    access_token = _get_access_token()
    folder_id = _find_or_create_folder(access_token, DRIVE_FOLDER_NAME)

    # 履歴を取得して重複チェック
    history = _fetch_history(access_token, folder_id)
    version = script_data["version"]
    existing = [e["version"] for e in history.get("episodes", [])]
    if version in existing:
        logger.warning(f"v{version} は既にアップロード済みです。スキップします")
        return {"skipped": True, "version": version}

    jst = timezone(timedelta(hours=9))
    now_str = datetime.now(jst).strftime("%Y-%m-%d %H:%M JST")
    episode_folder_name = f"v{version}_{datetime.now(jst).strftime('%Y%m%d')}"

    # エピソード用サブフォルダ作成
    episode_folder_id = _find_or_create_folder(access_token, episode_folder_name)

    # MP3 アップロード
    mp3_id = _upload_file(access_token, mp3_path, episode_folder_id, "audio/mpeg")

    # 台本テキストをアップロード
    script_txt_path = OUTPUT_DIR / "script.txt"
    if script_txt_path.exists():
        _upload_file(access_token, script_txt_path, episode_folder_id, "text/plain")

    # 調査結果をアップロード
    research_path = OUTPUT_DIR / "research.json"
    if research_path.exists():
        _upload_file(access_token, research_path, episode_folder_id, "application/json")

    # 履歴を更新
    episode_entry = {
        "version": version,
        "title": research.get("topic", {}).get("title", f"Claude Code {version}"),
        "mp3_id": mp3_id,
        "folder_id": episode_folder_id,
        "uploaded_at": now_str,
    }
    history.setdefault("episodes", []).append(episode_entry)
    _update_history(access_token, folder_id, history)

    logger.info(f"v{version} のアップロード完了")
    return {"uploaded": True, "version": version, "mp3_id": mp3_id}


if __name__ == "__main__":
    import sys
    mp3_files = list(OUTPUT_DIR.glob("*.mp3"))
    if not mp3_files:
        print("MP3 ファイルが見つかりません。先に step5 を実行してください")
        sys.exit(1)
    mp3_path = mp3_files[0]
    script_data = json.loads((OUTPUT_DIR / "script.json").read_text(encoding="utf-8"))
    research = json.loads((OUTPUT_DIR / "research.json").read_text(encoding="utf-8"))
    result = upload_podcast(mp3_path, script_data, research)
    print(json.dumps(result, ensure_ascii=False, indent=2))

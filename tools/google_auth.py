"""
Google OAuth2 認証ツール（初回のみ手動実行）。
取得した refresh_token を GitHub Secrets に登録してください。

使い方:
  python tools/google_auth.py

事前に必要なもの:
  1. Google Cloud Console でプロジェクトを作成
  2. Google Drive API を有効化
  3. OAuth 2.0 クライアント ID を作成（アプリ種別: デスクトップ）
  4. クライアント ID とシークレットを環境変数に設定:
       GOOGLE_CLIENT_ID=xxx
       GOOGLE_CLIENT_SECRET=xxx
"""

import json
import os
import sys
import urllib.parse
import urllib.request
import http.server
import threading
import webbrowser
from typing import Optional

CLIENT_ID = os.environ.get("GOOGLE_CLIENT_ID", "")
CLIENT_SECRET = os.environ.get("GOOGLE_CLIENT_SECRET", "")
REDIRECT_URI = "http://localhost:8080"
SCOPE = "https://www.googleapis.com/auth/drive.file"
AUTH_URL = "https://accounts.google.com/o/oauth2/v2/auth"
TOKEN_URL = "https://oauth2.googleapis.com/token"


def _get_auth_code() -> Optional[str]:
    """ローカルサーバーで認証コードを受け取る。"""
    auth_code = None
    server_ready = threading.Event()

    class Handler(http.server.BaseHTTPRequestHandler):
        def do_GET(self):
            nonlocal auth_code
            params = urllib.parse.parse_qs(urllib.parse.urlparse(self.path).query)
            if "code" in params:
                auth_code = params["code"][0]
                self.send_response(200)
                self.send_header("Content-Type", "text/html; charset=utf-8")
                self.end_headers()
                self.wfile.write(
                    "<html><body><h2>認証成功！このウィンドウを閉じてください。</h2></body></html>".encode()
                )
            else:
                self.send_response(400)
                self.end_headers()

        def log_message(self, *args):
            pass  # ログを抑制

    server = http.server.HTTPServer(("localhost", 8080), Handler)

    def run_server():
        server_ready.set()
        server.handle_request()

    thread = threading.Thread(target=run_server, daemon=True)
    thread.start()
    server_ready.wait()
    return auth_code


def _exchange_code_for_tokens(code: str) -> dict:
    data = urllib.parse.urlencode({
        "code": code,
        "client_id": CLIENT_ID,
        "client_secret": CLIENT_SECRET,
        "redirect_uri": REDIRECT_URI,
        "grant_type": "authorization_code",
    }).encode()
    req = urllib.request.Request(
        TOKEN_URL,
        data=data,
        headers={"Content-Type": "application/x-www-form-urlencoded"},
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=30) as resp:
        return json.loads(resp.read().decode())


def main():
    if not CLIENT_ID or not CLIENT_SECRET:
        print("エラー: GOOGLE_CLIENT_ID と GOOGLE_CLIENT_SECRET を環境変数に設定してください")
        sys.exit(1)

    # 認証 URL を構築してブラウザで開く
    params = urllib.parse.urlencode({
        "client_id": CLIENT_ID,
        "redirect_uri": REDIRECT_URI,
        "response_type": "code",
        "scope": SCOPE,
        "access_type": "offline",
        "prompt": "consent",
    })
    auth_url = f"{AUTH_URL}?{params}"

    print("ブラウザで Google 認証を行います...")
    print(f"自動で開かない場合は以下の URL を開いてください:\n{auth_url}\n")
    webbrowser.open(auth_url)

    # コールバックを待機
    print("認証完了を待機中...")
    code = _get_auth_code()
    if not code:
        print("エラー: 認証コードを受け取れませんでした")
        sys.exit(1)

    # トークンを取得
    tokens = _exchange_code_for_tokens(code)

    refresh_token = tokens.get("refresh_token")
    if not refresh_token:
        print("エラー: refresh_token が取得できませんでした")
        print(f"応答: {tokens}")
        sys.exit(1)

    print("\n=== 認証成功 ===")
    print(f"refresh_token: {refresh_token}")
    print("\n以下を GitHub Secrets に登録してください:")
    print(f"  GOOGLE_CLIENT_ID     = {CLIENT_ID}")
    print(f"  GOOGLE_CLIENT_SECRET = {CLIENT_SECRET}")
    print(f"  GOOGLE_REFRESH_TOKEN = {refresh_token}")


if __name__ == "__main__":
    main()

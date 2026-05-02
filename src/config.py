import os
import logging
from pathlib import Path

BASE_DIR = Path(__file__).parent.parent
OUTPUT_DIR = BASE_DIR / "output"
OUTPUT_DIR.mkdir(exist_ok=True)

# ログ設定
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger(__name__)

# 環境変数
GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY", "")
GOOGLE_CLIENT_ID = os.environ.get("GOOGLE_CLIENT_ID", "")
GOOGLE_CLIENT_SECRET = os.environ.get("GOOGLE_CLIENT_SECRET", "")
GOOGLE_REFRESH_TOKEN = os.environ.get("GOOGLE_REFRESH_TOKEN", "")

# Gemini モデル (調査・台本生成)
RESEARCH_MODEL = "gemini-2.5-flash-lite"
RESEARCH_MODEL_FALLBACK = "gemini-2.5-flash"

# Gemini TTS モデル
TTS_MODEL = "gemini-3.1-flash-tts-preview"
TTS_MODEL_FALLBACK = "gemini-2.5-flash-preview-tts"

# 音声設定 (日本語品質の良い声を選択)
VOICE_MALE = "Kore"     # 男性声
VOICE_FEMALE = "Aoede"  # 女性声
SPEAKER_MALE = "田中"
SPEAKER_FEMALE = "鈴木"

# TTS 分割制限 (無料枠の安定動作のため2000文字未満)
MAX_CHARS_PER_TTS = 1800

# Google Drive
DRIVE_FOLDER_NAME = "Podcasts"
HISTORY_FILE_NAME = "podcast_history.json"

# リトライ設定
MAX_RETRIES = 3
RETRY_DELAY_BASE = 60   # 初回待機秒 (指数バックオフ)
RETRY_DELAY_MAX = 300   # 最大待機秒

# PCM 音声パラメータ (Gemini TTS 出力形式)
AUDIO_SAMPLE_RATE = 24000
AUDIO_CHANNELS = 1
AUDIO_SAMPLE_WIDTH = 2  # 16bit

# Google OAuth2 エンドポイント
GOOGLE_TOKEN_URL = "https://oauth2.googleapis.com/token"
GOOGLE_DRIVE_UPLOAD_URL = "https://www.googleapis.com/upload/drive/v3/files"
GOOGLE_DRIVE_FILES_URL = "https://www.googleapis.com/drive/v3/files"

"""
メインパイプライン: 全ステップを順番に実行する。
各ステップの成果物は output/ に保存され、失敗時は詳細ログを出力する。
"""

import json
import sys
import traceback
from pathlib import Path

from config import logger, OUTPUT_DIR, GEMINI_API_KEY, GOOGLE_REFRESH_TOKEN


def _check_env() -> bool:
    """必須環境変数の確認。"""
    missing = []
    if not GEMINI_API_KEY:
        missing.append("GEMINI_API_KEY")
    if not GOOGLE_REFRESH_TOKEN:
        missing.append("GOOGLE_REFRESH_TOKEN")
    if missing:
        logger.error(f"必須環境変数が未設定: {', '.join(missing)}")
        return False
    return True


def run_pipeline(date_override: str = None) -> bool:
    """
    パイプライン全体を実行する。
    date_override: テスト用の日付指定 (YYYY-MM-DD)
    戻り値: 成功なら True
    """
    logger.info("=" * 60)
    logger.info("Podcast 生成パイプライン 開始")
    logger.info("=" * 60)

    if not _check_env():
        return False

    # ステップ1: トピック取得
    logger.info("\n--- ステップ1: トピック取得 ---")
    try:
        from step1_fetch_topic import fetch_topic
        topic = fetch_topic(date_override)
        if not topic:
            logger.warning("前日のリリースが見つかりませんでした。本日は Podcast をスキップします")
            return True  # エラーではなく正常終了（リリースなし日）
    except Exception as e:
        logger.error(f"ステップ1 失敗: {e}\n{traceback.format_exc()}")
        return False

    # ステップ2: 調査
    logger.info("\n--- ステップ2: 情報調査 ---")
    try:
        from step2_research import research_topic
        research = research_topic(topic)
    except Exception as e:
        logger.error(f"ステップ2 失敗: {e}\n{traceback.format_exc()}")
        return False

    # 履歴を事前に取得して重複チェック
    logger.info("\n--- 重複チェック ---")
    try:
        from step6_upload_drive import _get_access_token, _find_or_create_folder, _fetch_history
        from config import DRIVE_FOLDER_NAME
        access_token = _get_access_token()
        folder_id = _find_or_create_folder(access_token, DRIVE_FOLDER_NAME)
        history = _fetch_history(access_token, folder_id)
        existing_versions = [e["version"] for e in history.get("episodes", [])]

        version = topic["version"]
        if version in existing_versions:
            logger.warning(f"v{version} は既に Podcast 化済みです。スキップします")
            return True
        logger.info(f"v{version} は未処理です。生成を続行します")
    except Exception as e:
        logger.warning(f"重複チェック失敗 (無視して続行): {e}")
        history = {}

    # ステップ3: 台本生成
    logger.info("\n--- ステップ3: 台本生成 ---")
    try:
        from step3_generate_script import generate_script
        script_data = generate_script(research, history)
        logger.info(f"台本生成完了: {script_data['char_count']} 文字")
    except Exception as e:
        logger.error(f"ステップ3 失敗: {e}\n{traceback.format_exc()}")
        return False

    # ステップ4: 音声生成
    logger.info("\n--- ステップ4: 音声生成 ---")
    try:
        from step4_generate_audio import generate_audio_segments
        segment_paths = generate_audio_segments(script_data)
        logger.info(f"音声セグメント {len(segment_paths)} 個生成完了")
    except Exception as e:
        logger.error(f"ステップ4 失敗: {e}\n{traceback.format_exc()}")
        return False

    # ステップ5: 音声結合
    logger.info("\n--- ステップ5: 音声結合・MP3 変換 ---")
    try:
        from step5_combine_audio import combine_audio
        mp3_path = combine_audio(segment_paths, script_data["version"])
        logger.info(f"MP3 生成完了: {mp3_path}")
    except Exception as e:
        logger.error(f"ステップ5 失敗: {e}\n{traceback.format_exc()}")
        return False

    # ステップ6: アップロード
    logger.info("\n--- ステップ6: Google Drive アップロード ---")
    try:
        from step6_upload_drive import upload_podcast
        result = upload_podcast(mp3_path, script_data, research)
        logger.info(f"アップロード結果: {result}")
    except Exception as e:
        logger.error(f"ステップ6 失敗: {e}\n{traceback.format_exc()}")
        return False

    logger.info("=" * 60)
    logger.info(f"Podcast 生成パイプライン 完了: v{topic['version']}")
    logger.info("=" * 60)
    return True


if __name__ == "__main__":
    date_arg = sys.argv[1] if len(sys.argv) > 1 else None
    success = run_pipeline(date_arg)
    sys.exit(0 if success else 1)

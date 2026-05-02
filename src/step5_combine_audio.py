"""
ステップ5: WAV セグメントを結合して MP3 に変換する。
ffmpeg を使用（GitHub Actions ubuntu-latest に標準搭載）。
"""

import json
import subprocess
import sys
from pathlib import Path

from config import logger, OUTPUT_DIR


def _check_ffmpeg() -> bool:
    try:
        subprocess.run(
            ["ffmpeg", "-version"],
            capture_output=True, check=True,
        )
        return True
    except (subprocess.CalledProcessError, FileNotFoundError):
        return False


def _combine_wav_files(segment_paths: list[Path], combined_wav: Path) -> None:
    """複数の WAV ファイルを1つに結合する（concat フィルタ使用）。"""
    if len(segment_paths) == 1:
        import shutil
        shutil.copy(segment_paths[0], combined_wav)
        logger.info(f"セグメントが1つのためコピー: {combined_wav}")
        return

    # ffmpeg の concat フィルタ用のファイルリスト作成
    list_path = OUTPUT_DIR / "concat_list.txt"
    with list_path.open("w", encoding="utf-8") as f:
        for p in segment_paths:
            f.write(f"file '{p.resolve()}'\n")

    cmd = [
        "ffmpeg", "-y",
        "-f", "concat",
        "-safe", "0",
        "-i", str(list_path),
        "-c", "copy",
        str(combined_wav),
    ]
    logger.info(f"WAV 結合: {len(segment_paths)} セグメント → {combined_wav}")
    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.returncode != 0:
        logger.error(f"ffmpeg エラー:\n{result.stderr}")
        raise RuntimeError(f"WAV 結合失敗: {result.stderr}")
    logger.info("WAV 結合完了")


def _wav_to_mp3(wav_path: Path, mp3_path: Path) -> None:
    """WAV を MP3 (128kbps) に変換する。"""
    cmd = [
        "ffmpeg", "-y",
        "-i", str(wav_path),
        "-codec:a", "libmp3lame",
        "-b:a", "128k",
        "-ar", "24000",
        str(mp3_path),
    ]
    logger.info(f"MP3 変換: {wav_path} → {mp3_path}")
    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.returncode != 0:
        logger.error(f"ffmpeg MP3 変換エラー:\n{result.stderr}")
        raise RuntimeError(f"MP3 変換失敗: {result.stderr}")
    size_kb = mp3_path.stat().st_size // 1024
    logger.info(f"MP3 変換完了: {mp3_path} ({size_kb} KB)")


def combine_audio(segment_paths: list[Path], version: str) -> Path:
    """
    音声セグメントを結合して MP3 ファイルを生成する。
    """
    if not _check_ffmpeg():
        raise RuntimeError("ffmpeg が見つかりません。インストールが必要です")

    combined_wav = OUTPUT_DIR / "combined.wav"
    mp3_filename = f"claude_code_{version.replace('.', '_')}.mp3"
    mp3_path = OUTPUT_DIR / mp3_filename

    _combine_wav_files(segment_paths, combined_wav)
    _wav_to_mp3(combined_wav, mp3_path)

    # 一時ファイルを削除
    combined_wav.unlink(missing_ok=True)

    return mp3_path


if __name__ == "__main__":
    meta_path = OUTPUT_DIR / "audio_segments.json"
    if not meta_path.exists():
        print("audio_segments.json が見つかりません。先に step4 を実行してください")
        sys.exit(1)

    meta = json.loads(meta_path.read_text(encoding="utf-8"))
    segment_paths = [Path(p) for p in meta["segments"]]
    version = meta["version"]

    mp3_path = combine_audio(segment_paths, version)
    print(f"MP3 生成完了: {mp3_path}")

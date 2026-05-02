"""
ステップ4: 台本を2000文字未満のセグメントに分割し、
Gemini TTS APIで各セグメントの音声を生成する。
"""

import json
import time
import wave
from pathlib import Path

from google import genai
from google.genai import types

from config import (
    logger, OUTPUT_DIR, GEMINI_API_KEY,
    TTS_MODEL, TTS_MODEL_FALLBACK,
    VOICE_MALE, VOICE_FEMALE,
    SPEAKER_MALE, SPEAKER_FEMALE,
    MAX_CHARS_PER_TTS,
    MAX_RETRIES, RETRY_DELAY_BASE, RETRY_DELAY_MAX,
    AUDIO_SAMPLE_RATE, AUDIO_CHANNELS, AUDIO_SAMPLE_WIDTH,
)


def _split_script_into_segments(script: str, max_chars: int = MAX_CHARS_PER_TTS) -> list[str]:
    """
    台本を発言単位で max_chars 以下のセグメントに分割する。
    会話の途中で発言が切れないようにする。
    """
    lines = [line.strip() for line in script.strip().splitlines() if line.strip()]
    segments = []
    current_seg = []
    current_len = 0

    for line in lines:
        line_len = len(line)
        if current_len + line_len + 1 > max_chars and current_seg:
            segments.append("\n".join(current_seg))
            current_seg = [line]
            current_len = line_len
        else:
            current_seg.append(line)
            current_len += line_len + 1

    if current_seg:
        segments.append("\n".join(current_seg))

    logger.info(f"台本を {len(segments)} セグメントに分割 (各 ≤{max_chars} 文字)")
    for i, seg in enumerate(segments):
        logger.info(f"  セグメント {i+1}: {len(seg)} 文字")
    return segments


def _build_tts_config(model: str) -> types.GenerateContentConfig:
    """TTS 設定を構築する（多話者モード）。"""
    return types.GenerateContentConfig(
        response_modalities=["AUDIO"],
        speech_config=types.SpeechConfig(
            multi_speaker_voice_config=types.MultiSpeakerVoiceConfig(
                speaker_voice_configs=[
                    types.SpeakerVoiceConfig(
                        speaker=SPEAKER_MALE,
                        voice_config=types.VoiceConfig(
                            prebuilt_voice_config=types.PrebuiltVoiceConfig(
                                voice_name=VOICE_MALE
                            )
                        ),
                    ),
                    types.SpeakerVoiceConfig(
                        speaker=SPEAKER_FEMALE,
                        voice_config=types.VoiceConfig(
                            prebuilt_voice_config=types.PrebuiltVoiceConfig(
                                voice_name=VOICE_FEMALE
                            )
                        ),
                    ),
                ]
            )
        ),
    )


def _call_tts_with_retry(client: genai.Client, segment_text: str, seg_index: int) -> bytes:
    """TTS APIをリトライとフォールバック付きで呼び出す。"""
    models = [TTS_MODEL, TTS_MODEL_FALLBACK]

    for model in models:
        logger.info(f"TTS: モデル {model}, セグメント {seg_index+1}")
        for attempt in range(MAX_RETRIES):
            try:
                # TTS プロンプト: 話者名を先頭に明示する形式
                tts_prompt = (
                    f"以下の{SPEAKER_MALE}と{SPEAKER_FEMALE}の会話を読み上げてください:\n"
                    + segment_text
                )
                response = client.models.generate_content(
                    model=model,
                    contents=tts_prompt,
                    config=_build_tts_config(model),
                )
                part = response.candidates[0].content.parts[0]
                # SDK が既にバイナリとして返す (base64 デコード不要)
                audio_data = part.inline_data.data
                logger.info(
                    f"TTS 成功: セグメント {seg_index+1}, "
                    f"{len(audio_data)} bytes (モデル: {model})"
                )
                return audio_data

            except Exception as e:
                err_msg = str(e).lower()
                is_transient = any(k in err_msg for k in [
                    "quota", "rate", "429", "500", "503", "unavailable",
                    "resource exhausted", "timeout",
                ])
                if is_transient:
                    wait = min(RETRY_DELAY_BASE * (2 ** attempt), RETRY_DELAY_MAX)
                    logger.warning(
                        f"TTS API エラー ({type(e).__name__}): {wait}秒後にリトライ "
                        f"(試行 {attempt+1}/{MAX_RETRIES})"
                    )
                    time.sleep(wait)
                else:
                    logger.error(f"TTS 予期しないエラー (セグメント {seg_index+1}): {e}")
                    break

        logger.warning(f"TTS モデル {model} で失敗。フォールバックへ")

    raise RuntimeError(f"セグメント {seg_index+1} の TTS 生成に失敗しました")


def _pcm_to_wav(pcm_data: bytes, path: Path) -> None:
    """生PCMデータをWAVファイルとして保存する。"""
    with wave.open(str(path), "wb") as wf:
        wf.setnchannels(AUDIO_CHANNELS)
        wf.setsampwidth(AUDIO_SAMPLE_WIDTH)
        wf.setframerate(AUDIO_SAMPLE_RATE)
        wf.writeframes(pcm_data)


def generate_audio_segments(script_data: dict) -> list[Path]:
    """
    台本を分割してTTSで音声を生成し、WAVファイルのリストを返す。
    """
    if not GEMINI_API_KEY:
        raise ValueError("GEMINI_API_KEY が設定されていません")

    client = genai.Client(api_key=GEMINI_API_KEY)
    script_text = script_data["script"]
    version = script_data["version"]

    segments = _split_script_into_segments(script_text)
    audio_dir = OUTPUT_DIR / "audio_segments"
    audio_dir.mkdir(exist_ok=True)

    segment_paths = []
    for i, segment in enumerate(segments):
        out_path = audio_dir / f"segment_{i:03d}.wav"

        # 既存ファイルがあればスキップ（再実行時の節約）
        if out_path.exists():
            logger.info(f"セグメント {i+1} は既存ファイルを使用: {out_path}")
            segment_paths.append(out_path)
            continue

        logger.info(f"セグメント {i+1}/{len(segments)} の音声を生成中...")
        pcm_data = _call_tts_with_retry(client, segment, i)
        _pcm_to_wav(pcm_data, out_path)
        logger.info(f"WAV 保存: {out_path}")
        segment_paths.append(out_path)

        # RPM 制限対策: セグメント間に待機
        if i < len(segments) - 1:
            logger.info("次のセグメントまで10秒待機 (RPM 制限対策)")
            time.sleep(10)

    # セグメントパスを JSON に記録
    paths_data = {
        "version": version,
        "segments": [str(p) for p in segment_paths],
        "count": len(segment_paths),
    }
    meta_path = OUTPUT_DIR / "audio_segments.json"
    meta_path.write_text(json.dumps(paths_data, ensure_ascii=False, indent=2), encoding="utf-8")
    logger.info(f"音声セグメント {len(segment_paths)} 個を生成完了")

    return segment_paths


if __name__ == "__main__":
    import sys
    script_path = OUTPUT_DIR / "script.json"
    if not script_path.exists():
        print("script.json が見つかりません。先に step3 を実行してください")
        sys.exit(1)
    script_data = json.loads(script_path.read_text(encoding="utf-8"))
    paths = generate_audio_segments(script_data)
    for p in paths:
        print(p)

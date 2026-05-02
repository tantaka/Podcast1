"""
ステップ3: 調査結果をもとに5分程度の日本語Podcast台本を生成する。
過去のPodcastと重複しないよう履歴を参照する。
"""

import json
import time
from pathlib import Path

from google import genai
from google.genai import types

from config import (
    logger, OUTPUT_DIR, GEMINI_API_KEY,
    RESEARCH_MODEL, RESEARCH_MODEL_FALLBACK,
    SPEAKER_MALE, SPEAKER_FEMALE,
    MAX_RETRIES, RETRY_DELAY_BASE, RETRY_DELAY_MAX,
)


def _build_script_prompt(research: dict, past_versions: list[str]) -> str:
    version = research.get("version", research["topic"]["version"])
    summary = research.get("research_summary", "")
    features = research.get("main_features", [])
    reactions = research.get("community_reactions", "")
    highlights = research.get("key_highlights", [])
    feedback = research.get("developer_feedback", "")
    use_cases = research.get("interesting_use_cases", [])
    sentiment = research.get("overall_sentiment", "neutral")

    past_str = "、".join(past_versions[-10:]) if past_versions else "なし"

    return f"""
あなたはIT系Podcastのプロの台本ライターです。
以下の調査結果をもとに、5分程度で読める日本語のPodcast台本を作成してください。

## 調査情報
- バージョン: {version}
- 主な機能: {", ".join(features)}
- コミュニティの反応: {reactions}
- 注目ポイント: {", ".join(highlights)}
- 開発者フィードバック: {feedback}
- 使用事例: {", ".join(use_cases)}
- 全体的な評価: {sentiment}
- 概要: {summary}

## 過去に扱ったバージョン (重複を避けること)
{past_str}

## 台本の要件
- 話者は {SPEAKER_MALE}（男性、ホスト）と {SPEAKER_FEMALE}（女性、ゲスト/コメンテーター）の2名
- 5分程度（読み上げ速度を考慮して1500〜2000文字程度）
- 自然な会話形式で、技術的な内容をわかりやすく解説する
- 最初と最後に簡単な挨拶・締めを入れる
- 聴取者はエンジニアを想定（専門用語は使えるが、コンテキストを添える）
- テンポ良く、聴きやすい会話にする

## 出力形式
以下の形式で台本のみを出力してください（JSON不要、前置きや説明も不要）:

{SPEAKER_MALE}: （セリフ）
{SPEAKER_FEMALE}: （セリフ）
{SPEAKER_MALE}: （セリフ）
...

各発言は1〜4文程度にまとめること。
""".strip()


def _call_with_retry(client: genai.Client, prompt: str) -> str:
    models = [RESEARCH_MODEL, RESEARCH_MODEL_FALLBACK]

    for model in models:
        logger.info(f"台本生成: モデル {model}")
        for attempt in range(MAX_RETRIES):
            try:
                response = client.models.generate_content(
                    model=model,
                    contents=prompt,
                    config=types.GenerateContentConfig(temperature=0.9),
                )
                text = response.text
                if not text and response.candidates:
                    parts = response.candidates[0].content.parts
                    text = "".join(p.text for p in parts if hasattr(p, "text") and p.text)
                if not text:
                    raise ValueError("Gemini API からテキストレスポンスが得られませんでした")
                logger.info(f"台本生成完了 (モデル: {model})")
                return text
            except Exception as e:
                err_msg = str(e).lower()
                is_transient = any(k in err_msg for k in ["quota", "rate", "429", "500", "503", "unavailable"])
                if is_transient:
                    wait = min(RETRY_DELAY_BASE * (2 ** attempt), RETRY_DELAY_MAX)
                    logger.warning(f"API エラー: {wait}秒後にリトライ (試行 {attempt+1}/{MAX_RETRIES})")
                    time.sleep(wait)
                else:
                    logger.error(f"予期しないエラー: {e}")
                    break
        logger.warning(f"モデル {model} で失敗。フォールバックへ")

    raise RuntimeError("台本生成に失敗しました")


def _validate_script(script: str) -> bool:
    """台本に両話者の発言が含まれているか確認する。"""
    has_male = f"{SPEAKER_MALE}:" in script
    has_female = f"{SPEAKER_FEMALE}:" in script
    min_length = 500
    return has_male and has_female and len(script) >= min_length


def generate_script(research: dict, history: dict) -> dict:
    """
    調査結果と過去履歴から台本を生成して返す。
    """
    if not GEMINI_API_KEY:
        raise ValueError("GEMINI_API_KEY が設定されていません")

    client = genai.Client(api_key=GEMINI_API_KEY)
    past_versions = [entry["version"] for entry in history.get("episodes", [])]
    prompt = _build_script_prompt(research, past_versions)

    version = research.get("version", research["topic"]["version"])
    logger.info(f"v{version} の台本を生成中...")

    script_text = _call_with_retry(client, prompt)

    if not _validate_script(script_text):
        logger.error("生成された台本が不正な形式です")
        logger.debug(f"台本内容:\n{script_text[:300]}")
        raise ValueError("台本の検証に失敗しました")

    script_data = {
        "version": version,
        "script": script_text,
        "char_count": len(script_text),
        "speakers": [SPEAKER_MALE, SPEAKER_FEMALE],
    }

    out_path = OUTPUT_DIR / "script.json"
    out_path.write_text(json.dumps(script_data, ensure_ascii=False, indent=2), encoding="utf-8")

    # 台本テキストも別途保存（確認しやすいように）
    script_txt_path = OUTPUT_DIR / "script.txt"
    script_txt_path.write_text(script_text, encoding="utf-8")

    logger.info(f"台本を保存: {out_path} ({len(script_text)} 文字)")
    return script_data


if __name__ == "__main__":
    import sys
    research_path = OUTPUT_DIR / "research.json"
    if not research_path.exists():
        print("research.json が見つかりません。先に step2 を実行してください")
        sys.exit(1)
    research = json.loads(research_path.read_text(encoding="utf-8"))
    result = generate_script(research, {})
    print(result["script"])

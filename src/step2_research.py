"""
ステップ2: Gemini (Google Search grounding) を使って
X (Twitter) 上での対象バージョンの反応・議論を調査する。
"""

import json
import time
from pathlib import Path

from google import genai
from google.genai import types

from config import (
    logger, OUTPUT_DIR, GEMINI_API_KEY,
    RESEARCH_MODEL, RESEARCH_MODEL_FALLBACK,
    MAX_RETRIES, RETRY_DELAY_BASE, RETRY_DELAY_MAX,
)


def _build_research_prompt(topic: dict) -> str:
    version = topic["version"]
    title = topic["title"]
    body = topic.get("body", "")

    return f"""
あなたはPodcastのリサーチアシスタントです。
以下のClaude Codeの新バージョンについて、X (Twitter) 上での反応・議論・評価を徹底的に調査してください。

## 調査対象
- バージョン: {version}
- タイトル: {title}
- 公式変更点:
{body[:2000] if body else "(変更点の詳細はGitHubで検索してください)"}

## 調査してほしい内容
1. このバージョンで追加・変更された主な機能
2. X上でのユーザーの反応（肯定的・否定的・中立）
3. 特に話題になっているポイント
4. エンジニアや開発者コミュニティの評価
5. 実際の使用事例や感想

## 出力形式
以下のJSON形式で回答してください:
{{
  "version": "{version}",
  "main_features": ["機能1", "機能2", ...],
  "community_reactions": "Xでの反応の要約（200文字程度）",
  "key_highlights": ["注目ポイント1", "注目ポイント2", ...],
  "developer_feedback": "開発者コミュニティの評価（200文字程度）",
  "interesting_use_cases": ["事例1", "事例2", ...],
  "overall_sentiment": "positive/negative/mixed/neutral",
  "research_summary": "全体的な調査まとめ（400文字程度）"
}}

必ずJSONのみを返し、コードブロック記号(```)は使わないでください。
""".strip()


def _extract_text(response) -> str:
    """response からテキストを抽出する（grounding 使用時は response.text が None になる場合がある）。"""
    text = response.text
    if text:
        return text
    # candidates から直接取得
    if response.candidates:
        parts = response.candidates[0].content.parts
        text = "".join(p.text for p in parts if hasattr(p, "text") and p.text)
        if text:
            return text
    raise ValueError(
        f"Gemini API からテキストレスポンスが得られませんでした。"
        f"finish_reason={response.candidates[0].finish_reason if response.candidates else 'N/A'}"
    )


def _call_gemini_with_grounding(client: genai.Client, model: str, prompt: str) -> str:
    """Google Search grounding を使ってGeminiにリクエストする。"""
    response = client.models.generate_content(
        model=model,
        contents=prompt,
        config=types.GenerateContentConfig(
            tools=[types.Tool(google_search=types.GoogleSearch())],
            temperature=0.7,
        ),
    )
    return _extract_text(response)


def _call_with_retry(client: genai.Client, prompt: str) -> str:
    """リトライとフォールバックモデル付きでGemini APIを呼び出す。"""
    models = [RESEARCH_MODEL, RESEARCH_MODEL_FALLBACK]

    for model in models:
        logger.info(f"Gemini モデル: {model} で調査開始")
        for attempt in range(MAX_RETRIES):
            try:
                result = _call_gemini_with_grounding(client, model, prompt)
                logger.info(f"調査完了 (モデル: {model}, 試行: {attempt+1})")
                return result
            except Exception as e:
                err_msg = str(e).lower()
                is_rate_limit = any(k in err_msg for k in ["quota", "rate", "429", "resource exhausted"])
                is_server_error = any(k in err_msg for k in ["500", "503", "unavailable", "internal"])

                if is_rate_limit or is_server_error:
                    wait = min(RETRY_DELAY_BASE * (2 ** attempt), RETRY_DELAY_MAX)
                    logger.warning(
                        f"API エラー ({type(e).__name__}): {wait}秒後にリトライ "
                        f"(試行 {attempt+1}/{MAX_RETRIES}, モデル: {model})"
                    )
                    time.sleep(wait)
                else:
                    logger.error(f"予期しないエラー: {e}")
                    break

        logger.warning(f"モデル {model} でのリトライ上限到達。フォールバックへ移行")

    raise RuntimeError("全モデルでのリトライが失敗しました")


def _parse_json_response(text: str) -> dict:
    """GeminiのレスポンスからJSONを抽出する。"""
    text = text.strip()
    # ```json ... ``` を除去
    if text.startswith("```"):
        text = text.split("\n", 1)[1] if "\n" in text else text[3:]
        if text.endswith("```"):
            text = text[:-3]
        text = text.strip()
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        # JSON部分だけ抽出を試みる
        start = text.find("{")
        end = text.rfind("}") + 1
        if start >= 0 and end > start:
            return json.loads(text[start:end])
        raise


def research_topic(topic: dict) -> dict:
    """
    Claude Code のバージョン情報を調査してリサーチ結果を返す。
    """
    if not GEMINI_API_KEY:
        raise ValueError("GEMINI_API_KEY が設定されていません")

    client = genai.Client(api_key=GEMINI_API_KEY)
    prompt = _build_research_prompt(topic)

    logger.info(f"Claude Code v{topic['version']} の調査を開始します")
    raw_response = _call_with_retry(client, prompt)

    logger.debug(f"Gemini 生レスポンス:\n{raw_response[:500]}...")

    research = _parse_json_response(raw_response)
    research["topic"] = topic

    out_path = OUTPUT_DIR / "research.json"
    out_path.write_text(json.dumps(research, ensure_ascii=False, indent=2), encoding="utf-8")
    logger.info(f"調査結果を保存: {out_path}")

    return research


if __name__ == "__main__":
    import sys
    topic_path = OUTPUT_DIR / "topic.json"
    if not topic_path.exists():
        print("topic.json が見つかりません。先に step1 を実行してください")
        sys.exit(1)
    topic = json.loads(topic_path.read_text(encoding="utf-8"))
    result = research_topic(topic)
    print(json.dumps(result, ensure_ascii=False, indent=2))

"""Thin OpenAI wrapper. Returns dicts so the caller can log prompt/output/tokens."""
import logging

from openai import OpenAI

_log = logging.getLogger("xbot.llm")

_MAX_CHARS = 270  # leave headroom under X's 280 limit


class LLM:
    def __init__(self, cfg):
        self.client = OpenAI(api_key=cfg["api_key"])
        self.model = cfg.get("model", "gpt-4o-mini")
        self.system = cfg.get(
            "system_prompt",
            "You write short, friendly, on-topic X/Twitter messages. No quote marks.",
        )

    def _chat(self, user_prompt):
        resp = self.client.chat.completions.create(
            model=self.model,
            messages=[
                {"role": "system", "content": self.system},
                {"role": "user", "content": user_prompt},
            ],
            temperature=0.8,
            max_tokens=120,
        )
        text = (resp.choices[0].message.content or "").strip().strip('"').strip()
        tokens = resp.usage.total_tokens if resp.usage else None
        _log.debug("llm out (%s tokens): %s", tokens, text)
        return {"prompt": user_prompt, "output": text, "tokens": tokens}

    def generate_post(self, topic=None):
        if topic:
            prompt = f"Write one original, engaging tweet (max {_MAX_CHARS} chars) about: {topic}."
        else:
            prompt = f"Write one original, engaging tweet (max {_MAX_CHARS} chars)."
        return self._chat(prompt)

    def generate_reply(self, tweet_text):
        prompt = (
            f"Write a short, friendly, relevant reply (max {_MAX_CHARS} chars) to this tweet. "
            f"No quote marks, no @mentions:\n\n\"{tweet_text}\""
        )
        return self._chat(prompt)

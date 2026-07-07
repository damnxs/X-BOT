"""Thin OpenAI wrapper. Returns dicts so the caller can log prompt/output/tokens."""
import logging

from openai import OpenAI

_log = logging.getLogger("xbot.llm")

_MAX_CHARS = 270  # leave headroom under X's 280 limit

_POST_DEFAULT = (
    "You write original, engaging X/Twitter posts. Max 100 characters. "
    "On-topic. No quote marks, no emojis, no hashtags."
)
_REPLY_DEFAULT = (
    "You write short, friendly, relevant X/Twitter replies. Max 100 characters. "
    "No quote marks, no @mentions, no emojis, no hashtags."
)


class LLM:
    def __init__(self, cfg):
        self.client = OpenAI(api_key=cfg["api_key"])
        self.model = cfg.get("model", "gpt-4o-mini")
        # CLI/config.yaml still passes the single "system_prompt" key — fall back to it so
        # there's always a prompt until the split post_system/reply_system keys are wired.
        legacy = cfg.get("system_prompt")
        self.post_system = cfg.get("post_system") or legacy or _POST_DEFAULT
        self.reply_system = cfg.get("reply_system") or legacy or _REPLY_DEFAULT

    def _chat(self, user_prompt, system):
        resp = self.client.chat.completions.create(
            model=self.model,
            messages=[
                {"role": "system", "content": system},
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
        return self._chat(prompt, self.post_system)

    def generate_reply(self, tweet_text):
        prompt = (
            f"Write a short, friendly, relevant reply (max {_MAX_CHARS} chars) to this tweet. "
            f"No quote marks, no @mentions:\n\n\"{tweet_text}\""
        )
        return self._chat(prompt, self.reply_system)

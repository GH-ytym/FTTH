"""Qwen compatibility around the pinned langchain-openai message adapter.

Reasoning is preserved as protocol metadata, never displayed as the final answer.
No requests execute while constructing the client.
"""
from langchain_openai import ChatOpenAI
from langchain_core.messages import AIMessage


class QwenChatOpenAI(ChatOpenAI):
    def _convert_chunk_to_generation_chunk(self, chunk, default_chunk_class, base_generation_info):
        result = super()._convert_chunk_to_generation_chunk(chunk, default_chunk_class, base_generation_info)
        choices = chunk.get("choices") or chunk.get("chunk", {}).get("choices") or []
        if result is not None and choices:
            reasoning = (choices[0].get("delta") or {}).get("reasoning_content")
            if reasoning is not None:
                result.message.additional_kwargs["reasoning_content"] = reasoning
        return result

    def _create_chat_result(self, response, generation_info=None):
        result = super()._create_chat_result(response, generation_info)
        data = response if isinstance(response, dict) else response.model_dump()
        for generation, choice in zip(result.generations, data.get("choices", [])):
            reasoning = choice.get("message", {}).get("reasoning_content")
            if reasoning is not None:
                generation.message.additional_kwargs["reasoning_content"] = reasoning
        return result

    def _get_request_payload(self, input_, *, stop=None, **kwargs):
        payload = super()._get_request_payload(input_, stop=stop, **kwargs)
        if "messages" in payload:
            messages = self._convert_input(input_).to_messages()
            for message, encoded in zip(messages, payload["messages"]):
                if isinstance(message, AIMessage) and "reasoning_content" in message.additional_kwargs:
                    encoded["reasoning_content"] = message.additional_kwargs["reasoning_content"]
        return payload


def build_model(settings, **overrides):
    options = dict(model=settings.model, base_url=settings.base_url, api_key=settings.api_key or "local",
                   timeout=90, max_retries=1, streaming=True, use_responses_api=settings.model_api == "responses")
    client_type = ChatOpenAI
    if settings.provider == "qwen":
        client_type = QwenChatOpenAI
        options.update(streaming=True, stream_usage=False, extra_body={"enable_thinking": settings.enable_thinking})
    else:
        options["temperature"] = 0
    options.update(overrides)
    return client_type(**options)

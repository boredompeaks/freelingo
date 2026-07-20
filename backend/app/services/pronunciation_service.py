from pydantic import BaseModel

from app.services.llm_adapter import LLMAdapter


class PronunciationEvaluation(BaseModel):
    is_acceptable: bool
    explanation: str


async def evaluate_pronunciation_with_llm(
    reference_text: str, transcribed_text: str, target_language: str
) -> PronunciationEvaluation:
    adapter = LLMAdapter()
    messages = [
        {
            "role": "system",
            "content": f"You are a strict {target_language} language teacher evaluating pronunciation. "
            "A student was supposed to say a word. You are given the reference text and what the speech-to-text system transcribed. "
            "If the transcription is very close or an acceptable homophone/minor error, it is acceptable. "
            "If it's a completely different word or significantly wrong, it is not acceptable. Return JSON.",
        },
        {
            "role": "user",
            "content": f"Reference: '{reference_text}'\nTranscribed: '{transcribed_text}'",
        },
    ]
    return await adapter.structured_output(messages, PronunciationEvaluation)

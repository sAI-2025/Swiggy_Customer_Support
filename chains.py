"""LLM clients and chains.

- Groq (llama-3.1-8b-instant): cheap text nodes — rewriter, classifier,
  router, final response.
- OpenRouter: free VLM for image validation, with a FALLBACK LIST because
  free-tier model availability on OpenRouter rotates/changes frequently
  (models get removed or renamed without notice).

Every chain uses PydanticOutputParser with a retry mechanism for robustness.
"""
import os

from dotenv import load_dotenv
from langchain_core.output_parsers import PydanticOutputParser
from langchain_core.exceptions import OutputParserException
from langchain_groq import ChatGroq
from langchain_openai import ChatOpenAI
from pydantic import BaseModel

from prompts import (
    CLASSIFIER_PROMPT,
    RESPONSE_PROMPT,
    REWRITER_PROMPT,
    ROUTER_PROMPT,
    VLM_PROMPT,
)
from schema import (
    ClassificationOutput,
    ResponseOutput,
    RewriterOutput,
    RouterOutput,
    VLMOutput,
)

load_dotenv()

# Cheap Groq text model for normal chat/routing; ~10x cheaper than 70B.
GROQ_MODEL = "llama-3.1-8b-instant"

# Free OpenRouter vision models, tried in order. Free-tier availability
# rotates frequently, so never hardcode a single model — try each until
# one succeeds. Verify current IDs at https://openrouter.ai/models?max_price=0
VLM_MODEL_FALLBACKS = [
    "nvidia/nemotron-nano-12b-v2-vl:free",
    "qwen/qwen-2.5-vl-7b-instruct:free",
    "google/gemma-3-27b-it:free",
    "meta-llama/llama-3.2-11b-vision-instruct:free",
]


def get_groq_llm(temperature: float = 0) -> ChatGroq:
    api_key = os.getenv("GROQ_API_KEY")
    if not api_key:
        raise ValueError("GROQ_API_KEY is missing from .env")
    return ChatGroq(
        model=GROQ_MODEL,
        api_key=api_key,
        temperature=temperature,
        max_tokens=500,
    )


def get_vlm_llm(model_id: str) -> ChatOpenAI:
    """OpenRouter vision model via the OpenAI-compatible LangChain client."""
    api_key = os.getenv("OPENROUTER_API_KEY")
    if not api_key:
        raise ValueError("OPENROUTER_API_KEY is missing from .env")
    return ChatOpenAI(
        model=model_id,
        api_key=api_key,
        base_url="https://openrouter.ai/api/v1",
        temperature=0,
        max_tokens=500,
    )


def parse_with_retry(chain, parser, input_dict, max_retries: int = 1):
    """Run chain, catch OutputParserException, retry once with error context."""
    try:
        return chain.invoke(input_dict)
    except OutputParserException as e:
        if max_retries <= 0:
            raise
        input_dict["previous_error"] = str(e)
        input_dict["format_instructions"] = parser.get_format_instructions()
        return parse_with_retry(chain, parser, input_dict, max_retries - 1)


def build_parser_chain(prompt, schema: type[BaseModel], temperature: float = 0):
    llm = get_groq_llm(temperature=temperature)
    parser = PydanticOutputParser(pydantic_object=schema)
    prompt_with_format = prompt.partial(
        format_instructions=parser.get_format_instructions()
    )
    chain = prompt_with_format | llm | parser
    chain._parser = parser
    return chain


def get_rewriter_chain():
    return build_parser_chain(REWRITER_PROMPT, RewriterOutput, temperature=0)


def get_classifier_chain():
    return build_parser_chain(CLASSIFIER_PROMPT, ClassificationOutput, temperature=0)


def get_router_chain():
    return build_parser_chain(ROUTER_PROMPT, RouterOutput, temperature=0)


def get_response_chain():
    return build_parser_chain(RESPONSE_PROMPT, ResponseOutput, temperature=0.4)


def invoke_vlm_with_fallback(image_b64: str, claim: str) -> dict:
    """Try each free VLM model in order until one succeeds.

    Returns the parsed VLMOutput as a dict, plus which model actually
    answered (useful for debugging/logging which free model is currently
    alive on OpenRouter).
    """
    parser = PydanticOutputParser(pydantic_object=VLMOutput)
    prompt_with_format = VLM_PROMPT.partial(
        format_instructions=parser.get_format_instructions()
    )

    last_error = None
    for model_id in VLM_MODEL_FALLBACKS:
        try:
            llm = get_vlm_llm(model_id)
            chain = prompt_with_format | llm | parser
            result = chain.invoke({"claim": claim, "image_b64": image_b64})
            output = result.model_dump()
            output["_model_used"] = model_id
            return output
        except Exception as exc:
            last_error = exc
            continue

    raise RuntimeError(
        f"All VLM fallback models failed. Last error: {last_error}. "
        f"Tried: {VLM_MODEL_FALLBACKS}. "
        f"Check https://openrouter.ai/models?max_price=0 for currently valid free model IDs."
    )

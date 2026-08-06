"""LLM clients and chains.

- Groq (llama-3.1-8b-instant): cheap text nodes — rewriter, classifier,
  router, final response.
- VLM for image validation: DASHSCOPE FIRST, OpenRouter as fallback.

  Rationale (per requirement #2): OpenRouter free-tier vision models were
  observed to have high latency and tight rate limits. DashScope's
  qwen-omni-turbo (Alibaba) is used as the PRIMARY vision path via its
  OpenAI-compatible endpoint. If DashScope raises ANY exception (auth,
  rate limit, timeout, network), we transparently fall back to a list of
  small/cheap OpenRouter vision models (lightweight, low-parameter,
  cheaper pricing — NOT the large 70B+ models), tried in order.

Every chain uses PydanticOutputParser with a retry mechanism for robustness.
"""
import base64
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

# ---------- Text models (Groq) ----------

GROQ_MODEL = "llama-3.1-8b-instant"


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


# ---------- Vision models: DashScope PRIMARY, OpenRouter FALLBACK ----------

# DashScope (Alibaba) international endpoint — OpenAI-compatible.
DASHSCOPE_MODEL = "qwen-omni-turbo"
DASHSCOPE_BASE_URL = "https://dashscope-intl.aliyuncs.com/compatible-mode/v1"

# OpenRouter fallback: intentionally SMALL / lightweight / cheap vision
# models only (not large 70B+ models) — requirement #2 asks for a
# lighter-weight, cheaper option to keep fallback latency+cost low.
# Verify current IDs/pricing at https://openrouter.ai/models?max_price=0
OPENROUTER_VLM_FALLBACKS = [
    "qwen/qwen-2.5-vl-7b-instruct:free",       # smallest, free tier first
    "meta-llama/llama-3.2-11b-vision-instruct",  # cheap paid ($0.05-0.35/M)
    "google/gemma-3-27b-it:free",              # free tier backup
]


def _image_path_to_data_url(image_path: str) -> str:
    ext = image_path.split(".")[-1].lower()
    mime_map = {
        "jpg": "image/jpeg", "jpeg": "image/jpeg",
        "png": "image/png", "webp": "image/webp",
    }
    mime = mime_map.get(ext, "image/jpeg")
    with open(image_path, "rb") as f:
        encoded = base64.b64encode(f.read()).decode("utf-8")
    return f"data:{mime};base64,{encoded}"


def _get_dashscope_client():
    from openai import OpenAI
    api_key = os.getenv("DASHSCOPE_API_KEY")
    if not api_key:
        raise ValueError("DASHSCOPE_API_KEY is missing from .env")
    return OpenAI(api_key=api_key, base_url=DASHSCOPE_BASE_URL)


def _invoke_dashscope_vlm(image_b64: str, claim: str, format_instructions: str) -> dict:
    """Primary VLM path: DashScope qwen-omni-turbo (low latency, generous limits)."""
    client = _get_dashscope_client()
    prompt_text = (
        "You are an image-validation assistant for a food-delivery support "
        "system. Check whether the image is relevant to the customer's claim "
        "and whether the described issue is visibly present. Judge only on "
        f"visible evidence.\n\n{format_instructions}\n\n"
        f"Customer claim: {claim}\n\n"
        "Is this image relevant and does it support the claim?"
    )
    response = client.chat.completions.create(
        model=DASHSCOPE_MODEL,
        messages=[{
            "role": "user",
            "content": [
                {"type": "text", "text": prompt_text},
                {"type": "image_url", "image_url": {"url": image_b64}},
            ],
        }],
        temperature=0,
    )
    return response.choices[0].message.content


def _get_openrouter_llm(model_id: str) -> ChatOpenAI:
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


def invoke_vlm_with_fallback(image_path: str, claim: str) -> dict:
    """VLM entry point used by tools.py.

    Order of attempts:
      1. DashScope qwen-omni-turbo (PRIMARY — lower latency, better limits)
      2. OpenRouter small/cheap vision models, tried in order (FALLBACK)

    Returns the parsed VLMOutput as a dict, plus which provider/model
    actually answered (useful for debugging).
    """
    parser = PydanticOutputParser(pydantic_object=VLMOutput)
    format_instructions = parser.get_format_instructions()
    image_b64 = _image_path_to_data_url(image_path)

    # --- 1. Try DashScope first ---
    try:
        raw_content = _invoke_dashscope_vlm(image_b64, claim, format_instructions)
        result = parser.parse(raw_content)
        output = result.model_dump()
        output["_provider_used"] = f"dashscope:{DASHSCOPE_MODEL}"
        return output
    except Exception as dashscope_error:
        last_error = dashscope_error

    # --- 2. Fall back to OpenRouter (small/cheap models only) ---
    prompt_with_format = VLM_PROMPT.partial(format_instructions=format_instructions)
    for model_id in OPENROUTER_VLM_FALLBACKS:
        try:
            llm = _get_openrouter_llm(model_id)
            chain = prompt_with_format | llm | parser
            result = chain.invoke({"claim": claim, "image_b64": image_b64})
            output = result.model_dump()
            output["_provider_used"] = f"openrouter:{model_id}"
            return output
        except Exception as exc:
            last_error = exc
            continue

    raise RuntimeError(
        f"DashScope and all OpenRouter fallback models failed. "
        f"Last error: {last_error}. "
        f"Tried DashScope ({DASHSCOPE_MODEL}) then OpenRouter: {OPENROUTER_VLM_FALLBACKS}."
    )

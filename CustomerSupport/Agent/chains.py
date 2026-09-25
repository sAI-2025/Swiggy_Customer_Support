"""LLM clients and chains.

- Groq (llama-3.1-8b-instant): rewriter, classifier, generic-chat response.
  ToolRouterNode is DETERMINISTIC (no LLM) — see graph.py.
- VLM for image validation: DASHSCOPE FIRST (low latency, generous limits),
  OpenRouter small/cheap models as FALLBACK if DashScope errors.
"""
import base64
import os

from dotenv import load_dotenv
from langchain_core.output_parsers import PydanticOutputParser
from langchain_core.exceptions import OutputParserException
from langchain_groq import ChatGroq
from langchain_openai import ChatOpenAI
from pydantic import BaseModel

from prompts import CLASSIFIER_PROMPT, RESPONSE_PROMPT, REWRITER_PROMPT, VLM_PROMPT
from schema import ClassificationOutput, ResponseOutput, RewriterOutput, VLMOutput

load_dotenv()

GROQ_MODEL = "openai/gpt-oss-120b"


def get_groq_llm(temperature: float = 0) -> ChatGroq:
    api_key = os.getenv("GROQ_API_KEY")
    if not api_key:
        raise ValueError("GROQ_API_KEY is missing from .env")
    return ChatGroq(model=GROQ_MODEL, api_key=api_key, temperature=temperature, max_tokens=500)


def parse_with_retry(chain, parser, input_dict, max_retries: int = 1):
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
    prompt_with_format = prompt.partial(format_instructions=parser.get_format_instructions())
    chain = prompt_with_format | llm | parser
    chain._parser = parser
    return chain


def get_rewriter_chain():
    return build_parser_chain(REWRITER_PROMPT, RewriterOutput, temperature=0)


def get_classifier_chain():
    return build_parser_chain(CLASSIFIER_PROMPT, ClassificationOutput, temperature=0)


def get_response_chain():
    return build_parser_chain(RESPONSE_PROMPT, ResponseOutput, temperature=0.4)


# ---------- Vision: DashScope PRIMARY, OpenRouter FALLBACK ----------

DASHSCOPE_MODEL = "qwen-omni-turbo"
DASHSCOPE_BASE_URL = "https://dashscope-intl.aliyuncs.com/compatible-mode/v1"

# Small/cheap/lightweight only — not large 70B+ models (per requirement).
OPENROUTER_VLM_FALLBACKS = [
    "qwen/qwen-2.5-vl-7b-instruct:free",
    "meta-llama/llama-3.2-11b-vision-instruct",
    "google/gemma-3-27b-it:free",
]


def _image_path_to_data_url(image_path: str) -> str:
    ext = image_path.split(".")[-1].lower()
    mime_map = {"jpg": "image/jpeg", "jpeg": "image/jpeg", "png": "image/png", "webp": "image/webp"}
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


def _invoke_dashscope_vlm(image_b64: str, claim: str, format_instructions: str) -> str:
    client = _get_dashscope_client()
    prompt_text = (
        "You are an image-validation assistant for a food-delivery support "
        "system. Check whether the image is relevant to the customer's claim "
        "and whether the described issue is visibly present. Judge only on "
        f"visible evidence.\n\n{format_instructions}\n\n"
        f"Customer claim: {claim}\n\nIs this image relevant and does it support the claim?"
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
        model=model_id, api_key=api_key,
        base_url="https://openrouter.ai/api/v1", temperature=0, max_tokens=500,
    )


def invoke_vlm_with_fallback(image_path: str, claim: str) -> dict:
    """1. DashScope qwen-omni-turbo. 2. OpenRouter small/cheap fallbacks."""
    parser = PydanticOutputParser(pydantic_object=VLMOutput)
    format_instructions = parser.get_format_instructions()
    image_b64 = _image_path_to_data_url(image_path)
    last_error = None

    try:
        raw_content = _invoke_dashscope_vlm(image_b64, claim, format_instructions)
        result = parser.parse(raw_content)
        output = result.model_dump()
        output["_provider_used"] = f"dashscope:{DASHSCOPE_MODEL}"
        return output
    except Exception as dashscope_error:
        last_error = dashscope_error

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
        f"DashScope and all OpenRouter fallbacks failed. Last error: {last_error}. "
        f"Tried DashScope ({DASHSCOPE_MODEL}) then OpenRouter: {OPENROUTER_VLM_FALLBACKS}."
    )

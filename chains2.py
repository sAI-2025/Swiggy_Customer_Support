"""LLM clients and chains.

- Groq (llama-3.1-8b-instant): cheap text nodes — rewriter, classifier,
  router, final response.
- OpenRouter (meta-llama/llama-4-maverick:free): free VLM for image validation.

Every chain uses PydanticOutputParser with a retry mechanism for robustness.
If the LLM returns malformed JSON, the parser retries once with the error
message instead of crashing the graph.
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

# Free OpenRouter vision model.
# VLM_MODEL = "meta-llama/llama-4-maverick:free"
VLM_MODEL ="nvidia/nemotron-nano-12b-vl:free"
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


def get_vlm_llm() -> ChatOpenAI:
    """OpenRouter vision model via the OpenAI-compatible LangChain client."""
    api_key = os.getenv("OPENROUTER_API_KEY")
    if not api_key:
        raise ValueError("OPENROUTER_API_KEY is missing from .env")
    return ChatOpenAI(
        model=VLM_MODEL,
        api_key=api_key,
        base_url="https://openrouter.ai/api/v1",
        temperature=0,
        max_tokens=500,
    )


def parse_with_retry(chain, parser, input_dict, max_retries: int = 1):
    """Run chain, catch OutputParserException, retry once with error context.

    If parsing fails, the error is appended to the input and the chain is
    re-invoked — giving the model a chance to fix its own output format.
    """
    try:
        return chain.invoke(input_dict)
    except OutputParserException as e:
        if max_retries <= 0:
            raise
        # Add error context and retry
        input_dict["previous_error"] = str(e)
        input_dict["format_instructions"] = parser.get_format_instructions()
        return parse_with_retry(chain, parser, input_dict, max_retries - 1)


def build_parser_chain(prompt, schema: type[BaseModel], temperature: float = 0):
    """prompt -> LLM -> PydanticOutputParser.

    The chain itself is simple; parse_with_retry() handles recovery from
    malformed LLM outputs by re-invoking with error context.
    """
    llm = get_groq_llm(temperature=temperature)
    parser = PydanticOutputParser(pydantic_object=schema)
    prompt_with_format = prompt.partial(
        format_instructions=parser.get_format_instructions()
    )
    chain = prompt_with_format | llm | parser
    # Attach parser for retry logic
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


def get_vlm_chain():
    """VLM chain: prompt -> OpenRouter VLM -> VLMOutput parser."""
    llm = get_vlm_llm()
    parser = PydanticOutputParser(pydantic_object=VLMOutput)
    prompt_with_format = VLM_PROMPT.partial(
        format_instructions=parser.get_format_instructions()
    )
    chain = prompt_with_format | llm | parser
    chain._parser = parser
    return chain

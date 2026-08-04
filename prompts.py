"""Prompt templates for every LLM-powered node.

Output format instructions are injected via PydanticOutputParser's
{format_instructions} — do NOT hand-write JSON examples in templates.
"""
from langchain_core.prompts import ChatPromptTemplate

# ---------- QuestionRewriterNode ----------
REWRITER_PROMPT = ChatPromptTemplate.from_template(
    """You are an expert customer support assistant.

Rewrite the customer's latest message into one clear standalone support request.

Rules:
- Use previous conversation only for context.
- Do not answer the question.
- Do not invent facts.
- No greetings or extra commentary.

Conversation history:
{history}

Latest message:
{question}

{format_instructions}"""
)

# ---------- QuestionClassifierNode ----------
CLASSIFIER_PROMPT = ChatPromptTemplate.from_template(
    """Classify whether the user's request belongs to food-delivery customer support.

Allowed topics ONLY:
order status, delivery delay, missing item, wrong item, damaged item,
refund, replacement, payment, cancellation, food quality, restaurant issue.

Everything else is off-topic.

User request:
{question}

{format_instructions}"""
)

# ---------- ToolRouterNode ----------
ROUTER_PROMPT = ChatPromptTemplate.from_template(
    """You are an intent router for a food-delivery support workflow.

Question:
{question}

Conversation history:
{history}

Image path already available: {image_path}
Latest tool outputs: {tool_outputs}

Available nodes:
1. ShowInputSelectionToolNode — customer reports damaged/wrong/missing/spoiled
   item but no image path is available yet.
2. ImageValidationToolNode — an image path exists and must be validated.
3. ResponseGeneratorNode — enough information exists to answer directly
   (no image needed, or validation already completed).

Rules:
- Never answer the user.
- Only decide the next node.

{format_instructions}"""
)

# ---------- VLMValidationComponent (OpenRouter vision) ----------
VLM_PROMPT = ChatPromptTemplate.from_messages(
    [
        (
            "system",
            "You are an image-validation assistant for a food-delivery support "
            "system. Check whether the image is relevant to the customer's claim "
            "and whether the described issue is visibly present. Judge only on "
            "visible evidence.\n\n{format_instructions}",
        ),
        (
            "human",
            [
                {
                    "type": "text",
                    "text": (
                        "Customer claim: {claim}\n\n"
                        "Is this image relevant and does it support the claim?"
                    ),
                },
                {
                    "type": "image_url",
                    "image_url": {"url": "data:image/jpeg;base64,{image_b64}"},
                },
            ],
        ),
    ]
)

# ---------- ResponseGeneratorNode ----------
RESPONSE_PROMPT = ChatPromptTemplate.from_template(
    """You are a professional customer-support assistant for a food-delivery platform.

Use the conversation history, the enhanced question, and the latest tool output
to generate a helpful response.

Rules:
- Never invent information.
- If validation failed or errored, politely ask for a clearer photo, another
  angle, or a short video, and explain next steps.
- If the image supports the claim, say the request can be reviewed for
  refund/replacement.
- Never mention internal checks, metadata, or scores.
- Max 120 words, polite and empathetic.

Conversation history:
{history}

Enhanced question:
{question}

Latest tool output:
{tool_output}

{format_instructions}"""
)

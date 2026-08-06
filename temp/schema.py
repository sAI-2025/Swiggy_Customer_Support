"""Pydantic models for workflow state and all node outputs.

These schemas are sent to the LLM as JSON schema during parsing. Field
descriptions act as per-field instructions for the model — keep them
precise, rule-based, and concise.
"""
from enum import Enum
from typing import Any, Optional

from pydantic import BaseModel, ConfigDict, Field


# ---------- Node output schemas ----------


class RewriterOutput(BaseModel):
    enhanced_query: str = Field(
        ...,
        description=(
            "Customer's message rewritten as ONE clear standalone support "
            "question, including any needed context from chat history. "
            "No greetings, no extra text."
        ),
    )


class ClassificationOutput(BaseModel):
    is_related: bool = Field(
        ...,
        description=(
            "True ONLY for food-delivery support topics: order status, "
            "delivery delay, missing/wrong/damaged item, food quality, "
            "refund, replacement, payment, cancellation. False otherwise."
        ),
    )


class RouteEnum(str, Enum):
    SHOW_INPUT = "ShowInputSelectionToolNode"
    IMAGE_VALIDATION = "ImageValidationToolNode"
    RESPONSE_GENERATOR = "ResponseGeneratorNode"


class RouterOutput(BaseModel):
    route: RouteEnum = Field(
        ...,
        description=(
            "Routing decision. "
            "SHOW_INPUT: user reports damaged/wrong/missing/spoiled item but "
            "no image path was provided. "
            "IMAGE_VALIDATION: image path exists and must be verified. "
            "RESPONSE_GENERATOR: no image needed, or validation already done."
        ),
    )
    reason: str = Field(
        default="",
        description="One short sentence explaining the chosen route.",
    )


class ImageValidationOutput(BaseModel):
    metadata_found: bool = Field(
        ...,
        description="True if image file contains EXIF metadata.",
    )
    validation: bool = Field(
        ...,
        description=(
            "True if the image visibly supports the claim: correct item, "
            "visible damage/defect, consistent with complaint text."
        ),
    )
    reason: str = Field(
        default="",
        description="Short factual explanation based only on visible evidence.",
    )


class VLMOutput(BaseModel):
    validation: bool = Field(
        ...,
        description=(
            "True if image is relevant to the claim AND shows the reported "
            "damage/issue. Judge only on visible evidence, never guess."
        ),
    )
    visible_item: str = Field(
        default="",
        description="What item/packaging/food is actually visible.",
    )
    damage_visible: bool = Field(
        default=False,
        description="True if physical damage, spoilage, or defect is clearly visible.",
    )
    claim_consistent: bool = Field(
        default=False,
        description="True if what is visible matches the customer's claim text.",
    )
    confidence: float = Field(
        default=0.0,
        ge=0.0,
        le=1.0,
        description="Confidence in the decision, 0.0 (unsure) to 1.0 (certain).",
    )
    reason: str = Field(
        default="",
        description="1-2 short evidence-based sentences on what the image shows.",
    )


class ResponseOutput(BaseModel):
    final_answer: str = Field(
        ...,
        description=(
            "Final customer-facing reply, max 120 words, polite and empathetic, "
            "written as 'Swiggy Support'. NEVER mention internal checks, EXIF, "
            "or fraud scores, and never accuse the customer. If evidence is "
            "unclear, ask for a clearer photo, another angle, or a short "
            "video, and explain next steps. If a claim was already validated "
            "in this conversation, do NOT ask for another photo — proceed to "
            "confirm next steps (e.g. refund/replacement review) instead."
        ),
    )


# ---------- Shared workflow state (internal only, NOT sent to LLM) ----------


class WorkflowState(BaseModel):
    """Constructor for the INITIAL invoke() input dict only.

    IMPORTANT: this is NOT the object LangGraph passes between nodes
    (that's graph.GraphState, a TypedDict). Because LangGraph MERGES this
    dict into whatever is already checkpointed for the given thread_id,
    any field you explicitly set here to a default will OVERWRITE the
    checkpointed value. See main.py ChatSession.send() for how prior
    state is fetched and carried forward correctly.
    """

    model_config = ConfigDict(use_enum_values=True, arbitrary_types_allowed=True)

    history: list = Field(default_factory=list)
    current_question: str = Field(default="")
    rewritten_question: Optional[str] = None
    classifier_result: Optional[bool] = None
    router_decision: Optional[RouteEnum] = None
    image_path: Optional[str] = None
    metadata: dict[str, Any] = Field(default_factory=dict)
    vlm_result: Optional[str] = None
    fake_claim_result: Optional[str] = None
    validation_error: bool = False
    iteration_count: int = Field(default=0, ge=0)
    tool_outputs: list = Field(default_factory=list)
    final_response: Optional[str] = None

    # Explicit milestone: True once image evidence has been PROCESSED
    # (validated True or False) for the current claim. Prevents the
    # router from re-requesting a photo on later turns like "refund me".
    is_claim_submitted: bool = Field(
        default=False,
        description="True once ImageValidationToolNode has processed evidence for the current claim.",
    )

    # True when the most recently submitted image had NO EXIF metadata,
    # which this prototype treats as a strong signal of an AI-generated
    # or re-exported (non-original) image. Triggers a re-upload request
    # instead of running the VLM (saves cost + avoids validating fakes).
    ai_generated_suspected: bool = Field(
        default=False,
        description="True when the last submitted image had no EXIF data and is suspected AI-generated.",
    )

    # True once the VLM has CONFIRMED the claim (validation=True). Used by
    # main.py to auto-clear this thread's checkpoint after the refund
    # confirmation message is shown to the customer.
    refund_approved: bool = Field(
        default=False,
        description="True once the VLM has confirmed the claim and a refund/replacement review was offered.",
    )

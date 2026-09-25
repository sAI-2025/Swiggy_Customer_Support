"""Pydantic models for workflow state and all node outputs.

These schemas are sent to the LLM as JSON schema during parsing (for the
generic-chat path only — the three terminal image-outcome messages are
FIXED TEMPLATES, no LLM call). Field descriptions act as per-field
instructions for the model — keep them precise, rule-based, and concise.
"""
from enum import Enum
from typing import Any, Optional

from pydantic import BaseModel, ConfigDict, Field


# ---------- Node output schemas (LLM-backed nodes only) ----------


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
            "True ONLY for food, grocery, and quick-commerce support topics: "
            "order status, delivery delay, missing/wrong/damaged item, food "
            "quality, grocery quality, substitution issues, refund, replacement, "
            "payment, cancellation, and item mismatch. False otherwise."
        ),
    )


class RouteEnum(str, Enum):
    SHOW_INPUT = "ShowInputSelectionToolNode"
    IMAGE_VALIDATION = "ImageValidationToolNode"
    RESPONSE_GENERATOR = "ResponseGeneratorNode"


class RouterOutput(BaseModel):
    route: RouteEnum = Field(..., description="Routing decision.")
    reason: str = Field(default="", description="One short sentence explaining the route.")


class ImageValidationOutput(BaseModel):
    metadata_found: bool = Field(..., description="True if image file contains EXIF metadata.")
    validation: bool = Field(
        ...,
        description=(
            "True if the image visibly supports the claim: correct item, "
            "visible damage/defect, consistent with complaint text."
        ),
    )
    reason: str = Field(default="", description="Short factual explanation based only on visible evidence.")


class VLMOutput(BaseModel):
    validation: bool = Field(
        ...,
        description=(
            "True if image is relevant to the claim AND shows the reported "
            "damage/issue. Judge only on visible evidence, never guess."
        ),
    )
    visible_item: str = Field(default="", description="What item/packaging/food is actually visible.")
    damage_visible: bool = Field(default=False, description="True if damage/spoilage is clearly visible.")
    claim_consistent: bool = Field(default=False, description="True if visible content matches the claim.")
    confidence: float = Field(default=0.0, ge=0.0, le=1.0, description="Confidence, 0.0-1.0.")
    reason: str = Field(default="", description="1-2 short evidence-based sentences.")


class ResponseOutput(BaseModel):
    final_answer: str = Field(
        ...,
        description=(
            "Final customer-facing reply for GENERIC chat only (no image "
            "evidence involved), max 120 words, polite. Never mention internal checks or scores."
        ),
    )


# ---------- Shared workflow state (internal only, NOT sent to LLM) ----------


class WorkflowState(BaseModel):
    """Constructor for the INITIAL invoke() input dict only.

    NOT the object LangGraph passes between nodes (that's graph.GraphState,
    a TypedDict). Because LangGraph MERGES this dict into the checkpoint,
    every field you don't explicitly carry forward from the prior
    checkpoint gets reset to its Pydantic default here — see
    main.py ChatSession.send() for the correct carry-forward pattern.
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

    # ---- Workflow/UI control signal (NEW — the core of this redesign) ----
    # None            -> normal chat mode (UI shows text input)
    # "ShowInputSelectionToolNode" -> bot is waiting for an image upload
    #                    (UI shows image widget, disables text input)
    # "ImageValidationToolNode"    -> image was just processed this turn
    #                    (transient — ResponseGeneratorNode resets it to
    #                    None before the turn ends; a real UI would show
    #                    a brief "Processing image..." state here)
    tool_usage: Optional[str] = Field(
        default=None,
        description="Workflow/UI phase marker consumed by ToolRouterNode and the frontend.",
    )

    # Detection signal: True only when the LAST submitted image had no
    # EXIF data (treated as AI-generated / non-original in this prototype).
    ai_generated_suspected: bool = Field(default=False)

    # True once image evidence has been PROCESSED by the VLM (validation
    # True or False) for a NON-suspected (EXIF-present) image. Prevents
    # re-asking for a photo on later turns like "please refund me".
    is_claim_submitted: bool = Field(default=False)

    # True only when the VLM confirmed the claim (validation=True).
    # Business-meaningful outcome flag (kept separate from
    # conversation_resolved, which controls checkpoint clearing).
    refund_approved: bool = Field(default=False)

    # True on BOTH terminal outcomes of the image-evidence flow (refund
    # approved OR claim reviewed-but-rejected). main.py clears the
    # thread's checkpoint after showing the message when this is True.
    # False for the AI-generated-suspected loop (session must stay alive
    # for re-upload) and for ordinary chat turns.
    conversation_resolved: bool = Field(default=False)

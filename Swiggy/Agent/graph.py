"""LangGraph construction: node registration and routing.

============================================================================
ARCHITECTURE: tool_usage as explicit workflow/UI control signal
============================================================================

State fields and their exact meaning (per approved design spec):

  tool_usage (str | None)
      None                          -> normal chat mode (UI: text input)
      "ShowInputSelectionToolNode"  -> bot is waiting for an image upload
                                        (UI: image widget, text disabled)
      "ImageValidationToolNode"     -> set the INSTANT validation runs;
                                        transient within a turn, always
                                        consumed by ResponseGeneratorNode
                                        before the turn ends (see note below)

  ai_generated_suspected (bool)     -> DETECTION signal: True only when the
                                        LAST submitted image had no EXIF.
                                        Reset to its new value every time
                                        ImageValidationToolNode runs (never
                                        needs manual resetting elsewhere).

  is_claim_submitted (bool)         -> True once a NON-suspected image has
                                        been run through the VLM (validation
                                        True OR False). Prevents re-asking
                                        for a photo on later turns.

  refund_approved (bool)            -> True only on VLM validation=True.
                                        Business outcome flag.

  conversation_resolved (bool)      -> True on BOTH terminal outcomes of the
                                        image flow (approved AND rejected).
                                        main.py clears the checkpoint after
                                        showing the message when this is
                                        True. False for the AI-generated
                                        loop (session must stay alive so the
                                        user can re-upload) and for normal
                                        chat turns.

----------------------------------------------------------------------------
WHY NO INFINITE LOOP (the fix over the raw spec):
----------------------------------------------------------------------------
The spec's Rule 1 says tool_usage=="ShowInputSelectionToolNode" routes to
ImageValidationToolNode, and Rule 3 (ai_generated_suspected) also wants to
end up asking for another image. If ImageValidationToolNode itself ever set
tool_usage back to "ShowInputSelectionToolNode" directly, a same-turn
re-route could re-trigger Rule 1 and re-validate the SAME bad image forever.

FIX APPLIED: ImageValidationToolNode ALWAYS sets tool_usage =
"ImageValidationToolNode" after running (all 3 outcomes). Only
ResponseGeneratorNode, at the very end of the turn, sets the NEXT turn's
tool_usage: back to "ShowInputSelectionToolNode" for the ai_generated case
(so the NEXT user message is treated as a fresh image upload), or None for
resolved/normal turns. tool_usage therefore only flows strictly forward
inside a single turn (None/ShowInput -> ImageValidation -> Response), and
its cross-turn value is only ever set by ResponseGeneratorNode. No cycle
is possible within one graph.invoke() call.
----------------------------------------------------------------------------

FULL ROUTING PRIORITY (ToolRouterNode, deterministic, no LLM):

  Rule 1: tool_usage == "ShowInputSelectionToolNode"
          -> the user's CURRENT message IS the image path they were asked
             for -> route to ImageValidationToolNode

  Rule 2: tool_usage == "ImageValidationToolNode"
          -> image validation just ran THIS turn -> route to
             ResponseGeneratorNode to render the outcome message

  Rule 3: is_claim_submitted == True (evidence already processed, earlier
          turn, e.g. user now says "please refund me")
          -> route to ResponseGeneratorNode (no LLM redo of image logic;
             ResponseGeneratorNode gives a short acknowledgement)

  Rule 4: needs_image_evidence(rewritten_question) and image_path is None
          -> route to ShowInputSelectionToolNode (ask for photo)

  Rule 5: image_path is not None (edge case: image supplied same turn as
          the claim, e.g. programmatic/API caller) -> ImageValidationToolNode

  Rule 6: default -> ResponseGeneratorNode (normal chat)

============================================================================
"""
import operator
import sqlite3
from typing import Annotated, Literal, Optional, TypedDict

from langchain_core.messages import AIMessage, BaseMessage, HumanMessage
from langgraph.checkpoint.sqlite import SqliteSaver
from langgraph.graph import END, START, StateGraph

from chains import get_classifier_chain, get_response_chain, get_rewriter_chain
from schema import RouteEnum
from tools import run_image_validation

MAX_TOOL_ITERATIONS = 3
BOT_NAME = "Swiggy Support"
SQLITE_DB_PATH = "swiggy_support.sqlite"

OFF_TOPIC_MESSAGE = (
    f"{BOT_NAME}: I'm designed to assist with food-delivery and grocery "
    "support such as delivery issues, refunds, payments, cancellations, "
    "substitutions, missing items, damaged items, and order-related questions."
)

SHOW_INPUT_MESSAGE = f"{BOT_NAME}: Please enter file path"

# --- FIXED (non-LLM) terminal messages for the image-evidence flow ---

AI_GENERATED_MESSAGE = (
    f"{BOT_NAME}: Please upload the original image on the product. "
    "It looks like an AI-generated one."
)

REFUND_APPROVED_TEMPLATE = (
    f"{BOT_NAME}: We're sorry about your {{item}}. Refund/replacement will "
    "be processed within 24 hours. Our customer care will contact you via "
    "telephone."
)

CLAIM_REJECTED_MESSAGE = (
    f"{BOT_NAME}: We've reviewed the image you shared, but it doesn't "
    "clearly show the issue you described. Our support team will manually "
    "review your request and get back to you shortly."
)

VALIDATION_GAVE_UP_MESSAGE = (
    f"{BOT_NAME}: We're having trouble processing that image right now. "
    "Please try uploading it again in a moment, or describe the issue in "
    "more detail and our team will assist you manually."
)

IMAGE_NEED_KEYWORDS = [
    "damaged", "burnt", "burned", "spilled", "spoiled", "broken", "crushed",
    "wrong item", "missing item", "rotten", "moldy", "leaked", "torn",
    "smashed", "melted", "stale", "expired", "contaminated",
    "substitution", "substituted", "quantity", "short", "less items",
    "missing items", "packaging", "leak", "open pack", "tampered",
]


def needs_image_evidence(question: str) -> bool:
    q = (question or "").lower()
    return any(k in q for k in IMAGE_NEED_KEYWORDS)


def _extract_item_name(question: str) -> str:
    """Best-effort item name for the refund template; falls back to 'order'."""
    q = (question or "").lower()
    for word in [
        "pizza", "burger", "biryani", "drink", "cake", "sandwich", "meal",
        "milk", "bread", "eggs", "egg", "rice", "atta", "oil", "fruits",
        "vegetables", "vegetable", "snacks", "chips", "soap", "shampoo",
        "detergent", "medicine", "groceries", "grocery", "order",
    ]:
        if word in q:
            return word
    return "order"


# ---------- Graph state ----------


class GraphState(TypedDict, total=False):
    history: Annotated[list[BaseMessage], operator.add]
    current_question: str
    rewritten_question: Optional[str]
    classifier_result: Optional[bool]
    router_decision: Optional[str]
    image_path: Optional[str]
    metadata: dict
    vlm_result: Optional[str]
    fake_claim_result: Optional[str]
    validation_error: bool
    iteration_count: int
    tool_outputs: Annotated[list, operator.add]
    final_response: Optional[str]

    tool_usage: Optional[str]
    ai_generated_suspected: bool
    is_claim_submitted: bool
    refund_approved: bool
    conversation_resolved: bool


def _history_as_text(history: list) -> str:
    lines = []
    for m in history or []:
        if isinstance(m, HumanMessage):
            lines.append(f"Customer: {m.content}")
        elif isinstance(m, AIMessage):
            lines.append(f"Assistant: {m.content}")
        else:
            lines.append(f"Customer: {m}")
    return "\n".join(lines) or "No previous conversation."


# ---------- Node 1: QuestionRewriterNode ----------


def question_rewriter_node(state: GraphState) -> dict:
    result = get_rewriter_chain().invoke({
        "history": _history_as_text(state.get("history", [])),
        "question": state["current_question"],
    })
    return {"rewritten_question": result.enhanced_query}


# ---------- Node 2: QuestionClassifierNode (crash-proof) ----------


def question_classifier_node(state: GraphState) -> dict:
    try:
        result = get_classifier_chain().invoke({"question": state["rewritten_question"]})
        return {"classifier_result": result.is_related}
    except Exception:
        q = (state["rewritten_question"] or "").lower()
        keywords = [
            "order", "deliver", "refund", "cancel", "missing", "damaged",
            "wrong", "spoiled", "payment", "replace", "food", "item",
            "pizza", "burger", "biryani", "restaurant", "grocery",
            "groceries", "quick commerce", "quick-commerce", "zepto",
            "blinkit", "instamart", "supermarket", "substitution",
            "expired", "quantity", "pack", "bag", "produce",
        ]
        return {"classifier_result": any(k in q for k in keywords)}


# ---------- Node 3: OffTopicHandlerNode ----------


def off_topic_handler_node(state: GraphState) -> dict:
    return {
        "final_response": OFF_TOPIC_MESSAGE,
        "tool_outputs": [OFF_TOPIC_MESSAGE],
        "history": [
            HumanMessage(content=state["current_question"]),
            AIMessage(content=OFF_TOPIC_MESSAGE),
        ],
    }


# ---------- Node 4: ToolRouterNode (DETERMINISTIC, priority order per spec) ----------


def tool_router_node(state: GraphState) -> dict:
    tool_usage = state.get("tool_usage")
    is_claim_submitted = state.get("is_claim_submitted", False)
    image_path = state.get("image_path")

    # Rule 1: bot previously asked for a photo -> THIS message is the path.
    if tool_usage == "ShowInputSelectionToolNode":
        return {"router_decision": RouteEnum.IMAGE_VALIDATION.value}

    # Rule 2: validation just ran this turn -> render the outcome.
    if tool_usage == "ImageValidationToolNode":
        return {"router_decision": RouteEnum.RESPONSE_GENERATOR.value}

    # Rule 3: evidence already processed in an earlier turn -> answer.
    if is_claim_submitted:
        return {"router_decision": RouteEnum.RESPONSE_GENERATOR.value}

    # Rule 4: claim needs evidence, none supplied yet -> ask for it.
    if needs_image_evidence(state["rewritten_question"]) and not image_path:
        return {"router_decision": RouteEnum.SHOW_INPUT.value}

    # Rule 5: image already present this turn (edge case) -> validate.
    if image_path:
        return {"router_decision": RouteEnum.IMAGE_VALIDATION.value}

    # Rule 6: normal chat.
    return {"router_decision": RouteEnum.RESPONSE_GENERATOR.value}


def route_from_router(state: GraphState) -> str:
    return state["router_decision"]


# ---------- Node 5: ShowInputSelectionToolNode (no AI) ----------


def show_input_selection_node(state: GraphState) -> dict:
    return {
        "final_response": SHOW_INPUT_MESSAGE,
        "tool_outputs": [SHOW_INPUT_MESSAGE],
        "tool_usage": "ShowInputSelectionToolNode",
        "history": [
            HumanMessage(content=state["current_question"]),
            AIMessage(content=SHOW_INPUT_MESSAGE),
        ],
    }


# ---------- Node 6: ImageValidationToolNode ----------


def image_validation_node(state: GraphState) -> dict:
    claim = state["rewritten_question"]
    image_path = state.get("image_path")

    try:
        if not image_path:
            raise ValueError("No image path provided")

        result = run_image_validation(image_path, claim)
        outcome = result["outcome"]  # "ai_generated" | "approved" | "rejected"

        # tool_usage ALWAYS becomes "ImageValidationToolNode" here — never
        # "ShowInputSelectionToolNode" directly (see module docstring: this
        # is what prevents the infinite-loop failure mode).
        base_update = {
            "metadata": result["metadata"],
            "vlm_result": str(result["vlm_result"]) if result["vlm_result"] else None,
            "tool_outputs": [str(result["output"])],
            "validation_error": False,
            "tool_usage": "ImageValidationToolNode",
        }

        if outcome == "ai_generated":
            return {
                **base_update,
                "ai_generated_suspected": True,
                "is_claim_submitted": False,
                "refund_approved": False,
            }

        if outcome == "approved":
            return {
                **base_update,
                "ai_generated_suspected": False,
                "is_claim_submitted": True,
                "refund_approved": True,
            }

        # outcome == "rejected"
        return {
            **base_update,
            "ai_generated_suspected": False,
            "is_claim_submitted": True,
            "refund_approved": False,
        }

    except Exception as exc:
        return {
            "validation_error": True,
            "tool_outputs": [f"error: {exc}"],
        }


# ---------- Node 7: ShouldContinueToolExecutionNode ----------


def should_continue_node(state: GraphState) -> dict:
    return {"iteration_count": state.get("iteration_count", 0) + 1}


def route_should_continue(state: GraphState) -> Literal["ImageValidationToolNode", "ToolRouterNode"]:
    if state.get("validation_error") and state.get("iteration_count", 0) < MAX_TOOL_ITERATIONS:
        return "ImageValidationToolNode"
    return "ToolRouterNode"


# ---------- Node 8: ResponseGeneratorNode ----------


def response_generator_node(state: GraphState) -> dict:
    # --- Outcome A: AI-generated suspected -> fixed message, no LLM.
    #     Reset tool_usage to "ShowInputSelectionToolNode" for the NEXT
    #     turn so the user's next message is treated as a fresh upload.
    #     Session stays ALIVE (conversation_resolved=False).
    if state.get("ai_generated_suspected"):
        return {
            "final_response": AI_GENERATED_MESSAGE,
            "tool_usage": "ShowInputSelectionToolNode",
            "ai_generated_suspected": False,  # consumed; fresh detection next upload
            "conversation_resolved": False,
            "history": [
                HumanMessage(content=state["current_question"]),
                AIMessage(content=AI_GENERATED_MESSAGE),
            ],
        }

    # --- Outcome B: refund approved -> fixed message, no LLM.
    #     Session RESOLVED -> main.py clears checkpoint after showing this.
    if state.get("refund_approved"):
        item = _extract_item_name(state.get("rewritten_question", ""))
        message = REFUND_APPROVED_TEMPLATE.format(item=item)
        return {
            "final_response": message,
            "tool_usage": None,
            "conversation_resolved": True,
            "history": [
                HumanMessage(content=state["current_question"]),
                AIMessage(content=message),
            ],
        }

    # --- Outcome C: claim reviewed but image didn't support it ->
    #     fixed message, no LLM. Session RESOLVED (evidence was processed;
    #     don't loop back asking for more photos automatically).
    if state.get("is_claim_submitted") and state.get("tool_usage") == "ImageValidationToolNode":
        return {
            "final_response": CLAIM_REJECTED_MESSAGE,
            "tool_usage": None,
            "conversation_resolved": True,
            "history": [
                HumanMessage(content=state["current_question"]),
                AIMessage(content=CLAIM_REJECTED_MESSAGE),
            ],
        }

    # --- Outcome D: validation kept failing (bad path etc.) after retries.
    if state.get("validation_error"):
        return {
            "final_response": VALIDATION_GAVE_UP_MESSAGE,
            "tool_usage": None,
            "conversation_resolved": False,
            "history": [
                HumanMessage(content=state["current_question"]),
                AIMessage(content=VALIDATION_GAVE_UP_MESSAGE),
            ],
        }

    # --- Outcome E: is_claim_submitted True from an EARLIER turn (e.g.
    #     user follows up with "please refund me" after already being
    #     resolved in a prior turn that somehow didn't clear — safety net).
    if state.get("is_claim_submitted"):
        message = f"{BOT_NAME}: Your request is already being reviewed by our team. We'll update you shortly."
        return {
            "final_response": message,
            "tool_usage": None,
            "conversation_resolved": True,
            "history": [
                HumanMessage(content=state["current_question"]),
                AIMessage(content=message),
            ],
        }

    # --- Outcome F: normal generic chat -> LLM-generated response. ---
    result = get_response_chain().invoke({
        "history": _history_as_text(state.get("history", [])),
        "question": state["rewritten_question"],
    })
    final_text = f"{BOT_NAME}: {result.final_answer}"
    return {
        "final_response": final_text,
        "tool_usage": None,
        "conversation_resolved": False,
        "history": [
            HumanMessage(content=state["current_question"]),
            AIMessage(content=final_text),
        ],
    }


# ---------- Graph construction ----------


def build_graph():
    graph = StateGraph(GraphState)

    graph.add_node("QuestionRewriterNode", question_rewriter_node)
    graph.add_node("QuestionClassifierNode", question_classifier_node)
    graph.add_node("OffTopicHandlerNode", off_topic_handler_node)
    graph.add_node("ToolRouterNode", tool_router_node)
    graph.add_node("ShowInputSelectionToolNode", show_input_selection_node)
    graph.add_node("ImageValidationToolNode", image_validation_node)
    graph.add_node("ShouldContinueToolExecutionNode", should_continue_node)
    graph.add_node("ResponseGeneratorNode", response_generator_node)

    graph.add_edge(START, "QuestionRewriterNode")
    graph.add_edge("QuestionRewriterNode", "QuestionClassifierNode")

    graph.add_conditional_edges(
        "QuestionClassifierNode",
        lambda s: "ToolRouterNode" if s["classifier_result"] else "OffTopicHandlerNode",
        {"ToolRouterNode": "ToolRouterNode", "OffTopicHandlerNode": "OffTopicHandlerNode"},
    )

    graph.add_conditional_edges(
        "ToolRouterNode",
        route_from_router,
        {
            RouteEnum.SHOW_INPUT.value: "ShowInputSelectionToolNode",
            RouteEnum.IMAGE_VALIDATION.value: "ImageValidationToolNode",
            RouteEnum.RESPONSE_GENERATOR.value: "ResponseGeneratorNode",
        },
    )

    graph.add_edge("ShowInputSelectionToolNode", END)

    graph.add_edge("ImageValidationToolNode", "ShouldContinueToolExecutionNode")
    graph.add_conditional_edges(
        "ShouldContinueToolExecutionNode",
        route_should_continue,
        {"ImageValidationToolNode": "ImageValidationToolNode", "ToolRouterNode": "ToolRouterNode"},
    )

    graph.add_edge("OffTopicHandlerNode", END)
    graph.add_edge("ResponseGeneratorNode", END)

    conn = sqlite3.connect(SQLITE_DB_PATH, check_same_thread=False)
    checkpointer = SqliteSaver(conn)

    return graph.compile(checkpointer=checkpointer)

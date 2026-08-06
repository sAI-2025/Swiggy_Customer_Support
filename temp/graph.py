"""LangGraph construction: node registration and routing.

Flow (per Workflow.txt), updated per this session's 4 requirements:

    START -> QuestionRewriterNode -> QuestionClassifierNode
        off-topic -> OffTopicHandlerNode -> END
        on-topic  -> ToolRouterNode (DETERMINISTIC)
            -> ShowInputSelectionToolNode -> END  (asks user for file path)
            -> ImageValidationToolNode -> ShouldContinueToolExecutionNode
                no EXIF found        -> ai_generated_suspected=True,
                                         ask user to re-upload ORIGINAL photo
                error & retries left  -> ImageValidationToolNode (retry)
                otherwise             -> ToolRouterNode
            -> ResponseGeneratorNode -> END

REQUIREMENT #1 — SQLITE CHECKPOINTER:
build_graph() now compiles with SqliteSaver (langgraph-checkpoint-sqlite)
instead of MemorySaver, so conversation state survives process restarts
and is stored in ./swiggy_support.sqlite on disk.

REQUIREMENT #2 — DASHSCOPE-FIRST VLM:
Handled in chains.py (invoke_vlm_with_fallback) and tools.py — this file
is unaffected except that ImageValidationToolNode now calls
run_image_validation(), which internally tries DashScope then OpenRouter.

REQUIREMENT #3 — AI-GENERATED IMAGE DETECTION:
When ImageValidationToolNode finds NO EXIF data, it sets
ai_generated_suspected=True and skips the VLM call entirely. ToolRouterNode
Rule 0.5 catches this and routes straight to ResponseGeneratorNode, which
(via prompts.py) tells the customer to upload the original photo.

REQUIREMENT #4 — AUTO-CLEAR CHECKPOINT AFTER REFUND:
ResponseGeneratorNode sets refund_approved=True whenever the last VLM
validation was True (claim confirmed). main.py's ChatSession checks this
flag after every response and calls checkpointer.delete_thread(thread_id)
right after showing the refund-confirmation message, so the NEXT message
starts a completely fresh conversation automatically (no /new needed).

ROUTER DESIGN NOTE:
ToolRouterNode remains DETERMINISTIC (no LLM call) for reliability —
testing showed a small 8B model unreliably ignores explicit facts when
asked to route via LLM.
"""
import operator
import sqlite3
from typing import Annotated, Literal, Optional, TypedDict

from langchain_core.messages import AIMessage, BaseMessage, HumanMessage
from langgraph.checkpoint.sqlite import SqliteSaver
from langgraph.graph import END, START, StateGraph

from chains import get_classifier_chain, get_response_chain, get_rewriter_chain
from schema import RouteEnum
from tools import AI_GENERATED_MESSAGE, run_image_validation

MAX_TOOL_ITERATIONS = 3

BOT_NAME = "Swiggy Support"

OFF_TOPIC_MESSAGE = (
    f"{BOT_NAME}: I'm designed to assist with food-delivery customer support "
    "such as delivery issues, refunds, payments, cancellations, and "
    "order-related questions."
)

SHOW_INPUT_MESSAGE = f"{BOT_NAME}: Please enter file path"

SQLITE_DB_PATH = "swiggy_support.sqlite"

# Keywords that imply the customer needs to submit photo evidence.
IMAGE_NEED_KEYWORDS = [
    "damaged", "burnt", "burned", "spilled", "spoiled", "broken", "crushed",
    "wrong item", "missing item", "rotten", "moldy", "leaked", "torn",
    "smashed", "melted", "stale", "expired", "contaminated",
]


def needs_image_evidence(question: str) -> bool:
    q = (question or "").lower()
    return any(k in q for k in IMAGE_NEED_KEYWORDS)


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

    is_claim_submitted: bool
    ai_generated_suspected: bool
    refund_approved: bool


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
        result = get_classifier_chain().invoke({
            "question": state["rewritten_question"],
        })
        return {"classifier_result": result.is_related}
    except Exception:
        q = (state["rewritten_question"] or "").lower()
        keywords = [
            "order", "deliver", "refund", "cancel", "missing", "damaged",
            "wrong", "spoiled", "payment", "replace", "food", "item",
            "pizza", "burger", "biryani", "restaurant",
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


# ---------- Node 4: ToolRouterNode (DETERMINISTIC) ----------


def tool_router_node(state: GraphState) -> dict:
    is_claim_submitted = state.get("is_claim_submitted", False)
    ai_generated_suspected = state.get("ai_generated_suspected", False)
    image_path = state.get("image_path")
    validation_error = state.get("validation_error", False)
    iteration_count = state.get("iteration_count", 0)

    # Rule 0: claim evidence already processed this conversation -> answer.
    # (Covers both a confirmed claim AND an ai_generated rejection that
    # still needs a customer-facing explanation, not another photo request.)
    if is_claim_submitted or ai_generated_suspected:
        return {"router_decision": RouteEnum.RESPONSE_GENERATOR.value}

    # Rule 1: an image path exists but hasn't been validated yet this turn.
    if image_path:
        if validation_error and iteration_count >= MAX_TOOL_ITERATIONS:
            return {"router_decision": RouteEnum.RESPONSE_GENERATOR.value}
        return {"router_decision": RouteEnum.IMAGE_VALIDATION.value}

    # Rule 2: claim implies damage/quality evidence but no image supplied yet.
    if needs_image_evidence(state["rewritten_question"]):
        return {"router_decision": RouteEnum.SHOW_INPUT.value}

    # Rule 3: nothing image-related -> answer directly.
    return {"router_decision": RouteEnum.RESPONSE_GENERATOR.value}


def route_from_router(state: GraphState) -> str:
    return state["router_decision"]


# ---------- Node 5: ShowInputSelectionToolNode (no AI) ----------


def show_input_selection_node(state: GraphState) -> dict:
    return {
        "final_response": SHOW_INPUT_MESSAGE,
        "tool_outputs": [SHOW_INPUT_MESSAGE],
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

        update = {
            "metadata": result["metadata"],
            "vlm_result": result["vlm_result"],
            "fake_claim_result": result["fake_claim_result"],
            "validation_error": False,
            "tool_outputs": [str(result["output"])],
            "ai_generated_suspected": result.get("ai_generated_suspected", False),
        }

        # Mark claim as "processed" ONLY when a real VLM check ran.
        # If ai_generated_suspected=True, we do NOT set is_claim_submitted —
        # the customer must re-upload; router Rule 0 still catches this
        # via ai_generated_suspected so it won't loop back to ShowInput.
        if not result.get("ai_generated_suspected", False):
            update["is_claim_submitted"] = True

        return update

    except Exception as exc:
        return {
            "validation_error": True,
            "tool_outputs": [f"error: {exc}"],
        }


# ---------- Node 7: ShouldContinueToolExecutionNode ----------


def should_continue_node(state: GraphState) -> dict:
    return {"iteration_count": state.get("iteration_count", 0) + 1}


def route_should_continue(state: GraphState) -> Literal[
    "ImageValidationToolNode", "ToolRouterNode"
]:
    if state.get("validation_error") and state.get("iteration_count", 0) < MAX_TOOL_ITERATIONS:
        return "ImageValidationToolNode"
    return "ToolRouterNode"


# ---------- Node 8: ResponseGeneratorNode ----------


def response_generator_node(state: GraphState) -> dict:
    # Requirement #3: AI-generated image suspected -> short-circuit with
    # the exact required message, skip the LLM response chain entirely.
    if state.get("ai_generated_suspected"):
        message = f"{BOT_NAME}: {AI_GENERATED_MESSAGE}"
        return {
            "final_response": message,
            "history": [
                HumanMessage(content=state["current_question"]),
                AIMessage(content=message),
            ],
        }

    tool_outputs = state.get("tool_outputs") or []
    result = get_response_chain().invoke({
        "history": _history_as_text(state.get("history", [])),
        "question": state["rewritten_question"],
        "tool_output": tool_outputs[-1] if tool_outputs else "No tool output available",
    })

    final_text = f"{BOT_NAME}: {result.final_answer}"

    # Requirement #4: detect a confirmed claim (VLM validation=True) to
    # flag this turn as refund-approved. main.py checks this flag and
    # clears the thread's checkpoint right after showing this message.
    vlm_result_str = state.get("vlm_result") or ""
    refund_approved = "'validation': True" in vlm_result_str or '"validation": true' in vlm_result_str.lower()

    return {
        "final_response": final_text,
        "refund_approved": refund_approved,
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
        {
            "ToolRouterNode": "ToolRouterNode",
            "OffTopicHandlerNode": "OffTopicHandlerNode",
        },
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
        {
            "ImageValidationToolNode": "ImageValidationToolNode",
            "ToolRouterNode": "ToolRouterNode",
        },
    )

    graph.add_edge("OffTopicHandlerNode", END)
    graph.add_edge("ResponseGeneratorNode", END)

    # Requirement #1: SQLite checkpointer instead of in-RAM MemorySaver.
    # check_same_thread=False is required because LangGraph may access the
    # connection from a different thread than the one that created it.
    conn = sqlite3.connect(SQLITE_DB_PATH, check_same_thread=False)
    checkpointer = SqliteSaver(conn)

    return graph.compile(checkpointer=checkpointer)

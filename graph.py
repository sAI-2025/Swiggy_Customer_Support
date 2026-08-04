"""LangGraph construction: node registration and routing.

Flow (per Workflow.txt):
    START -> QuestionRewriterNode -> QuestionClassifierNode
        off-topic -> OffTopicHandlerNode -> END
        on-topic  -> ToolRouterNode (DETERMINISTIC, see below)
            -> ShowInputSelectionToolNode -> END  (asks user for file path)
            -> ImageValidationToolNode -> ShouldContinueToolExecutionNode
                error & retries left -> ImageValidationToolNode (retry)
                otherwise            -> ToolRouterNode
            -> ResponseGeneratorNode -> END

ROUTER DESIGN NOTE:
ToolRouterNode was originally a pure LLM call, but testing showed a small
8B model unreliably ignores explicit facts (e.g. picks "ask for image" even
when an image_path is already present). Routing here only needs 3 simple
facts already available in state — image_path set? validated already?
claim implies damage evidence? — so it is now DETERMINISTIC (no LLM call).
This is cheaper, faster, and fully reproducible for testing. The LLM
router chain (chains.get_router_chain) is kept available for future,
genuinely ambiguous multi-tool scenarios.

Memory: compiled with MemorySaver checkpointer. Invoking with the same
thread_id restores prior state (history persists across turns); history is
stored as HumanMessage/AIMessage via a reducer. IMPORTANT: use a UNIQUE
thread_id per independent test case — sharing one thread_id across
unrelated tests leaks state (tool_outputs/history) between them.
"""
import operator
from typing import Annotated, Literal, Optional, TypedDict

from langchain_core.messages import AIMessage, BaseMessage, HumanMessage
from langgraph.checkpoint.memory import MemorySaver
from langgraph.graph import END, START, StateGraph

from chains import get_classifier_chain, get_response_chain, get_rewriter_chain
from schema import RouteEnum
from tools import run_image_validation

MAX_TOOL_ITERATIONS = 3

OFF_TOPIC_MESSAGE = (
    "I'm designed to assist with food-delivery customer support such as "
    "delivery issues, refunds, payments, cancellations, and order-related questions."
)

SHOW_INPUT_MESSAGE = "Please enter file path"

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


# ---------- Node 4: ToolRouterNode (DETERMINISTIC — see module docstring) ----------


def tool_router_node(state: GraphState) -> dict:
    image_path = state.get("image_path")
    vlm_result = state.get("vlm_result")
    validation_error = state.get("validation_error", False)
    iteration_count = state.get("iteration_count", 0)

    # Rule 1: validation already completed successfully -> answer now.
    if vlm_result is not None:
        return {"router_decision": RouteEnum.RESPONSE_GENERATOR.value}

    # Rule 2: an image path exists but hasn't been validated yet.
    if image_path:
        # Give up after MAX_TOOL_ITERATIONS failed attempts -> explain to user.
        if validation_error and iteration_count >= MAX_TOOL_ITERATIONS:
            return {"router_decision": RouteEnum.RESPONSE_GENERATOR.value}
        return {"router_decision": RouteEnum.IMAGE_VALIDATION.value}

    # Rule 3: claim implies damage/quality evidence but no image supplied yet.
    if needs_image_evidence(state["rewritten_question"]):
        return {"router_decision": RouteEnum.SHOW_INPUT.value}

    # Rule 4: nothing image-related -> answer directly.
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
        return {
            "metadata": result["metadata"],
            "vlm_result": result["vlm_result"],
            "fake_claim_result": result["fake_claim_result"],
            "validation_error": False,
            "tool_outputs": [str(result["output"])],
        }
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
    tool_outputs = state.get("tool_outputs") or []
    result = get_response_chain().invoke({
        "history": _history_as_text(state.get("history", [])),
        "question": state["rewritten_question"],
        "tool_output": tool_outputs[-1] if tool_outputs else "No tool output available",
    })
    return {
        "final_response": result.final_answer,
        "history": [
            HumanMessage(content=state["current_question"]),
            AIMessage(content=result.final_answer),
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

    memory = MemorySaver()
    return graph.compile(checkpointer=memory)

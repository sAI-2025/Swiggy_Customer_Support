"""LangGraph construction: node registration and routing.

Flow (per Workflow.txt):
    START -> QuestionRewriterNode -> QuestionClassifierNode
        off-topic -> OffTopicHandlerNode -> END
        on-topic  -> ToolRouterNode
            -> ShowInputSelectionToolNode -> END  (asks user for file path)
            -> ImageValidationToolNode -> ShouldContinueToolExecutionNode
                error & retries left -> ImageValidationToolNode (retry)
                otherwise            -> ToolRouterNode
            -> ResponseGeneratorNode -> END

Memory: compiled with MemorySaver checkpointer. Invoking with the same
thread_id restores prior state (history persists across turns); history is
stored as HumanMessage/AIMessage via a reducer.
"""
import operator
from typing import Annotated, Any, Literal, Optional, TypedDict

from langchain_core.messages import AIMessage, BaseMessage, HumanMessage
from langgraph.checkpoint.memory import MemorySaver
from langgraph.graph import END, START, StateGraph

from chains import (
    get_classifier_chain,
    get_response_chain,
    get_rewriter_chain,
    get_router_chain,
)
from schema import RouteEnum
from tools import run_image_validation

MAX_TOOL_ITERATIONS = 3

OFF_TOPIC_MESSAGE = (
    "I'm designed to assist with food-delivery customer support such as "
    "delivery issues, refunds, payments, cancellations, and order-related questions."
)

SHOW_INPUT_MESSAGE = "Please enter file path"


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
        # Keyword fallback so a parser failure never kills the graph.
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


# ---------- Node 4: ToolRouterNode ----------


def tool_router_node(state: GraphState) -> dict:
    result = get_router_chain().invoke({
        "question": state["rewritten_question"],
        "history": _history_as_text(state.get("history", [])[-4:]),
        "image_path": state.get("image_path") or "None",
        "tool_outputs": (state.get("tool_outputs") or ["None"])[-1],
    })
    route = result.route.value if isinstance(result.route, RouteEnum) else str(result.route)
    return {"router_decision": route}


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

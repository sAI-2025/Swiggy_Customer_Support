"""Entry point: build the graph and verify every node works end-to-end.

Runs a small test suite against the compiled LangGraph app:
1. Off-topic question -> OffTopicHandlerNode
2. Simple support question -> ResponseGeneratorNode (no image needed)
3. Image claim, no image -> ShowInputSelectionToolNode ("Please enter file path")
4. Image claim + bad path -> retry loop, then graceful response
5. Image claim + real image -> full validation pipeline (EXIF + VLM)

IMPORTANT: each test case uses its OWN thread_id. The graph is compiled
with a MemorySaver checkpointer, so invocations sharing a thread_id will
inherit prior state (history, tool_outputs, image_path, etc). Using one
shared thread_id across unrelated tests was the root cause of earlier
failures (Test 2/3/4 all got contaminated by Test 1's leftover state).
"""
import sys

from graph import build_graph
from schema import WorkflowState

SEPARATOR = "=" * 70


def run_case(app, name: str, thread_id: str, question: str, image_path: str | None = None):
    print(f"\n{SEPARATOR}\nTEST: {name}\n{SEPARATOR}")
    config = {"configurable": {"thread_id": thread_id}}
    state = WorkflowState(current_question=question, image_path=image_path)

    result = app.invoke(state.model_dump(), config=config)

    checks = {
        "rewritten_question": result.get("rewritten_question"),
        "classifier_result": result.get("classifier_result"),
        "router_decision": result.get("router_decision"),
        "iteration_count": result.get("iteration_count", 0),
        "validation_error": result.get("validation_error"),
        "metadata_found": bool(result.get("metadata")) and "status" not in (result.get("metadata") or {}),
        "vlm_result": result.get("vlm_result"),
        "tool_outputs": result.get("tool_outputs", []),
        "final_response": result.get("final_response"),
    }
    for key, value in checks.items():
        print(f"  {key}: {value}")
    return result


def interactive_chat(app):
    print(f"\n{SEPARATOR}\nINTERACTIVE CHAT (type 'exit' to stop)\n{SEPARATOR}")
    thread = {"configurable": {"thread_id": "live-session-1"}}
    last_question = None

    while True:
        user_input = input("\nCustomer: ").strip()
        if user_input.lower() in {"exit", "quit"}:
            break
        if not user_input:
            continue

        # If the graph is waiting for an image path, treat this input as that.
        state_check = app.get_state(thread)
        awaiting_image = (
            state_check.values.get("router_decision") == "ShowInputSelectionToolNode"
        )

        if awaiting_image and last_question:
            state = WorkflowState(current_question=last_question, image_path=user_input)
        else:
            state = WorkflowState(current_question=user_input)
            last_question = user_input

        result = app.invoke(state.model_dump(), config=thread)
        print(f"\nAssistant: {result.get('final_response')}")


def main():
    app = build_graph()
    failures = []

    # --- Test 1: off-topic -> OffTopicHandlerNode ---
    r = run_case(app, "Off-topic question", "test-1-offtopic",
                 "Who won the football match last night?")
    if r.get("classifier_result") is not False or "food-delivery" not in (r.get("final_response") or ""):
        failures.append("Test 1 (off-topic)")

    # --- Test 2: plain support question -> ResponseGeneratorNode (NO image needed) ---
    r = run_case(app, "Simple support question", "test-2-cancel",
                 "How do I cancel my order?")
    if r.get("router_decision") != "ResponseGeneratorNode":
        failures.append(f"Test 2 (expected ResponseGeneratorNode, got {r.get('router_decision')})")

    # --- Test 3: image claim without image -> ShowInputSelectionToolNode ---
    r = run_case(app, "Image claim without image", "test-3-noimage",
                 "My pizza arrived completely burnt and ruined")
    if r.get("final_response") != "Please enter file path":
        failures.append("Test 3 (show input selection)")

    # --- Test 4: bad image path -> retry loop, then graceful finish ---
    r = run_case(app, "Image claim with invalid path", "test-4-badpath",
                 "My drink spilled all over the box, order 12345",
                 image_path="C:/does_not_exist.jpg")
    if r.get("iteration_count", 0) < 1 or not r.get("validation_error"):
        failures.append("Test 4 (retry loop)")

    # --- Test 5: real image -> full validation pipeline (EXIF + VLM) ---
    default_image = r"C:\Users\chskc\Desktop\Swiggy\bruned_piza.jpg"
    real_image = sys.argv[1] if len(sys.argv) > 1 else default_image

    r = run_case(app, "Image claim with real image", "test-5-realimage",
                 "My pizza arrived completely burnt, here is the photo",
                 image_path=real_image)
    if r.get("validation_error"):
        failures.append(f"Test 5 (image validation errored: {r.get('tool_outputs')})")
    elif not r.get("final_response"):
        failures.append("Test 5 (no final response)")

    print(f"\n{SEPARATOR}")
    if failures:
        print("FAILED:", ", ".join(failures))
    else:
        print("All executed tests passed.")

    interactive_chat(app)


if __name__ == "__main__":
    main()

"""Entry point: interactive Swiggy Support chat application.

Commands:
    /new      start a brand-new conversation (clears memory for this session)
    /state    print the raw internal graph state (debugging)
    /help     show available commands
    /exit     quit the application

UI CONTRACT (for a future web/mobile frontend):
    result["tool_usage"] == "ShowInputSelectionToolNode"
        -> show image upload widget, DISABLE text input
    result["tool_usage"] is None
        -> show normal text chat input
    (In this terminal prototype, print_step() simulates the "Processing
    image..." transient state a real UI would show for tool_usage ==
    "ImageValidationToolNode", which only exists mid-turn and never
    reaches the UI as a rendered state.)

AUTO-CLEAR ON RESOLUTION:
    result["conversation_resolved"] == True (refund approved OR claim
    reviewed-and-rejected) -> after printing final_response, the thread's
    SQLite checkpoint is deleted automatically. The AI-generated-image
    loop does NOT resolve the conversation (conversation_resolved stays
    False) so the user can re-upload within the same session.

STATE CARRY-FORWARD (critical — do not build a bare WorkflowState()):
    Every new turn fetches the prior checkpoint and explicitly forwards
    history/tool_usage/is_claim_submitted/etc. LangGraph MERGES the input
    dict into the checkpoint, so any field left at its Pydantic default
    would silently overwrite (erase) the real checkpointed value.
"""
import os

os.environ["LANGCHAIN_TRACING_V2"] = "false"
os.environ["LANGSMITH_TRACING"] = "false"

from graph import build_graph
from schema import WorkflowState


class Color:
    RESET = "\033[0m"
    BOLD = "\033[1m"
    DIM = "\033[2m"
    CYAN = "\033[36m"
    GREEN = "\033[32m"
    YELLOW = "\033[33m"
    RED = "\033[31m"
    MAGENTA = "\033[35m"
    BLUE = "\033[34m"


def enable_windows_ansi():
    if os.name == "nt":
        os.system("")


def banner():
    print(f"{Color.CYAN}{Color.BOLD}")
    print("=" * 70)
    print("             SWIGGY SUPPORT — Customer Care Assistant".center(70))
    print("=" * 70)
    print(f"{Color.RESET}{Color.DIM}")
    print("  Type your message naturally, like a real customer.")
    print("  Commands: /new  /state  /help  /exit")
    print(f"{Color.RESET}")


def print_assistant(message: str):
    print(f"\n{Color.GREEN}{Color.BOLD}{message}{Color.RESET}")


def print_system(message: str):
    print(f"{Color.YELLOW}{message}{Color.RESET}")


def print_error(message: str):
    print(f"\n{Color.RED}{Color.BOLD}Error:{Color.RESET} {Color.RED}{message}{Color.RESET}")


def print_step(message: str):
    print(f"{Color.DIM}  -> {message}{Color.RESET}")


def print_ui_hint(tool_usage: str | None):
    """Simulates what a real frontend would render based on tool_usage."""
    if tool_usage == "ShowInputSelectionToolNode":
        print(f"{Color.BLUE}  [UI] Image upload widget shown. Text input disabled.{Color.RESET}")


def print_debug_state(state_values: dict):
    print(f"\n{Color.MAGENTA}{Color.BOLD}--- Internal graph state ---{Color.RESET}")
    keys_to_show = [
        "rewritten_question", "classifier_result", "router_decision",
        "image_path", "iteration_count", "validation_error",
        "tool_usage", "ai_generated_suspected", "is_claim_submitted",
        "refund_approved", "conversation_resolved",
        "metadata", "vlm_result",
    ]
    for key in keys_to_show:
        print(f"{Color.MAGENTA}  {key}:{Color.RESET} {state_values.get(key)}")
    print(f"{Color.MAGENTA}{Color.BOLD}----------------------------{Color.RESET}")


def print_help():
    print(f"\n{Color.BLUE}{Color.BOLD}Available commands:{Color.RESET}")
    print(f"{Color.BLUE}  /new     Start a new conversation (clears this session's memory){Color.RESET}")
    print(f"{Color.BLUE}  /state   Show internal graph state for debugging{Color.RESET}")
    print(f"{Color.BLUE}  /help    Show this help message{Color.RESET}")
    print(f"{Color.BLUE}  /exit    Quit the application{Color.RESET}")


def clean_path(raw: str) -> str:
    cleaned = raw.strip()
    if len(cleaned) >= 2 and cleaned[0] == cleaned[-1] and cleaned[0] in ("'", '"'):
        cleaned = cleaned[1:-1]
    return cleaned.strip()


class ChatSession:
    def __init__(self, app):
        self.app = app
        self.thread_id = "live-session-1"

    @property
    def config(self):
        return {"configurable": {"thread_id": self.thread_id}}

    def _get_prior_state(self) -> dict:
        snapshot = self.app.get_state(self.config)
        return snapshot.values if snapshot and snapshot.values else {}

    def reset(self, silent: bool = False):
        try:
            self.app.checkpointer.delete_thread(self.thread_id)
        except Exception:
            pass
        if not silent:
            print_system("Started a new conversation. Previous memory cleared.")

    def send(self, user_input: str) -> dict:
        prior = self._get_prior_state()
        tool_usage = prior.get("tool_usage")

        # --- tool_usage == "ShowInputSelectionToolNode": this input IS the
        #     image path the bot asked for (Router Rule 1 handles routing;
        #     we just need to make sure image_path is populated). ---
        if tool_usage == "ShowInputSelectionToolNode":
            image_path = clean_path(user_input)
            print_step(f"Received image path: {image_path}")
            print_step("Extracting EXIF metadata...")
            print_step("Validating image (DashScope primary, OpenRouter fallback)...")

            state = WorkflowState(
                current_question=prior.get("rewritten_question") or user_input,
                history=prior.get("history", []),
                image_path=image_path,
                tool_usage=tool_usage,
                is_claim_submitted=prior.get("is_claim_submitted", False),
            )
        else:
            print_step("Classifying and routing your request...")
            state = WorkflowState(
                current_question=user_input,
                history=prior.get("history", []),
                image_path=prior.get("image_path"),
                metadata=prior.get("metadata", {}),
                vlm_result=prior.get("vlm_result"),
                tool_usage=tool_usage,
                ai_generated_suspected=prior.get("ai_generated_suspected", False),
                is_claim_submitted=prior.get("is_claim_submitted", False),
                refund_approved=prior.get("refund_approved", False),
            )

        result = self.app.invoke(state.model_dump(), config=self.config)
        return result

    def debug_state(self):
        print_debug_state(self._get_prior_state())


def run_interactive_chat(app):
    session = ChatSession(app)
    banner()

    while True:
        try:
            user_input = input(f"{Color.BOLD}You:{Color.RESET} ").strip()
        except (EOFError, KeyboardInterrupt):
            print_system("\nSession ended.")
            break

        if not user_input:
            continue

        lowered = user_input.lower()

        if lowered in {"/exit", "exit", "quit"}:
            print_system("Goodbye!")
            break

        if lowered == "/new":
            session.reset()
            continue

        if lowered == "/state":
            session.debug_state()
            continue

        if lowered == "/help":
            print_help()
            continue

        try:
            result = session.send(user_input)
        except Exception as exc:
            print_error(str(exc))
            continue

        print_assistant(result.get("final_response") or "(no response generated)")
        print_ui_hint(result.get("tool_usage"))

        # Auto-clear AFTER the message is shown, per requirement.
        if result.get("conversation_resolved"):
            session.reset(silent=True)
            print_system(
                "(Conversation resolved — memory cleared automatically. "
                "Start a new message for a new request.)"
            )


def main():
    enable_windows_ansi()
    app = build_graph()
    run_interactive_chat(app)


if __name__ == "__main__":
    main()

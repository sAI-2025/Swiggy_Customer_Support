"""Entry point: interactive Swiggy Support chat application.

Commands (type these instead of a message):
    /new      start a brand-new conversation (clears memory for this session)
    /state    print the raw internal graph state (debugging)
    /help     show available commands
    /exit     quit the application

REQUIREMENT #4 — AUTO-CLEAR AFTER REFUND:
After every graph.invoke() call, ChatSession checks result["refund_approved"].
If True, the refund-confirmation message is shown to the customer FIRST,
then this thread's SQLite checkpoint is deleted automatically — so the
very next message starts a completely fresh conversation with no /new
needed. This matches "after successful refund msg generated, then delete
the corresponding thread checkpoint" from the requirement.

STATE-PERSISTENCE (carried over from earlier fix, still required):
ChatSession.send() fetches the prior checkpointed state before every new
turn and explicitly carries forward history/image_path/vlm_result/
is_claim_submitted/ai_generated_suspected — otherwise a bare
WorkflowState(current_question=...) would silently overwrite (erase)
those fields on merge, making the graph "forget" a claim was validated.
"""
import os

os.environ["LANGCHAIN_TRACING_V2"] = "false"
os.environ["LANGSMITH_TRACING"] = "false"

from graph import build_graph
from schema import WorkflowState


# ---------- Terminal styling ----------


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


def print_debug_state(state_values: dict):
    print(f"\n{Color.MAGENTA}{Color.BOLD}--- Internal graph state ---{Color.RESET}")
    keys_to_show = [
        "rewritten_question", "classifier_result", "router_decision",
        "image_path", "iteration_count", "validation_error",
        "is_claim_submitted", "ai_generated_suspected", "refund_approved",
        "metadata", "vlm_result",
    ]
    for key in keys_to_show:
        value = state_values.get(key)
        print(f"{Color.MAGENTA}  {key}:{Color.RESET} {value}")
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


# ---------- Chat session ----------


class ChatSession:
    def __init__(self, app):
        self.app = app
        self.thread_id = "live-session-1"
        self.awaiting_image = False

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
        self.awaiting_image = False
        if not silent:
            print_system("Started a new conversation. Previous memory cleared.")

    def send(self, user_input: str) -> dict:
        prior = self._get_prior_state()

        # --- Branch 1: this input is an image path we asked for ---
        if self.awaiting_image:
            image_path = clean_path(user_input)

            print_step(f"Received image path: {image_path}")
            print_step("Extracting EXIF metadata...")
            print_step("Validating image (DashScope primary, OpenRouter fallback)...")

            state = WorkflowState(
                current_question=prior.get("rewritten_question") or user_input,
                history=prior.get("history", []),
                image_path=image_path,
                is_claim_submitted=prior.get("is_claim_submitted", False),
            )
            result = self.app.invoke(state.model_dump(), config=self.config)
            self.awaiting_image = False
            self._handle_post_response(result)
            return result

        # --- Branch 2: normal new message — preserve prior state ---
        print_step("Classifying and routing your request...")

        state = WorkflowState(
            current_question=user_input,
            history=prior.get("history", []),
            image_path=prior.get("image_path"),
            metadata=prior.get("metadata", {}),
            vlm_result=prior.get("vlm_result"),
            fake_claim_result=prior.get("fake_claim_result"),
            is_claim_submitted=prior.get("is_claim_submitted", False),
            ai_generated_suspected=prior.get("ai_generated_suspected", False),
        )

        result = self.app.invoke(state.model_dump(), config=self.config)

        if result.get("router_decision") == "ShowInputSelectionToolNode":
            self.awaiting_image = True

        self._handle_post_response(result)
        return result

    def _handle_post_response(self, result: dict):
        """Requirement #4: auto-clear checkpoint AFTER showing a refund
        confirmation. The clear happens after this function returns and
        the caller has already printed result['final_response'] — so the
        customer sees the message first, then the thread resets silently
        for the next message."""
        if result.get("refund_approved"):
            self._pending_reset = True
        else:
            self._pending_reset = False

    def apply_pending_reset_if_needed(self):
        if getattr(self, "_pending_reset", False):
            self.reset(silent=True)
            print_system(
                "(Refund confirmed — this conversation has been closed and "
                "memory cleared. Start a new message for a new request.)"
            )
            self._pending_reset = False

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

        # Requirement #4: clear checkpoint AFTER the message is shown.
        session.apply_pending_reset_if_needed()


def main():
    enable_windows_ansi()
    app = build_graph()
    run_interactive_chat(app)


if __name__ == "__main__":
    main()

"""Entry point: interactive customer-support chat application.

A single live conversation with the LangGraph app. Type naturally like a
real customer — the graph internally handles rewriting, classification,
routing, image-evidence requests, EXIF + VLM validation, and retries.

Commands (type these instead of a message):
    /new      start a brand-new conversation (clears memory for this session)
    /state    print the raw internal graph state (debugging)
    /help     show available commands
    /exit     quit the application
"""
import os
import sys

from graph import build_graph
from schema import WorkflowState

# ---------- Terminal styling (safe on Windows Terminal / VS Code / most shells) ----------

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
    """Enable ANSI color codes on older Windows terminals."""
    if os.name == "nt":
        os.system("")


def banner():
    print(f"{Color.CYAN}{Color.BOLD}")
    print("=" * 70)
    print("   SWIGGY-STYLE CUSTOMER SUPPORT ASSISTANT (Prototype)".center(70))
    print("=" * 70)
    print(f"{Color.RESET}{Color.DIM}")
    print("  Type your message naturally, like a real customer.")
    print("  Commands: /new  /state  /help  /exit")
    print(f"{Color.RESET}")


def print_assistant(message: str):
    print(f"\n{Color.GREEN}{Color.BOLD}Assistant:{Color.RESET} {Color.GREEN}{message}{Color.RESET}")


def print_system(message: str):
    print(f"\n{Color.YELLOW}{message}{Color.RESET}")


def print_error(message: str):
    print(f"\n{Color.RED}{Color.BOLD}Error:{Color.RESET} {Color.RED}{message}{Color.RESET}")


def print_debug_state(state_values: dict):
    print(f"\n{Color.MAGENTA}{Color.BOLD}--- Internal graph state ---{Color.RESET}")
    keys_to_show = [
        "rewritten_question",
        "classifier_result",
        "router_decision",
        "image_path",
        "iteration_count",
        "validation_error",
        "metadata",
        "vlm_result",
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


# ---------- Chat session ----------

class ChatSession:
    def __init__(self, app):
        self.app = app
        self.thread_id = "live-session-1"
        self.last_question = None

    @property
    def config(self):
        return {"configurable": {"thread_id": self.thread_id}}

    def is_awaiting_image(self) -> bool:
        try:
            snapshot = self.app.get_state(self.config)
            return snapshot.values.get("router_decision") == "ShowInputSelectionToolNode"
        except Exception:
            return False

    def reset(self):
        """Clear all checkpointed memory for this session and start fresh."""
        try:
            self.app.checkpointer.delete_thread(self.thread_id)
        except Exception:
            pass
        self.last_question = None
        print_system("Started a new conversation. Previous memory cleared.")

    def send(self, user_input: str):
        if self.is_awaiting_image() and self.last_question:
            state = WorkflowState(current_question=self.last_question, image_path=user_input)
        else:
            state = WorkflowState(current_question=user_input)
            self.last_question = user_input

        result = self.app.invoke(state.model_dump(), config=self.config)
        return result

    def debug_state(self):
        snapshot = self.app.get_state(self.config)
        print_debug_state(snapshot.values)


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

        if session.is_awaiting_image():
            print_system("Waiting for an image file path...")

        try:
            result = session.send(user_input)
        except Exception as exc:
            print_error(str(exc))
            continue

        print_assistant(result.get("final_response") or "(no response generated)")


def main():
    enable_windows_ansi()
    app = build_graph()
    run_interactive_chat(app)


if __name__ == "__main__":
    main()

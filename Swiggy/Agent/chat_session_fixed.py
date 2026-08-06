"""Fixed ChatSession class — preserves checkpointed state across turns.

The bug was that every new message invoked the graph with a fresh, empty
state dict, causing the graph to "forget" the image was already validated.
The fix: fetch the prior checkpointed state, copy it, and only update
current_question + append to history. This matches how LangGraph expects
stateful apps to work.
"""
from schema import WorkflowState


class ChatSession:
    def __init__(self, app):
        self.app = app
        self.thread_id = "live-session-1"
        self.awaiting_image = False  # local flag, set explicitly

    @property
    def config(self):
        return {"configurable": {"thread_id": self.thread_id}}

    def reset(self):
        try:
            self.app.checkpointer.delete_thread(self.thread_id)
        except Exception:
            pass
        self.awaiting_image = False
        print("\nStarted a new conversation. Previous memory cleared.\n")

    def send(self, user_input: str) -> dict:
        # --- Branch 1: we just asked for a photo, this input IS the path ---
        if self.awaiting_image:
            image_path = user_input.strip()
            if image_path.startswith('"') and image_path.endswith('"'):
                image_path = image_path[1:-1]

            print(f"  -> Received image path: {image_path}")
            print("  -> Extracting EXIF metadata...")
            print("  -> Sending image to VLM for validation...")

            # Fetch prior state so we don't lose the original claim
            snapshot = self.app.get_state(self.config)
            prior = snapshot.values if snapshot else {}

            state = WorkflowState(
                current_question=prior.get("rewritten_question") or user_input,
                image_path=image_path,
            )
            result = self.app.invoke(state.model_dump(), config=self.config)
            self.awaiting_image = False
            return result

        # --- Branch 2: new message — but MUST preserve prior state! ---
        print("  -> Classifying and routing your request...")

        # Fetch the full checkpointed state (history, vlm_result, etc.)
        snapshot = self.app.get_state(self.config)
        prior = snapshot.values if snapshot else {}

        # Build the new state by copying prior and only updating the new message
        state = WorkflowState(
            current_question=user_input,
            history=prior.get("history", []),  # Preserve conversation history!
            # Do NOT reset image_path, vlm_result, metadata — let checkpoint hold them
        )

        result = self.app.invoke(state.model_dump(), config=self.config)

        # If the graph just asked for a photo, remember the ORIGINAL claim
        # text and flip the flag so the next input is treated as a path.
        if result.get("router_decision") == "ShowInputSelectionToolNode":
            self.awaiting_image = True

        return result

    def debug_state(self):
        snapshot = self.app.get_state(self.config)
        values = snapshot.values if snapshot else {}
        print("\n--- Internal graph state ---")
        for key in [
            "rewritten_question", "classifier_result", "router_decision",
            "image_path", "iteration_count", "validation_error",
            "metadata", "vlm_result",
        ]:
            print(f"  {key}: {values.get(key)}")
        print("----------------------------")
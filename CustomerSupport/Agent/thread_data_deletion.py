from graph import build_graph

app = build_graph()

# Clear one specific conversation's memory
app.checkpointer.delete_thread("live-session-1")

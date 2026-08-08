document.addEventListener("DOMContentLoaded", () => {
  const toggleBtn = document.getElementById("chat-toggle-btn");
  const chatWindow = document.getElementById("chat-window");
  const closeBtn = document.getElementById("chat-close");
  const chatBody = document.getElementById("chat-body");
  const chatForm = document.getElementById("chat-form");
  const chatInput = document.getElementById("chat-input");
  const sendBtn = document.getElementById("chat-send-btn");

  const SEND_URL = "/chat/send/";
  const HISTORY_URL = "/chat/history/";
  const UPLOAD_NODE = "ShowInputSelectionToolNode";
  const ALLOWED_TYPES = new Set(["image/jpeg", "image/png", "image/webp", "image/gif"]);
  const MAX_SIZE = 5 * 1024 * 1024;

  let awaitingUpload = false;

  function getCookie(name) {
    const cookies = `; ${document.cookie}`;
    const parts = cookies.split(`; ${name}=`);
    return parts.length === 2 ? parts.pop().split(";").shift() : null;
  }

  const csrfToken = getCookie("csrftoken");

  function escapeHtml(value) {
    const element = document.createElement("div");
    element.textContent = value || "";
    return element.innerHTML;
  }

  function appendMessage(sender, message = "", time = "", imageUrl = null) {
    const element = document.createElement("div");
    element.className = `chat-msg ${sender}`;

    let html = "";
    if (imageUrl) {
      html += `<img class="chat-msg-img" src="${imageUrl}" alt="Uploaded image" loading="lazy">`;
    }
    if (message) html += escapeHtml(message);
    if (time) html += `<div class="chat-time">${escapeHtml(time)}</div>`;

    element.innerHTML = html;
    chatBody.appendChild(element);
    chatBody.scrollTop = chatBody.scrollHeight;
  }

  function showTyping() {
    hideTyping();
    const element = document.createElement("div");
    element.id = "chat-typing-indicator";
    element.className = "chat-typing";
    element.innerHTML = "<span></span><span></span><span></span>";
    chatBody.appendChild(element);
    chatBody.scrollTop = chatBody.scrollHeight;
  }

  function hideTyping() {
    document.getElementById("chat-typing-indicator")?.remove();
  }

  function setTextInputEnabled(enabled) {
    chatInput.disabled = !enabled;
    sendBtn.disabled = !enabled;
    chatInput.placeholder = enabled ? "Type a message..." : "Please upload an image first...";
  }

  function removeUploadWidgets() {
    chatBody.querySelectorAll(".upload-widget-msg").forEach((element) => element.remove());
  }

  function renderUploadWidget() {
    removeUploadWidgets();

    const wrapper = document.createElement("div");
    wrapper.className = "chat-msg bot upload-widget-msg";
    const inputId = `upload-${Date.now()}`;

    wrapper.innerHTML = `
      <div class="upload-widget">
        <div class="upload-widget-icon">🖼️</div>
        <p class="upload-widget-text">Please upload the original product image</p>
        <label class="upload-widget-choose" for="${inputId}">Choose image</label>
        <input id="${inputId}" class="upload-widget-file" type="file" accept="image/jpeg,image/png,image/webp,image/gif" hidden>
        <div class="upload-widget-preview" style="display:none">
          <img class="upload-widget-thumb" src="" alt="Selected image preview">
          <button type="button" class="upload-widget-send">Upload</button>
          <button type="button" class="upload-widget-cancel">Cancel</button>
        </div>
        <div class="upload-widget-status" role="status"></div>
      </div>
    `;

    chatBody.appendChild(wrapper);
    chatBody.scrollTop = chatBody.scrollHeight;

    const fileInput = wrapper.querySelector(".upload-widget-file");
    const chooseButton = wrapper.querySelector(".upload-widget-choose");
    const preview = wrapper.querySelector(".upload-widget-preview");
    const thumbnail = wrapper.querySelector(".upload-widget-thumb");
    const uploadButton = wrapper.querySelector(".upload-widget-send");
    const cancelButton = wrapper.querySelector(".upload-widget-cancel");
    const status = wrapper.querySelector(".upload-widget-status");

    let selectedFile = null;

    fileInput.addEventListener("change", () => {
      const file = fileInput.files?.[0];
      if (!file) return;

      if (!ALLOWED_TYPES.has(file.type)) {
        status.textContent = "Unsupported format. Use JPG, PNG, WEBP, or GIF.";
        fileInput.value = "";
        return;
      }

      if (file.size > MAX_SIZE) {
        status.textContent = "Image too large. Maximum size is 5 MB.";
        fileInput.value = "";
        return;
      }

      selectedFile = file;
      status.textContent = "";
      const reader = new FileReader();
      reader.onload = (event) => {
        thumbnail.src = event.target.result;
        chooseButton.style.display = "none";
        preview.style.display = "flex";
      };
      reader.readAsDataURL(file);
    });

    cancelButton.addEventListener("click", () => {
      renderUploadWidget();
    });

    uploadButton.addEventListener("click", async () => {
      if (!selectedFile) {
        status.textContent = "Choose an image first.";
        return;
      }

      uploadButton.disabled = true;
      cancelButton.disabled = true;
      status.textContent = "Uploading and validating...";

      const formData = new FormData();
      formData.append("message", "");
      formData.append("image", selectedFile);

      try {
        const response = await fetch(SEND_URL, {
          method: "POST",
          headers: { "X-CSRFToken": csrfToken },
          body: formData,
        });
        const data = await response.json();

        if (!response.ok || data.error) {
          throw new Error(data.error || "Upload failed.");
        }

        wrapper.remove();
        appendMessage("user", "", "", data.user_image);
        if (data.bot_reply) appendMessage("bot", data.bot_reply);
        applyToolUsage(data.tool_usage);
      } catch (error) {
        status.textContent = error.message || "Upload failed. Please try again.";
        status.style.color = "#c0392b";
        uploadButton.disabled = false;
        cancelButton.disabled = false;
      }
    });
  }

  function applyToolUsage(toolUsage) {
    if (toolUsage === UPLOAD_NODE) {
      awaitingUpload = true;
      setTextInputEnabled(false);
      if (!document.querySelector(".upload-widget-msg")) renderUploadWidget();
      return;
    }

    awaitingUpload = false;
    removeUploadWidgets();
    setTextInputEnabled(true);
  }

  async function loadHistory() {
    try {
      const response = await fetch(HISTORY_URL);
      const data = await response.json();
      chatBody.innerHTML = "";

      if (!data.messages?.length) {
        appendMessage("bot", "Hi! How can I help you today? 👋");
      } else {
        data.messages.forEach((message) => {
          appendMessage(message.sender, message.message, message.time, message.image);
        });
      }

      applyToolUsage(data.tool_usage);
    } catch {
      appendMessage("bot", "Hi! How can I help you today? 👋");
    }
  }

  async function sendTextMessage(message) {
    const response = await fetch(SEND_URL, {
      method: "POST",
      headers: {
        "Content-Type": "application/json",
        "X-CSRFToken": csrfToken,
      },
      body: JSON.stringify({ message }),
    });
    const data = await response.json();
    if (!response.ok || data.error) throw new Error(data.error || "Request failed.");
    return data;
  }

  toggleBtn?.addEventListener("click", () => {
    chatWindow.classList.toggle("open");
    if (chatWindow.classList.contains("open")) {
      loadHistory();
      if (!awaitingUpload) chatInput.focus();
    }
  });

  closeBtn?.addEventListener("click", () => {
    chatWindow.classList.remove("open");
  });

  chatForm?.addEventListener("submit", async (event) => {
    event.preventDefault();
    if (awaitingUpload) return;

    const message = chatInput.value.trim();
    if (!message) return;

    appendMessage("user", message);
    chatInput.value = "";
    setTextInputEnabled(false);
    showTyping();

    try {
      const data = await sendTextMessage(message);
      hideTyping();
      if (data.bot_reply) appendMessage("bot", data.bot_reply);
      applyToolUsage(data.tool_usage);
    } catch (error) {
      hideTyping();
      appendMessage("bot", error.message || "Sorry, something went wrong.");
      setTextInputEnabled(true);
    }
  });
});

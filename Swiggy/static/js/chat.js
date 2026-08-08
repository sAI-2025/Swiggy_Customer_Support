document.addEventListener("DOMContentLoaded", () => {
  const toggleBtn = document.getElementById("chat-toggle-btn");
  const chatWindow = document.getElementById("chat-window");
  const closeBtn = document.getElementById("chat-close");
  const newBtn = document.getElementById("chat-new-btn");
  const chatBody = document.getElementById("chat-body");
  const chatForm = document.getElementById("chat-form");
  const chatInput = document.getElementById("chat-input");
  const sendBtn = document.getElementById("chat-send-btn");

  const SEND_URL = "/chat/send/";
  const HISTORY_URL = "/chat/history/";
  const NEW_URL = "/chat/new/";
  const UPLOAD_NODE = "ShowInputSelectionToolNode";
  const ALLOWED_TYPES = new Set(["image/jpeg", "image/png", "image/webp", "image/gif"]);
  const MAX_SIZE = 5 * 1024 * 1024;

  let awaitingUpload = false;
  let historyLoaded = false;

  function getCookie(name) {
    const cookies = `; ${document.cookie}`;
    const parts = cookies.split(`; ${name}=`);
    return parts.length === 2 ? parts.pop().split(";").shift() : null;
  }
  const csrfToken = getCookie("csrftoken");

  function escapeHtml(value) {
    const el = document.createElement("div");
    el.textContent = value || "";
    return el.innerHTML;
  }

  // ---------- Message rendering ----------

  function appendMessage(sender, message = "", time = "", imageUrl = null) {
    const el = document.createElement("div");
    el.className = `chat-msg ${sender}`;

    let html = "";
    if (imageUrl) {
      // Controlled preview only — max-width/max-height + object-fit in
      // CSS prevents portrait/landscape/oversized images from
      // expanding the chat column. Click opens the original full size.
      html += `<a class="chat-img-link" href="${imageUrl}" target="_blank" rel="noopener">
                 <img class="chat-msg-img" src="${imageUrl}" alt="Uploaded photo" loading="lazy">
               </a>`;
    }
    if (message) html += `<div class="chat-msg-text">${escapeHtml(message)}</div>`;
    if (time) html += `<div class="chat-time">${escapeHtml(time)}</div>`;

    el.innerHTML = html;
    chatBody.appendChild(el);
    scrollToLatest();
    return el;
  }

  function appendErrorMessage(text, { retry } = {}) {
    const el = document.createElement("div");
    el.className = "chat-msg bot chat-error";
    el.innerHTML = `
      <div class="chat-msg-text">⚠️ ${escapeHtml(text)}</div>
      ${retry ? `<button type="button" class="chat-retry-btn">Retry</button>` : ""}
    `;
    chatBody.appendChild(el);
    scrollToLatest();
    if (retry) {
      el.querySelector(".chat-retry-btn").addEventListener("click", () => {
        el.remove();
        retry();
      });
    }
  }

  function scrollToLatest() {
    chatBody.scrollTop = chatBody.scrollHeight;
  }

  function showTyping() {
    hideTyping();
    const el = document.createElement("div");
    el.id = "chat-typing-indicator";
    el.className = "chat-typing";
    el.innerHTML = "<span></span><span></span><span></span>";
    chatBody.appendChild(el);
    scrollToLatest();
  }

  function hideTyping() {
    document.getElementById("chat-typing-indicator")?.remove();
  }

  function showWelcome() {
    const el = document.createElement("div");
    el.className = "chat-welcome";
    el.innerHTML = `
      <div class="chat-welcome-icon">🛵</div>
      <p><strong>Hi! I'm Swiggy Support.</strong></p>
      <p>Ask about an order, a refund, a missing item, or paste in what went wrong — I'm here to help.</p>
    `;
    chatBody.appendChild(el);
  }

  // ---------- Composer state ----------

  function setTextInputEnabled(enabled) {
    chatInput.disabled = !enabled;
    sendBtn.disabled = !enabled;
    chatInput.placeholder = enabled ? "Type a message..." : "Please upload an image to continue...";
  }

  function setSendingState(sending) {
    sendBtn.disabled = sending;
    chatInput.disabled = sending || awaitingUpload;
    sendBtn.innerHTML = sending
      ? `<span class="btn-spinner"></span>`
      : "➤";
  }

  function removeUploadWidgets() {
    chatBody.querySelectorAll(".upload-widget-msg").forEach((el) => el.remove());
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

  // ---------- Image upload widget ----------

  function renderUploadWidget() {
    removeUploadWidgets();

    const wrapper = document.createElement("div");
    wrapper.className = "chat-msg bot upload-widget-msg";
    const inputId = `upload-${Date.now()}`;

    wrapper.innerHTML = `
      <div class="upload-widget" data-state="empty">
        <div class="upload-widget-icon">🖼️</div>
        <p class="upload-widget-text">Please upload a clear photo of the issue</p>
        <label class="upload-widget-choose" for="${inputId}">Choose image</label>
        <input id="${inputId}" class="upload-widget-file" type="file"
               accept="image/jpeg,image/png,image/webp,image/gif" hidden>

        <div class="upload-widget-preview" hidden>
          <div class="upload-widget-thumb-frame">
            <img class="upload-widget-thumb" src="" alt="Selected preview">
            <button type="button" class="upload-widget-remove" aria-label="Remove selected image">×</button>
          </div>
          <div class="upload-widget-progress" hidden><div class="upload-widget-progress-bar"></div></div>
          <div class="upload-widget-actions">
            <button type="button" class="upload-widget-send">Upload</button>
            <button type="button" class="upload-widget-cancel">Cancel</button>
          </div>
        </div>
        <div class="upload-widget-status" role="status"></div>
      </div>
    `;

    chatBody.appendChild(wrapper);
    scrollToLatest();

    const widget = wrapper.querySelector(".upload-widget");
    const fileInput = wrapper.querySelector(".upload-widget-file");
    const chooseBtn = wrapper.querySelector(".upload-widget-choose");
    const preview = wrapper.querySelector(".upload-widget-preview");
    const thumb = wrapper.querySelector(".upload-widget-thumb");
    const removeBtn = wrapper.querySelector(".upload-widget-remove");
    const progressWrap = wrapper.querySelector(".upload-widget-progress");
    const progressBar = wrapper.querySelector(".upload-widget-progress-bar");
    const uploadBtn = wrapper.querySelector(".upload-widget-send");
    const cancelBtn = wrapper.querySelector(".upload-widget-cancel");
    const status = wrapper.querySelector(".upload-widget-status");

    let selectedFile = null;

    function resetToEmpty() {
      selectedFile = null;
      fileInput.value = "";
      preview.hidden = true;
      chooseBtn.style.display = "block";
      widget.dataset.state = "empty";
      status.textContent = "";
    }

    fileInput.addEventListener("change", () => {
      const file = fileInput.files?.[0];
      if (!file) return;

      if (!ALLOWED_TYPES.has(file.type)) {
        status.textContent = "Unsupported format. Use JPG, PNG, WEBP, or GIF.";
        status.classList.add("is-error");
        fileInput.value = "";
        return;
      }
      if (file.size > MAX_SIZE) {
        status.textContent = "Image too large. Maximum size is 5 MB.";
        status.classList.add("is-error");
        fileInput.value = "";
        return;
      }

      selectedFile = file;
      status.textContent = "";
      status.classList.remove("is-error");

      const reader = new FileReader();
      reader.onload = (event) => {
        thumb.src = event.target.result;
        chooseBtn.style.display = "none";
        preview.hidden = false;
        widget.dataset.state = "selected";
      };
      reader.readAsDataURL(file);
    });

    removeBtn.addEventListener("click", resetToEmpty);
    cancelBtn.addEventListener("click", resetToEmpty);

    uploadBtn.addEventListener("click", () => {
      if (!selectedFile) {
        status.textContent = "Choose an image first.";
        status.classList.add("is-error");
        return;
      }
      uploadImage(selectedFile, { wrapper, uploadBtn, removeBtn, cancelBtn, status, progressWrap, progressBar });
    });
  }

  function uploadImage(file, ui) {
    const { wrapper, uploadBtn, removeBtn, cancelBtn, status, progressWrap, progressBar } = ui;

    uploadBtn.disabled = true;
    removeBtn.disabled = true;
    cancelBtn.disabled = true;
    status.classList.remove("is-error");
    status.textContent = "Uploading...";
    progressWrap.hidden = false;
    progressBar.style.width = "0%";

    const formData = new FormData();
    formData.append("message", "");
    formData.append("image", file);

    const xhr = new XMLHttpRequest();
    xhr.open("POST", SEND_URL);
    xhr.setRequestHeader("X-CSRFToken", csrfToken);

    xhr.upload.onprogress = (event) => {
      if (!event.lengthComputable) return;
      const pct = Math.round((event.loaded / event.total) * 100);
      progressBar.style.width = `${pct}%`;
      status.textContent = pct < 100 ? `Uploading... ${pct}%` : "Validating image...";
    };

    xhr.onload = () => {
      let data;
      try {
        data = JSON.parse(xhr.responseText);
      } catch {
        data = { error: "Unexpected server response." };
      }

      if (xhr.status < 200 || xhr.status >= 300 || data.error) {
        status.textContent = data.error || "Upload failed. Please try again.";
        status.classList.add("is-error");
        uploadBtn.disabled = false;
        removeBtn.disabled = false;
        cancelBtn.disabled = false;
        progressWrap.hidden = true;
        return;
      }

      wrapper.remove();
      appendMessage("user", "", "", data.user_image);
      if (data.bot_reply) appendMessage("bot", data.bot_reply, nowLabel());
      applyToolUsage(data.tool_usage);
    };

    xhr.onerror = () => {
      status.textContent = "Network error during upload. Please try again.";
      status.classList.add("is-error");
      uploadBtn.disabled = false;
      removeBtn.disabled = false;
      cancelBtn.disabled = false;
      progressWrap.hidden = true;
    };

    xhr.send(formData);
  }

  function nowLabel() {
    return new Date().toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" });
  }

  // ---------- History / bootstrap ----------

  async function loadHistory() {
    if (historyLoaded) return;
    try {
      const response = await fetch(HISTORY_URL);
      const data = await response.json();
      chatBody.innerHTML = "";

      if (!data.messages?.length) {
        showWelcome();
      } else {
        data.messages.forEach((m) => appendMessage(m.sender, m.message, m.time, m.image));
      }

      applyToolUsage(data.tool_usage);
      historyLoaded = true;
    } catch {
      chatBody.innerHTML = "";
      showWelcome();
      appendErrorMessage("Could not load previous messages.", { retry: () => { historyLoaded = false; loadHistory(); } });
    }
  }

  async function sendTextMessage(message) {
    const response = await fetch(SEND_URL, {
      method: "POST",
      headers: { "Content-Type": "application/json", "X-CSRFToken": csrfToken },
      body: JSON.stringify({ message }),
    });
    const data = await response.json();
    if (!response.ok || data.error) throw new Error(data.error || "Request failed.");
    return data;
  }

  // ---------- Event wiring ----------

  toggleBtn?.addEventListener("click", () => {
    chatWindow.classList.toggle("open");
    if (chatWindow.classList.contains("open")) {
      loadHistory();
      if (!awaitingUpload) chatInput.focus();
    }
  });

  closeBtn?.addEventListener("click", () => chatWindow.classList.remove("open"));

  newBtn?.addEventListener("click", async () => {
    try {
      await fetch(NEW_URL, { method: "POST", headers: { "X-CSRFToken": csrfToken } });
    } catch {
      /* best-effort; still reset the visible UI below */
    }
    chatBody.innerHTML = "";
    showWelcome();
    awaitingUpload = false;
    setTextInputEnabled(true);
    chatInput.focus();
  });

  chatForm?.addEventListener("submit", async (event) => {
    event.preventDefault();
    if (awaitingUpload) return;

    const message = chatInput.value.trim();
    if (!message) return;

    document.querySelector(".chat-welcome")?.remove();
    appendMessage("user", message, nowLabel());
    chatInput.value = "";
    setSendingState(true);
    showTyping();

    try {
      const data = await sendTextMessage(message);
      hideTyping();
      setSendingState(false);
      if (data.bot_reply) appendMessage("bot", data.bot_reply, nowLabel());
      applyToolUsage(data.tool_usage);
    } catch (error) {
      hideTyping();
      setSendingState(false);
      setTextInputEnabled(true);
      appendErrorMessage(error.message || "Something went wrong.", {
        retry: () => {
          chatInput.value = message;
          chatForm.requestSubmit();
        },
      });
    }
  });
});

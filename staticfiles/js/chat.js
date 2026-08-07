document.addEventListener("DOMContentLoaded", function () {
  const toggleBtn = document.getElementById("chat-toggle-btn");
  const chatWindow = document.getElementById("chat-window");
  const closeBtn = document.getElementById("chat-close");
  const chatBody = document.getElementById("chat-body");
  const chatForm = document.getElementById("chat-form");
  const chatInput = document.getElementById("chat-input");
  const sendBtn = document.getElementById("chat-send-btn");

  const ALLOWED_TYPES = ["image/jpeg", "image/png", "image/webp", "image/gif"];
  const MAX_SIZE = 5 * 1024 * 1024; // 5MB

  let awaitingUpload = false; // true while the upload widget is open in chat-body

  function getCookie(name) {
    const value = `; ${document.cookie}`;
    const parts = value.split(`; ${name}=`);
    if (parts.length === 2) return parts.pop().split(";").shift();
    return null;
  }
  const csrftoken = getCookie("csrftoken");

  function escapeHtml(str) {
    const d = document.createElement("div");
    d.innerText = str || "";
    return d.innerHTML;
  }

  function appendMessage(sender, message, time, imageUrl) {
    const div = document.createElement("div");
    div.className = "chat-msg " + sender;
    let html = "";
    if (imageUrl) {
      html += `<img class="chat-msg-img" src="${imageUrl}" alt="Sent image">`;
    }
    if (message) {
      html += escapeHtml(message);
    }
    html += `<div class="chat-time">${time || ""}</div>`;
    div.innerHTML = html;
    chatBody.appendChild(div);
    chatBody.scrollTop = chatBody.scrollHeight;
  }

  function showTyping() {
    const div = document.createElement("div");
    div.className = "chat-typing";
    div.id = "chat-typing-indicator";
    div.innerHTML = "<span></span><span></span><span></span>";
    chatBody.appendChild(div);
    chatBody.scrollTop = chatBody.scrollHeight;
  }

  function hideTyping() {
    const el = document.getElementById("chat-typing-indicator");
    if (el) el.remove();
  }

  function lockInput() {
    awaitingUpload = true;
    chatInput.disabled = true;
    sendBtn.disabled = true;
    chatInput.placeholder = "Finish the upload above...";
  }

  function unlockInput() {
    awaitingUpload = false;
    chatInput.disabled = false;
    sendBtn.disabled = false;
    chatInput.placeholder = "Type a message...";
    chatInput.focus();
  }

  // ===== Inline upload widget rendered as a card inside chat-body =====
  function renderUploadWidget() {
    lockInput();

    const uid = "uw-" + Date.now();
    const wrap = document.createElement("div");
    wrap.className = "chat-msg bot upload-widget-msg";
    wrap.innerHTML = `
      <div class="upload-widget">
        <div class="upload-widget-icon">🖼️</div>
        <p class="upload-widget-text">Select an image to upload</p>
        <label class="upload-widget-choose" for="${uid}">Choose Image</label>
        <input type="file" id="${uid}" class="upload-widget-file"
               accept="image/png, image/jpeg, image/webp, image/gif" hidden>
        <div class="upload-widget-preview" style="display:none;">
          <img class="upload-widget-thumb" src="" alt="Preview">
          <button type="button" class="upload-widget-send">Upload</button>
          <button type="button" class="upload-widget-cancel">Cancel</button>
        </div>
        <div class="upload-widget-status"></div>
      </div>
    `;
    chatBody.appendChild(wrap);
    chatBody.scrollTop = chatBody.scrollHeight;

    const fileInput = wrap.querySelector(".upload-widget-file");
    const chooseLabel = wrap.querySelector(".upload-widget-choose");
    const previewBox = wrap.querySelector(".upload-widget-preview");
    const thumb = wrap.querySelector(".upload-widget-thumb");
    const uploadBtn = wrap.querySelector(".upload-widget-send");
    const cancelBtn = wrap.querySelector(".upload-widget-cancel");
    const statusEl = wrap.querySelector(".upload-widget-status");

    let chosenFile = null;

    fileInput.addEventListener("change", function () {
      const file = fileInput.files[0];
      if (!file) return;

      if (!ALLOWED_TYPES.includes(file.type)) {
        statusEl.textContent = "Unsupported format. Use JPG, PNG, WEBP or GIF.";
        fileInput.value = "";
        return;
      }
      if (file.size > MAX_SIZE) {
        statusEl.textContent = "Image too large. Max size is 5MB.";
        fileInput.value = "";
        return;
      }

      statusEl.textContent = "";
      chosenFile = file;
      const reader = new FileReader();
      reader.onload = (e) => {
        thumb.src = e.target.result;
        previewBox.style.display = "flex";
        chooseLabel.style.display = "none";
      };
      reader.readAsDataURL(file);
    });

    cancelBtn.addEventListener("click", function () {
      wrap.remove();
      unlockInput();
    });

    uploadBtn.addEventListener("click", function () {
      if (!chosenFile) return;

      uploadBtn.disabled = true;
      cancelBtn.disabled = true;
      statusEl.style.color = "#666";
      statusEl.textContent = "Uploading...";

      const formData = new FormData();
      formData.append("message", "");
      formData.append("image", chosenFile);

      fetch("/chat/send/", {
        method: "POST",
        headers: { "X-CSRFToken": csrftoken },
        body: formData,
      })
        .then((res) => res.json())
        .then((data) => {
          if (data.error) {
            statusEl.style.color = "#c0392b";
            statusEl.textContent = data.error;
            uploadBtn.disabled = false;
            cancelBtn.disabled = false;
            return;
          }
          wrap.remove();
          appendMessage("user", "", "", data.user_image);
          if (data.bot_reply) appendMessage("bot", data.bot_reply, "");
          unlockInput();
        })
        .catch(() => {
          statusEl.style.color = "#c0392b";
          statusEl.textContent = "Upload failed. Please try again.";
          uploadBtn.disabled = false;
          cancelBtn.disabled = false;
        });
    });
  }

  // Load chat history (GET) — pop-in previous conversation
  function loadHistory() {
    fetch("/chat/history/")
      .then((res) => res.json())
      .then((data) => {
        chatBody.innerHTML = "";
        if (data.messages.length === 0) {
          appendMessage("bot", "Hi! How can I help you today? 👋 Type 'upload' to send an image.", "");
        } else {
          data.messages.forEach((m) =>
            appendMessage(m.sender, m.message, m.time, m.image),
          );
        }
      })
      .catch(() => {
        appendMessage("bot", "Hi! How can I help you today? 👋", "");
      });
  }

  // Toggle open/close
  toggleBtn.addEventListener("click", function () {
    chatWindow.classList.toggle("open");
    if (chatWindow.classList.contains("open")) {
      loadHistory();
      chatInput.focus();
    }
  });

  closeBtn.addEventListener("click", function () {
    chatWindow.classList.remove("open");
  });

  // Send a text message; if it's the "upload" command, pop the widget after the bot reply
  chatForm.addEventListener("submit", function (e) {
    e.preventDefault();
    if (awaitingUpload) return; // block while widget is open

    const msg = chatInput.value.trim();
    if (!msg) return;

    const isUploadCommand = msg.toLowerCase() === "upload";

    appendMessage("user", msg, "");
    chatInput.value = "";
    sendBtn.disabled = true;
    showTyping();

    fetch("/chat/send/", {
      method: "POST",
      headers: {
        "Content-Type": "application/json",
        "X-CSRFToken": csrftoken,
      },
      body: JSON.stringify({ message: msg }),
    })
      .then((res) => res.json())
      .then((data) => {
        hideTyping();
        sendBtn.disabled = false;
        if (data.error) {
          appendMessage("bot", data.error, "");
          return;
        }
        if (data.bot_reply) appendMessage("bot", data.bot_reply, "");

        if (isUploadCommand) {
          renderUploadWidget();
        }
      })
      .catch(() => {
        hideTyping();
        sendBtn.disabled = false;
        appendMessage("bot", "Sorry, something went wrong. Please try again.", "");
      });
  });
});

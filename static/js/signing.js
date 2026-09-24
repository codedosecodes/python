(() => {
  const form = document.querySelector("#signing-form");
  if (!form) return;
  const fields = [...document.querySelectorAll(".signer-field")];
  const requiredFields = fields.filter((field) => field.dataset.required === "true");
  const dialog = document.querySelector("#signature-dialog");
  const canvas = document.querySelector("#signature-canvas");
  const context = canvas.getContext("2d");
  let activeIndex = -1;
  let signatureKind = "signature";
  let signatureMode = "adopt";
  let selectedStyle = "script-one";
  let uploadedImage = "";
  let strokes = [];
  let currentStroke = null;
  let drawing = false;

  const signatureText = document.querySelector("#signature-text");
  const signaturePayload = document.querySelector("#signature-payload");
  const signatureMethod = document.querySelector("#signature-method");
  const initialsText = document.querySelector("#initials-text");
  const initialsPayload = document.querySelector("#initials-payload");

  function resizeCanvas() {
    const rect = canvas.getBoundingClientRect();
    const ratio = window.devicePixelRatio || 1;
    canvas.width = Math.max(1, Math.floor(rect.width * ratio));
    canvas.height = Math.max(1, Math.floor(rect.height * ratio));
    context.setTransform(ratio, 0, 0, ratio, 0, 0);
    context.lineCap = "round";
    context.lineJoin = "round";
    context.lineWidth = 2.2;
    context.strokeStyle = "#14233a";
    redraw();
  }

  function redraw() {
    const ratio = window.devicePixelRatio || 1;
    context.clearRect(0, 0, canvas.width / ratio, canvas.height / ratio);
    strokes.forEach(drawStroke);
  }

  function drawStroke(points) {
    if (!points?.length) return;
    context.beginPath();
    context.moveTo(points[0].x, points[0].y);
    for (let index = 1; index < points.length - 1; index += 1) {
      const point = points[index];
      const next = points[index + 1];
      context.quadraticCurveTo(point.x, point.y, (point.x + next.x) / 2, (point.y + next.y) / 2);
    }
    const last = points[points.length - 1];
    context.lineTo(last.x, last.y);
    context.stroke();
  }

  function pointerPosition(event) {
    const rect = canvas.getBoundingClientRect();
    return { x: event.clientX - rect.left, y: event.clientY - rect.top };
  }

  canvas.addEventListener("pointerdown", (event) => {
    drawing = true;
    currentStroke = [pointerPosition(event)];
    strokes.push(currentStroke);
    canvas.setPointerCapture(event.pointerId);
  });
  canvas.addEventListener("pointermove", (event) => {
    if (!drawing) return;
    currentStroke.push(pointerPosition(event));
    redraw();
  });
  canvas.addEventListener("pointerup", () => { drawing = false; currentStroke = null; });
  canvas.addEventListener("pointercancel", () => { drawing = false; currentStroke = null; });

  function initialsFor(name) {
    return name.trim().split(/\s+/).slice(0, 3).map((part) => part[0]?.toUpperCase() || "").join("");
  }

  function renderStyles() {
    const name = document.querySelector("#adopt-name").value.trim();
    document.querySelectorAll("#signature-styles button").forEach((button) => {
      button.textContent = signatureKind === "initials" ? initialsFor(name) : name;
      button.className = button.dataset.style;
      button.classList.toggle("selected", button.dataset.style === selectedStyle);
    });
  }

  function openSignature(kind) {
    signatureKind = kind;
    document.querySelector("#signature-dialog-title").textContent = kind === "initials" ? "Create your initials" : "Create your signature";
    renderStyles();
    dialog.showModal();
    requestAnimationFrame(resizeCanvas);
  }

  document.querySelectorAll("[data-signature-kind]").forEach((button) => button.addEventListener("click", () => openSignature(button.dataset.signatureKind)));
  document.querySelector("#adopt-name").addEventListener("input", renderStyles);
  document.querySelectorAll("#signature-styles button").forEach((button) => button.addEventListener("click", () => { selectedStyle = button.dataset.style; renderStyles(); }));
  document.querySelectorAll("[data-signature-tab]").forEach((button) => button.addEventListener("click", () => {
    signatureMode = button.dataset.signatureTab;
    document.querySelectorAll("[data-signature-tab]").forEach((tab) => tab.setAttribute("aria-selected", tab === button ? "true" : "false"));
    document.querySelectorAll("[data-signature-panel]").forEach((panel) => { panel.hidden = panel.dataset.signaturePanel !== signatureMode; });
    if (signatureMode === "draw") requestAnimationFrame(resizeCanvas);
  }));
  document.querySelector("#signature-clear").addEventListener("click", () => { strokes = []; redraw(); });
  document.querySelector("#signature-undo").addEventListener("click", () => { strokes.pop(); redraw(); });
  document.querySelector("#signature-upload").addEventListener("change", (event) => {
    const file = event.target.files[0];
    if (!file) return;
    if (!["image/png", "image/jpeg"].includes(file.type) || file.size > 2 * 1024 * 1024) {
      event.target.setCustomValidity("Choose a PNG or JPEG smaller than 2 MB.");
      event.target.reportValidity();
      return;
    }
    const reader = new FileReader();
    reader.onload = () => {
      const image = document.querySelector("#signature-preview");
      image.onload = () => {
        if (image.naturalWidth < 20 || image.naturalHeight < 10 || image.naturalWidth > 6000 || image.naturalHeight > 6000) {
          event.target.setCustomValidity("Image dimensions are not supported.");
          uploadedImage = "";
        } else {
          event.target.setCustomValidity("");
          uploadedImage = reader.result;
          image.hidden = false;
        }
      };
      image.src = reader.result;
    };
    reader.readAsDataURL(file);
  });

  document.querySelector("#signature-adopt").addEventListener("click", () => {
    const name = document.querySelector("#adopt-name").value.trim();
    let payload = "";
    let method = "ADOPTED";
    if (signatureMode === "draw") {
      if (!strokes.length) return;
      payload = canvas.toDataURL("image/png");
      method = "DRAWN";
    } else if (signatureMode === "upload") {
      if (!uploadedImage) return;
      payload = uploadedImage;
      method = "UPLOADED";
    }
    const value = signatureKind === "initials" ? initialsFor(name) : name;
    if (!value) return;
    if (signatureKind === "initials") {
      initialsText.value = value;
      initialsPayload.value = payload;
    } else {
      signatureText.value = value;
      signaturePayload.value = payload;
      signatureMethod.value = method;
    }
    document.querySelectorAll(`[data-signature-kind="${signatureKind}"]`).forEach((button) => {
      button.classList.add("complete");
      button.querySelector("span").textContent = value;
      button.dataset.complete = "true";
    });
    dialog.close();
    updateProgress();
    focusNextIncomplete();
  });

  function isComplete(field) {
    const type = field.dataset.fieldType;
    if (type === "SIGNATURE") return Boolean(signatureText.value);
    if (type === "INITIAL") return Boolean(initialsText.value);
    if (type === "NOTE") return true;
    if (type === "ATTACHMENT") return field.dataset.required !== "true";
    const controls = [...field.querySelectorAll("input,select,textarea")];
    if (["CHECKBOX", "RADIO"].includes(type)) return controls.some((control) => control.checked);
    return controls.some((control) => control.value.trim() !== "" && control.checkValidity());
  }

  function updateProgress() {
    const completed = requiredFields.filter(isComplete).length;
    document.querySelector("#progress-label").textContent = `${completed} of ${requiredFields.length} required fields complete`;
    document.querySelector("#progress-bar").style.width = `${requiredFields.length ? (completed / requiredFields.length) * 100 : 100}%`;
    document.querySelector("#finish-signing").disabled = completed !== requiredFields.length;
    fields.forEach((field) => field.classList.toggle("complete", isComplete(field)));
  }

  function focusField(index) {
    if (!fields.length) return;
    activeIndex = Math.max(0, Math.min(index, fields.length - 1));
    fields.forEach((field, fieldIndex) => field.classList.toggle("active", fieldIndex === activeIndex));
    const field = fields[activeIndex];
    field.scrollIntoView({ behavior: "smooth", block: "center" });
    const control = field.querySelector("button,input,select,textarea");
    control?.focus({ preventScroll: true });
  }

  function focusNextIncomplete() {
    const index = fields.findIndex((field, fieldIndex) => fieldIndex > activeIndex && field.dataset.required === "true" && !isComplete(field));
    if (index >= 0) focusField(index);
    else {
      const first = fields.findIndex((field) => field.dataset.required === "true" && !isComplete(field));
      if (first >= 0) focusField(first);
    }
  }

  form.addEventListener("input", updateProgress);
  form.addEventListener("change", updateProgress);
  form.addEventListener("submit", (event) => {
    updateProgress();
    if (requiredFields.some((field) => !isComplete(field))) {
      event.preventDefault();
      focusNextIncomplete();
    } else document.querySelector("#finish-signing").textContent = "Finishing…";
  });
  document.querySelector("#start-signing").addEventListener("click", () => { document.querySelector(".ceremony-intro").classList.add("started"); focusNextIncomplete(); });
  document.querySelector("#previous-field").addEventListener("click", () => focusField(activeIndex - 1));
  document.querySelector("#next-field").addEventListener("click", () => { updateProgress(); focusNextIncomplete(); });
  document.querySelector("#decline-open").addEventListener("click", () => document.querySelector("#decline-dialog").showModal());
  document.querySelector("#decline-cancel").addEventListener("click", () => document.querySelector("#decline-dialog").close());
  document.querySelectorAll("[data-document-target]").forEach((button) => button.addEventListener("click", () => {
    document.querySelectorAll(".sign-documents iframe").forEach((frame) => { frame.hidden = frame.id !== button.dataset.documentTarget; });
    document.querySelectorAll("[data-document-target]").forEach((tab) => tab.setAttribute("aria-selected", tab === button ? "true" : "false"));
  }));
  let zoom = 1;
  document.querySelector("[data-sign-action=zoom-in]").addEventListener("click", () => { zoom = Math.min(1.75, zoom + .1); document.querySelector(".sign-documents").style.setProperty("--sign-zoom", zoom); document.querySelector("#sign-zoom").textContent = `${Math.round(zoom * 100)}%`; });
  document.querySelector("[data-sign-action=zoom-out]").addEventListener("click", () => { zoom = Math.max(.65, zoom - .1); document.querySelector(".sign-documents").style.setProperty("--sign-zoom", zoom); document.querySelector("#sign-zoom").textContent = `${Math.round(zoom * 100)}%`; });
  document.querySelector("[data-sign-action=previous]").addEventListener("click", () => focusField(activeIndex - 1));
  document.querySelector("[data-sign-action=next]").addEventListener("click", focusNextIncomplete);
  window.addEventListener("resize", () => { if (dialog.open && signatureMode === "draw") resizeCanvas(); });
  renderStyles();
  updateProgress();
})();

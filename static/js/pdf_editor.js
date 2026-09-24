import * as pdfjsLib from "https://cdnjs.cloudflare.com/ajax/libs/pdf.js/4.10.38/pdf.min.mjs";

pdfjsLib.GlobalWorkerOptions.workerSrc =
  "https://cdnjs.cloudflare.com/ajax/libs/pdf.js/4.10.38/pdf.worker.min.mjs";

const root = document.querySelector("#pdf-editor");
if (root) {
  const documents = JSON.parse(document.querySelector("#editor-documents").textContent);
  const initialFields = JSON.parse(document.querySelector("#editor-fields").textContent);
  const pagesNode = document.querySelector("#pdf-pages");
  const thumbnailsNode = document.querySelector("#thumbnail-list");
  const tabsNode = document.querySelector("#document-tabs");
  const workspace = document.querySelector("#pdf-workspace");
  const loading = document.querySelector("#pdf-loading");
  const error = document.querySelector("#pdf-error");
  const propertyForm = document.querySelector("#property-form");
  const propertyEmpty = document.querySelector(".properties-empty");
  const csrfToken = document.querySelector("#create-field-form [name=csrfmiddlewaretoken]").value;
  const state = {
    documentIndex: 0,
    pdf: null,
    scale: 1.15,
    pages: new Map(),
    fields: new Map(initialFields.map((field) => [field.id, field])),
    selectedId: null,
    activeType: "SIGNATURE",
    copiedField: null,
    rendering: false,
  };

  const clamp = (value, minimum, maximum) => Math.min(maximum, Math.max(minimum, value));
  const round = (value) => Math.round(value * 10000000) / 10000000;
  const currentDocument = () => documents[state.documentIndex];
  const fieldApi = (id) => `/envelopes/${root.dataset.envelopeId}/fields/${id}/`;

  async function request(url, options = {}) {
    const response = await fetch(url, {
      ...options,
      headers: { "X-CSRFToken": csrfToken, "Content-Type": "application/json", ...(options.headers || {}) },
    });
    if (!response.ok) throw new Error((await response.text()) || "Request failed");
    return response.json();
  }

  function setSaveState(message, failed = false) {
    const node = document.querySelector("#property-save-state");
    node.textContent = message;
    node.classList.toggle("error", failed);
  }

  function buildDocumentTabs() {
    tabsNode.replaceChildren();
    documents.forEach((documentItem, index) => {
      const button = window.document.createElement("button");
      button.type = "button";
      button.textContent = documentItem.name;
      button.title = documentItem.name;
      button.classList.toggle("active", index === state.documentIndex);
      button.addEventListener("click", () => loadDocument(index));
      tabsNode.append(button);
    });
  }

  async function loadDocument(index) {
    if (state.rendering) return;
    state.rendering = true;
    state.documentIndex = index;
    state.pages.clear();
    state.selectedId = null;
    propertyForm.hidden = true;
    propertyEmpty.hidden = false;
    pagesNode.replaceChildren();
    thumbnailsNode.replaceChildren();
    loading.hidden = false;
    error.hidden = true;
    buildDocumentTabs();
    try {
      state.pdf = await pdfjsLib.getDocument({ url: currentDocument().url, withCredentials: true }).promise;
      document.querySelector("#page-count").textContent = state.pdf.numPages;
      const fragment = document.createDocumentFragment();
      for (let pageNumber = 1; pageNumber <= state.pdf.numPages; pageNumber += 1) {
        fragment.append(createPageShell(pageNumber));
        thumbnailsNode.append(createThumbnailShell(pageNumber));
      }
      pagesNode.append(fragment);
      observePages();
      await renderVisiblePages();
      loading.hidden = true;
    } catch (reason) {
      console.error("PDF rendering failed", reason);
      loading.hidden = true;
      error.hidden = false;
    } finally {
      state.rendering = false;
    }
  }

  function createPageShell(pageNumber) {
    const section = document.createElement("section");
    section.className = "rendered-page pending";
    section.dataset.page = pageNumber;
    section.innerHTML = `<span class="page-label">Page ${pageNumber}</span><canvas></canvas><div class="field-layer" aria-label="Fields on page ${pageNumber}"></div>`;
    const layer = section.querySelector(".field-layer");
    layer.addEventListener("dragover", (event) => event.preventDefault());
    layer.addEventListener("drop", dropPaletteField);
    layer.addEventListener("pointerdown", (event) => {
      if (event.target === layer) selectField(null);
    });
    return section;
  }

  function createThumbnailShell(pageNumber) {
    const button = document.createElement("button");
    button.type = "button";
    button.className = "page-thumbnail";
    button.dataset.page = pageNumber;
    button.innerHTML = `<canvas></canvas><span>${pageNumber}</span>`;
    button.addEventListener("click", () => scrollToPage(pageNumber));
    return button;
  }

  function observePages() {
    const observer = new IntersectionObserver(
      (entries) => {
        entries.forEach((entry) => {
          if (entry.isIntersecting) renderPage(Number(entry.target.dataset.page));
        });
        const visible = entries.filter((entry) => entry.isIntersecting).sort((a, b) => b.intersectionRatio - a.intersectionRatio)[0];
        if (visible) updateCurrentPage(Number(visible.target.dataset.page));
      },
      { root: workspace, rootMargin: "500px 0px", threshold: [0.15, 0.6] },
    );
    document.querySelectorAll(".rendered-page").forEach((page) => observer.observe(page));
  }

  async function renderVisiblePages() {
    await Promise.all([1, 2].filter((number) => number <= state.pdf.numPages).map(renderPage));
  }

  async function renderPage(pageNumber) {
    const existing = state.pages.get(pageNumber);
    if (existing?.scale === state.scale) return;
    const page = await state.pdf.getPage(pageNumber);
    const viewport = page.getViewport({ scale: state.scale });
    const shell = document.querySelector(`.rendered-page[data-page="${pageNumber}"]`);
    const canvas = shell.querySelector("canvas");
    const ratio = window.devicePixelRatio || 1;
    canvas.width = Math.floor(viewport.width * ratio);
    canvas.height = Math.floor(viewport.height * ratio);
    canvas.style.width = `${viewport.width}px`;
    canvas.style.height = `${viewport.height}px`;
    shell.style.width = `${viewport.width}px`;
    shell.style.height = `${viewport.height}px`;
    await page.render({ canvasContext: canvas.getContext("2d"), viewport, transform: ratio === 1 ? null : [ratio, 0, 0, ratio, 0, 0] }).promise;
    shell.classList.remove("pending");
    state.pages.set(pageNumber, { page, viewport, scale: state.scale });
    renderFields(pageNumber);
    renderThumbnail(page, pageNumber);
  }

  async function renderThumbnail(page, pageNumber) {
    const viewport = page.getViewport({ scale: 0.18 });
    const canvas = document.querySelector(`.page-thumbnail[data-page="${pageNumber}"] canvas`);
    canvas.width = viewport.width;
    canvas.height = viewport.height;
    await page.render({ canvasContext: canvas.getContext("2d"), viewport }).promise;
  }

  function renderFields(pageNumber) {
    const layer = document.querySelector(`.rendered-page[data-page="${pageNumber}"] .field-layer`);
    layer.replaceChildren();
    [...state.fields.values()]
      .filter((field) => field.documentId === currentDocument().id && field.page === pageNumber)
      .forEach((field) => layer.append(createFieldElement(field)));
  }

  function createFieldElement(field) {
    const element = document.createElement("div");
    element.className = "document-field";
    element.dataset.fieldId = field.id;
    element.tabIndex = 0;
    element.style.cssText = `left:${field.x * 100}%;top:${field.y * 100}%;width:${field.width * 100}%;height:${field.height * 100}%;--recipient-color:${field.color}`;
    element.innerHTML = `<span class="field-recipient">${escapeHtml(field.recipientName)}</span><span class="field-label">${escapeHtml(field.label || field.type.replaceAll("_", " "))}</span><span class="resize-handle" aria-hidden="true"></span>`;
    element.classList.toggle("selected", field.id === state.selectedId);
    element.addEventListener("pointerdown", startMove);
    element.querySelector(".resize-handle").addEventListener("pointerdown", startResize);
    element.addEventListener("click", (event) => {
      event.stopPropagation();
      selectField(field.id);
    });
    return element;
  }

  function escapeHtml(value) {
    const node = document.createElement("span");
    node.textContent = value;
    return node.innerHTML;
  }

  function dropPaletteField(event) {
    event.preventDefault();
    const fieldType = event.dataTransfer.getData("application/x-signflow-field") || state.activeType;
    const bounds = event.currentTarget.getBoundingClientRect();
    submitNewField({
      documentId: currentDocument().id,
      recipientId: document.querySelector("#recipient-select").value,
      fieldType,
      page: Number(event.currentTarget.closest(".rendered-page").dataset.page),
      x: clamp((event.clientX - bounds.left) / bounds.width, 0, 0.78),
      y: clamp((event.clientY - bounds.top) / bounds.height, 0, 0.94),
    });
  }

  function submitNewField({ documentId, recipientId, fieldType, page, x, y }) {
    const dimensions = ["SIGNATURE", "INITIAL"].includes(fieldType) ? [0.24, 0.065] : fieldType === "CHECKBOX" ? [0.04, 0.04] : [0.2, 0.045];
    const form = document.querySelector("#create-field-form");
    Object.entries({ envelope_document: documentId, recipient: recipientId, field_type: fieldType, page_number: page, x: round(x), y: round(y), width: dimensions[0], height: dimensions[1], label: titleCase(fieldType) }).forEach(([name, value]) => {
      form.elements[name].value = value;
    });
    form.submit();
  }

  function titleCase(value) {
    return value.toLowerCase().replaceAll("_", " ").replace(/\b\w/g, (letter) => letter.toUpperCase());
  }

  function startMove(event) {
    if (event.target.classList.contains("resize-handle")) return;
    event.preventDefault();
    const element = event.currentTarget;
    const field = state.fields.get(element.dataset.fieldId);
    selectField(field.id);
    const layer = element.parentElement;
    const bounds = layer.getBoundingClientRect();
    const start = { x: event.clientX, y: event.clientY, fieldX: field.x, fieldY: field.y };
    const move = (pointer) => {
      field.x = round(clamp(start.fieldX + (pointer.clientX - start.x) / bounds.width, 0, 1 - field.width));
      field.y = round(clamp(start.fieldY + (pointer.clientY - start.y) / bounds.height, 0, 1 - field.height));
      element.style.left = `${field.x * 100}%`;
      element.style.top = `${field.y * 100}%`;
      updateDimensionInputs(field);
    };
    const stop = async () => {
      window.removeEventListener("pointermove", move);
      window.removeEventListener("pointerup", stop);
      await saveField(field, { x: field.x, y: field.y });
    };
    window.addEventListener("pointermove", move);
    window.addEventListener("pointerup", stop, { once: true });
  }

  function startResize(event) {
    event.preventDefault();
    event.stopPropagation();
    const element = event.currentTarget.parentElement;
    const field = state.fields.get(element.dataset.fieldId);
    selectField(field.id);
    const bounds = element.parentElement.getBoundingClientRect();
    const start = { x: event.clientX, y: event.clientY, width: field.width, height: field.height };
    const move = (pointer) => {
      field.width = round(clamp(start.width + (pointer.clientX - start.x) / bounds.width, 0.025, 1 - field.x));
      field.height = round(clamp(start.height + (pointer.clientY - start.y) / bounds.height, 0.02, 1 - field.y));
      element.style.width = `${field.width * 100}%`;
      element.style.height = `${field.height * 100}%`;
      updateDimensionInputs(field);
    };
    const stop = async () => {
      window.removeEventListener("pointermove", move);
      window.removeEventListener("pointerup", stop);
      await saveField(field, { width: field.width, height: field.height });
    };
    window.addEventListener("pointermove", move);
    window.addEventListener("pointerup", stop, { once: true });
  }

  function selectField(id) {
    state.selectedId = id;
    document.querySelectorAll(".document-field").forEach((node) => node.classList.toggle("selected", node.dataset.fieldId === id));
    if (!id) {
      propertyForm.hidden = true;
      propertyEmpty.hidden = false;
      return;
    }
    const field = state.fields.get(id);
    propertyForm.hidden = false;
    propertyEmpty.hidden = true;
    document.querySelector("#property-title").textContent = titleCase(field.type);
    propertyForm.elements.recipient.value = field.recipientId;
    propertyForm.elements.label.value = field.label;
    propertyForm.elements.required.checked = field.required;
    propertyForm.elements.validation_type.value = field.validation?.type || "none";
    propertyForm.elements.max_length.value = field.validation?.maxLength || "";
    propertyForm.elements.options.value = (field.options || []).join("\n");
    propertyForm.querySelector("[data-option-properties]").hidden = !["DROPDOWN", "RADIO"].includes(field.type);
    propertyForm.querySelector("[data-text-properties]").hidden = !["TEXT", "NUMBER", "DATE", "EMAIL"].includes(field.type);
    updateDimensionInputs(field);
  }

  function updateDimensionInputs(field) {
    ["x", "y", "width", "height"].forEach((name) => {
      propertyForm.elements[name].value = `${Math.round(field[name] * 1000) / 10}%`;
    });
  }

  async function saveField(field, changes) {
    setSaveState("Saving…");
    try {
      await request(fieldApi(field.id), { method: "POST", body: JSON.stringify(changes) });
      setSaveState("Saved");
    } catch (reason) {
      console.error("Field update failed", reason);
      setSaveState("Could not save. Refresh to restore the last saved position.", true);
    }
  }

  async function removeSelectedField() {
    if (!state.selectedId) return;
    const field = state.fields.get(state.selectedId);
    await request(fieldApi(field.id), { method: "DELETE", body: "{}" });
    state.fields.delete(field.id);
    renderFields(field.page);
    selectField(null);
  }

  function scrollToPage(pageNumber) {
    document.querySelector(`.rendered-page[data-page="${clamp(pageNumber, 1, state.pdf.numPages)}"]`)?.scrollIntoView({ behavior: "smooth", block: "start" });
  }

  function updateCurrentPage(pageNumber) {
    document.querySelector("#current-page").value = pageNumber;
    document.querySelectorAll(".page-thumbnail").forEach((node) => node.classList.toggle("active", Number(node.dataset.page) === pageNumber));
  }

  async function setScale(scale) {
    state.scale = clamp(scale, 0.5, 2.5);
    state.pages.clear();
    document.querySelector("#zoom-label").textContent = `${Math.round(state.scale * 100)}%`;
    await Promise.all([...document.querySelectorAll(".rendered-page")].map((node) => renderPage(Number(node.dataset.page))));
  }

  document.querySelectorAll("[data-field-type]").forEach((button) => {
    button.addEventListener("dragstart", (event) => {
      state.activeType = button.dataset.fieldType;
      event.dataTransfer.setData("application/x-signflow-field", state.activeType);
      event.dataTransfer.effectAllowed = "copy";
    });
    button.addEventListener("click", () => {
      state.activeType = button.dataset.fieldType;
      document.querySelectorAll("[data-field-type]").forEach((node) => node.classList.toggle("selected", node === button));
    });
  });

  document.querySelector("[data-action=previous-page]").addEventListener("click", () => scrollToPage(Number(document.querySelector("#current-page").value) - 1));
  document.querySelector("[data-action=next-page]").addEventListener("click", () => scrollToPage(Number(document.querySelector("#current-page").value) + 1));
  document.querySelector("#current-page").addEventListener("change", (event) => scrollToPage(Number(event.target.value)));
  document.querySelector("[data-action=zoom-out]").addEventListener("click", () => setScale(state.scale - 0.15));
  document.querySelector("[data-action=zoom-in]").addEventListener("click", () => setScale(state.scale + 0.15));
  document.querySelector("[data-action=fit-width]").addEventListener("click", () => setScale(clamp((workspace.clientWidth - 70) / 612, 0.5, 2.5)));
  document.querySelector("[data-property-action=delete]").addEventListener("click", removeSelectedField);
  propertyForm.addEventListener("change", async () => {
    if (!state.selectedId) return;
    const field = state.fields.get(state.selectedId);
    const recipientOption = propertyForm.elements.recipient.selectedOptions[0];
    Object.assign(field, {
      recipientId: propertyForm.elements.recipient.value,
      recipientName: recipientOption.textContent,
      label: propertyForm.elements.label.value,
      required: propertyForm.elements.required.checked,
      validation: { type: propertyForm.elements.validation_type.value, maxLength: Number(propertyForm.elements.max_length.value) || null },
      options: propertyForm.elements.options.value.split("\n").map((value) => value.trim()).filter(Boolean),
    });
    await saveField(field, { recipient: field.recipientId, label: field.label, required: field.required, validation: field.validation, options: field.options });
    renderFields(field.page);
    selectField(field.id);
  });
  workspace.addEventListener("keydown", async (event) => {
    if (!state.selectedId || ["INPUT", "TEXTAREA", "SELECT"].includes(event.target.tagName)) return;
    const field = state.fields.get(state.selectedId);
    if (["Delete", "Backspace"].includes(event.key)) {
      event.preventDefault();
      await removeSelectedField();
    } else if (event.key === "Escape") selectField(null);
    else if (["ArrowLeft", "ArrowRight", "ArrowUp", "ArrowDown"].includes(event.key)) {
      event.preventDefault();
      const amount = event.shiftKey ? 0.01 : 0.001;
      if (event.key === "ArrowLeft") field.x = clamp(field.x - amount, 0, 1 - field.width);
      if (event.key === "ArrowRight") field.x = clamp(field.x + amount, 0, 1 - field.width);
      if (event.key === "ArrowUp") field.y = clamp(field.y - amount, 0, 1 - field.height);
      if (event.key === "ArrowDown") field.y = clamp(field.y + amount, 0, 1 - field.height);
      renderFields(field.page);
      selectField(field.id);
      await saveField(field, { x: round(field.x), y: round(field.y) });
    }
  });

  buildDocumentTabs();
  loadDocument(0);
}

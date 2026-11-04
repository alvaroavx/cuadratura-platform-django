(() => {
  // Consulta de antecedentes por RUT (ADR 0007).
  // - Creación: «Usar estos datos» copia la vista previa al formulario; guarda el POST normal.
  // - Edición: «Actualizar desde Tupana» compara campos, actividades y direcciones, deja elegir campo a campo y exige
  //   confirmación explícita antes de enviar los cambios seleccionados.
  // Nunca se consulta en cada pulsación: solo al presionar «Consultar datos».

  const MANUAL = " Puedes completar o editar los datos a mano.";
  const MESSAGES = {
    invalid_tax_id: "RUT inválido: revisa el número y el dígito verificador.",
    not_found: "No se encontraron antecedentes públicos para este RUT." + MANUAL,
    already_registered: "Ya existe un contribuyente con este RUT en Cuadratura (puede estar eliminado). No se consultó al proveedor.",
    unavailable: "El servicio de consulta no está disponible en este momento." + MANUAL,
    provider_limited: "El proveedor está limitando las consultas; intenta más tarde." + MANUAL,
    disabled: "La consulta automática no está configurada en este ambiente." + MANUAL,
    rate_limited: "Hiciste muchas consultas seguidas; espera un minuto antes de volver a consultar.",
    error: "No fue posible completar la consulta." + MANUAL,
  };
  const WARNING_STATES = new Set(["not_found", "already_registered"]);

  // ---------- Utilidades comunes ----------

  function compact(value) {
    return String(value || "").replace(/[.\s-]/g, "").toUpperCase();
  }

  function verifierDigit(body) {
    let total = 0;
    let factor = 2;
    for (const digit of body.split("").reverse()) {
      total += Number(digit) * factor;
      factor = factor === 7 ? 2 : factor + 1;
    }
    const remainder = 11 - (total % 11);
    return remainder === 11 ? "0" : remainder === 10 ? "K" : String(remainder);
  }

  function isValidRut(value) {
    const raw = String(value || "").trim();
    if (!/^[0-9.\s-]*[0-9kK]$/.test(raw)) return false;
    const match = compact(raw).match(/^(\d{1,8})([0-9K])$/);
    return Boolean(match) && Number(match[1]) > 0 && verifierDigit(String(Number(match[1]))) === match[2];
  }

  function statusLine(element) {
    return (text, tone = "", link = "") => {
      element.textContent = text;
      element.dataset.tone = tone;
      if (link && link.startsWith("/")) {
        const anchor = document.createElement("a");
        anchor.href = link;
        anchor.textContent = "Abrir la ficha registrada";
        element.append(" ", anchor);
      }
    };
  }

  function provenanceText(data) {
    const date = new Date(data.fetched_at);
    const when = Number.isNaN(date.getTime()) ? "" : date.toLocaleString("es-CL", { dateStyle: "short", timeStyle: "short" });
    return `Fuente: ${data.source}${when ? ` · consultado el ${when}` : ""}. Antecedentes públicos informados por un tercero; no es una validación del SII.`;
  }

  function addressText(a) {
    return [a.street, a.district, a.city].filter(Boolean).join(", ")
      + (a.branch_code ? ` · sucursal SII ${a.branch_code}` : "") + (a.phone ? ` · ${a.phone}` : "");
  }

  function activityText(a) {
    return a.name ? `${a.code} · ${a.name}` : a.code;
  }

  // Lista de casillas para la vista previa de creación.
  function renderChoices(fieldset, items, text, checkedByDefault) {
    const container = fieldset.querySelector("[data-choices]");
    container.replaceChildren(...items.map((item, index) => {
      const label = document.createElement("label");
      label.className = "choice-inline";
      const input = document.createElement("input");
      input.type = "checkbox";
      input.dataset.index = String(index);
      input.checked = checkedByDefault(item);
      const span = document.createElement("span");
      span.textContent = text(item);
      if (item.is_main_in_sii) {
        const badge = document.createElement("span");
        badge.className = "badge badge-info";
        badge.textContent = "Casa matriz según SII";
        span.append(" ", badge);
      }
      label.append(input, span);
      return label;
    }));
    fieldset.hidden = !items.length;
    const count = fieldset.querySelector("[data-count]");
    if (count) count.textContent = `(${items.length})`;
  }

  function chosen(fieldset, items) {
    return [...fieldset.querySelectorAll("input[type=checkbox][data-index]:checked")].map((input) => items[Number(input.dataset.index)]);
  }

  function bindSelectAll(scope) {
    scope.querySelector("[data-select-all]")?.addEventListener("click", () => {
      scope.querySelectorAll("input[type=checkbox]:not(:disabled)").forEach((input) => { input.checked = true; });
    });
    scope.querySelector("[data-select-none]")?.addEventListener("click", () => {
      scope.querySelectorAll("input[type=checkbox]:not(:disabled)").forEach((input) => { input.checked = false; });
    });
  }

  // Ejecuta la consulta y entrega `data` si hubo resultado; si no, informa el estado.
  async function runLookup({ url, body, csrf, button, say }) {
    const label = button.textContent;
    button.disabled = true;
    button.textContent = "Consultando…";
    button.setAttribute("aria-busy", "true");
    say("Consultando información…");
    try {
      const response = await fetch(url, {
        method: "POST",
        body: new URLSearchParams(body),
        credentials: "same-origin",
        headers: { "X-CSRFToken": csrf, Accept: "application/json" },
      });
      const type = response.headers.get("Content-Type") || "";
      const data = type.includes("application/json") ? await response.json() : {};
      if (response.ok && data.status === "found") return data;
      say(data.message || MESSAGES[data.status] || MESSAGES.error, WARNING_STATES.has(data.status) ? "warning" : "error", data.organization_url);
    } catch (_error) {
      say(MESSAGES.error, "error");
    } finally {
      button.textContent = label;
      button.removeAttribute("aria-busy");
      button.disabled = false;
    }
    return null;
  }

  // ---------- Creación ----------

  function initCreate(root) {
    const form = root.closest("form");
    const taxIdInput = document.getElementById(root.dataset.taxIdInput);
    const targets = {
      legal_name: document.getElementById(root.dataset.legalNameInput),
      trade_name: document.getElementById(root.dataset.tradeNameInput),
      public_email: document.getElementById(root.dataset.publicEmailInput),
    };
    const button = root.querySelector("[data-lookup-button]");
    const say = statusLine(root.querySelector("[data-lookup-status]"));
    const preview = root.querySelector("[data-lookup-preview]");
    const previewRut = root.querySelector("[data-lookup-rut]");
    const provenance = root.querySelector("[data-lookup-provenance]");
    const activitiesBox = root.querySelector("[data-preview-activities]");
    const addressesBox = root.querySelector("[data-preview-addresses]");
    const acceptedNote = root.querySelector("[data-lookup-accepted]");
    const sourceInput = root.querySelector("[data-lookup-source]");
    const hidden = {
      reference: root.querySelector("[data-lookup-reference-id]"),
      activities: root.querySelector("[data-lookup-activities]"),
      addresses: root.querySelector("[data-lookup-addresses]"),
    };
    let consulted = { activities: [], addresses: [], reference: "" };
    const previewFields = [...root.querySelectorAll("[data-preview-field]")];
    if (!form || !taxIdInput || !button) return;

    const IDLE = "Ingresa un RUT válido para consultar sus antecedentes públicos. También puedes completar los datos a mano.";
    let consultedRut = null;
    let consultedSource = "";
    let acceptedRut = null;
    let busy = false;

    function hidePreview() {
      preview.hidden = true;
      previewFields.forEach((input) => { input.value = ""; });
      renderChoices(activitiesBox, [], activityText, () => false);
      renderChoices(addressesBox, [], addressText, () => false);
      consultedRut = null;
    }

    function refreshButton() {
      const valid = isValidRut(taxIdInput.value);
      button.disabled = busy || !valid;
      return valid;
    }

    async function consult() {
      if (busy || !refreshButton()) {
        say(MESSAGES.invalid_tax_id, "error");
        return;
      }
      hidePreview();
      busy = true;
      const data = await runLookup({
        url: root.dataset.lookupUrl,
        body: { tax_id: taxIdInput.value },
        csrf: form.querySelector("[name=csrfmiddlewaretoken]")?.value || "",
        button,
        say,
      });
      busy = false;
      refreshButton();
      if (!data) return;
      consultedRut = compact(data.tax_id);
      consultedSource = data.source || "";
      previewRut.textContent = data.tax_id;
      previewFields.forEach((input) => { input.value = (data.fields || {})[input.dataset.previewField] || ""; });
      provenance.textContent = provenanceText(data);
      consulted = { activities: data.activities || [], addresses: data.addresses || [], reference: data.provider_reference || "" };
      const few = consulted.addresses.length <= 10;
      renderChoices(activitiesBox, consulted.activities, activityText, () => true);
      renderChoices(addressesBox, consulted.addresses, addressText, (a) => few || a.is_main_in_sii);
      addressesBox.querySelector("[data-truncated]").hidden = !data.addresses_truncated;
      preview.hidden = false;
      say("Información encontrada. Revísala antes de usarla.", "success");
      preview.focus();
    }

    function accept() {
      if (!consultedRut || compact(taxIdInput.value) !== consultedRut) {
        hidePreview();
        say("El RUT cambió después de la consulta; vuelve a consultar.", "warning");
        return;
      }
      taxIdInput.value = previewRut.textContent;
      previewFields.forEach((input) => {
        const target = targets[input.dataset.previewField];
        const value = input.value.trim();
        // Un valor vacío nunca borra lo que la persona ya escribió.
        if (target && value) target.value = value;
      });
      sourceInput.value = consultedSource;
      const activities = chosen(activitiesBox, consulted.activities);
      const addresses = chosen(addressesBox, consulted.addresses);
      hidden.reference.value = consulted.reference;
      hidden.activities.value = JSON.stringify(activities);
      hidden.addresses.value = JSON.stringify(addresses);
      acceptedNote.textContent = `Al guardar también se registrarán ${activities.length} actividades y ${addresses.length} direcciones aceptadas (fuente: ${consultedSource}).`;
      acceptedNote.hidden = false;
      acceptedRut = consultedRut;
      hidePreview();
      say("Datos incorporados al formulario. Revísalos y presiona Guardar para crear el contribuyente.", "success");
      targets.legal_name?.focus();
    }

    function discard() {
      hidePreview();
      say("Consulta descartada. El formulario no cambió.");
      button.focus();
    }

    taxIdInput.addEventListener("input", () => {
      const valid = refreshButton();
      if (sourceInput.value && compact(taxIdInput.value) !== acceptedRut) {
        // Otro RUT: lo aceptado deja de corresponder y no se envía.
        sourceInput.value = "";
        Object.values(hidden).forEach((input) => { input.value = ""; });
        acceptedNote.hidden = true;
      }
      if (!preview.hidden && compact(taxIdInput.value) !== consultedRut) {
        hidePreview();
        say("El RUT cambió; la consulta anterior se descartó.", "warning");
        return;
      }
      if (preview.hidden) {
        if (valid) say("RUT válido. Puedes consultar sus datos.");
        else if (compact(taxIdInput.value).length >= 8) say(MESSAGES.invalid_tax_id, "error");
        else say(IDLE);
      }
    });
    // Enter dentro de la vista previa no debe enviar el formulario principal.
    previewFields.forEach((input) => input.addEventListener("keydown", (event) => {
      if (event.key === "Enter") event.preventDefault();
    }));
    bindSelectAll(addressesBox);
    button.addEventListener("click", consult);
    root.querySelector("[data-lookup-accept]").addEventListener("click", accept);
    root.querySelector("[data-lookup-discard]").addEventListener("click", discard);
    say(IDLE);
    refreshButton();
  }

  // ---------- Edición: «Actualizar datos desde RUT» ----------

  function initUpdate(root) {
    const button = root.querySelector("[data-lookup-button]");
    const say = statusLine(root.querySelector("[data-lookup-status]"));
    const form = root.querySelector("[data-update-form]");
    const mainForm = document.getElementById(root.dataset.mainForm);
    const body = form.querySelector("[data-diff-body]");
    const noDifferences = form.querySelector("[data-no-differences]");
    const reviewActions = form.querySelector("[data-review-actions]");
    const reviewButton = form.querySelector("[data-review]");
    const confirmStep = form.querySelector("[data-confirm-step]");
    const confirmSummary = form.querySelector("[data-confirm-summary]");
    const confirmInput = form.querySelector("[data-confirm]");
    const sections = {
      activities: form.querySelector('[data-collection="activities"]'),
      addresses: form.querySelector('[data-collection="addresses"]'),
    };
    const STATE_LABELS = {
      new: "Nuevo en la fuente",
      changed: "Diferente",
      same: "Sin cambios",
      not_reported: "No informado por la fuente; se conserva",
    };
    let rows = [];
    let collections = { activities: [], addresses: [] };

    function mainFormDirty() {
      if (!mainForm) return false;
      return [...mainForm.elements].some((element) => {
        if (element.type === "hidden" || !element.name) return false;
        if (element.type === "checkbox" || element.type === "radio") return element.checked !== element.defaultChecked;
        return element.value !== element.defaultValue;
      });
    }

    function reset() {
      form.hidden = true;
      body.replaceChildren();
      confirmStep.hidden = true;
      reviewActions.hidden = false;
      confirmInput.value = "";
      rows = [];
      collections = { activities: [], addresses: [] };
      Object.values(sections).forEach((section) => {
        section.hidden = true;
        section.querySelector("[data-collection-body]").replaceChildren();
      });
    }

    function renderCollection(kind, items, label) {
      const section = sections[kind];
      const few = items.filter((item) => item.state === "new").length <= 10;
      section.querySelector("[data-collection-body]").replaceChildren(...items.map((item, index) => {
        const tr = document.createElement("tr");
        const th = document.createElement("th");
        th.scope = "row";
        th.textContent = kind === "activities" ? item.code : (item.street || "");
        const currentText = kind === "activities" ? item.current_name : item.current;
        const consultedText = item.state === "not_reported" ? "" : (kind === "activities" ? item.name : addressText(item));
        const consulted = cell(consultedText, "No informado");
        if (item.state === "new" || item.state === "changed") consulted.classList.add("is-different");
        const state = document.createElement("td");
        state.textContent = STATE_LABELS[item.state] + (item.is_main_in_sii ? " · casa matriz según SII" : "");
        const apply = document.createElement("td");
        if (item.state === "new" || item.state === "changed") {
          const choice = document.createElement("label");
          const input = document.createElement("input");
          input.type = "checkbox";
          input.dataset.kind = kind;
          input.dataset.index = String(index);
          input.checked = kind === "activities" || item.state === "changed" || few || item.is_main_in_sii;
          choice.append(input, " Sí");
          apply.append(choice);
        } else {
          apply.textContent = "—";
        }
        tr.append(th, cell(currentText, "Sin registro"), consulted, state, apply);
        return tr;
      }));
      section.hidden = !items.length;
      return items.some((item) => item.state === "new" || item.state === "changed");
    }

    function selectedItems(kind) {
      return [...sections[kind].querySelectorAll(`input[type=checkbox][data-kind="${kind}"]:checked`)]
        .map((input) => collections[kind][Number(input.dataset.index)]);
    }

    function cell(text, empty = "Sin información") {
      const td = document.createElement("td");
      if (text) td.textContent = text;
      else {
        const muted = document.createElement("span");
        muted.className = "help-text";
        muted.textContent = empty;
        td.append(muted);
      }
      return td;
    }

    function actionCell(row) {
      const td = document.createElement("td");
      const group = document.createElement("fieldset");
      group.className = "lookup-action";
      const legend = document.createElement("legend");
      legend.className = "visually-hidden";
      legend.textContent = `Acción para ${row.label}`;
      group.append(legend);
      const options = row.changed ? [["update", "Sí"], ["keep", "No, mantener"]] : [["keep", "No, mantener"]];
      options.forEach(([value, text]) => {
        const label = document.createElement("label");
        const input = document.createElement("input");
        input.type = "radio";
        input.name = `action_${row.field}`;
        input.value = value;
        input.checked = value === row.suggested_action;
        label.append(input, ` ${text}`);
        group.append(label);
      });
      if (row.changed) {
        const hidden = document.createElement("input");
        hidden.type = "hidden";
        hidden.name = `value_${row.field}`;
        hidden.value = row.consulted;
        group.append(hidden);
      }
      td.append(group);
      return td;
    }

    function render(data) {
      rows = data.comparison || [];
      form.querySelector("[data-version]").value = data.version || "";
      form.querySelector("[data-source]").value = data.source || "";
      form.querySelector("[data-fetched-at]").value = data.fetched_at || "";
      form.querySelector("[data-lookup-provenance]").textContent = provenanceText(data);
      form.querySelector("[data-provider-reference]").value = data.provider_reference || "";
      collections = data.collections || { activities: [], addresses: [] };
      // Se envía lo consultado tal como llegó (sin «no informados»); el servidor recompara y valida.
      form.querySelector("[data-consulted-activities]").value = JSON.stringify(data.activities || []);
      form.querySelector("[data-consulted-addresses]").value = JSON.stringify(data.addresses || []);
      sections.addresses.querySelector("[data-truncated]").hidden = !data.addresses_truncated;
      const collectionChanges = [renderCollection("activities", collections.activities), renderCollection("addresses", collections.addresses)].some(Boolean);
      body.replaceChildren(...rows.map((row) => {
        const tr = document.createElement("tr");
        const th = document.createElement("th");
        th.scope = "row";
        th.textContent = row.label;
        const consulted = cell(row.consulted);
        if (row.changed) consulted.classList.add("is-different");
        tr.append(th, cell(row.current, "Vacío"), consulted, actionCell(row));
        return tr;
      }));
      const anyChange = rows.some((row) => row.changed) || collectionChanges;
      noDifferences.hidden = anyChange;
      reviewButton.hidden = !anyChange;
      form.hidden = false;
      say(anyChange ? "Información diferente de la registrada. Elige qué cambios aplicar." : "Sin diferencias con los datos registrados.", anyChange ? "warning" : "success");
      form.focus();
    }

    async function consult() {
      reset();
      const data = await runLookup({
        url: root.dataset.lookupUrl,
        body: {},
        csrf: form.querySelector("[name=csrfmiddlewaretoken]")?.value || "",
        button,
        say,
      });
      if (data) render(data);
    }

    function selected() {
      return rows.filter((row) => form.querySelector(`[name="action_${row.field}"]:checked`)?.value === "update");
    }

    function review() {
      const chosen = selected();
      const activities = selectedItems("activities");
      const addresses = selectedItems("addresses");
      if (!chosen.length && !activities.length && !addresses.length) {
        say("No seleccionaste cambios. Los datos registrados se mantienen.", "warning");
        return;
      }
      if (mainFormDirty()) {
        say("Tienes cambios sin guardar en el formulario de arriba. Guárdalos o recarga la página antes de aplicar datos consultados.", "error");
        return;
      }
      const kept = rows.filter((row) => !chosen.includes(row)).map((row) => row.label);
      const parts = chosen.map((row) => `${row.label} → «${row.consulted}»`);
      if (activities.length) parts.push(`${activities.length} actividad(es): ${activities.map((a) => a.code).join(", ")}`);
      if (addresses.length) parts.push(`${addresses.length} dirección(es)`);
      form.querySelector("[data-selected-activities]").value = JSON.stringify(activities.map((a) => a.code));
      form.querySelector("[data-selected-addresses]").value = JSON.stringify(addresses.map((a) => a.key));
      confirmSummary.textContent = `Se aplicará: ${parts.join("; ")}.`
        + (kept.length ? ` Se mantiene: ${kept.join(", ")}.` : "") + " Lo no seleccionado y lo no informado se conserva; nada se elimina. El RUT no cambia.";
      reviewActions.hidden = true;
      confirmStep.hidden = false;
      form.querySelector("[data-confirm-submit]").focus();
    }

    form.addEventListener("submit", (event) => {
      if (confirmStep.hidden) {
        event.preventDefault();
        return;
      }
      confirmInput.value = "yes";
    });
    form.addEventListener("change", () => {
      if (!confirmStep.hidden) {
        confirmStep.hidden = true;
        reviewActions.hidden = false;
      }
    });
    form.querySelector("[data-confirm-back]").addEventListener("click", () => {
      confirmStep.hidden = true;
      reviewActions.hidden = false;
      reviewButton.focus();
    });
    form.querySelector("[data-lookup-discard]").addEventListener("click", () => {
      reset();
      say("Consulta descartada. No se cambió ningún dato.");
      button.focus();
    });
    bindSelectAll(sections.addresses);
    reviewButton.addEventListener("click", review);
    button.addEventListener("click", consult);
  }

  document.querySelectorAll("[data-rut-lookup]").forEach(initCreate);
  document.querySelectorAll("[data-rut-update]").forEach(initUpdate);
})();

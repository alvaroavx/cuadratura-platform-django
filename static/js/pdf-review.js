const workspace = document.querySelector('[data-pdf-workspace]');

if (workspace) {
  const status = workspace.querySelector('[data-pdf-request-status]');
  let pdfUrl;

  function showPreview(stage, file) {
    const preview = stage.querySelector('.pdf-review-preview');
    if (!preview || !file) return;
    if (pdfUrl) URL.revokeObjectURL(pdfUrl);
    pdfUrl = URL.createObjectURL(file.slice(0, file.size, 'application/pdf'));
    preview.querySelector('[data-pdf-open]').href = pdfUrl;
    preview.querySelector('[data-pdf-frame]').src = pdfUrl;
    preview.hidden = false;
    stage.querySelector('[data-pdf-review]').classList.add('has-pdf-preview');
  }

  function focusResult(region) {
    const target = region.querySelector('[role="alert"], h1');
    if (!target) return;
    target.tabIndex = -1;
    target.focus();
  }

  workspace.addEventListener('submit', async (event) => {
    const form = event.target;
    if (!form.matches('[data-pdf-submit]')) return;
    event.preventDefault();
    if (form.getAttribute('aria-busy') === 'true') return;

    const data = new FormData(form);
    const saving = data.get('action') === 'save';
    const submit = form.querySelector('button[type="submit"]');
    submit.disabled = true;
    form.setAttribute('aria-busy', 'true');
    status.textContent = saving ? 'Guardando…' : 'Leyendo PDF…';
    status.hidden = false;

    try {
      const response = await fetch(form.getAttribute('action') || window.location.href, {
        method: 'POST',
        body: data,
        headers: { 'X-PDF-Review': '1' },
      });
      if (response.redirected) {
        window.location.assign(response.url);
        return;
      }
      if (response.status === 403) {
        status.textContent = 'La sesión o el período ya no permiten continuar. Abre el panel de nuevo para revisar su estado.';
        return;
      }
      if (!response.ok) throw new Error('Error al procesar el documento');
      if (response.headers.get('Content-Type')?.includes('application/json')) {
        const result = await response.json();
        window.location.assign(result.redirect);
        return;
      }

      const page = new DOMParser().parseFromString(await response.text(), 'text/html');
      const stage = page.querySelector('[data-pdf-stage]');
      if (!stage) throw new Error('No llegó el formulario de revisión');
      if (saving) {
        const reviewedForm = stage.querySelector('.pdf-review-form');
        if (!reviewedForm) throw new Error('No llegó el formulario de revisión');
        form.replaceWith(reviewedForm);
        focusResult(reviewedForm);
      } else {
        workspace.querySelector('[data-pdf-stage]').replaceWith(stage);
        showPreview(stage, data.get('pdf'));
        focusResult(stage);
      }
      status.hidden = true;
    } catch {
      status.textContent = 'No se pudo completar la solicitud. Puedes intentarlo de nuevo.';
    } finally {
      submit.disabled = false;
      form.removeAttribute('aria-busy');
    }
  });
}

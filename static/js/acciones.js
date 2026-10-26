// SEG-09: acciones de las pantallas sin código en línea (onclick/onchange), para una CSP sin 'unsafe-inline'.
// Lo cargan base.html, el portal de proveedores y las páginas de impresión.
(function () {
  if (window.ERP_ACCIONES) return;
  window.ERP_ACCIONES = true;
  const interactivo = 'a, button, input, select, textarea, label, summary, [data-sin-propagar]';

  document.addEventListener('click', (e) => {
    const t = e.target;
    if (!(t instanceof Element)) return;
    const imprimir = t.closest('[data-imprimir]');
    if (imprimir) { e.preventDefault(); window.print(); return; }
    if (t.closest('[data-volver]')) { e.preventDefault(); window.history.back(); return; }
    // enlaces o casillas con confirmación propia (los botones ya la tienen en app.js)
    const conf = t.closest('a[data-confirm], input[type=submit][data-confirm], input[type=checkbox][data-confirm]');
    if (conf && !confirm(conf.dataset.confirm)) { e.preventDefault(); e.stopImmediatePropagation(); return; }
    const marcar = t.closest('[data-marcar-todos]');
    if (marcar) {
      const ambito = marcar.hasAttribute('data-en-tabla') ? (marcar.closest('table') || document) : document;
      ambito.querySelectorAll(marcar.dataset.marcarTodos).forEach((c) => { c.checked = marcar.checked; });
      return;
    }
    const fijar = t.closest('[data-fijar-campo]');
    if (fijar && fijar.form && fijar.form.elements[fijar.dataset.fijarCampo]) {
      fijar.form.elements[fijar.dataset.fijarCampo].value = fijar.dataset.fijarValor || '';
    }
    const ir = t.closest('button[data-ir-con]');
    if (ir) { e.preventDefault(); irCon(ir); return; }
    // filas de detalle: agregar desde un <template> (__N__ = siguiente índice) y quitar
    const agregar = t.closest('[data-agregar-fila]');
    if (agregar) {
      const plantilla = document.querySelector(agregar.dataset.agregarFila);
      const destino = document.querySelector(agregar.dataset.destino);
      if (plantilla && destino) {
        const n = parseInt(destino.dataset.siguiente || '0', 10);
        destino.dataset.siguiente = String(n + 1);
        destino.insertAdjacentHTML('beforeend', plantilla.innerHTML.replace(/__N__/g, String(n)));
      }
      return;
    }
    const quitar = t.closest('[data-quitar-fila]');
    if (quitar) { quitar.closest('tr').remove(); return; }
    // filas que abren su documento: no si se hizo clic en un control de la fila
    const fila = t.closest('[data-href]');
    if (fila && !(t.closest(interactivo) && t.closest(interactivo) !== fila)) {
      if (e.ctrlKey || e.metaKey) window.open(fila.dataset.href, '_blank');
      else window.location.href = fila.dataset.href;
    }
  });

  document.addEventListener('change', (e) => {
    const t = e.target;
    if (!(t instanceof Element)) return;
    if (t.matches('[data-autoenvio]') && t.form) { t.form.submit(); return; }
    if (t.matches('select[data-ir-con]')) { irCon(t); return; }
    if (t.matches('[data-alterna]')) {
      document.querySelectorAll(t.dataset.alterna).forEach((x) => x.classList.toggle('d-none', !t.checked));
    }
  });

  // va a la misma pantalla con los valores de los campos indicados del formulario (?cuenta=…&hasta=…)
  function irCon(el) {
    const form = el.form || el.closest('form');
    const params = new URLSearchParams();
    el.dataset.irCon.split(',').forEach((n) => {
      const campo = form && form.elements[n.trim()];
      if (campo && campo.value !== '') params.set(n.trim(), campo.value);
    });
    window.location.search = params.toString();
  }
})();

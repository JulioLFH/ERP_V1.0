// MAN-22: tabla de drivers de la declaración de producción (horas máquina, preparación, horas hombre por persona o
// rol y los drivers del puesto). La propuesta sigue a la cantidad declarada hasta que el usuario cambia la fila.
(function () {
  const tabla = document.querySelector('table.js-cargos');
  if (!tabla) return;
  const form = tabla.closest('form');
  const cuerpo = tabla.tBodies[0];
  const plantilla = document.getElementById('fila-cargo');
  const cantidad = form && form.elements.cantidad;
  const num = (v) => { const n = parseFloat(String(v || '').replace(/,/g, '')); return Number.isFinite(n) ? n : null; };
  const dinero = (n) => n.toLocaleString('en-US', { minimumFractionDigits: 2, maximumFractionDigits: 2 });

  function sincronizar(tr) {
    const hora = tr.querySelector('.js-hora').value;
    const tipo = tr.querySelector('.js-tipo');
    [...tipo.options].forEach((o) => { o.hidden = o.dataset.hora !== hora; o.disabled = o.hidden; });
    if (tipo.selectedOptions[0] && tipo.selectedOptions[0].hidden) {
      const primero = [...tipo.options].find((o) => !o.hidden);
      if (primero) primero.selected = true;
    }
    const op = tipo.selectedOptions[0];
    const esPersona = tipo.value === 'MO';
    tr.querySelector('.js-persona').classList.toggle('d-none', !esPersona);
    tr.querySelector('.js-unidad').textContent = op ? op.dataset.unidad : '';
    const tarifa = tr.querySelector('.js-tarifa');
    const conTrabajador = esPersona && tr.querySelector('.js-trabajador').value;
    if (op && op.dataset.tarifa !== undefined) {
      tarifa.placeholder = conTrabajador ? 'del trabajador' : dinero(num(op.dataset.tarifa) || 0);
    }
    const importe = tr.querySelector('.js-importe');
    if (importe) {
      const t = num(tarifa.value) !== null ? num(tarifa.value) : (conTrabajador || !op ? null : num(op.dataset.tarifa));
      const q = num(tr.querySelector('.js-cantidad').value);
      importe.textContent = t !== null && q !== null ? dinero(q * t) : '—';
    }
  }

  function proponer() {
    const q = num(cantidad && cantidad.value);
    if (q === null) return;
    cuerpo.querySelectorAll('tr.js-fila-cargo').forEach((tr) => {
      const pu = num(tr.dataset.porUnidad);
      if (pu === null || tr.dataset.editado) return;
      tr.querySelector('.js-cantidad').value = (q * pu).toFixed(2);
      sincronizar(tr);
    });
  }

  cuerpo.querySelectorAll('tr.js-fila-cargo').forEach(sincronizar);
  if (cantidad) cantidad.addEventListener('input', proponer);
  tabla.addEventListener('change', (e) => {
    const tr = e.target.closest('tr.js-fila-cargo');
    if (!tr) return;
    if (e.target.matches('.js-hora, .js-tipo')) tr.dataset.editado = '1';
    sincronizar(tr);
  });
  tabla.addEventListener('input', (e) => {
    const tr = e.target.closest('tr.js-fila-cargo');
    if (!tr) return;
    if (e.target.matches('.js-cantidad')) tr.dataset.editado = '1';
    sincronizar(tr);
  });
  tabla.addEventListener('click', (e) => {
    const b = e.target.closest('.js-quitar-cargo');
    if (b) b.closest('tr').remove();
  });
  const agregar = form && form.querySelector('.js-agregar-cargo');
  if (agregar && plantilla) {
    agregar.addEventListener('click', () => {
      const n = parseInt(tabla.dataset.siguiente || '0', 10);
      tabla.dataset.siguiente = String(n + 1);
      const caja = document.createElement('tbody');
      caja.innerHTML = plantilla.innerHTML.replace(/__N__/g, String(n));
      const tr = caja.querySelector('tr');
      tr.dataset.editado = '1';
      cuerpo.appendChild(tr);
      sincronizar(tr);
      tr.querySelector('.js-cantidad').focus();
    });
  }
})();

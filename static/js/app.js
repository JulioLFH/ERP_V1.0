// ERP V1.0 — detalle de ítems (agregar filas, autocompletar producto y totales en vivo)
(function () {
  const tabla = document.getElementById('items-body');
  if (!tabla) return;

  const prefix = tabla.dataset.prefix;
  const totalForms = document.getElementById(`id_${prefix}-TOTAL_FORMS`);
  const plantilla = document.getElementById('item-template');
  const productos = JSON.parse(document.getElementById('productos-data').textContent);
  const precioCampo = tabla.dataset.precioCampo;
  const igvTasa = parseFloat(tabla.dataset.igv) || 0;
  const fmt = (n) => n.toLocaleString('en-US', { minimumFractionDigits: 2, maximumFractionDigits: 2 });
  const num = (el) => parseFloat(el && el.value) || 0;

  function recalcular() {
    let subtotal = 0;
    tabla.querySelectorAll('tr.item-row').forEach((tr) => {
      const borrar = tr.querySelector('input[name$="-DELETE"]');
      const st = num(tr.querySelector('.js-cantidad')) * num(tr.querySelector('.js-precio'));
      tr.querySelector('.js-subtotal').textContent = fmt(st);
      tr.classList.toggle('opacity-50', !!(borrar && borrar.checked));
      if (!(borrar && borrar.checked)) subtotal += st;
    });
    const op = document.getElementById('id_tipo_operacion');
    const tipo = document.getElementById('id_tipo_comprobante');
    const gravada = (!op || op.value === 'GRAVADA') && !(tipo && ['02', '00'].includes(tipo.value));
    const gratuita = op && op.value === 'GRATUITA';
    const icbper = num(document.getElementById('id_icbper'));
    const base = gratuita ? 0 : subtotal;
    const igv = gravada && !gratuita ? Math.round(base * igvTasa) / 100 : 0;
    const total = base + igv + icbper;
    document.getElementById('t-base').textContent = fmt(gravada ? base : 0);
    document.getElementById('t-nograv').textContent = fmt(gravada ? 0 : base);
    document.getElementById('t-igv').textContent = fmt(igv);
    document.getElementById('t-total').textContent = fmt(total);
    const ret = document.getElementById('t-extra');
    if (ret) {
      const d = num(document.getElementById('id_detraccion_pct'));
      const r = num(document.getElementById('id_retencion_pct'));
      const p = num(document.getElementById('id_percepcion_pct'));
      const partes = [];
      if (d) partes.push(`Detracción ${d}%: ${fmt(total * d / 100)}`);
      if (r) partes.push(`Retención ${r}%: ${fmt(total * r / 100)}`);
      if (p) partes.push(`Percepción ${p}%: ${fmt(total * p / 100)}`);
      ret.textContent = partes.join(' · ');
    }
  }

  function enlazar(tr) {
    const sel = tr.querySelector('.js-producto');
    if (sel) {
      sel.addEventListener('change', () => {
        const p = productos.find((x) => String(x.id) === sel.value);
        if (!p) return;
        tr.querySelector('.js-descripcion').value = p.nombre;
        const precio = tr.querySelector('.js-precio');
        precio.value = parseFloat(p[precioCampo] || 0).toFixed(2);
        const cant = tr.querySelector('.js-cantidad');
        if (!num(cant)) cant.value = 1;
        recalcular();
      });
    }
    tr.querySelectorAll('input').forEach((i) => i.addEventListener('input', recalcular));
    tr.querySelectorAll('input[type=checkbox]').forEach((i) => i.addEventListener('change', recalcular));
  }

  tabla.querySelectorAll('tr.item-row').forEach(enlazar);
  document.getElementById('agregar-item').addEventListener('click', () => {
    const idx = parseInt(totalForms.value, 10);
    const html = plantilla.innerHTML.replace(/__prefix__/g, idx);
    tabla.insertAdjacentHTML('beforeend', html);
    totalForms.value = idx + 1;
    const tr = tabla.lastElementChild;
    enlazar(tr);
    tr.querySelector('.js-producto').focus();
  });
  ['id_tipo_operacion', 'id_tipo_comprobante', 'id_icbper', 'id_detraccion_pct', 'id_retencion_pct', 'id_percepcion_pct']
    .forEach((id) => { const el = document.getElementById(id); if (el) el.addEventListener('input', recalcular); });
  recalcular();
})();

// confirmación de acciones destructivas
document.querySelectorAll('form[data-confirm]').forEach((f) => {
  f.addEventListener('submit', (e) => { if (!confirm(f.dataset.confirm)) e.preventDefault(); });
});

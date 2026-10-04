// ERP — detalle de ítems (agregar filas, autocompletar producto y totales en vivo)
(function () {
  const tabla = document.getElementById('items-body');
  if (!tabla) return;

  const prefix = tabla.dataset.prefix;
  const totalForms = document.getElementById(`id_${prefix}-TOTAL_FORMS`);
  const plantilla = document.getElementById('item-template');
  const datos = document.getElementById('productos-data');
  const productos = datos ? JSON.parse(datos.textContent) : [];
  const precioCampo = tabla.dataset.precioCampo;
  const igvTasa = parseFloat(tabla.dataset.igv) || 0;
  const fmt = (n) => n.toLocaleString('en-US', { minimumFractionDigits: 2, maximumFractionDigits: 2 });
  const num = (el) => parseFloat(el && el.value) || 0;
  const setText = (id, txt) => { const el = document.getElementById(id); if (el) el.textContent = txt; };

  // afectación real de la línea (misma regla que ItemBase.afectacion_en en el servidor)
  function afectacion(tr) {
    const op = document.getElementById('id_tipo_operacion');
    const tipo = document.getElementById('id_tipo_comprobante');
    const cabecera = op ? op.value : 'GRAVADA';
    if (['EXPORTACION', 'GRATUITA'].includes(cabecera)) return cabecera;
    const sel = tr.querySelector('.js-afectacion');
    const a = (sel && sel.value) || cabecera;
    return a === 'GRAVADA' && tipo && ['02', '00'].includes(tipo.value) ? 'INAFECTA' : a;
  }

  function recalcular() {
    const sumas = { GRAVADA: 0, EXONERADA: 0, INAFECTA: 0, EXPORTACION: 0, GRATUITA: 0 };
    tabla.querySelectorAll('tr.item-row').forEach((tr) => {
      const borrar = tr.querySelector('input[name$="-DELETE"]');
      tr.classList.toggle('opacity-50', !!(borrar && borrar.checked));
      const celda = tr.querySelector('.js-subtotal');
      if (!celda) return; // guías: sin importes
      const st = Math.round(num(tr.querySelector('.js-cantidad')) * num(tr.querySelector('.js-precio')) * 100) / 100;
      celda.textContent = fmt(st);
      if (!(borrar && borrar.checked)) sumas[afectacion(tr)] += st;
    });
    if (!document.getElementById('t-total')) return;
    const icbper = num(document.getElementById('id_icbper'));
    const base = sumas.GRAVADA;
    const igv = Math.round(base * igvTasa) / 100;
    const total = base + sumas.EXONERADA + sumas.INAFECTA + sumas.EXPORTACION + igv + icbper;
    setText('t-base', fmt(base));
    setText('t-exo', fmt(sumas.EXONERADA));
    setText('t-nograv', fmt(sumas.INAFECTA + sumas.EXPORTACION));
    setText('t-igv', fmt(igv));
    setText('t-total', fmt(total));
    const extra = document.getElementById('t-extra');
    if (extra) {
      const d = num(document.getElementById('id_detraccion_pct'));
      const r = num(document.getElementById('id_retencion_pct'));
      const p = num(document.getElementById('id_percepcion_pct'));
      const partes = [];
      if (d) partes.push(`Detracción ${d}%: ${fmt(total * d / 100)}`);
      if (r) partes.push(`Retención ${r}%: ${fmt(total * r / 100)}`);
      if (p) partes.push(`Percepción ${p}%: ${fmt(total * p / 100)}`);
      extra.textContent = partes.join(' · ');
    }
  }

  function enlazar(tr) {
    const sel = tr.querySelector('.js-producto');
    if (sel) {
      sel.addEventListener('change', () => {
        const p = productos.find((x) => String(x.id) === sel.value);
        if (!p) return;
        const desc = tr.querySelector('.js-descripcion');
        if (desc) desc.value = p.nombre;
        const precio = tr.querySelector('.js-precio');
        if (precio) precio.value = parseFloat(p[precioCampo] || 0).toFixed(2);
        const unidad = tr.querySelector('.js-unidad');
        if (unidad && p.unidad) unidad.value = p.unidad;
        const afec = tr.querySelector('.js-afectacion');
        if (afec && p.afectacion_igv !== undefined) afec.value = p.afectacion_igv || '';
        const cant = tr.querySelector('.js-cantidad');
        if (!num(cant)) cant.value = 1;
        recalcular();
      });
    }
    tr.querySelectorAll('input').forEach((i) => i.addEventListener('input', recalcular));
    tr.querySelectorAll('.js-afectacion').forEach((s) => s.addEventListener('change', recalcular));
    tr.querySelectorAll('input[type=checkbox]').forEach((i) => i.addEventListener('change', recalcular));
  }

  tabla.querySelectorAll('tr.item-row').forEach(enlazar);
  document.getElementById('agregar-item').addEventListener('click', () => {
    const idx = parseInt(totalForms.value, 10);
    tabla.insertAdjacentHTML('beforeend', plantilla.innerHTML.replace(/__prefix__/g, idx));
    totalForms.value = idx + 1;
    const tr = tabla.lastElementChild;
    enlazar(tr);
    tr.querySelector('.js-producto').focus();
  });
  ['id_tipo_operacion', 'id_tipo_comprobante', 'id_icbper', 'id_detraccion_pct', 'id_retencion_pct', 'id_percepcion_pct']
    .forEach((id) => {
      const el = document.getElementById(id);
      if (el) { el.addEventListener('input', recalcular); el.addEventListener('change', recalcular); }
    });
  recalcular();
})();

// Tipo de cambio SUNAT automático al elegir dólares (o cambiar la fecha)
(function () {
  const moneda = document.getElementById('id_moneda');
  const tc = document.getElementById('id_tipo_cambio');
  if (!moneda || !tc || !window.ERP_TC_URL) return;
  const fecha = document.getElementById('id_fecha_emision') || document.getElementById('id_fecha');
  let aviso = document.createElement('div');
  aviso.className = 'form-text small';
  tc.insertAdjacentElement('afterend', aviso);

  async function actualizar(forzar) {
    if (moneda.value !== 'USD') {
      tc.value = '1.000';
      aviso.textContent = '';
      return;
    }
    if (!forzar && parseFloat(tc.value) > 1) return;
    aviso.textContent = 'Consultando SUNAT…';
    try {
      const f = fecha && fecha.value ? fecha.value : '';
      const r = await fetch(`${window.ERP_TC_URL}?fecha=${encodeURIComponent(f)}`);
      const d = await r.json();
      if (d.ok) {
        tc.value = parseFloat(d.venta).toFixed(3);
        aviso.textContent = `T.C. venta ${d.fuente} del ${d.fecha.split('-').reverse().join('/')}`;
      } else {
        aviso.textContent = 'Sin tipo de cambio: regístrelo en Maestros > Tipo de cambio.';
      }
    } catch (e) {
      aviso.textContent = 'No se pudo obtener el tipo de cambio.';
    }
  }
  moneda.addEventListener('change', () => actualizar(true));
  if (fecha) fecha.addEventListener('change', () => actualizar(true));
  actualizar(false);
})();

// Ubigeo en cascada: departamento → provincia → distrito (el valor guardado es el ubigeo del distrito)
(function () {
  const dist = document.querySelector('select[data-ubigeo-nivel="distrito"]');
  if (!dist) return;
  const dep = document.querySelector('select[data-ubigeo-nivel="departamento"]');
  const prov = document.querySelector('select[data-ubigeo-nivel="provincia"]');
  const ayuda = dist.parentElement.querySelector('.form-text');
  const actual = dist.dataset.valor || '';
  const llenar = (sel, items, valor) => {
    sel.innerHTML = '<option value="">---------</option>' +
      items.map(([c, n]) => `<option value="${c}"${c === valor ? ' selected' : ''}>${n}</option>`).join('');
  };
  fetch('/ubigeos.json').then((r) => r.json()).then((arbol) => {
    const deps = Object.fromEntries(arbol.map((d) => [d[0], d]));
    const provincias = () => (deps[dep.value] ? deps[dep.value][2] : []);
    const distritos = () => { const p = provincias().find((x) => x[0] === prov.value); return p ? p[2] : []; };
    const mostrar = () => {
      const d = distritos().find((x) => x[0] === dist.value);
      if (ayuda) ayuda.textContent = d ? `Ubigeo ${d[0]}` : 'Elija departamento, provincia y distrito: el ubigeo se completa solo';
    };
    llenar(dep, arbol.map((d) => [d[0], d[1]]), actual.slice(0, 2));
    llenar(prov, provincias().map((p) => [p[0], p[1]]), actual.slice(0, 4));
    llenar(dist, distritos(), actual);
    mostrar();
    dep.addEventListener('change', () => { llenar(prov, provincias().map((p) => [p[0], p[1]]), ''); llenar(dist, [], ''); mostrar(); });
    prov.addEventListener('change', () => { llenar(dist, distritos(), ''); mostrar(); });
    dist.addEventListener('change', mostrar);
  });
})();

// Campana de alertas: se carga en segundo plano para no demorar la pantalla
(function () {
  const campana = document.getElementById('campana');
  if (!campana) return;
  const badge = document.getElementById('campana-n');
  const lista = document.getElementById('campana-lista');
  fetch(campana.dataset.url, { credentials: 'same-origin' }).then((r) => r.json()).then((d) => {
    lista.innerHTML = '';
    if (d.total) {
      badge.textContent = d.total;
      badge.classList.remove('d-none');
      badge.classList.add(d.urgentes ? 'text-bg-danger' : 'text-bg-warning');
    }
    if (!d.alertas.length) {
      const vacio = document.createElement('div');
      vacio.className = 'p-3 small text-muted';
      vacio.textContent = 'Todo al día.';
      lista.appendChild(vacio);
    }
    d.alertas.forEach((a) => {
      const item = document.createElement('a');
      item.className = 'dropdown-item d-flex gap-2 py-2 border-bottom small text-wrap';
      item.href = a.url;
      const icono = document.createElement('i');
      icono.className = `bi ${a.icono} text-${a.nivel} fs-5`;
      const texto = document.createElement('div');
      const titulo = document.createElement('div');
      titulo.className = 'fw-semibold';
      titulo.textContent = a.titulo;
      const detalle = document.createElement('div');
      detalle.className = 'text-muted';
      detalle.textContent = a.detalle;
      texto.append(titulo, detalle);
      item.append(icono, texto);
      lista.appendChild(item);
    });
    const todas = document.createElement('a');
    todas.className = 'dropdown-item text-center small py-2';
    todas.href = campana.getAttribute('href');
    todas.textContent = 'Ver todas las alertas';
    lista.appendChild(todas);
  }).catch(() => { lista.innerHTML = '<div class="p-3 small text-muted">No se pudieron cargar las alertas.</div>'; });
})();

// confirmación de acciones destructivas
document.querySelectorAll('form[data-confirm]').forEach((f) => {
  f.addEventListener('submit', (e) => { if (!confirm(f.dataset.confirm)) e.preventDefault(); });
});

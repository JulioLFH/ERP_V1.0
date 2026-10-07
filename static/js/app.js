// Selectores de listas grandes (data-opciones): la página trae solo la opción elegida y aquí se completa la lista,
// descargada una vez y compartida por todas las filas (también las que se agregan después).
(function () {
  const cache = {};
  const esc = (t) => String(t).replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/"/g, '&quot;');
  function html(url) {
    if (!cache[url]) {
      cache[url] = fetch(url, { credentials: 'same-origin' }).then((r) => (r.ok ? r.json() : []))
        .then((lista) => '<option value="">---------</option>' +
          lista.map(([v, t]) => `<option value="${v}">${esc(t)}</option>`).join(''))
        .catch(() => null);
    }
    return cache[url];
  }
  function llenar(sel) {
    if (sel.dataset.llenado) return;
    sel.dataset.llenado = '1';
    const actual = sel.value;
    const elegida = sel.selectedOptions[0] && actual ? sel.selectedOptions[0].outerHTML : '';
    html(sel.dataset.opciones).then((opciones) => {
      if (!opciones) { delete sel.dataset.llenado; return; }
      sel.innerHTML = opciones;
      if (actual) {
        sel.value = actual;
        if (sel.value !== actual && elegida) { sel.insertAdjacentHTML('beforeend', elegida); sel.value = actual; }
      }
    });
  }
  function preparar(raiz) {
    raiz.querySelectorAll('select[data-opciones]').forEach((sel) => {
      if (sel.dataset.perezoso) {  // lista grande y poco usada: se llena después de mostrar la página
        ['focus', 'mousedown', 'touchstart'].forEach((ev) => sel.addEventListener(ev, () => llenar(sel), { once: true }));
        setTimeout(() => llenar(sel), 1200);
      } else {
        llenar(sel);
      }
    });
  }
  window.prepararSelectores = preparar;
  preparar(document);
  new MutationObserver((cambios) => cambios.forEach((c) => c.addedNodes.forEach((n) => {
    if (n.nodeType === 1) preparar(n.matches && n.matches('select[data-opciones]') ? n.parentNode : n);
  }))).observe(document.body, { childList: true, subtree: true });
})();

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
      const desc = Math.min(num(tr.querySelector('.js-descuento')), 100);
      const st = Math.round(num(tr.querySelector('.js-cantidad')) * num(tr.querySelector('.js-precio')) * (1 - desc / 100) * 100) / 100;
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

  // lista de precios (ventas): precio y descuento según cliente / lista, producto y cantidad
  const precioUrl = tabla.dataset.precioUrl;
  function precioLista(tr, alElegir) {
    const sel = tr.querySelector('.js-producto');
    if (!precioUrl || !sel || !sel.value) return;
    const val = (id) => { const el = document.getElementById(id); return el ? el.value : ''; };
    const q = new URLSearchParams({ producto: sel.value, cantidad: num(tr.querySelector('.js-cantidad')) || 1,
      tercero: val('id_tercero'), lista: val('id_lista_precios'), fecha: val('id_fecha_emision') });
    fetch(`${precioUrl}?${q}`).then((r) => r.ok ? r.json() : null).then((d) => {
      if (!d || (!d.lista && !alElegir)) return;
      const precio = tr.querySelector('.js-precio');
      if (precio && d.precio !== null) precio.value = parseFloat(d.precio).toFixed(2);
      const dcto = tr.querySelector('.js-descuento');
      if (dcto) dcto.value = parseFloat(d.descuento || 0).toFixed(2);
      recalcular();
    }).catch(() => {});
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
        const dcto = tr.querySelector('.js-descuento');
        if (dcto) dcto.value = '0';
        recalcular();
        precioLista(tr, true);
      });
      const cant = tr.querySelector('.js-cantidad');
      if (cant) cant.addEventListener('change', () => precioLista(tr, false));
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
// botones con confirmación propia (varias acciones en un mismo formulario)
document.querySelectorAll('button[data-confirm]').forEach((b) => {
  b.addEventListener('click', (e) => { if (!confirm(b.dataset.confirm)) e.preventDefault(); });
});

// aviso de carga: al abrir pantallas o enviar formularios (si el servidor tarda más de un instante) y bloqueo del
// botón pulsado para no registrar dos veces lo mismo. Las descargas (Excel, PDF, plantillas) no lo muestran.
(function () {
  const aviso = document.getElementById('cargando');
  if (!aviso) return;
  const texto = aviso.querySelector('.cargando-texto');
  let timer = null;
  const esDescarga = (url) => /[?&](formato|descargar|plantilla|solicitud)=|\/(imprimir|boletas|plame|afp|voucher|archivo)\b|\.(pdf|xlsx|zip|txt|xml)(\?|$)/i.test(url || '');
  function mostrar(mensaje) {
    clearTimeout(timer);
    timer = setTimeout(() => { texto.textContent = mensaje; aviso.hidden = false; }, 250);
  }
  function ocultar() {
    clearTimeout(timer);
    aviso.hidden = true;
    document.querySelectorAll('[data-enviando]').forEach((b) => {
      b.disabled = false;
      b.removeAttribute('data-enviando');
      if (b.dataset.textoOriginal) b.innerHTML = b.dataset.textoOriginal;
    });
  }
  window.addEventListener('pageshow', ocultar);  // al volver con el botón "atrás"
  document.addEventListener('click', (e) => {
    const a = e.target.closest('a[href]');
    if (!a || e.defaultPrevented || e.button !== 0 || e.ctrlKey || e.metaKey || e.shiftKey || e.altKey) return;
    const href = a.getAttribute('href');
    if (!href || href.startsWith('#') || href.startsWith('javascript:') || a.target === '_blank' ||
        a.hasAttribute('download') || a.dataset.bsToggle || esDescarga(href)) return;
    if (a.origin !== location.origin) return;
    mostrar('Cargando…');
  });
  document.addEventListener('submit', (e) => {
    const f = e.target;
    if (e.defaultPrevented || f.target === '_blank') return;
    const boton = e.submitter;
    const accion = (boton && boton.getAttribute('formaction')) || f.getAttribute('action') || location.href;
    const consulta = f.method.toLowerCase() === 'get' ? new URLSearchParams(new FormData(f)).toString() : '';
    if (esDescarga(accion) || esDescarga('?' + consulta)) return;
    const post = f.method.toLowerCase() === 'post';
    mostrar(post ? 'Procesando…' : 'Cargando…');
    if (post && boton) {
      // se deshabilita después de enviar (si se deshabilita antes, su name/value no viaja en el formulario)
      setTimeout(() => {
        boton.dataset.textoOriginal = boton.innerHTML;
        boton.setAttribute('data-enviando', '1');
        boton.disabled = true;
        boton.innerHTML = '<span class="spinner-border spinner-border-sm"></span> Procesando…';
      }, 0);
    }
  });
})();

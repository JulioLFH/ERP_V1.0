// Selectores de listas grandes (data-opciones): la página trae solo la opción elegida y aquí se completa la lista,
// descargada una vez y compartida por todas las filas (también las que se agregan después).
(function () {
  const cache = {};
  const esc = (t) => String(t).replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/"/g, '&quot;');
  function lista(url) {
    if (!cache[url]) {
      cache[url] = fetch(url, { credentials: 'same-origin' }).then((r) => (r.ok ? r.json() : []))
        .catch(() => null);
    }
    return cache[url];
  }
  function llenar(sel) {
    if (sel.dataset.llenado) return;
    sel.dataset.llenado = '1';
    lista(sel.dataset.opciones).then((filas) => {
      if (!filas) { delete sel.dataset.llenado; return; }
      const ts = sel.tomselect;
      if (ts) {
        // CRM-13: con el buscador las opciones se cargan por su API; reemplazar el <select> por debajo hacía que el
        // buscador volviera a agregar la opción elegida (salía repetida)
        // solo se agregan las que faltan: la elegida ya está (actualizarla hacía que se duplicara en el <select>)
        ts.addOptions(filas.filter(([v]) => !ts.options[String(v)]).map(([v, t]) => ({ value: String(v), text: t })));
        ts.refreshOptions(false);
        return;
      }
      const actual = sel.value;
      const elegida = sel.selectedOptions[0] && actual ? sel.selectedOptions[0].outerHTML : '';
      sel.innerHTML = '<option value="">---------</option>' +
        filas.map(([v, t]) => `<option value="${v}">${esc(t)}</option>`).join('');
      if (actual) {
        sel.value = actual;
        if (sel.value !== actual && elegida) { sel.insertAdjacentHTML('beforeend', elegida); sel.value = actual; }
      }
    });
  }
  // buscador: escribir cualquier palabra (FRESA) lista todo lo que la contiene, en cualquier parte del texto;
  // varias palabras deben estar todas (FRESA 155). Se aplica a las listas grandes; data-sin-buscador lo evita.
  function buscable(sel) {
    if (!window.TomSelect || sel.tomselect || sel.multiple || sel.dataset.sinBuscador !== undefined ||
        sel.dataset.ubigeoNivel) return;
    new window.TomSelect(sel, {
      // CAL-11: la opción elegida ya se ve en la casilla; en la lista salía otra vez (parecía repetida)
      maxOptions: 200, allowEmptyOption: false, dropdownParent: 'body', searchField: ['text'], hideSelected: true,
      placeholder: 'Escriba para buscar…', plugins: sel.required ? [] : ['clear_button'],
      onFocus: () => { if (sel.dataset.opciones) llenar(sel); },
      render: { no_results: () => '<div class="no-results px-2 py-1 text-muted small">Sin coincidencias</div>' },
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
      buscable(sel);
    });
    raiz.querySelectorAll('select:not([data-opciones])').forEach((sel) => {
      if (sel.options.length > 50 || sel.dataset.buscable !== undefined) buscable(sel);  // PLAT-13: data-buscable
    });
  }
  window.prepararSelectores = preparar;
  preparar(document);
  new MutationObserver((cambios) => cambios.forEach((c) => c.addedNodes.forEach((n) => {
    if (n.nodeType === 1) preparar(n.matches && n.matches('select[data-opciones]') ? n.parentNode : n);
  }))).observe(document.body, { childList: true, subtree: true });
})();

// Reportes agrupados (table.js-plegable): la fila de grupo (tr[data-grupo]) muestra u oculta sus filas de detalle
// (tr[data-de]); botones [data-plegar="todo"|"nada"] dentro de [data-tabla="id"] abren o cierran todos.
(function () {
  function poner(tabla, grupo, abierto) {
    tabla.querySelectorAll(`tr[data-de="${grupo}"]`).forEach((f) => { f.hidden = !abierto; });
    const fila = tabla.querySelector(`tr[data-grupo="${grupo}"]`);
    if (fila) {
      fila.setAttribute('aria-expanded', abierto ? 'true' : 'false');
      const ico = fila.querySelector('.js-chevron');
      if (ico) ico.className = `bi ${abierto ? 'bi-chevron-down' : 'bi-chevron-right'} js-chevron text-muted`;
    }
  }
  document.querySelectorAll('table.js-plegable').forEach((tabla) => {
    const grupos = [...tabla.querySelectorAll('tr[data-grupo]')];
    grupos.forEach((fila) => {
      fila.style.cursor = 'pointer';
      fila.title = 'Clic para mostrar u ocultar el detalle';
      poner(tabla, fila.dataset.grupo, tabla.dataset.inicio !== 'plegado');
      fila.addEventListener('click', () => poner(tabla, fila.dataset.grupo, fila.getAttribute('aria-expanded') !== 'true'));
    });
    document.querySelectorAll(`[data-tabla="${tabla.id}"] [data-plegar]`).forEach((b) => b.addEventListener('click', () => {
      grupos.forEach((fila) => poner(tabla, fila.dataset.grupo, b.dataset.plegar === 'todo'));
    }));
  });
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

  // datos del producto: los del documento vienen en la página; los demás se piden al elegirlos y se guardan
  async function producto(id) {
    let p = productos.find((x) => String(x.id) === String(id));
    if (p || !id || !window.ERP_PRODUCTO_URL) return p;
    try {
      const r = await fetch(window.ERP_PRODUCTO_URL.replace('/0/', `/${id}/`));
      if (!r.ok) return undefined;
      p = await r.json();
      productos.push(p);
      return p;
    } catch (e) {
      return undefined;
    }
  }

  function enlazar(tr) {
    const sel = tr.querySelector('.js-producto');
    if (sel) {
      sel.addEventListener('change', async () => {
        const p = await producto(sel.value);
        if (!p) return;
        const desc = tr.querySelector('.js-descripcion');
        if (desc) desc.value = p.nombre;
        const precio = tr.querySelector('.js-precio');
        // INV-31: se propone el precio o costo del producto solo si la casilla está vacía o sigue con lo propuesto
        // (la respuesta puede llegar después de que el usuario escribió el suyo)
        if (precio && (!num(precio) || precio.dataset.propuesto === precio.value)) {
          precio.value = parseFloat(p[precioCampo] || 0).toFixed(2);
          precio.dataset.propuesto = precio.value;
        }
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

// MAN-24: la fecha contable propuesta ([data-sigue-fecha="campo"]) toma el día de la fecha y hora de su formulario
// mientras el usuario no la cambie a mano
document.querySelectorAll('input[data-sigue-fecha]').forEach((contable) => {
  const origen = contable.form && contable.form.elements[contable.dataset.sigueFecha];
  if (!origen) return;
  contable.addEventListener('input', () => { contable.dataset.editado = '1'; });
  origen.addEventListener('input', () => {
    const dia = (origen.value || '').slice(0, 10);
    if (!contable.dataset.editado && /^\d{4}-\d{2}-\d{2}$/.test(dia)) contable.value = dia;
  });
});

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

// PLAT-12: buscadores de las listas (campo «q» de un formulario de filtros): al escribir 2 o más caracteres se
// proponen hasta 10 coincidencias de lo registrado según la pantalla; al elegir una, se filtra.
(function () {
  const url = window.ERP_SUGERENCIAS_URL;
  if (!url) return;
  document.querySelectorAll('form input[name="q"]').forEach((input, i) => {
    const form = input.form;
    if (!form || (form.method || 'get').toLowerCase() !== 'get' || input.list) return;
    const lista = document.createElement('datalist');
    lista.id = `sugerencias-${i}`;
    input.insertAdjacentElement('afterend', lista);
    input.setAttribute('list', lista.id);
    input.setAttribute('autocomplete', 'off');
    let timer = null;
    let ultimas = [];
    input.addEventListener('input', (e) => {
      const valor = input.value.trim();
      // elegir una sugerencia de la lista: filtra en el acto
      if (e.inputType === 'insertReplacementText' || ultimas.includes(input.value)) { form.requestSubmit(); return; }
      clearTimeout(timer);
      if (valor.length < 2) { lista.innerHTML = ''; return; }
      timer = setTimeout(() => {
        const q = new URLSearchParams({ q: valor, ruta: location.pathname });
        fetch(`${url}?${q}`, { credentials: 'same-origin' }).then((r) => (r.ok ? r.json() : [])).then((filas) => {
          ultimas = filas.map((f) => f.valor);
          lista.innerHTML = '';
          filas.forEach((f) => {
            const op = document.createElement('option');
            op.value = f.valor;
            op.label = f.texto;
            op.textContent = f.texto;
            lista.appendChild(op);
          });
        }).catch(() => {});
      }, 250);
    });
  });
})();

// PLAT-13: filtros de lista con varias opciones (select[multiple][data-filtro]): lista de casillas con buscador,
// «Todos / Ninguno», contador y etiquetas que se quitan con una x; se recuerdan por usuario y pantalla.
(function () {
  const usuario = (document.body.dataset.usuario || '');
  const esc = (t) => String(t).replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/"/g, '&quot;');
  document.querySelectorAll('select[multiple][data-filtro]').forEach((sel) => {
    const form = sel.form;
    const clave = `filtro:${usuario}:${location.pathname}:${sel.name}`;
    const params = new URLSearchParams(location.search);
    // sin filtros en la dirección: se recupera lo último elegido por este usuario en esta pantalla
    if (![...params.keys()].length) {
      try {
        const guardado = JSON.parse(localStorage.getItem(clave) || '[]');
        if (guardado.length && form) {
          [...sel.options].forEach((o) => { o.selected = guardado.includes(o.value); });
          if ([...sel.selectedOptions].length && !sessionStorage.getItem(clave)) {
            sessionStorage.setItem(clave, '1');
            form.requestSubmit();
            return;
          }
        }
      } catch (e) { /* sin almacenamiento local: no se recuerda */ }
    }
    try { localStorage.setItem(clave, JSON.stringify([...sel.selectedOptions].map((o) => o.value))); } catch (e) { /* */ }
    const etiqueta = sel.dataset.filtro || sel.name;
    const caja = document.createElement('div');
    caja.className = 'dropdown d-inline-block filtro-multiple';
    const contar = () => {
      const n = [...sel.selectedOptions].length;
      return n ? `${etiqueta}: ${n === 1 ? sel.selectedOptions[0].text : n + ' seleccionados'}` : `${etiqueta}: todos`;
    };
    caja.innerHTML = `<button type="button" class="btn btn-sm btn-outline-secondary dropdown-toggle" data-bs-toggle="dropdown" data-bs-auto-close="outside">${esc(contar())}</button>
      <div class="dropdown-menu p-2" style="min-width:16rem;max-width:22rem">
        <input type="search" class="form-control form-control-sm mb-2" placeholder="Buscar…" aria-label="Buscar en ${esc(etiqueta)}">
        <div class="d-flex gap-2 mb-2 small"><a href="#" data-todos>Todos</a><a href="#" data-ninguno>Ninguno</a></div>
        <div class="opciones" style="max-height:16rem;overflow:auto">${[...sel.options].filter((o) => o.value !== '').map((o) =>
          `<label class="form-check small d-block"><input type="checkbox" class="form-check-input" value="${esc(o.value)}"${o.selected ? ' checked' : ''}> ${esc(o.text)}</label>`).join('')}</div>
        <button type="submit" class="btn btn-sm btn-primary w-100 mt-2">Aplicar</button>
      </div>`;
    sel.hidden = true;
    sel.insertAdjacentElement('afterend', caja);
    const boton = caja.querySelector('button');
    const casillas = [...caja.querySelectorAll('input[type=checkbox]')];
    const sincronizar = () => {
      const marcados = casillas.filter((c) => c.checked).map((c) => c.value);
      [...sel.options].forEach((o) => { o.selected = marcados.includes(o.value); });
      boton.textContent = contar();
    };
    casillas.forEach((c) => c.addEventListener('change', sincronizar));
    caja.querySelector('input[type=search]').addEventListener('input', (e) => {
      const t = e.target.value.toLowerCase();
      casillas.forEach((c) => { c.parentElement.hidden = t && !c.parentElement.textContent.toLowerCase().includes(t); });
    });
    caja.querySelector('[data-todos]').addEventListener('click', (e) => { e.preventDefault(); casillas.forEach((c) => { if (!c.parentElement.hidden) c.checked = true; }); sincronizar(); });
    caja.querySelector('[data-ninguno]').addEventListener('click', (e) => { e.preventDefault(); casillas.forEach((c) => { c.checked = false; }); sincronizar(); });
    // etiquetas de lo elegido, con x para quitar cada valor
    const marcados = [...sel.selectedOptions];
    if (marcados.length && form) {
      let barra = form.querySelector('.filtros-elegidos');
      if (!barra) {
        barra = document.createElement('div');
        barra.className = 'filtros-elegidos d-flex flex-wrap gap-1 w-100 small';
        form.appendChild(barra);
      }
      marcados.forEach((o) => {
        const chip = document.createElement('span');
        chip.className = 'badge text-bg-light border';
        chip.innerHTML = `${esc(etiqueta)}: ${esc(o.text)} <a href="#" class="ms-1 text-danger text-decoration-none" aria-label="Quitar">×</a>`;
        chip.querySelector('a').addEventListener('click', (e) => { e.preventDefault(); o.selected = false; form.requestSubmit(); });
        barra.appendChild(chip);
      });
    }
  });
  // «Limpiar» olvida lo recordado de la pantalla
  document.querySelectorAll('a[data-limpiar-filtros]').forEach((a) => a.addEventListener('click', () => {
    try { Object.keys(localStorage).filter((k) => k.startsWith(`filtro:${usuario}:${location.pathname}:`)).forEach((k) => localStorage.removeItem(k)); } catch (e) { /* */ }
  }));
})();

// PLAT-14: fechas propuestas. Vencimiento = emisión + días de crédito del cliente o proveedor (si la venta o compra es
// al crédito), mientras el usuario no lo cambie. Desde / Hasta de los reportes: el mes en curso.
(function () {
  const emision = document.getElementById('id_fecha_emision');
  const vence = document.getElementById('id_fecha_vencimiento');
  const tercero = document.getElementById('id_tercero');
  const forma = document.getElementById('id_forma_pago');
  if (emision && vence && window.ERP_TERCERO_CREDITO_URL) {
    let manual = !!vence.value;
    vence.addEventListener('input', () => { manual = true; });
    const sumar = (iso, dias) => {
      const d = new Date(`${iso}T12:00:00`);
      d.setDate(d.getDate() + dias);
      return d.toISOString().slice(0, 10);
    };
    async function proponer() {
      if (manual || !emision.value) return;
      let dias = 0;
      if (tercero && tercero.value && (!forma || forma.value === 'CREDITO')) {
        try {
          const r = await fetch(window.ERP_TERCERO_CREDITO_URL.replace('/0/', `/${tercero.value}/`), { credentials: 'same-origin' });
          if (r.ok) dias = (await r.json()).dias || 0;
        } catch (e) { /* sin datos: vence el día de emisión */ }
      }
      if (forma && forma.value === 'CREDITO' && !dias) { vence.value = ''; return; }  // lo calcula el servidor
      vence.value = sumar(emision.value, dias);
    }
    [emision, tercero, forma].forEach((el) => { if (el) el.addEventListener('change', proponer); });
    proponer();
  }
  // reportes con Desde / Hasta vacíos y sin filtros en la dirección: se propone el mes en curso
  if (!location.search) {
    document.querySelectorAll('form').forEach((f) => {
      if ((f.method || 'get').toLowerCase() !== 'get') return;
      const desde = f.querySelector('input[type=date][name=desde]');
      const hasta = f.querySelector('input[type=date][name=hasta]');
      if (!desde || !hasta || desde.value || hasta.value) return;
      const hoy = new Date();
      const iso = (d) => `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, '0')}-${String(d.getDate()).padStart(2, '0')}`;
      desde.value = iso(new Date(hoy.getFullYear(), hoy.getMonth(), 1));
      hasta.value = iso(hoy);
      f.requestSubmit();
    });
  }
})();

// confirmación de acciones destructivas
document.querySelectorAll('form[data-confirm]').forEach((f) => {
  f.addEventListener('submit', (e) => { if (!confirm(f.dataset.confirm)) e.preventDefault(); });
});
// botones con confirmación propia (varias acciones en un mismo formulario)
document.querySelectorAll('button[data-confirm]').forEach((b) => {
  b.addEventListener('click', (e) => { if (!confirm(b.dataset.confirm)) e.preventDefault(); });
});
// copiar al portapapeles el texto de otro elemento (enlaces para pegar en la página web, etc.)
document.querySelectorAll('[data-copiar]').forEach((b) => {
  b.addEventListener('click', () => {
    const origen = document.querySelector(b.dataset.copiar);
    if (!origen) return;
    const texto = origen.value || origen.textContent.trim();
    const listo = () => { const antes = b.innerHTML; b.textContent = 'Copiado'; setTimeout(() => { b.innerHTML = antes; }, 1500); };
    if (navigator.clipboard && window.isSecureContext) navigator.clipboard.writeText(texto).then(listo);
    else { const t = document.createElement('textarea'); t.value = texto; document.body.appendChild(t); t.select(); document.execCommand('copy'); t.remove(); listo(); }
  });
});

// paleta de búsqueda (como la de Odoo): Ctrl + K desde cualquier pantalla y, en Inicio, empezar a escribir o la barra
// espaciadora. Primero los módulos y opciones del menú, luego los registros (contactos, documentos…); flechas para
// moverse, Enter abre, Ctrl + Enter en una pestaña nueva, Esc cierra
(() => {
  const ventana = document.getElementById('paleta');
  if (!ventana || !window.bootstrap) return;
  const caja = document.getElementById('paleta-q');
  const lista = document.getElementById('paleta-lista');
  const modal = bootstrap.Modal.getOrCreateInstance(ventana);
  const url = ventana.dataset.url;
  let espera = null;
  let pedido = 0;
  let activo = 0;
  const esc = (t) => String(t ?? '').replace(/[&<>"']/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
  const opciones = () => [...lista.querySelectorAll('.paleta-opcion')];
  const marcar = (n) => {
    const o = opciones();
    if (!o.length) return;
    activo = (n + o.length) % o.length;
    o.forEach((a, i) => a.classList.toggle('activa', i === activo));
    o[activo].scrollIntoView({ block: 'nearest' });
  };
  const fila = (f, icono, color) => `<a class="paleta-opcion" href="${esc(f.url)}" role="option">`
    + `<span class="paleta-icono" style="${color ? `color:${esc(color)}` : ''}"><i class="bi ${esc(icono)}"></i></span>`
    + `<span class="paleta-texto"><span>${esc(f.texto)}</span><small>${esc(f.detalle)}</small></span>`
    + '<span class="paleta-tecla"><kbd>Ctrl</kbd> + <kbd>↵</kbd> nueva pestaña</span></a>';
  const mostrar = (d) => {
    let html = '';
    if (d.menus.length) {
      html += (d.q ? '<div class="paleta-grupo">Menús</div>' : '') + d.menus.map((m) => fila(m, m.icono, m.color)).join('');
    }
    d.grupos.forEach((g) => {
      html += `<div class="paleta-grupo"><i class="bi ${esc(g.icono)}"></i> ${esc(g.titulo)}</div>`
        + g.filas.map((f) => fila(f, g.icono, '')).join('');
    });
    if (d.q && d.q.length >= 2) {
      html += `<a class="paleta-opcion paleta-todos" href="${esc(url)}?q=${encodeURIComponent(d.q)}">`
        + '<span class="paleta-icono"><i class="bi bi-search"></i></span>'
        + `<span class="paleta-texto"><span>Ver todos los resultados de «${esc(d.q)}»</span></span></a>`;
    }
    lista.innerHTML = html || '<div class="p-3 small text-muted">Sin resultados en sus módulos.</div>';
    marcar(0);
  };
  const consultar = () => {
    clearTimeout(espera);
    espera = setTimeout(() => {
      const q = caja.value.replace(/^\//, '').trim();
      const n = ++pedido;
      fetch(`${url}?formato=json&q=${encodeURIComponent(q)}`, { headers: { Accept: 'application/json' } })
        .then((r) => r.json()).then((d) => { if (n === pedido) mostrar(d); })
        .catch(() => { lista.innerHTML = '<div class="p-3 small text-danger">No se pudo buscar.</div>'; });
    }, 200);
  };
  let abierta = false;  // Bootstrap marca la ventana como abierta recién después de su animación
  const abrir = (inicial = '') => {
    if (abierta) return;
    abierta = true;
    caja.value = inicial;
    modal.show();
    consultar();
  };
  ventana.addEventListener('shown.bs.modal', () => { caja.focus(); const n = caja.value.length; caja.setSelectionRange(n, n); });
  ventana.addEventListener('hidden.bs.modal', () => { abierta = false; });
  caja.addEventListener('input', consultar);
  caja.addEventListener('keydown', (e) => {
    if (e.key === 'ArrowDown') { e.preventDefault(); marcar(activo + 1); }
    if (e.key === 'ArrowUp') { e.preventDefault(); marcar(activo - 1); }
    if (e.key === 'Enter') {
      const o = opciones()[activo];
      if (!o) return;
      e.preventDefault();
      if (e.ctrlKey || e.metaKey) window.open(o.href, '_blank');
      else window.location.href = o.href;
    }
  });
  lista.addEventListener('mousemove', (e) => {
    const o = e.target.closest('.paleta-opcion');
    if (o) marcar(opciones().indexOf(o));
  });
  document.querySelectorAll('[data-paleta]').forEach((b) => b.addEventListener('click', () => abrir()));
  const escribiendo = (t) => t.closest('input, textarea, select, [contenteditable], .modal.show');
  document.addEventListener('keydown', (e) => {
    if ((e.ctrlKey || e.metaKey) && e.key.toLowerCase() === 'k') { e.preventDefault(); abrir(); return; }
    if (abierta && document.activeElement !== caja && e.key.length === 1
        && !e.ctrlKey && !e.metaKey && !e.altKey) {  // lo que se escribe mientras la ventana se abre no se pierde
      e.preventDefault();
      caja.value += e.key;
      caja.focus();
      consultar();
      return;
    }
    if (abierta || escribiendo(e.target) || e.ctrlKey || e.metaKey || e.altKey) return;
    if (e.key === '/') { e.preventDefault(); abrir(); return; }
    // en Inicio: la barra espaciadora o cualquier letra abre la paleta con lo que se empezó a escribir
    if ('inicio' in ventana.dataset && e.key.length === 1) { e.preventDefault(); abrir(e.key === ' ' ? '' : e.key); }
  });
})();

// OBR-32: muestra cómo se leerá el número (coma o punto decimal) antes de grabar; igual que core.formato.numero_usuario
function numeroUsuario(texto) {
  let t = String(texto || '').replace(/[\s ']/g, '');
  if (!t) return null;
  if (t.includes(',') && t.includes('.')) {
    t = t.lastIndexOf(',') > t.lastIndexOf('.') ? t.replace(/\./g, '').replace(',', '.') : t.replace(/,/g, '');
  } else if (t.includes(',')) {
    const partes = t.split(',');
    if (partes.length === 2 && partes[1].length !== 3) t = t.replace(',', '.');
    else if (partes.length > 2 && partes.slice(1).every((p) => p.length === 3)) t = t.replace(/,/g, '');
    else return 'ambiguo';
  } else if ((t.match(/\./g) || []).length > 1) {
    if (!t.split('.').slice(1).every((p) => p.length === 3)) return NaN;
    t = t.replace(/\./g, '');
  }
  return /^-?\d*\.?\d+$/.test(t) ? Number(t) : NaN;
}
document.querySelectorAll('input[data-ver-numero]').forEach((inp) => {
  const ayuda = document.createElement('div');
  ayuda.className = 'form-text';
  inp.insertAdjacentElement('afterend', ayuda);
  inp.addEventListener('input', () => {
    const n = numeroUsuario(inp.value);
    if (n === null) ayuda.textContent = '';
    else if (n === 'ambiguo') ayuda.textContent = 'Ambiguo: escriba 1500 o 1.5 (punto decimal)';
    else if (Number.isNaN(n)) ayuda.textContent = 'No es un número';
    else ayuda.textContent = 'Se registrará: ' + n.toLocaleString('en-US', { maximumFractionDigits: 4 });
  });
});

// aviso de carga: al abrir pantallas o enviar formularios (si el servidor tarda más de un instante) y bloqueo del
// botón pulsado para no registrar dos veces lo mismo. Las descargas (Excel, PDF, plantillas) no lo muestran.
(function () {
  const aviso = document.getElementById('cargando');
  if (!aviso) return;
  const texto = aviso.querySelector('.cargando-texto');
  let timer = null;
  const esDescarga = (url) => /[?&](formato|descargar|plantilla|solicitud)=|\/(imprimir|boletas|plame|afp|voucher|archivo|plantilla|excel|csv|exportar|descargar)\b|\.(pdf|xlsx|xls|csv|zip|txt|xml)(\?|$)/i.test(url || '');
  // respaldo para cualquier otra descarga: el servidor responde los archivos con la marca «ceiba_descarga» y, mientras
  // el aviso está visible, se revisa si llegó (una descarga no cambia de página, así que el aviso no se iría solo)
  let vigia = null;
  const MARCA = 'ceiba_descarga=';
  const borrarMarca = () => { document.cookie = MARCA + '; max-age=0; path=/'; };
  function vigilarDescarga() {
    clearInterval(vigia);
    vigia = setInterval(() => {
      if (document.cookie.split('; ').some((c) => c.startsWith(MARCA + '1'))) { borrarMarca(); ocultar(); }
    }, 400);
  }
  function mostrar(mensaje) {
    clearTimeout(timer);
    borrarMarca();
    timer = setTimeout(() => { texto.textContent = mensaje; aviso.hidden = false; }, 250);
    vigilarDescarga();
  }
  function ocultar() {
    clearTimeout(timer);
    clearInterval(vigia);
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

// LIS-01: opciones comunes en cualquier lista (table[data-lista]): columnas que cada usuario muestra u oculta y
// filtros guardados (en el servidor, por usuario), agrupar las filas con subtotales y exportar a Excel lo que se ve.
(function () {
  const tabla = document.querySelector('table[data-lista]');
  if (!tabla || !tabla.tHead || !window.ERP_LISTA_URL) return;
  const clave = tabla.dataset.lista;
  const token = (document.querySelector('input[name=csrfmiddlewaretoken]') || {}).value || '';
  const ths = [...tabla.tHead.rows[0].cells];
  const titulos = ths.map((th) => th.textContent.trim().replace(/\s+/g, ' '));
  const n = ths.length;
  const esc = (t) => String(t).replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/"/g, '&quot;');
  let estado = { ocultas: [], filtros: [] };
  const datos = () => [...tabla.tBodies].flatMap((b) => [...b.rows]).filter((tr) => tr.cells.length === n &&
    !tr.dataset.grupoAuto);

  function guardar(cuerpo) {
    const f = new URLSearchParams(cuerpo);
    f.set('lista', clave);
    return fetch(window.ERP_LISTA_URL, { method: 'POST', credentials: 'same-origin', body: f,
      headers: { 'X-CSRFToken': token } }).then((r) => (r.ok ? r.json() : estado)).then((d) => { estado = d; return d; });
  }
  function aplicar() {
    const ocultas = new Set(estado.ocultas);
    [...tabla.rows].forEach((tr) => {
      if (tr.cells.length !== n) return;
      [...tr.cells].forEach((td, i) => { td.hidden = !!titulos[i] && ocultas.has(titulos[i]); });
    });
  }
  function numero(td) {
    const t = (td.textContent || '').replace(/[^\d.,-]/g, '').replace(/,/g, '');
    const v = parseFloat(t);
    return Number.isFinite(v) ? v : 0;
  }
  function agrupar(i) {
    tabla.querySelectorAll('tr[data-grupo-auto]').forEach((tr) => tr.remove());
    const filas = datos();
    if (i === '' || i === null) { filas.forEach((tr) => tr.parentNode.appendChild(tr)); return; }
    const cuerpo = filas.length ? filas[0].parentNode : null;
    if (!cuerpo) return;
    const numericas = ths.map((th, k) => th.classList.contains('num') || (filas[0] && filas[0].cells[k] &&
      filas[0].cells[k].classList.contains('num')));
    const grupos = new Map();
    filas.forEach((tr) => {
      const k = (tr.cells[i].textContent || '').trim().replace(/\s+/g, ' ') || '—';
      if (!grupos.has(k)) grupos.set(k, []);
      grupos.get(k).push(tr);
    });
    [...grupos.keys()].sort((a, b) => a.localeCompare(b, 'es')).forEach((k) => {
      const fs = grupos.get(k);
      const subt = numericas.map((esNum, c) => (esNum && c !== i ? fs.reduce((s, tr) => s + numero(tr.cells[c]), 0) : null));
      const cab = document.createElement('tr');
      cab.dataset.grupoAuto = '1';
      cab.className = 'table-light fw-semibold';
      cab.innerHTML = `<td colspan="${n}">${esc(k)} · ${fs.length} fila(s)` + subt.map((s, c) => (s === null ? '' :
        ` · ${esc(titulos[c])}: ${s.toLocaleString('en-US', { minimumFractionDigits: 2, maximumFractionDigits: 2 })}`))
        .join('') + '</td>';
      cuerpo.appendChild(cab);
      fs.forEach((tr) => cuerpo.appendChild(tr));
    });
  }
  function excel() {
    const visibles = ths.map((th, i) => !th.hidden);
    const fila = (celdas, etiqueta) => '<tr>' + celdas.filter((_, i) => visibles[i] && titulos[i] !== '')
      .map((c) => `<${etiqueta}>${esc((c.textContent || '').trim().replace(/\s+/g, ' '))}</${etiqueta}>`).join('') + '</tr>';
    const html = '<html><head><meta charset="utf-8"></head><body><table>' + fila(ths, 'th') +
      datos().map((tr) => fila([...tr.cells], 'td')).join('') + '</table></body></html>';
    const a = document.createElement('a');
    a.href = URL.createObjectURL(new Blob(['﻿' + html], { type: 'application/vnd.ms-excel' }));
    a.download = `${(document.title || clave).replace(/[^\w\- áéíóúñÁÉÍÓÚÑ]/g, '').trim() || 'lista'}.xls`;
    a.setAttribute('download', a.download);
    document.body.appendChild(a);
    a.click();
    setTimeout(() => { URL.revokeObjectURL(a.href); a.remove(); }, 1000);
  }
  function barra() {
    const div = document.createElement('div');
    div.className = 'd-flex flex-wrap gap-2 align-items-center px-2 py-2 small d-print-none';
    const columnas = titulos.map((t, i) => (t ? `<label class="d-block"><input type="checkbox" class="form-check-input me-1" data-col="${esc(t)}" ${estado.ocultas.includes(t) ? '' : 'checked'}>${esc(t)}</label>` : '')).join('');
    const filtros = estado.filtros.map((f) => `<div class="d-flex justify-content-between align-items-center gap-2"><a href="?${esc(f.query)}">${esc(f.nombre)}</a><button type="button" class="btn btn-sm btn-link text-danger p-0" data-borrar="${esc(f.nombre)}" title="Quitar">×</button></div>`).join('') || '<div class="text-muted">Sin filtros guardados.</div>';
    const opciones = titulos.map((t, i) => (t ? `<option value="${i}">Agrupar por ${esc(t.toLowerCase())}</option>` : '')).join('');
    const hayExcel = !!document.querySelector('a[href*="formato=excel"]');
    div.innerHTML = `<div class="dropdown"><button class="btn btn-sm btn-outline-secondary dropdown-toggle" data-bs-toggle="dropdown" data-bs-auto-close="outside" type="button"><i class="bi bi-layout-three-columns"></i> Columnas</button><div class="dropdown-menu p-2" style="min-width:230px;max-height:60vh;overflow:auto">${columnas}<button type="button" class="btn btn-sm btn-link px-0" data-todas>Mostrar todas</button></div></div>` +
      `<div class="dropdown"><button class="btn btn-sm btn-outline-secondary dropdown-toggle" data-bs-toggle="dropdown" data-bs-auto-close="outside" type="button"><i class="bi bi-star"></i> Filtros</button><div class="dropdown-menu p-2" style="min-width:260px">${filtros}<div class="d-flex gap-1 mt-2 border-top pt-2"><input class="form-control form-control-sm" maxlength="40" placeholder="Guardar el filtro actual como…" data-nombre><button type="button" class="btn btn-sm btn-outline-primary" data-guardar title="Guardar"><i class="bi bi-save"></i></button></div></div></div>` +
      `<select class="form-select form-select-sm w-auto" data-agrupar><option value="">Sin agrupar</option>${opciones}</select>` +
      `<button type="button" class="btn btn-sm btn-outline-success" data-excel title="Exportar a Excel las filas y columnas que se ven"><i class="bi bi-file-earmark-excel"></i>${hayExcel ? ' Lo que se ve' : ''}</button>`;
    div.addEventListener('change', (e) => {
      if (e.target.dataset.col !== undefined) {
        const ocultas = [...div.querySelectorAll('[data-col]')].filter((c) => !c.checked).map((c) => c.dataset.col);
        estado.ocultas = ocultas;
        aplicar();
        const f = new URLSearchParams({ accion: 'ocultas' });
        ocultas.forEach((c) => f.append('col', c));
        guardar(f);
      } else if (e.target.dataset.agrupar !== undefined) {
        try { localStorage.setItem(`agrupar:${clave}`, e.target.value); } catch (err) { /* sin almacenamiento */ }
        agrupar(e.target.value === '' ? '' : parseInt(e.target.value, 10));
      }
    });
    div.addEventListener('click', (e) => {
      const b = e.target.closest('button');
      if (!b) return;
      if (b.dataset.todas !== undefined) {
        div.querySelectorAll('[data-col]').forEach((c) => { c.checked = true; });
        estado.ocultas = [];
        aplicar();
        guardar({ accion: 'ocultas' });
      } else if (b.dataset.guardar !== undefined) {
        const nombre = div.querySelector('[data-nombre]').value.trim();
        if (!nombre) return;
        const q = new URLSearchParams(location.search);
        q.delete('page');
        guardar({ accion: 'guardar_filtro', nombre, query: q.toString() }).then(() => { div.replaceWith(barra()); });
      } else if (b.dataset.borrar !== undefined) {
        guardar({ accion: 'borrar_filtro', nombre: b.dataset.borrar }).then(() => { div.replaceWith(barra()); });
      } else if (b.dataset.excel !== undefined) {
        excel();
      }
    });
    let previo = '';
    try { previo = localStorage.getItem(`agrupar:${clave}`) || ''; } catch (err) { previo = ''; }
    if (previo !== '' && titulos[parseInt(previo, 10)]) {
      div.querySelector('[data-agrupar]').value = previo;
      setTimeout(() => agrupar(parseInt(previo, 10)), 0);
    }
    return div;
  }
  // la barra se muestra de inmediato; las columnas ocultas y los filtros del usuario llegan después
  const destino = tabla.closest('.table-responsive') || tabla;
  let actual = barra();
  destino.parentNode.insertBefore(actual, destino);
  fetch(`${window.ERP_LISTA_URL}?lista=${encodeURIComponent(clave)}`, { credentials: 'same-origin' })
    .then((r) => (r.ok ? r.json() : estado)).catch(() => estado).then((d) => {
      estado = { ocultas: d.ocultas || [], filtros: d.filtros || [] };
      const nueva = barra();
      actual.replaceWith(nueva);
      actual = nueva;
      aplicar();
    });
})();

// Consulta RUC (padrón de SUNAT vía Decolecta): botón «Traer datos de SUNAT» junto al número de documento; al
// escribir un RUC válido (o al pulsar el botón) llena razón social, dirección y ubigeo y muestra la ficha de SUNAT.
// Uso: consultaRuc({url, ruc: 'id_ruc', nombre: 'id_razon_social', direccion: 'id_direccion',
//                   token: 'id_token_tipo_cambio' (opcional), tipoDoc: 'id_tipo_doc' (opcional: solo con RUC),
//                   ubigeo: id de un campo simple de ubigeo (opcional, si el formulario no tiene los selectores),
//                   excluir: pk del registro que se edita (opcional, para avisar si el RUC ya está registrado)})
window.consultaRuc = function (op) {
  const campo = (id) => (id ? document.getElementById(id) : null);
  const ruc = campo(op.ruc);
  if (!ruc) return;
  const tipoDoc = campo(op.tipoDoc);
  const esRuc = () => !tipoDoc || tipoDoc.value === '6';
  const boton = document.createElement('button');
  boton.type = 'button';
  boton.className = 'btn btn-sm btn-outline-secondary mt-1';
  boton.innerHTML = '<i class="bi bi-search"></i> Traer datos de SUNAT';
  ruc.insertAdjacentElement('afterend', boton);
  const aviso = document.createElement('div');
  aviso.className = 'small mt-1';
  boton.insertAdjacentElement('afterend', aviso);
  // ficha con todo lo que devuelve SUNAT (los textos se ponen con textContent: vienen de un servicio externo)
  const ficha = document.createElement('div');
  ficha.className = 'col-12 d-none';
  (ruc.closest('[class*="col-"]') || aviso).insertAdjacentElement('afterend', ficha);
  const csrf = document.querySelector('input[name=csrfmiddlewaretoken]').value;
  const valido = (n) => {
    if (!/^(10|15|16|17|20)\d{9}$/.test(n)) return false;
    const pesos = [5, 4, 3, 2, 7, 6, 5, 4, 3, 2];
    const suma = pesos.reduce((s, p, i) => s + p * Number(n[i]), 0);
    const resto = 11 - (suma % 11);
    return ({ 10: 0, 11: 1 }[resto] ?? resto) === Number(n[10]);
  };
  const elegir = (nivel, valor) => {
    const sel = document.querySelector(`select[data-ubigeo-nivel="${nivel}"]`);
    if (!sel || ![...sel.options].some((o) => o.value === valor)) return false;
    sel.value = valor;
    sel.dispatchEvent(new Event('change'));
    return true;
  };
  // la lista de distritos se carga aparte (app.js): si aún no llegó se reintenta unos segundos
  const ponerUbigeo = (u) => new Promise((listo) => {
    if (!u || !document.querySelector('select[data-ubigeo-nivel="distrito"]')) { listo(false); return; }
    const intentar = (quedan) => {
      if (elegir('departamento', u.slice(0, 2)) && elegir('provincia', u.slice(0, 4)) && elegir('distrito', u)) listo(true);
      else if (quedan > 0) setTimeout(() => intentar(quedan - 1), 300);
      else listo(false);
    };
    intentar(10);
  });
  const mostrar = (nodos, clase) => {
    aviso.className = `small mt-1 ${clase}`;
    aviso.replaceChildren(...[].concat(nodos).map((n) => (typeof n === 'string' ? document.createTextNode(n) : n)));
  };
  const pintarFicha = (d) => {
    const filas = [
      ['Razón social', d.razon_social], ['Estado / condición', [d.estado, d.condicion].filter(Boolean).join(' · ')],
      ['Tipo de contribuyente', d.tipo], ['Actividad económica', d.actividad_economica],
      ['Domicilio fiscal', [d.direccion, [d.distrito, d.provincia, d.departamento].filter(Boolean).join(' - ')].filter(Boolean).join(', ')],
      ['Ubigeo', d.ubigeo], ['Sistema de emisión', d.tipo_facturacion], ['Sistema de contabilidad', d.tipo_contabilidad],
      ['Comercio exterior', d.comercio_exterior], ['Trabajadores', d.numero_trabajadores],
      ['Agente de retención', d.agente_retencion ? 'Sí' : 'No'], ['Buen contribuyente', d.buen_contribuyente ? 'Sí' : 'No'],
      ['Locales anexos', d.locales_anexos.length ? d.locales_anexos.join(' | ') : (d.n_locales ? String(d.n_locales) : 'Ninguno')],
    ];
    const titulo = document.createElement('div');
    titulo.className = 'fw-semibold mb-1';
    titulo.textContent = 'Datos en SUNAT';
    const tabla = document.createElement('table');
    tabla.className = 'table table-sm mb-0';
    filas.filter(([, v]) => v).forEach(([k, v]) => {
      const tr = tabla.insertRow();
      const th = document.createElement('th');
      th.className = 'fw-normal text-muted text-nowrap pe-3';
      th.textContent = k;
      tr.appendChild(th);
      tr.insertCell().textContent = v;
    });
    const caja = document.createElement('div');
    caja.className = 'border rounded p-2 small';
    caja.append(titulo, tabla);
    ficha.replaceChildren(caja);
    ficha.classList.remove('d-none');
  };
  let ultimo = '';
  const consultar = (forzar) => {
    const n = ruc.value.trim();
    if (!esRuc()) { if (forzar) mostrar('La consulta a SUNAT es para RUC: elija «RUC» como tipo de documento.', 'text-danger'); return; }
    if (!valido(n)) { if (forzar) mostrar('Revise el RUC: 11 dígitos con dígito verificador correcto.', 'text-danger'); return; }
    if (n === ultimo && !forzar) return;
    ultimo = n;
    ficha.classList.add('d-none');
    mostrar('Consultando SUNAT…', 'text-muted');
    const datos = new FormData();
    datos.append('ruc', n);
    datos.append('token', (campo(op.token) || {}).value || '');
    datos.append('excluir', op.excluir || '');
    datos.append('contexto', op.contexto || '');
    datos.append('csrfmiddlewaretoken', csrf);
    fetch(op.url, { method: 'POST', body: datos, credentials: 'same-origin' })
      .then((r) => r.json())
      .then((d) => {
        if (!d.ok) { mostrar(d.error, 'text-danger'); ultimo = ''; return null; }
        campo(op.nombre).value = d.razon_social;
        if (d.direccion && campo(op.direccion)) campo(op.direccion).value = d.direccion;
        if (campo(op.ubigeo)) {  // formulario sin selectores de departamento / provincia / distrito: campo simple
          campo(op.ubigeo).value = d.ubigeo || '';
          return [d, Boolean(d.ubigeo)];
        }
        return ponerUbigeo(d.ubigeo).then((ubigeo) => [d, ubigeo]);
      })
      .then((r) => {
        if (!r) return;
        const [d, ubigeo] = r;
        const faltan = [d.direccion ? '' : 'dirección', ubigeo ? '' : 'distrito'].filter(Boolean);
        const partes = [`Se completaron razón social, dirección y distrito desde SUNAT${faltan.length ? ` (falta ${faltan.join(' y ')}: complételo)` : ''}. `
                        + 'El correo y el teléfono se ingresan a mano (SUNAT no los publica).'];
        if (d.alerta) partes.push(` ⚠ ${d.alerta}.`);
        if (d.registrado) {
          const enlace = document.createElement('a');
          enlace.href = d.registrado.url;
          enlace.textContent = d.registrado.nombre;
          partes.push(' ⚠ Ya está registrado como ', enlace, '.');
        }
        mostrar(partes, d.alerta || d.registrado ? 'text-warning-emphasis' : 'text-success');
        pintarFicha(d);
      })
      .catch(() => { mostrar('No se pudo consultar SUNAT. Complete los datos a mano.', 'text-danger'); ultimo = ''; });
  };
  ruc.addEventListener('input', () => { if (ruc.value.trim().length === 11) consultar(false); });
  boton.addEventListener('click', () => consultar(true));
  if (tipoDoc) {
    const sync = () => boton.classList.toggle('d-none', !esRuc());
    tipoDoc.addEventListener('change', sync);
    sync();
  }
};

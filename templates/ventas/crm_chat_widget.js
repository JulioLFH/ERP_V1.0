/* Chat de la página web conectado al CRM de Ceiba (v1.107). Se pega con:
   <script src="…/widget.js" async></script> */
(function () {
  if (window.__ceibaChat) return;
  window.__ceibaChat = true;
  var C = {{ config|safe }};
  var LLAVE = 'ceiba-chat:' + C.api, conv = '', ultimo = 0, abierto = false, temporizador = null;
  try { conv = localStorage.getItem(LLAVE) || ''; } catch (e) {}

  function el(tag, estilo, texto) {
    var n = document.createElement(tag);
    if (estilo) n.style.cssText = estilo;
    if (texto) n.textContent = texto;
    return n;
  }
  var COLOR = '#0f3d2e';
  var boton = el('button', 'position:fixed;right:20px;bottom:20px;width:58px;height:58px;border-radius:50%;border:0;' +
    'background:' + COLOR + ';color:#fff;font-size:26px;cursor:pointer;box-shadow:0 4px 14px rgba(0,0,0,.25);z-index:2147483000', '💬');
  boton.setAttribute('aria-label', 'Abrir el chat');
  var panel = el('div', 'position:fixed;right:20px;bottom:90px;width:340px;max-width:calc(100vw - 32px);height:470px;' +
    'max-height:calc(100vh - 120px);background:#fff;color:#1f2937;border-radius:14px;box-shadow:0 8px 30px rgba(0,0,0,.25);' +
    'display:none;flex-direction:column;overflow:hidden;font:14px/1.4 system-ui,sans-serif;z-index:2147483000');
  var cab = el('div', 'background:' + COLOR + ';color:#fff;padding:12px 14px;font-weight:600', C.empresa);
  var hilo = el('div', 'flex:1;overflow-y:auto;padding:12px;background:#f6f7f9');
  var datos = el('div', 'padding:8px 12px;border-top:1px solid #e5e7eb;display:flex;flex-direction:column;gap:6px');
  function campo(nombre, placeholder, tipo) {
    var i = el('input', 'padding:7px 9px;border:1px solid #d1d5db;border-radius:8px;font:inherit');
    i.name = nombre; i.placeholder = placeholder; i.type = tipo || 'text';
    datos.appendChild(i);
    return i;
  }
  var fNombre = campo('nombre', 'Su nombre'), fTel = campo('telefono', 'Celular', 'tel'), fMail = campo('email', 'Correo', 'email');
  var trampa = el('input', 'position:absolute;left:-9999px'); trampa.name = 'sitio_web'; trampa.tabIndex = -1;
  datos.appendChild(trampa);
  var etiqueta = el('label', 'font-size:12px;display:flex;gap:6px;align-items:flex-start');
  var check = el('input'); check.type = 'checkbox';
  etiqueta.appendChild(check);
  etiqueta.appendChild(el('span', '', 'Autorizo el uso de mis datos para: ' + C.finalidad + ' (Ley 29733).'));
  datos.appendChild(etiqueta);
  var pie = el('form', 'display:flex;gap:6px;padding:10px 12px;border-top:1px solid #e5e7eb');
  var caja = el('textarea', 'flex:1;resize:none;height:42px;padding:8px;border:1px solid #d1d5db;border-radius:8px;font:inherit');
  caja.placeholder = 'Escriba su mensaje…';
  var enviar = el('button', 'border:0;border-radius:8px;background:' + COLOR + ';color:#fff;padding:0 14px;cursor:pointer', 'Enviar');
  enviar.type = 'submit';
  pie.appendChild(caja); pie.appendChild(enviar);
  var aviso = el('div', 'color:#b91c1c;font-size:12px;padding:0 12px');
  panel.appendChild(cab); panel.appendChild(hilo); panel.appendChild(datos); panel.appendChild(aviso); panel.appendChild(pie);

  function burbuja(texto, mio, hora) {
    var fila = el('div', 'display:flex;margin-bottom:8px;justify-content:' + (mio ? 'flex-end' : 'flex-start'));
    var b = el('div', 'max-width:80%;padding:7px 10px;border-radius:12px;white-space:pre-wrap;' +
      (mio ? 'background:' + COLOR + ';color:#fff' : 'background:#fff;border:1px solid #e5e7eb'), texto);
    if (hora) b.appendChild(el('div', 'font-size:11px;opacity:.7;text-align:right', hora));
    fila.appendChild(b); hilo.appendChild(fila); hilo.scrollTop = hilo.scrollHeight;
  }
  function pintar(lista) {
    (lista || []).forEach(function (m) { if (m.id > ultimo) { burbuja(m.texto, m.yo, m.hora); ultimo = m.id; } });
  }
  function modo() { datos.style.display = conv ? 'none' : 'flex'; }
  function consultar() {
    if (!conv || !abierto) return;
    fetch(C.api + '?c=' + encodeURIComponent(conv) + '&desde=' + ultimo).then(function (r) {
      if (r.status === 404) { conv = ''; try { localStorage.removeItem(LLAVE); } catch (e) {} modo(); return null; }
      return r.json();
    }).then(function (d) { if (d) pintar(d.mensajes); }).catch(function () {});
  }
  pie.addEventListener('submit', function (ev) {
    ev.preventDefault();
    var texto = caja.value.trim();
    if (!texto) return;
    var f = new URLSearchParams();
    f.append('texto', texto); f.append('desde', ultimo);
    if (conv) { f.append('c', conv); } else {
      f.append('nombre', fNombre.value); f.append('telefono', fTel.value); f.append('email', fMail.value);
      if (check.checked) f.append('consentimiento', '1');
      if (trampa.value) f.append('sitio_web', trampa.value);
    }
    enviar.disabled = true; aviso.textContent = '';
    fetch(C.api, {method: 'POST', body: f}).then(function (r) { return r.json(); }).then(function (d) {
      if (!d.ok) { aviso.textContent = (d.errores || ['No se pudo enviar.']).join(' '); return; }
      caja.value = '';
      if (d.c && !conv) { conv = d.c; try { localStorage.setItem(LLAVE, conv); } catch (e) {} modo(); }
      pintar(d.mensajes);
    }).catch(function () { aviso.textContent = 'Sin conexión: intente de nuevo.'; })
      .then(function () { enviar.disabled = false; });
  });
  caja.addEventListener('keydown', function (ev) {
    if (ev.key === 'Enter' && !ev.shiftKey) { ev.preventDefault(); pie.requestSubmit ? pie.requestSubmit() : enviar.click(); }
  });
  boton.addEventListener('click', function () {
    abierto = !abierto;
    panel.style.display = abierto ? 'flex' : 'none';
    if (abierto) {
      if (!hilo.childNodes.length && !conv && C.saludo) burbuja(C.saludo, false);
      modo(); consultar();
      if (!temporizador) temporizador = setInterval(consultar, 6000);
    }
  });
  function montar() { document.body.appendChild(panel); document.body.appendChild(boton); }
  if (document.body) montar(); else document.addEventListener('DOMContentLoaded', montar);
})();

/* App del celular para rendir gastos (Ceiba ERP).
   Foto con recorte y compresión (< 1 MB), lectura del QR SUNAT, consulta de RUC, lectura con IA y cola sin
   internet: los gastos se guardan en el teléfono (IndexedDB) y se suben solos al recuperar la señal. */
(function () {
  'use strict';
  var D = JSON.parse(document.getElementById('datos-app').textContent);
  var $ = function (id) { return document.getElementById(id); };
  var MAX_BYTES = 1000000, MAX_LADO = 1600;

  // ---------------------------------------------------------------- datos y selectores
  try {
    if (navigator.onLine) localStorage.setItem('rend-datos', JSON.stringify(D));
    else D = JSON.parse(localStorage.getItem('rend-datos') || 'null') || D;
  } catch (e) { /* almacenamiento no disponible */ }

  function opciones(sel, lista, texto, vacio) {
    sel.innerHTML = vacio ? '<option value="">' + vacio + '</option>' : '';
    lista.forEach(function (x) {
      var o = document.createElement('option'); o.value = x.id; o.textContent = x[texto]; sel.appendChild(o);
    });
  }
  opciones($('rendicion'), D.rendiciones, 'texto');
  var nueva = document.createElement('option'); nueva.value = 'nueva'; nueva.textContent = '+ Nueva rendición…';
  $('rendicion').appendChild(nueva);
  try { var ultima = localStorage.getItem('rend-ultima'); if (ultima) $('rendicion').value = ultima; } catch (e) {}
  if (!D.rendiciones.length) $('rendicion').value = 'nueva';
  opciones($('entrega'), D.entregas, 'texto');
  opciones($('indicador'), D.indicadores || [], 'nombre', 'Elija el indicador');
  opciones($('categoria'), D.categorias, 'nombre');
  opciones($('centro_costo'), D.centros, 'nombre', '—');
  opciones($('proyecto'), D.proyectos, 'nombre', '—');
  function hoy() {  // fecha local (no UTC: de noche en Perú ya sería mañana)
    var d = new Date(); return new Date(d.getTime() - d.getTimezoneOffset() * 60000).toISOString().slice(0, 10);
  }
  $('fecha').value = hoy();
  if (D.ia) $('ia').classList.remove('oculto');

  function verNueva() {
    $('nueva-rend').classList.toggle('oculto', $('rendicion').value !== 'nueva');
    // saldo en poder: entregado, rendido (incluye lo que está en trámite) y lo que falta rendir o devolver
    var r = D.rendiciones.filter(function (x) { return String(x.id) === $('rendicion').value; })[0];
    $('saldo').textContent = r && r.entregado ? 'Entregado ' + r.simbolo + ' ' + r.entregado + ' · rendido ' + r.rendido +
      ' · falta rendir o devolver ' + r.falta + ' · ' + r.estado : '';
  }
  $('rendicion').addEventListener('change', function () {
    verNueva();
    try { if ($('rendicion').value !== 'nueva') localStorage.setItem('rend-ultima', $('rendicion').value); } catch (e) {}
  });
  verNueva();

  function aviso(id, texto, clase) {
    $(id).innerHTML = texto ? '<div class="aviso ' + (clase || 'inf') + '"></div>' : '';
    if (texto) $(id).firstChild.textContent = texto;
  }

  function csrf() {
    var m = document.cookie.match(/(?:^|;\s*)csrftoken=([^;]+)/);
    return m ? decodeURIComponent(m[1]) : window.CSRF;
  }

  function post(url, datos) {
    return fetch(url, { method: 'POST', body: datos, credentials: 'same-origin', headers: { 'X-CSRFToken': csrf() } })
      .then(function (r) {
        var tipo = r.headers.get('Content-Type') || '';
        if (r.redirected || tipo.indexOf('application/json') < 0) throw new Error('sesion');
        return r.json().then(function (j) { j._status = r.status; return j; });
      });
  }

  $('crear-rend').addEventListener('click', function () {
    if (!navigator.onLine) { aviso('msg-form', 'Para crear una rendición necesita señal.', 'mal'); return; }
    var f = new FormData(); f.append('entrega', $('entrega').value); f.append('descripcion', $('desc-rend').value);
    post(D.urls.nueva, f).then(function (j) {
      if (j.error) { aviso('msg-form', j.error, 'mal'); return; }
      D.rendiciones.unshift(j);
      var o = document.createElement('option'); o.value = j.id; o.textContent = j.texto;
      $('rendicion').insertBefore(o, $('rendicion').firstChild); $('rendicion').value = j.id; verNueva();
      try { localStorage.setItem('rend-datos', JSON.stringify(D)); } catch (e) {}
    }).catch(function () { aviso('msg-form', 'Inicie sesión nuevamente en el sistema.', 'mal'); });
  });

  // ---------------------------------------------------------------- tipo de comprobante
  var indicadorTocado = false;
  $('indicador').addEventListener('change', function () { indicadorTocado = true; });
  function sugerirIndicador() {  // indicador sugerido por el comprobante (el usuario puede cambiarlo)
    if (indicadorTocado || !D.sugerencias) return;
    var t = $('tipo_comprobante').value, s = D.sugerencias;
    var v = t === '01' ? ($('a_nombre_empresa').value ? s['01'] : s['01N']) : (s[t] || s.otros);
    if (v) $('indicador').value = v;
  }

  function ajustarTipo() {
    sugerirIndicador();
    var t = $('tipo_comprobante').value;
    var mostrar = function (clase, si) {
      document.querySelectorAll('.' + clase).forEach(function (e) { e.classList.toggle('oculto', !si); });
    };
    mostrar('campo-ruc', ['PM', 'KM', 'SC'].indexOf(t) < 0);
    mostrar('campo-num', ['PM', 'KM', 'SC'].indexOf(t) < 0);
    mostrar('campo-igv', t === '01');
    mostrar('campo-km', t === 'KM');
    mostrar('campo-total', t !== 'KM');
    mostrar('campo-destino', t === 'PM');
  }
  $('tipo_comprobante').addEventListener('change', ajustarTipo);
  ajustarTipo();

  // ---------------------------------------------------------------- foto: recorte, giro y compresión
  var lienzo = $('lienzo'), ctx = lienzo.getContext('2d');
  var imagen = null, sel = null, inicio = null, fotos = [];

  function dibujar() {
    if (!imagen) return;
    lienzo.width = imagen.width; lienzo.height = imagen.height;
    ctx.drawImage(imagen, 0, 0);
    if (sel) {
      ctx.fillStyle = 'rgba(0,0,0,.45)';
      ctx.fillRect(0, 0, lienzo.width, sel.y); ctx.fillRect(0, sel.y + sel.h, lienzo.width, lienzo.height);
      ctx.fillRect(0, sel.y, sel.x, sel.h); ctx.fillRect(sel.x + sel.w, sel.y, lienzo.width, sel.h);
      ctx.strokeStyle = '#4dd4e6'; ctx.lineWidth = Math.max(2, lienzo.width / 300);
      ctx.strokeRect(sel.x, sel.y, sel.w, sel.h);
    }
  }

  function aCanvas(fuente, w, h) {
    var c = document.createElement('canvas'); c.width = w; c.height = h;
    c.getContext('2d').drawImage(fuente, 0, 0, w, h); return c;
  }

  function cargar(archivo) {
    if (archivo.type === 'application/pdf') {  // un PDF se adjunta tal cual
      fotos.push(archivo); $('info-foto').textContent = fotos.length + ' archivo(s) listo(s).'; return;
    }
    var url = URL.createObjectURL(archivo), img = new Image();
    img.onload = function () {
      var k = Math.min(1, MAX_LADO / Math.max(img.width, img.height));
      imagen = aCanvas(img, Math.round(img.width * k), Math.round(img.height * k));
      URL.revokeObjectURL(url); sel = null;
      lienzo.style.display = 'block'; $('herr').classList.remove('oculto'); dibujar();
      leerQR(imagen);
    };
    img.src = url;
  }

  function punto(ev) {
    var r = lienzo.getBoundingClientRect();
    return { x: (ev.clientX - r.left) * lienzo.width / r.width, y: (ev.clientY - r.top) * lienzo.height / r.height };
  }
  lienzo.addEventListener('pointerdown', function (ev) { inicio = punto(ev); lienzo.setPointerCapture(ev.pointerId); });
  lienzo.addEventListener('pointermove', function (ev) {
    if (!inicio) return;
    var p = punto(ev);
    sel = { x: Math.min(inicio.x, p.x), y: Math.min(inicio.y, p.y), w: Math.abs(p.x - inicio.x), h: Math.abs(p.y - inicio.y) };
    dibujar();
  });
  lienzo.addEventListener('pointerup', function () { inicio = null; });

  $('recortar').addEventListener('click', function () {
    if (!imagen || !sel || sel.w < 20 || sel.h < 20) { aviso('msg-lectura', 'Marque primero el área con el dedo.', 'inf'); return; }
    var c = document.createElement('canvas'); c.width = Math.round(sel.w); c.height = Math.round(sel.h);
    c.getContext('2d').drawImage(imagen, sel.x, sel.y, sel.w, sel.h, 0, 0, c.width, c.height);
    imagen = c; sel = null; dibujar();
  });
  $('girar').addEventListener('click', function () {
    if (!imagen) return;
    var c = document.createElement('canvas'); c.width = imagen.height; c.height = imagen.width;
    var g = c.getContext('2d'); g.translate(c.width, 0); g.rotate(Math.PI / 2); g.drawImage(imagen, 0, 0);
    imagen = c; sel = null; dibujar();
  });

  function comprimir(canvas) {
    return new Promise(function (ok) {
      var calidad = 0.85, fuente = canvas;
      (function intento() {
        fuente.toBlob(function (b) {
          if (b.size <= MAX_BYTES || (calidad <= 0.45 && fuente.width < 600)) { ok(b); return; }
          if (calidad > 0.45) calidad -= 0.1;
          else fuente = aCanvas(fuente, Math.round(fuente.width * 0.8), Math.round(fuente.height * 0.8));
          intento();
        }, 'image/jpeg', calidad);
      })();
    });
  }

  function guardarActual() {
    if (!imagen) return Promise.resolve();
    var c = imagen; imagen = null; sel = null; lienzo.style.display = 'none';
    return comprimir(c).then(function (b) {
      fotos.push(b); $('info-foto').textContent = fotos.length + ' foto(s) lista(s) (' + Math.round(b.size / 1024) + ' KB la última).';
    });
  }
  $('otra').addEventListener('click', function () { guardarActual().then(function () { $('foto').click(); }); });
  $('btn-foto').addEventListener('click', function () { $('foto').click(); });
  $('btn-galeria').addEventListener('click', function () { $('galeria').click(); });
  $('foto').addEventListener('change', function () { if (this.files[0]) cargar(this.files[0]); this.value = ''; });
  $('galeria').addEventListener('change', function () {
    var lista = Array.prototype.slice.call(this.files); this.value = '';
    lista.forEach(function (a, i) { if (i === lista.length - 1 || a.type === 'application/pdf') cargar(a); });
  });

  // ---------------------------------------------------------------- QR SUNAT
  function aplicarQR(texto) {
    var p = String(texto || '').split('|').map(function (x) { return x.trim(); });
    if (p.length < 7 || !/^\d{11}$/.test(p[0])) return false;
    var tipos = { '01': '01', '03': '03', '02': '02', '12': '12' };
    var tipo = tipos[('0' + p[1]).slice(-2)] || '00';
    $('tipo_comprobante').value = tipo; ajustarTipo();
    $('ruc').value = p[0]; $('serie').value = (p[2] || '').toUpperCase(); $('numero').value = (p[3] || '').replace(/^0+(?=\d)/, '');
    $('igv').value = p[4] || ''; $('total').value = p[5] || '';
    var f = p[6] || '', m = f.match(/^(\d{2})[\/-](\d{2})[\/-](\d{4})$/);
    $('fecha').value = m ? m[3] + '-' + m[2] + '-' + m[1] : f.slice(0, 10);
    var cliente = p[8] || '', aNombre = !!D.ruc_empresa && cliente === D.ruc_empresa;
    $('a_nombre_empresa').value = aNombre ? 'on' : '';
    indicadorTocado = false; sugerirIndicador();
    if (navigator.onLine) verificarDuplicado(p[0], tipo, $('serie').value, $('numero').value);
    if (tipo === '01' && !aNombre) aviso('msg-lectura', 'QR leído. Ojo: la factura está a nombre de ' + (cliente || 'otro documento') + ', no del RUC de la empresa (' + D.ruc_empresa + '): no da crédito fiscal.', 'mal');
    else aviso('msg-lectura', 'QR leído: revise los datos y complete la categoría.', 'ok');
    if (navigator.onLine) consultarRuc(true);
    return true;
  }

  function leerQR(canvas) {
    var c = canvas.width > 1200 ? aCanvas(canvas, 1200, Math.round(canvas.height * 1200 / canvas.width)) : canvas;
    if ('BarcodeDetector' in window) {
      new window.BarcodeDetector({ formats: ['qr_code'] }).detect(c).then(function (r) {
        if (!(r.length && aplicarQR(r[0].rawValue))) conJsQR(c);
      }).catch(function () { conJsQR(c); });
    } else conJsQR(c);
  }
  function conJsQR(c) {
    if (!window.jsQR) { aviso('msg-lectura', 'Sin lector de QR en este equipo: ingrese los datos o use «Leer con IA».', 'inf'); return; }
    var d = c.getContext('2d').getImageData(0, 0, c.width, c.height), r = window.jsQR(d.data, d.width, d.height);
    if (!(r && aplicarQR(r.data))) aviso('msg-lectura', 'No se encontró el QR. Ingrese los datos' + (D.ia ? ' o use «Leer con IA».' : '.'), 'inf');
  }

  function verificarDuplicado(ruc, tipo, serie, numero) {
    if (!numero || !D.urls.duplicado) return;
    var q = '?ruc=' + encodeURIComponent(ruc) + '&tipo=' + tipo + '&serie=' + encodeURIComponent(serie) +
      '&numero=' + encodeURIComponent(numero);
    fetch(D.urls.duplicado + q, { credentials: 'same-origin' }).then(function (r) { return r.json(); })
      .then(function (j) { if (j.duplicado) aviso('msg-lectura', 'Comprobante repetido: ' + j.duplicado + '. No se podrá registrar.', 'mal'); })
      .catch(function () { /* sin señal: se verifica al subir */ });
  }
  $('numero').addEventListener('change', function () {
    if (navigator.onLine && $('ruc').value) verificarDuplicado($('ruc').value, $('tipo_comprobante').value, $('serie').value, $('numero').value);
  });

  // ---------------------------------------------------------------- RUC e IA
  function consultarRuc(silencioso) {
    var ruc = $('ruc').value.trim();
    if (!/^\d{11}$/.test(ruc)) { if (!silencioso) aviso('msg-form', 'El RUC tiene 11 dígitos.', 'mal'); return; }
    fetch(D.urls.ruc.replace('00000000000', ruc), { credentials: 'same-origin' }).then(function (r) { return r.json(); })
      .then(function (j) {
        if (j.error) { if (!silencioso) aviso('msg-form', j.error, 'mal'); return; }
        if (j.nombre) $('razon_social').value = j.nombre;
        aviso('msg-form', j.alerta ? 'Atención: ' + j.alerta : (j.estado ? 'RUC ' + j.estado + ' ' + j.condicion : ''), j.alerta ? 'mal' : 'ok');
      }).catch(function () { /* sin señal */ });
  }
  $('btn-ruc').addEventListener('click', function () { consultarRuc(false); });

  $('ia').addEventListener('click', function () {
    if (!imagen) { aviso('msg-lectura', 'Tome primero la foto.', 'inf'); return; }
    if (!navigator.onLine) { aviso('msg-lectura', 'La lectura con IA necesita señal.', 'mal'); return; }
    aviso('msg-lectura', 'Leyendo el comprobante…', 'inf');
    comprimir(imagen).then(function (b) {
      var f = new FormData(); f.append('foto', b, 'comprobante.jpg');
      return post(D.urls.ia, f);
    }).then(function (j) {
      if (j.error) { aviso('msg-lectura', j.error, 'mal'); return; }
      ['ruc', 'razon_social', 'serie', 'numero', 'fecha', 'total', 'igv', 'moneda'].forEach(function (k) { if (j[k]) $(k).value = j[k]; });
      $('a_nombre_empresa').value = j.a_nombre_empresa ? 'on' : '';
      if (j.tipo_comprobante) { $('tipo_comprobante').value = j.tipo_comprobante; indicadorTocado = false; ajustarTipo(); }
      aviso('msg-lectura', 'Datos leídos con IA: confírmelos antes de guardar.' + (j.aviso ? ' ' + j.aviso : ''), 'ok');
    }).catch(function () { aviso('msg-lectura', 'Inicie sesión nuevamente en el sistema.', 'mal'); });
  });

  // ---------------------------------------------------------------- cola sin internet (IndexedDB)
  function db() {
    return new Promise(function (ok, mal) {
      var r = indexedDB.open('ceiba-rendiciones', 1);
      r.onupgradeneeded = function () { r.result.createObjectStore('pendientes', { keyPath: 'id' }); };
      r.onsuccess = function () { ok(r.result); }; r.onerror = function () { mal(r.error); };
    });
  }
  function tx(modo, fn) {
    return db().then(function (d) {
      return new Promise(function (ok, mal) {
        var t = d.transaction('pendientes', modo), s = t.objectStore('pendientes'), res = fn(s);
        t.oncomplete = function () { ok(res && res.result !== undefined ? res.result : res); };
        t.onerror = function () { mal(t.error); };
      });
    });
  }
  function todos() { return tx('readonly', function (s) { return s.getAll(); }); }
  function uuid() { return (crypto.randomUUID ? crypto.randomUUID() : String(Date.now()) + Math.random()).replace(/-/g, ''); }

  function pintar() {
    todos().then(function (lista) {
      var cont = $('pendientes');
      if (!lista.length) { cont.innerHTML = '<div class="muted">Ninguno.</div>'; return; }
      cont.innerHTML = '';
      lista.forEach(function (p) {
        var d = document.createElement('div'); d.className = 'pend';
        var t = document.createElement('div');
        t.textContent = (p.campos.glosa || 'Gasto') + ' · ' + (p.campos.total || p.campos.kilometros + ' km') + ' · ' + p.fotos.length + ' foto(s)';
        if (p.error) { var e = document.createElement('div'); e.className = 'muted'; e.style.color = '#c92a2a'; e.textContent = p.error; t.appendChild(e); }
        var b = document.createElement('button'); b.className = 'sec peq'; b.textContent = 'Quitar'; b.type = 'button';
        b.addEventListener('click', function () { tx('readwrite', function (s) { s.delete(p.id); }).then(pintar); });
        d.appendChild(t); d.appendChild(b); cont.appendChild(d);
      });
    });
  }

  $('guardar').addEventListener('click', function () {
    var r = $('rendicion').value;
    if (!r || r === 'nueva') { aviso('msg-form', 'Elija o cree la rendición.', 'mal'); return; }
    var tipo = $('tipo_comprobante').value;
    if (tipo === 'KM' ? !(parseFloat($('kilometros').value) > 0) : !(parseFloat($('total').value) > 0)) {
      aviso('msg-form', tipo === 'KM' ? 'Indique los kilómetros.' : 'Indique el total.', 'mal'); return;
    }
    if (!$('glosa').value.trim()) { aviso('msg-form', 'Escriba el detalle del gasto.', 'mal'); return; }
    if (!$('indicador').value) { aviso('msg-form', 'Elija el indicador de impuesto.', 'mal'); return; }
    guardarActual().then(function () {
      var campos = {};
      new FormData($('form')).forEach(function (v, k) { campos[k] = v; });
      campos.a_nombre_empresa = $('a_nombre_empresa').value;
      var item = { id: uuid(), rendicion: r, campos: campos, fotos: fotos.slice(), creado: Date.now() };
      return tx('readwrite', function (s) { s.put(item); });
    }).then(function () {
      fotos = []; $('form').reset(); $('fecha').value = hoy(); indicadorTocado = false; ajustarTipo();
      $('info-foto').textContent = 'Gasto guardado en el teléfono.'; aviso('msg-lectura', ''); aviso('msg-form', '');
      $('herr').classList.add('oculto'); pintar(); sincronizar();
    });
  });

  // gastos ya subidos: lo que salió de la política (para justificarlo antes de enviar la rendición)
  function subidos() { try { return JSON.parse(localStorage.getItem('rend-subidos') || '[]'); } catch (e) { return []; } }
  function recordarSubido(p, j) {
    var lista = subidos();
    lista.unshift({ texto: (p.campos.glosa || 'Gasto') + ' · ' + (p.campos.total || p.campos.kilometros + ' km'),
                    rendicion: j.rendicion, id: p.rendicion, alertas: j.alertas || '' });
    try { localStorage.setItem('rend-subidos', JSON.stringify(lista.slice(0, 10))); } catch (e) {}
  }
  function pintarSubidos() {
    var lista = subidos(), cont = $('subidos');
    if (!lista.length) { cont.innerHTML = '<div class="muted">Ninguno.</div>'; $('revisar').classList.add('oculto'); return; }
    cont.innerHTML = '';
    lista.forEach(function (s) {
      var d = document.createElement('div'); d.className = 'pend';
      var t = document.createElement('div'); t.textContent = s.rendicion + ' · ' + s.texto;
      if (s.alertas) {
        var a = document.createElement('div'); a.className = 'aviso mal';
        a.textContent = 'Fuera de política: ' + s.alertas + '. Justifíquelo antes de enviar.'; t.appendChild(a);
      }
      d.appendChild(t); cont.appendChild(d);
    });
    $('revisar').href = D.urls.detalle.replace('/0/', '/' + lista[0].id + '/');
    $('revisar').classList.remove('oculto');
  }

  var subiendo = false;
  function sincronizar() {
    if (subiendo || !navigator.onLine) return;
    subiendo = true;
    todos().then(function (lista) {
      return lista.reduce(function (cadena, p) {
        return cadena.then(function () {
          var f = new FormData();
          Object.keys(p.campos).forEach(function (k) { f.append(k, p.campos[k]); });
          f.append('rendicion', p.rendicion); f.append('cliente_id', p.id);
          p.fotos.forEach(function (b, i) { f.append('fotos', b, b.name || ('comprobante-' + (i + 1) + '.jpg')); });
          return post(D.urls.gasto, f).then(function (j) {
            if (j.ok) {
              recordarSubido(p, j);
              return tx('readwrite', function (s) { s.delete(p.id); });
            }
            p.error = j.error || 'No se pudo subir.';
            return tx('readwrite', function (s) { s.put(p); });
          });
        });
      }, Promise.resolve());
    }).catch(function (e) {
      if (String(e && e.message) === 'sesion') aviso('msg-form', 'Su sesión venció: ingrese al sistema y vuelva a esta pantalla para subir los pendientes.', 'mal');
    }).then(function () { subiendo = false; pintar(); pintarSubidos(); });
  }
  $('sincronizar').addEventListener('click', sincronizar);

  function red() {
    $('estado-red').textContent = navigator.onLine ? 'En línea' : 'Sin señal: se guarda en el teléfono';
    if (navigator.onLine) sincronizar();
  }
  window.addEventListener('online', red); window.addEventListener('offline', red);
  red(); pintar(); pintarSubidos();

  if ('serviceWorker' in navigator) navigator.serviceWorker.register('/rendiciones/sw.js', { scope: '/rendiciones/' }).catch(function () {});
})();

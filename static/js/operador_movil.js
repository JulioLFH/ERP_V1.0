/* App del operador logístico (Ceiba ERP · Go Just In Time).
   Una acción por pantalla: «Escanear» y luego «Confirmar». Verde con sonido y vibración si está bien; rojo con el
   motivo si no. La cámara de la foto se abre sola cuando el paso la pide. Si el QR no se lee, se elige de una lista
   corta. Sin señal, todo se guarda en el teléfono (IndexedDB) y se envía solo al volver la conexión. */
(function () {
  'use strict';
  var D = JSON.parse(document.getElementById('datos-app').textContent);
  var $ = function (id) { return document.getElementById(id); };
  var MAX_LADO = 1600;
  var estado = { paso: null, qr: '', id: null, foto: null, gps: null, info: null };

  // ---------------------------------------------------------------- pantallas
  function ir(id) {
    document.querySelectorAll('.pantalla').forEach(function (p) { p.classList.toggle('activa', p.id === id); });
    window.scrollTo(0, 0);
  }
  document.querySelectorAll('[data-ir]').forEach(function (b) {
    b.addEventListener('click', function () { ir(b.getAttribute('data-ir')); });
  });
  document.querySelectorAll('[data-volver]').forEach(function (b) {
    b.addEventListener('click', function () { detenerCamara(); ir('p-inicio'); });
  });

  D.pasos.forEach(function (p) {
    var b = document.createElement('button');
    b.className = 'grande'; b.type = 'button'; b.textContent = p.nombre;
    b.addEventListener('click', function () { elegirPaso(p); });
    $('pasos').appendChild(b);
  });

  function elegirPaso(p) {
    estado.paso = p.clave;
    $('titulo-paso').textContent = p.nombre;
    $('cabecera').textContent = p.nombre;
    cargarLista(p.clave);
    ir('p-escanear');
  }

  // ---------------------------------------------------------------- sonido y vibración
  var audio = null;
  function tono(frecuencias, duracion) {
    try {
      audio = audio || new (window.AudioContext || window.webkitAudioContext)();
      var t = audio.currentTime;
      frecuencias.forEach(function (f, i) {
        var o = audio.createOscillator(), g = audio.createGain();
        o.frequency.value = f; o.type = 'square'; g.gain.value = 0.15;
        o.connect(g); g.connect(audio.destination);
        o.start(t + i * (duracion + 0.05)); o.stop(t + i * (duracion + 0.05) + duracion);
      });
    } catch (e) { /* sin audio */ }
  }
  function bien() { tono([880, 1320], 0.12); if (navigator.vibrate) navigator.vibrate(120); }
  function mal() { tono([220, 180], 0.3); if (navigator.vibrate) navigator.vibrate([300, 120, 300]); }

  function mostrar(clase, icono, titulo, detalle) {
    var r = $('resultado');
    r.className = 'resultado ' + clase;
    r.innerHTML = '<div class="icono"></div><div class="titulo"></div><div class="detalle"></div>';
    r.querySelector('.icono').textContent = icono;
    r.querySelector('.titulo').textContent = titulo;
    r.querySelector('.detalle').textContent = detalle || '';
  }

  // ---------------------------------------------------------------- GPS
  if (navigator.geolocation) {
    navigator.geolocation.watchPosition(function (pos) {
      estado.gps = { lat: pos.coords.latitude.toFixed(6), lng: pos.coords.longitude.toFixed(6) };
    }, function () {}, { enableHighAccuracy: true, maximumAge: 60000, timeout: 20000 });
  }

  // ---------------------------------------------------------------- red
  function csrf() {
    var m = document.cookie.match(/(?:^|;\s*)csrftoken=([^;]+)/);
    return m ? decodeURIComponent(m[1]) : window.CSRF;
  }
  function post(url, datos) {
    return fetch(url, { method: 'POST', body: datos, credentials: 'same-origin', headers: { 'X-CSRFToken': csrf() } })
      .then(function (r) {
        var tipo = r.headers.get('Content-Type') || '';
        if (r.status === 403) throw new Error('permiso');
        if (r.redirected || tipo.indexOf('application/json') < 0) throw new Error('sesion');
        return r.json();
      });
  }

  // ---------------------------------------------------------------- lista corta (también sirve sin señal)
  function guardarLocal(clave, valor) { try { localStorage.setItem(clave, JSON.stringify(valor)); } catch (e) {} }
  function leerLocal(clave, defecto) {
    try { return JSON.parse(localStorage.getItem(clave) || 'null') || defecto; } catch (e) { return defecto; }
  }
  function cargarLista(paso) {
    if (!navigator.onLine) return;
    fetch(D.urls.pendientes + '?paso=' + encodeURIComponent(paso), { credentials: 'same-origin' })
      .then(function (r) { return r.json(); })
      .then(function (j) { guardarLocal('ol-lista-' + paso, j.items || []); })
      .catch(function () {});
  }
  $('ver-lista').addEventListener('click', function () {
    detenerCamara();
    var items = leerLocal('ol-lista-' + estado.paso, []);
    var cont = $('lista'); cont.innerHTML = '';
    if (!items.length) {
      var p = document.createElement('p'); p.textContent = 'No hay pendientes en la lista.'; cont.appendChild(p);
    }
    items.forEach(function (it) {
      var b = document.createElement('button');
      b.className = 'item'; b.type = 'button';
      b.textContent = it.titulo;
      var s = document.createElement('small'); s.textContent = it.detalle; b.appendChild(s);
      b.addEventListener('click', function () { leido('', it.id, it); });
      cont.appendChild(b);
    });
    ir('p-lista');
  });

  // ---------------------------------------------------------------- cámara y QR
  var flujo = null, buscando = false, detector = null;
  if ('BarcodeDetector' in window) {
    try { detector = new window.BarcodeDetector({ formats: ['qr_code'] }); } catch (e) { detector = null; }
  }
  function detenerCamara() {
    buscando = false;
    if (flujo) { flujo.getTracks().forEach(function (t) { t.stop(); }); flujo = null; }
    $('video').classList.add('oculto'); $('detener').classList.add('oculto'); $('escanear').classList.remove('oculto');
  }
  $('detener').addEventListener('click', detenerCamara);
  $('escanear').addEventListener('click', function () {
    try { audio = audio || new (window.AudioContext || window.webkitAudioContext)(); } catch (e) {}
    if (!navigator.mediaDevices || !navigator.mediaDevices.getUserMedia) { $('foto-qr').click(); return; }
    navigator.mediaDevices.getUserMedia({ video: { facingMode: 'environment' }, audio: false }).then(function (s) {
      flujo = s; var v = $('video'); v.srcObject = s; v.classList.remove('oculto');
      $('detener').classList.remove('oculto'); $('escanear').classList.add('oculto');
      v.play(); buscando = true; buscar();
    }).catch(function () { $('foto-qr').click(); });  // sin permiso o sin HTTPS: foto del código
  });
  var lienzo = document.createElement('canvas');
  function decodificar(fuente, ancho, alto) {
    if (detector) {
      return detector.detect(fuente).then(function (r) { return r.length ? r[0].rawValue : decodificarJs(fuente, ancho, alto); })
        .catch(function () { return decodificarJs(fuente, ancho, alto); });
    }
    return Promise.resolve(decodificarJs(fuente, ancho, alto));
  }
  function decodificarJs(fuente, ancho, alto) {
    if (!window.jsQR || !ancho) return null;
    var esc = Math.min(1, 1000 / Math.max(ancho, alto));
    lienzo.width = Math.round(ancho * esc); lienzo.height = Math.round(alto * esc);
    var ctx = lienzo.getContext('2d'); ctx.drawImage(fuente, 0, 0, lienzo.width, lienzo.height);
    var d = ctx.getImageData(0, 0, lienzo.width, lienzo.height), r = window.jsQR(d.data, d.width, d.height);
    return r ? r.data : null;
  }
  function buscar() {
    if (!buscando) return;
    var v = $('video');
    if (v.readyState < 2) { setTimeout(buscar, 200); return; }
    decodificar(v, v.videoWidth, v.videoHeight).then(function (texto) {
      if (texto) { detenerCamara(); leido(texto, null, null); } else setTimeout(buscar, 250);
    });
  }
  $('foto-qr').addEventListener('change', function () {
    var f = this.files[0]; this.value = '';
    if (!f) return;
    var img = new Image(), url = URL.createObjectURL(f);
    img.onload = function () {
      decodificar(img, img.naturalWidth, img.naturalHeight).then(function (texto) {
        URL.revokeObjectURL(url);
        if (texto) leido(texto, null, null);
        else { mal(); mostrar('mal', '✗', 'No se pudo leer el código', 'Acérquese más o elija la guía de la lista.'); prepararResultado(false); }
      });
    };
    img.src = url;
  });

  // ---------------------------------------------------------------- después de leer: verde o rojo
  function prepararResultado(ok) {
    var pide = ok && estado.info || {};
    $('pide-foto').classList.toggle('oculto', !pide.foto);
    $('pide-dni').classList.toggle('oculto', !pide.dni);
    $('confirmar').classList.toggle('oculto', !ok);
    $('tomar-foto').textContent = pide.foto || 'Tomar foto';
    estado.foto = null; $('vista-foto').style.display = 'none'; $('dni').value = ''; limpiarFirma();
    ir('p-resultado');
    if (pide.foto) setTimeout(function () { try { $('foto').click(); } catch (e) {} }, 400);  // se abre sola
  }

  function coincideLocal(qr, item) {
    if (!item) return null;
    var lista = leerLocal('ol-lista-' + estado.paso, []);
    return lista.filter(function (it) {
      return (item && it.id === item.id) || (qr && (it.qr === qr || (it.serie && it.numero &&
        qr.toUpperCase().indexOf(it.serie + '-') >= 0 && qr.indexOf(it.numero) >= 0)));
    })[0] || null;
  }

  function leido(qr, id, item) {
    estado.qr = qr || ''; estado.id = id; estado.info = null;
    var f = new FormData(); f.append('paso', estado.paso); f.append('qr', estado.qr); if (id) f.append('id', id);
    if (!navigator.onLine) return sinSenal(qr, item);
    post(D.urls.verificar, f).then(function (j) {
      if (j.ok) {
        estado.info = j; bien(); mostrar('ok', '✓', j.titulo, j.detalle); prepararResultado(true);
      } else {
        mal(); mostrar(j.repetida ? 'amb' : 'mal', j.repetida ? '!' : '✗', j.mensaje, ''); prepararResultado(false);
      }
    }).catch(function (e) {
      if (e.message === 'sesion') { mal(); mostrar('mal', '✗', 'Inicie sesión nuevamente', ''); prepararResultado(false); }
      else sinSenal(qr, item);
    });
  }

  function sinSenal(qr, item) {
    var conocido = item || coincideLocal(qr, { id: null });
    var pasoFoto = D.pide_foto[estado.paso] || '';
    estado.info = { foto: pasoFoto, dni: estado.paso === 'ENTREGA' };
    if (conocido) { bien(); mostrar('ok', '✓', conocido.titulo, conocido.detalle + ' · sin señal: se enviará después'); }
    else { tono([660], 0.15); mostrar('amb', '?', 'Sin señal', 'Se guardará en el teléfono y se revisará al volver la conexión.'); }
    prepararResultado(true);
  }

  $('otra-vez').addEventListener('click', function () { ir('p-escanear'); });

  // ---------------------------------------------------------------- foto (reducida a menos de 1 MB)
  $('tomar-foto').addEventListener('click', function () { $('foto').click(); });
  $('foto').addEventListener('change', function () {
    var f = this.files[0]; this.value = '';
    if (!f) return;
    reducir(f).then(function (blob) {
      estado.foto = blob;
      var v = $('vista-foto'); v.src = URL.createObjectURL(blob); v.style.display = 'block';
      $('tomar-foto').textContent = 'Tomar otra foto';
      bien();
    });
  });
  function reducir(archivo) {
    return new Promise(function (ok) {
      var img = new Image(), url = URL.createObjectURL(archivo);
      img.onload = function () {
        var esc = Math.min(1, MAX_LADO / Math.max(img.naturalWidth, img.naturalHeight));
        var c = document.createElement('canvas');
        c.width = Math.round(img.naturalWidth * esc); c.height = Math.round(img.naturalHeight * esc);
        c.getContext('2d').drawImage(img, 0, 0, c.width, c.height);
        URL.revokeObjectURL(url);
        c.toBlob(function (b) { ok(b || archivo); }, 'image/jpeg', 0.8);
      };
      img.onerror = function () { ok(archivo); };
      img.src = url;
    });
  }

  // ---------------------------------------------------------------- firma en pantalla
  var firma = $('firma'), fctx = firma.getContext('2d'), trazando = false, firmado = false;
  function ajustarFirma() {
    var r = firma.getBoundingClientRect();
    if (!r.width) return;
    firma.width = Math.round(r.width); firma.height = Math.round(r.height);
    fctx.lineWidth = 3; fctx.lineCap = 'round'; fctx.strokeStyle = '#000';
  }
  function punto(e) { var r = firma.getBoundingClientRect(); return [e.clientX - r.left, e.clientY - r.top]; }
  firma.addEventListener('pointerdown', function (e) {
    if (firma.width !== Math.round(firma.getBoundingClientRect().width)) ajustarFirma();
    trazando = true; firmado = true; var p = punto(e); fctx.beginPath(); fctx.moveTo(p[0], p[1]);
    firma.setPointerCapture(e.pointerId);
  });
  firma.addEventListener('pointermove', function (e) {
    if (!trazando) return; var p = punto(e); fctx.lineTo(p[0], p[1]); fctx.stroke();
  });
  firma.addEventListener('pointerup', function () { trazando = false; });
  function limpiarFirma() { ajustarFirma(); fctx.clearRect(0, 0, firma.width, firma.height); firmado = false; }
  $('borrar-firma').addEventListener('click', limpiarFirma);

  // ---------------------------------------------------------------- confirmar (con señal o en cola)
  $('confirmar').addEventListener('click', function () {
    var info = estado.info || {};
    if (info.foto && !estado.foto) { mal(); $('foto').click(); return; }
    var dni = $('dni').value.replace(/\D/g, '');
    if (info.dni && dni.length !== 8) { mal(); $('dni').focus(); return; }
    var registro = {
      id_local: String(Date.now()) + Math.random().toString(16).slice(2, 8), paso: estado.paso, qr: estado.qr,
      id: estado.id || '', dni: dni, firma: info.dni && firmado ? firma.toDataURL('image/png') : '',
      latitud: estado.gps ? estado.gps.lat : '', longitud: estado.gps ? estado.gps.lng : '',
      cuando: new Date().toISOString(), foto: estado.foto || null, titulo: $('resultado').querySelector('.titulo').textContent
    };
    $('confirmar').disabled = true;
    enviar(registro).then(function (j) {
      $('confirmar').disabled = false;
      if (j.ok) { bien(); mostrar('ok', '✓', 'Listo', j.mensaje); }
      else { mal(); mostrar('mal', '✗', j.mensaje, ''); }
      despues(j.ok);
    }).catch(function () {
      encolar(registro).then(function () {
        $('confirmar').disabled = false; bien();
        mostrar('amb', '✓', 'Guardado en el teléfono', 'Se enviará solo cuando vuelva la señal.');
        despues(true); pintarRed();
      });
    });
  });
  function despues(ok) {
    $('pide-foto').classList.add('oculto'); $('pide-dni').classList.add('oculto'); $('confirmar').classList.add('oculto');
    $('otra-vez').textContent = ok ? 'Escanear la siguiente' : 'Volver a escanear';
    cargarLista(estado.paso);
  }

  function formulario(r) {
    var f = new FormData();
    ['id_local', 'paso', 'qr', 'id', 'dni', 'firma', 'latitud', 'longitud', 'cuando'].forEach(function (k) { f.append(k, r[k] || ''); });
    if (r.foto) f.append('foto', r.foto, 'foto.jpg');
    return f;
  }
  function enviar(r) {
    if (!navigator.onLine) return Promise.reject(new Error('sin-senal'));
    return post(D.urls.registrar, formulario(r));
  }

  // ---------------------------------------------------------------- cola sin señal (IndexedDB)
  function base() {
    return new Promise(function (ok, error) {
      var r = indexedDB.open('ceiba-operador', 1);
      r.onupgradeneeded = function () { r.result.createObjectStore('cola', { keyPath: 'id_local' }); };
      r.onsuccess = function () { ok(r.result); }; r.onerror = function () { error(r.error); };
    });
  }
  function tx(modo, fn) {
    return base().then(function (db) {
      return new Promise(function (ok, error) {
        var t = db.transaction('cola', modo), res = fn(t.objectStore('cola'));
        t.oncomplete = function () { ok(res && res.result); }; t.onerror = function () { error(t.error); };
      });
    });
  }
  function encolar(r) { return tx('readwrite', function (s) { return s.put(r); }); }
  function pendientesCola() { return tx('readonly', function (s) { return s.getAll(); }).then(function (x) { return x || []; }); }
  function quitar(id) { return tx('readwrite', function (s) { return s.delete(id); }); }

  var sincronizando = false;
  function sincronizar() {
    if (sincronizando || !navigator.onLine) return;
    sincronizando = true;
    pendientesCola().then(function (lista) {
      return lista.reduce(function (p, r) {
        return p.then(function () {
          return post(D.urls.registrar, formulario(r)).then(function (j) {
            if (!j.ok) problema(r.titulo + ': ' + j.mensaje);
            return quitar(r.id_local);
          }).catch(function () { /* sigue en la cola */ });
        });
      }, Promise.resolve());
    }).catch(function () {}).then(function () { sincronizando = false; pintarRed(); });
  }
  function problema(texto) {
    var lista = leerLocal('ol-problemas', []); lista.unshift(texto); guardarLocal('ol-problemas', lista.slice(0, 10));
    pintarProblemas();
  }
  function pintarProblemas() {
    var cont = $('problemas'); cont.innerHTML = '';
    leerLocal('ol-problemas', []).forEach(function (t) {
      var d = document.createElement('div'); d.className = 'resultado mal';
      d.innerHTML = '<div class="detalle"></div>'; d.firstChild.textContent = t; cont.appendChild(d);
    });
    if (cont.childNodes.length) {
      var b = document.createElement('button'); b.className = 'enlace'; b.type = 'button'; b.textContent = 'Ya lo revisé';
      b.addEventListener('click', function () { guardarLocal('ol-problemas', []); pintarProblemas(); });
      cont.appendChild(b);
    }
  }
  function pintarRed() {
    pendientesCola().then(function (lista) {
      var red = $('red');
      red.classList.toggle('sin', !navigator.onLine || lista.length > 0);
      red.textContent = (navigator.onLine ? 'En línea' : 'Sin señal') + (lista.length ? ' · ' + lista.length + ' por enviar' : '');
    }).catch(function () {});
  }
  window.addEventListener('online', function () { pintarRed(); sincronizar(); });
  window.addEventListener('offline', pintarRed);
  setInterval(sincronizar, 30000);
  pintarRed(); pintarProblemas(); sincronizar();

  // ---------------------------------------------------------------- v1.117.16: punto de partida de la ruta (su casa)
  var botonSalida = $('mi-salida');
  if (botonSalida) {
    botonSalida.addEventListener('click', function () {
      var msg = $('mi-salida-msg');
      if (!navigator.geolocation) { msg.textContent = 'Este celular no da la ubicación.'; return; }
      msg.textContent = 'Buscando su ubicación…';
      navigator.geolocation.getCurrentPosition(function (pos) {
        var f = new FormData();
        f.append('latitud', pos.coords.latitude.toFixed(6));
        f.append('longitud', pos.coords.longitude.toFixed(6));
        post(D.urls.salida, f).then(function (r) { msg.textContent = r.mensaje; })
          .catch(function () { msg.textContent = 'No se pudo guardar: revise la señal e intente otra vez.'; });
      }, function () { msg.textContent = 'Active la ubicación (GPS) del celular e intente otra vez.'; },
      { enableHighAccuracy: true, maximumAge: 0, timeout: 20000 });
    });
  }
})();

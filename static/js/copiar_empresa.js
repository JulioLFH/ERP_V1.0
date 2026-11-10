// REQ-01: las casillas de «Qué copiar» se habilitan al elegir una empresa modelo
document.querySelectorAll('.js-modelo').forEach((select) => {
  const caja = select.parentElement.querySelector('.js-copiar');
  if (!caja) return;
  const marcar = (valor) => caja.querySelectorAll('input[name="copiar"]').forEach((c) => { c.checked = valor; });
  const actualizar = () => { caja.disabled = !select.value; };
  select.addEventListener('change', actualizar);
  caja.querySelector('.js-copiar-todo').addEventListener('click', (e) => { e.preventDefault(); marcar(true); });
  caja.querySelector('.js-copiar-nada').addEventListener('click', (e) => { e.preventDefault(); marcar(false); });
  actualizar();
});

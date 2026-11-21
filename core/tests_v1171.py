"""v1.117.1: Ajustes › Empresa ya no muestra el token de Decolecta (va en el panel de servicios) ni las credenciales de
SUNAT y del SIRE, y Facturación electrónica tampoco el usuario SOL secundario ni las credenciales de guías: todo eso
está solo en Ajustes › Credenciales SUNAT y SIRE. Guardar esas pantallas no borra lo que ya estaba registrado."""
from django.forms.models import model_to_dict
from django.urls import reverse

from .cifrado import cifrar, descifrar
from .forms import EmpresaForm, FacturacionConfigForm
from .models import Empresa, FacturacionConfig
from .tests_v152 import Base

OCULTOS = ('token_tipo_cambio', 'sunat_client_id', 'sunat_client_secret', 'sire_client_id', 'sire_client_secret',
           'sire_usuario_sol', 'sire_clave_nueva', 'sire_quitar_clave')


class CredencialesFueraDeEmpresaTest(Base):
    def test_empresa_no_muestra_ni_borra_las_credenciales(self):
        Empresa.objects.update(token_tipo_cambio='tok-empresa', sunat_client_id='cid-sunat',
                               sunat_client_secret='sec-sunat', sire_client_id='cid-sire',
                               sire_client_secret='sec-sire', sire_usuario_sol='CONTA01',
                               sire_clave_sol=cifrar('ClaveSire'))
        e = Empresa.actual()
        self.assertFalse(set(OCULTOS) & set(EmpresaForm(instance=e).fields))
        datos = {k: v for k, v in model_to_dict(e).items() if v is not None and k not in OCULTOS}
        datos.update(direccion=e.direccion or 'Av. Lima 1', email=e.email or 'a@a.pe', razon_social='OTRO NOMBRE')
        f = EmpresaForm(datos, instance=e)
        self.assertTrue(f.is_valid(), f.errors)
        f.save()
        e.refresh_from_db()
        self.assertEqual(e.razon_social, 'OTRO NOMBRE')
        self.assertEqual((e.token_tipo_cambio, e.sunat_client_id, e.sunat_client_secret, e.sire_client_id,
                          e.sire_client_secret, e.sire_usuario_sol, descifrar(e.sire_clave_sol)),
                         ('tok-empresa', 'cid-sunat', 'sec-sunat', 'cid-sire', 'sec-sire', 'CONTA01', 'ClaveSire'))

    def test_facturacion_enlaza_a_credenciales_y_no_borra_el_usuario_sol(self):
        cfg = FacturacionConfig.actual()
        FacturacionConfig.objects.filter(pk=cfg.pk).update(sol_usuario='FACTURA1', sol_clave=cifrar('ClaveSol'),
                                                           gre_client_id='gre-id', gre_client_secret=cifrar('gre'))
        r = self.client.get(reverse('facturacion'))
        self.assertContains(r, reverse('credenciales_sunat'))
        for campo in ('sol_usuario', 'sol_clave_nueva', 'gre_client_id', 'gre_client_secret_nuevo'):
            self.assertNotIn(campo, FacturacionConfigForm().fields)
            self.assertNotContains(r, f'name="{campo}"')
        r = self.client.post(reverse('facturacion'), {'proveedor': 'SUNAT', 'entorno': 'BETA'})
        self.assertRedirects(r, reverse('facturacion'), fetch_redirect_response=False)
        cfg.refresh_from_db()
        self.assertEqual((cfg.sol_usuario, descifrar(cfg.sol_clave), cfg.gre_client_id, descifrar(cfg.gre_client_secret)),
                         ('FACTURA1', 'ClaveSol', 'gre-id', 'gre'))

    def test_credenciales_reune_sunat_sire_y_guias(self):
        r = self.client.get(reverse('credenciales_sunat'))
        for campo in ('sol_usuario', 'sol_clave', 'sire_usuario_sol', 'sire_clave_sol', 'sire_client_id',
                      'sire_client_secret', 'sunat_client_id', 'sunat_client_secret', 'gre_client_id',
                      'gre_client_secret'):
            self.assertContains(r, f'name="{campo}"')
        self.assertNotContains(r, 'token_tipo_cambio')

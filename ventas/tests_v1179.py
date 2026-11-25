"""v1.117.9: «Otros documentos de referencia» en la venta (orden de compra, contrato…): salen en la «Referencia» del
impreso junto a las guías, en el detalle y se pueden escribir en el formulario. No cambian el XML a SUNAT."""
from core import sunat_directo

from .models import Venta
from . import tests_v1174  # (sin importar la clase: que no se corra dos veces)

from django.urls import reverse


class OtrosDocumentosReferenciaTest(tests_v1174.FacturaTransporteTest):
    def test_guias_y_otros_documentos_en_el_impreso(self):
        v = self.emitir(otros_documentos='OC 4500012345;  Contrato 12-2026 , ')
        self.assertEqual(v.otros_citados, ['OC 4500012345', 'Contrato 12-2026'])
        self.assertEqual(v.referencias_impreso, ['GRE-EG03-00000035', 'GRE-EG03-00000040', 'GRE-T005-02721882',
                                                 'OC 4500012345', 'Contrato 12-2026'])
        r = self.client.get(reverse('ventas:imprimir', args=[v.pk]))
        self.assertContains(r, 'GRE-EG03-00000035, GRE-EG03-00000040, GRE-T005-02721882, OC 4500012345, '
                               'Contrato 12-2026')
        d = self.client.get(reverse('ventas:detalle', args=[v.pk]))
        self.assertContains(d, 'Otros documentos (referencia)')
        self.assertContains(d, 'OC 4500012345, Contrato 12-2026')

    def test_solo_otros_documentos_sin_guias(self):
        v = self.emitir(guias_referencia='', otros_documentos='Orden de servicio OS-2026-15')
        self.assertEqual(v.guias_citadas, [])  # «2026-15» no se toma como guía: es otro campo
        r = self.client.get(reverse('ventas:imprimir', args=[v.pk]))
        self.assertContains(r, '<td class="text-muted">Referencia</td><td colspan="3">Orden de servicio OS-2026-15</td>',
                            html=False)
        ns = {'cac': 'urn:oasis:names:specification:ubl:schema:xsd:CommonAggregateComponents-2'}
        self.assertEqual(sunat_directo.xml_comprobante(v).findall('cac:DespatchDocumentReference', namespaces=ns), [])

    def test_sin_referencias_no_sale_la_fila(self):
        v = self.emitir(guias_referencia='')
        self.assertEqual(v.referencias_impreso, [])
        self.assertNotContains(self.client.get(reverse('ventas:imprimir', args=[v.pk])),
                               '<td class="text-muted">Referencia</td>')

    def test_campo_en_el_formulario(self):
        r = self.client.get(reverse('ventas:nuevo'))
        self.assertContains(r, 'name="otros_documentos"')
        self.assertContains(r, 'Otros documentos de referencia')
        self.assertEqual(Venta._meta.get_field('otros_documentos').max_length, 250)

import io
from decimal import Decimal
from unittest.mock import patch, MagicMock
from django.test import TestCase, Client
from django.utils import timezone
from facturacion.dian_async import transmitir_factura_binapps, encolar_transmision_dian
from impresion.docx_engine import format_cop, renderizar_factura_docx

class DianAndDocxTestCase(TestCase):
    databases = {'qcluster_db', 'default'}

    def test_format_cop_decimal(self):
        """Verifica que format_cop transforme valores usando estrictamente decimal.Decimal en formato COP."""
        val1 = Decimal('1250000.50')
        self.assertEqual(format_cop(val1), '$ 1.250.000,50')

        val2 = Decimal('0.00')
        self.assertEqual(format_cop(val2), '$ 0,00')

    @patch('services.binapps_client.BinappsClient.transmit_document')
    @patch('services.legacy_repository.get_doors_invoice_data')
    def test_transmitir_factura_binapps_exito(self, mock_get_invoice_data, mock_transmit):
        """Prueba el flujo de transmisión asíncrona a Binapps y actualización en CT_VENTAS_FEL."""
        mock_get_invoice_data.return_value = (
            {
                'ID_DOCUMENTO': '99901',
                'NUM_DOCUMENTO': 'FES99901',
                'FCH_DOCUMENTO': '2026-07-29T11:00:00',
                'NIT_TERCERO': '900123456',
                'RAZON_SOCIAL': 'CLIENTE PRUEBA SAS',
                'REGIMEN': '2',
                'PLAZO_PAGO': 0,
                'TOT_MERCANCIA': Decimal('100000.00'),
                'TOT_DOCUMENTO': Decimal('119000.00')
            },
            [
                {
                    'ID_ARTICULO': 'ART-01',
                    'NOM_ARTICULO': 'Articulo Prueba',
                    'CANTIDAD': Decimal('1.00'),
                    'VLR_UNITARIO': Decimal('100000.00'),
                    'VLR_IVA_LINEA': Decimal('19000.00'),
                    'PORC_IVA': Decimal('19.00')
                }
            ],
            {}
        )

        mock_transmit.return_value = {
            'State': '30',
            'Status': 'APROBADO',
            'Message': 'Documento procesado correctamente',
            'Cufe': 'CUFE_MOCK_1234567890ABCDEF'
        }

        result = transmitir_factura_binapps('99901')
        self.assertTrue(result['success'])
        self.assertEqual(result['api_state'], '30')
        self.assertEqual(result['cufe'], 'CUFE_MOCK_1234567890ABCDEF')

    @patch('impresion.docx_engine.generar_contexto_factura_docx')
    def test_renderizar_factura_docx_stream(self, mock_generar_contexto):
        """Prueba que el motor renderizar_factura_docx produzca un BytesIO válido del archivo Word."""
        mock_generar_contexto.return_value = {
            'doc': {
                'id_documento': '99901',
                'num_documento': 'FES99901',
                'fch_documento': '2026-07-29 11:00:00',
                'resolucion': 'Res. 18760000001',
                'cufe': 'CUFE_MOCK_TEST'
            },
            'empresa': {'nom_empresa': 'NORTH TEST', 'nit': '890.501.170-1', 'direccion': 'Cúcuta'},
            'tercero': {'nit': '900123456', 'nom_tercero': 'CLIENTE TEST', 'direccion': 'Calle 1', 'telefono': '55555'},
            'vendedor': {'nombre': 'Vendedor 1'},
            'items': [
                {
                    'item_no': 1,
                    'referencia': 'REF-1',
                    'nom_articulo': 'Producto Test',
                    'cantidad': '1.00',
                    'vlr_unitario': '$ 100.000,00',
                    'tot_linea': '$ 119.000,00'
                }
            ],
            'totals': {
                'tot_mercancia': '$ 100.000,00',
                'tot_iva': '$ 19.000,00',
                'tot_retefuente': '$ 0,00',
                'tot_documento': '$ 119.000,00'
            },
            'qr_code': 'MOCK_QR_INLINE_IMAGE'
        }

        stream = renderizar_factura_docx('99901')
        self.assertIsInstance(stream, io.BytesIO)
        self.assertGreater(len(stream.getvalue()), 0)

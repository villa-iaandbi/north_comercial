from decimal import Decimal
from unittest.mock import patch
from django.test import TestCase, Client
from django.utils import timezone
from pos.models import PosTurno, PosTicketHeader, PosTicketDetail
from pos.pos_consolidator import consolidar_cierre_z
from impresion.docx_engine import renderizar_factura_docx

class IntegracionFlujoPosDianDocxTestCase(TestCase):
    databases = {'qcluster_db', 'default'}

    def setUp(self):
        self.client = Client()
        PosTicketHeader.objects.filter(ticket_id='TICKET-INTEG-001').delete()
        
        self.turno = PosTurno.objects.create(
            caja_id='CAJA-01',
            usuario='cajero_integ',
            base_economica=Decimal('150000.00'),
            estado='ABIERTO'
        )

        self.ticket = PosTicketHeader.objects.create(
            ticket_id='TICKET-INTEG-001',
            turno=self.turno,
            id_tercero='222222222222',
            nom_tercero='VENTAS MASIVAS (CONSUMIDOR FINAL)',
            fch_ticket=timezone.now(),
            tot_mercancia=Decimal('100000.00'),
            tot_iva=Decimal('19000.00'),
            tot_ticket=Decimal('119000.00'),
            descuento_promocion=Decimal('0.00'),
            pago_efectivo=Decimal('119000.00'),
            sync_status=True,
            consolidado_cierre=False
        )

        PosTicketDetail.objects.create(
            ticket=self.ticket,
            id_articulo='ART-INTEG-01',
            referencia='REF-INTEG-01',
            nom_articulo='Articulo de Prueba Integracion',
            cantidad=Decimal('2.00'),
            vlr_unitario=Decimal('50000.00'),
            porc_iva=Decimal('19.00'),
            vlr_iva=Decimal('19000.00'),
            tot_linea=Decimal('119000.00')
        )

    @patch('pos.pos_consolidator.get_system_parameter', return_value='01')
    @patch('facturacion.dian_async.async_task')
    @patch('django.db.connection.cursor')
    def test_flujo_completo_cierre_z_dian_y_docx(self, mock_cursor, mock_async_task, mock_sys_param):
        mock_async_task.return_value = 'task-uuid-q2-12345'
        
        cursor_instance = mock_cursor.return_value.__enter__.return_value
        cursor_instance.fetchone.side_effect = [
            (99001,), # SEC_DOCUMENTO.NEXTVAL para Cierre Z
            # Query header para docx_engine (Llamada 1)
            (
                '99001', '99001', timezone.now(), 119000.0, '01',
                '222222222222', 'VENTAS MASIVAS (CONSUMIDOR FINAL)',
                'ZONA INDUSTRIAL', '5551234', '54001',
                100000.0, 19000.0, 0.0, 119000.0, 'CUFE_INTEG_TEST_123'
            ),
            # Query header para docx_engine (Llamada 2 vía HTTP)
            (
                '99001', '99001', timezone.now(), 119000.0, '01',
                '222222222222', 'VENTAS MASIVAS (CONSUMIDOR FINAL)',
                'ZONA INDUSTRIAL', '5551234', '54001',
                100000.0, 19000.0, 0.0, 119000.0, 'CUFE_INTEG_TEST_123'
            )
        ]
        cursor_instance.fetchall.side_effect = [
            # query_items + ts_medio_pagos (Llamada 1)
            [('ART-INTEG-01', 'REF-INTEG-01', 'Articulo de Prueba Integracion', 2.0, 50000.0, 19000.0)],
            [('EFECTIVO', 119000.0)],
            # query_items + ts_medio_pagos (Llamada 2 vía HTTP)
            [('ART-INTEG-01', 'REF-INTEG-01', 'Articulo de Prueba Integracion', 2.0, 50000.0, 19000.0)],
            [('EFECTIVO', 119000.0)]
        ]
        cursor_instance.description = [
            ('id_documento',), ('num_documento',), ('fch_documento',), ('tot_documento',),
            ('id_vendedor',), ('id_tercero',), ('nom_tercero',), ('direccion',),
            ('telefono',), ('id_municipio_dian',), ('tot_mercancia',), ('tot_iva',),
            ('tot_retefuente',), ('vlr_venta',), ('cufe',)
        ]

        # 1. Ejecutar Cierre Z
        res_cierre = consolidar_cierre_z(self.turno.id_turno)
        self.assertEqual(res_cierre['status'], 'success')
        self.assertEqual(res_cierre['id_documento'], '99001')
        self.assertEqual(res_cierre['dian_task_id'], 'task-uuid-q2-12345')

        # 2. Verificar que se haya llamado la orquestación DIAN
        mock_async_task.assert_called_once_with(
            'facturacion.dian_async.transmitir_factura_binapps',
            '99001'
        )

        # 3. Renderizar documento Word .docx
        docx_stream = renderizar_factura_docx('99001')
        self.assertIsNotNone(docx_stream)
        self.assertGreater(len(docx_stream.getvalue()), 1000)

        # 4. Probar Endpoint HTTP de descarga de .docx
        response = self.client.get('/impresion/descargar-docx/99001/')
        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            response['Content-Type'],
            'application/vnd.openxmlformats-officedocument.wordprocessingml.document'
        )

from decimal import Decimal
from unittest.mock import patch
from django.test import TestCase, Client
from django.utils import timezone
from pos.models import PosTurno, PosTicketHeader, PosTicketDetail

class ReportesTestCase(TestCase):
    databases = {'qcluster_db', 'default'}

    def setUp(self):
        self.client = Client()
        PosTicketHeader.objects.filter(ticket_id='TICKET-REP-001').delete()
        
        self.turno = PosTurno.objects.create(
            caja_id='CAJA-01',
            usuario='cajero_reportes',
            base_economica=Decimal('100000.00'),
            estado='ABIERTO'
        )
        self.ticket = PosTicketHeader.objects.create(
            ticket_id='TICKET-REP-001',
            turno=self.turno,
            id_tercero='900111222',
            nom_tercero='CLIENTE REPORTES S.A.S.',
            fch_ticket=timezone.now(),
            tot_mercancia=Decimal('200000.00'),
            tot_iva=Decimal('38000.00'),
            tot_ticket=Decimal('238000.00'),
            descuento_promocion=Decimal('0.00'),
            pago_efectivo=Decimal('238000.00'),
            sync_status=True,
            consolidado_cierre=False
        )

    @patch('django.db.connection.cursor')
    def test_dashboard_view_http(self, mock_cursor):
        cursor_instance = mock_cursor.return_value.__enter__.return_value
        cursor_instance.fetchone.return_value = (500000.0, 10, 8)
        cursor_instance.fetchall.side_effect = [
            # query_agrupada
            [('PROV TEST', 'FAM TEST', 'LIN TEST', 5.0, 500000.0)],
            # query_chart
            [('29/07', 500000.0)]
        ]

        response = self.client.get('/reportes/dashboard/?periodo=mes')
        self.assertEqual(response.status_code, 200)
        self.assertIn('total_ventas', response.context)
        self.assertEqual(response.context['cant_pedidos'], 10)

    @patch('django.db.connection.cursor')
    def test_cartera_view_http(self, mock_cursor):
        cursor_instance = mock_cursor.return_value.__enter__.return_value
        cursor_instance.fetchone.side_effect = [
            # Edades de cartera
            (5000000.0, 2000000.0, 1000000.0, 1000000.0, 500000.0, 500000.0),
            # Recaudos del día
            (1500000.0,)
        ]
        cursor_instance.fetchall.return_value = [
            ('900111222', 'CLIENTE REPORTES S.A.S.', 5000000.0, 2000000.0, 1000000.0, 1000000.0, 500000.0, 500000.0)
        ]

        response = self.client.get('/reportes/cartera/')
        self.assertEqual(response.status_code, 200)
        self.assertIn('total_cartera', response.context)
        self.assertEqual(response.context['recaudos_dia'], Decimal('1500000.0'))

    def test_cierre_caja_view_http(self):
        # Base 100.000 + Efectivo 238.000 = Esperado 338.000
        # Declarado 338.000 -> Descuadre 0.00
        response = self.client.get(f'/reportes/cierre-caja/?turno_id={self.turno.id_turno}&efectivo_declarado=338000')
        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.context['turno_encontrado'])
        self.assertEqual(response.context['efectivo_esperado'], Decimal('338000.00'))
        self.assertEqual(response.context['descuadre'], Decimal('0.00'))
        self.assertTrue(response.context['es_cuadrado'])

        # Declarado 330.000 -> Descuadre -8.000 (Faltante)
        res_descuadre = self.client.get(f'/reportes/cierre-caja/?turno_id={self.turno.id_turno}&efectivo_declarado=330000')
        self.assertEqual(res_descuadre.context['descuadre'], Decimal('-8000.00'))
        self.assertFalse(res_descuadre.context['es_cuadrado'])

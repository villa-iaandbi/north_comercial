from decimal import Decimal
from django.test import TestCase, Client
from django.utils import timezone
from pos.models import PosTurno, PosTicketHeader, PosTicketDetail
from pos.pos_consolidator import consolidar_cierre_z

class PosModuleTestCase(TestCase):
    databases = {'qcluster_db'}

    def setUp(self):
        self.client = Client()
        self.turno = PosTurno.objects.create(
            caja_id='CAJA-01',
            usuario='cajero_test',
            base_economica=Decimal('150000.00'),
            estado='ABIERTO'
        )

    def test_apertura_y_status_turno(self):
        response = self.client.get('/pos/api/shift/status/?caja_id=CAJA-01')
        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertTrue(data['turno_abierto'])
        self.assertEqual(data['turno']['base_economica'], 150000.0)

    def test_sync_tickets_multiples_metodos_pago(self):
        ticket_payload = {
            'caja_id': 'CAJA-01',
            'tickets': [
                {
                    'ticket_id': 'TICKET-TEST-001',
                    'id_tercero': '222222222222',
                    'nom_tercero': 'VENTAS MASIVAS (CONSUMIDOR FINAL)',
                    'fch_ticket': timezone.now().isoformat(),
                    'tot_mercancia': '100000.00',
                    'tot_iva': '19000.00',
                    'tot_ticket': '119000.00',
                    'pago_efectivo': '50000.00',
                    'pago_tarjeta': '69000.00',
                    'pago_transferencia': '0.00',
                    'cambio': '0.00',
                    'items': [
                        {
                            'id_articulo': 'ART-101',
                            'referencia': 'REF-A',
                            'nom_articulo': 'Producto A Test',
                            'cantidad': '2.00',
                            'vlr_unitario': '50000.00',
                            'porc_descuento': '0.00',
                            'porc_iva': '19.00',
                            'vlr_iva': '19000.00',
                            'tot_linea': '119000.00'
                        }
                    ]
                },
                {
                    'ticket_id': 'TICKET-TEST-002',
                    'id_tercero': '222222222222',
                    'nom_tercero': 'VENTAS MASIVAS (CONSUMIDOR FINAL)',
                    'fch_ticket': timezone.now().isoformat(),
                    'tot_mercancia': '50000.00',
                    'tot_iva': '9500.00',
                    'tot_ticket': '59500.00',
                    'pago_efectivo': '60000.00',
                    'pago_tarjeta': '0.00',
                    'pago_transferencia': '0.00',
                    'cambio': '500.00',
                    'items': [
                        {
                            'id_articulo': 'ART-101', # Mismo artículo para probar consolidación por ID_ARTICULO
                            'referencia': 'REF-A',
                            'nom_articulo': 'Producto A Test',
                            'cantidad': '1.00',
                            'vlr_unitario': '50000.00',
                            'porc_descuento': '0.00',
                            'porc_iva': '19.00',
                            'vlr_iva': '9500.00',
                            'tot_linea': '59500.00'
                        }
                    ]
                }
            ]
        }
        
        response = self.client.post(
            '/pos/api/sync-tickets/',
            data=ticket_payload,
            content_type='application/json'
        )
        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertEqual(data['status'], 'success')
        self.assertEqual(len(data['synced_ids']), 2)

        # Verificar almacenamiento en SQLite local
        header1 = PosTicketHeader.objects.get(ticket_id='TICKET-TEST-001')
        self.assertEqual(header1.tot_ticket, Decimal('119000.00'))
        self.assertEqual(header1.pago_efectivo, Decimal('50000.00'))
        self.assertEqual(header1.pago_tarjeta, Decimal('69000.00'))
        self.assertEqual(header1.id_tercero, '222222222222')

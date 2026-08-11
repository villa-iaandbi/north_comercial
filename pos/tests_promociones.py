from decimal import Decimal
from django.test import TestCase, Client
from django.utils import timezone
from pos.models import (
    PosTurno, PosTicketHeader, PrPromocion, PrCondicion, PrAccion, PosPuntosCliente
)

class PromocionesFidelizacionTestCase(TestCase):
    databases = {'qcluster_db', 'default'}

    def setUp(self):
        self.client = Client()
        
        # Limpiar datos de prueba
        PrPromocion.objects.filter(nom_promocion='Descuento 10% Especial').delete()
        PosPuntosCliente.objects.filter(id_tercero='900999888').delete()
        PosTicketHeader.objects.filter(ticket_id='TICKET-FID-001').delete()

        self.turno = PosTurno.objects.create(
            caja_id='CAJA-01',
            usuario='cajero_promos',
            base_economica=Decimal('200000.00'),
            estado='ABIERTO'
        )

        # Crear una promoción de prueba: Descuento 10% por compra > $50.000
        self.promo = PrPromocion.objects.create(
            nom_promocion='Descuento 10% Especial',
            fch_inicio=timezone.now() - timezone.timedelta(days=1),
            fch_fin=timezone.now() + timezone.timedelta(days=30),
            activo=True,
            prioridad=10
        )
        PrCondicion.objects.create(
            promocion=self.promo,
            tipo_condicion='MONTO_MINIMO',
            valor_condicion='50000'
        )
        PrAccion.objects.create(
            promocion=self.promo,
            tipo_accion='DESCUENTO_PORC',
            valor_accion=Decimal('10.00')
        )

        # Crear saldo de puntos inicial para cliente de prueba
        PosPuntosCliente.objects.create(
            id_tercero='900999888',
            puntos_saldo=500,
            puntos_acumulados=500,
            puntos_redimidos=0
        )

    def test_api_promociones_activas(self):
        response = self.client.get('/pos/api/promociones/')
        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertEqual(data['status'], 'success')
        self.assertGreaterEqual(len(data['promociones']), 1)

    def test_api_puntos_saldo_y_redencion(self):
        # 1. Consulta de Saldo
        res_saldo = self.client.get('/pos/api/puntos/saldo/?id_tercero=900999888')
        self.assertEqual(res_saldo.status_code, 200)
        data_saldo = res_saldo.json()
        self.assertEqual(data_saldo['puntos_saldo'], 500)
        self.assertEqual(data_saldo['valor_cop'], 5000)

        # 2. Redención de Puntos
        payload_redimir = {
            'id_tercero': '900999888',
            'puntos_redimir': 200
        }
        res_redimir = self.client.post(
            '/pos/api/puntos/redimir/',
            data=payload_redimir,
            content_type='application/json'
        )
        self.assertEqual(res_redimir.status_code, 200)
        data_redimir = res_redimir.json()
        self.assertEqual(data_redimir['status'], 'success')
        self.assertEqual(data_redimir['nuevo_saldo_puntos'], 300)

    def test_sync_ticket_con_acumulacion_de_puntos(self):
        ticket_payload = {
            'caja_id': 'CAJA-01',
            'tickets': [
                {
                    'ticket_id': 'TICKET-FID-001',
                    'id_tercero': '900999888',
                    'nom_tercero': 'CLIENTE FIDELIZADO',
                    'fch_ticket': timezone.now().isoformat(),
                    'tot_mercancia': '100000.00',
                    'tot_iva': '19000.00',
                    'tot_ticket': '119000.00',
                    'descuento_promocion': '10000.00',
                    'pago_efectivo': '119000.00',
                    'puntos_ganados': 100,
                    'items': [
                        {
                            'id_articulo': 'ART-TEST',
                            'nom_articulo': 'Producto Fidel',
                            'cantidad': '1.00',
                            'vlr_unitario': '100000.00',
                            'vlr_iva': '19000.00',
                            'tot_linea': '119000.00'
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
        
        # Verificar que el cliente haya acumulado +100 puntos en la BD (500 + 100 = 600)
        puntos_obj = PosPuntosCliente.objects.get(id_tercero='900999888')
        self.assertEqual(puntos_obj.puntos_saldo, 600)

import os
import json
import logging
import datetime
from decimal import Decimal
from pathlib import Path
from zoneinfo import ZoneInfo
from django.conf import settings

logger = logging.getLogger(__name__)

BOGOTA_TZ = ZoneInfo("America/Bogota")

class InvoicePayloadBuilder:
    """
    Ensamblador de payloads para la facturación electrónica con Binapps.
    Actúa como un DTO, transformando la estructura legacy a la requerida por el API.
    Aplica registro de auditoría obligatoria almacenando el JSON final en el disco local.
    """

    @staticmethod
    def _get_current_timestamp() -> str:
        """Devuelve el timestamp de la transacción en hora de Colombia (ISO 8601)"""
        now = datetime.datetime.now(BOGOTA_TZ)
        return now.strftime("%Y-%m-%dT%H:%M:%S")

    @staticmethod
    def _save_payload_locally(id_documento: str, payload_dict: dict):
        """
        Guarda el objeto JSON de manera local como mecanismo de auditoría y respaldo.
        Los fallos de ES no bloquean la emisión principal.
        """
        try:
            # Resolución de la ruta dinámica
            base_dir = getattr(settings, 'BASE_DIR', Path(os.getcwd()))
            payloads_dir = getattr(settings, 'FEL_PAYLOADS_DIR', base_dir / 'logs' / 'fel_payloads')
            
            # Garantizar la existencia del directorio
            os.makedirs(payloads_dir, exist_ok=True)

            # Nombre de archivo basado en el ID y el timestamp
            timestamp_str = datetime.datetime.now(BOGOTA_TZ).strftime("%Y%m%d_%H%M%S")
            filename = f"FE_{id_documento}_{timestamp_str}.json"
            filepath = os.path.join(payloads_dir, filename)

            with open(filepath, 'w', encoding='utf-8') as json_file:
                json.dump(payload_dict, json_file, indent=4, ensure_ascii=False)

        except Exception as e:
            logger.error(f"Error Crítico No Bloqueante: No se pudo guardar el payload de auditoría local para {id_documento}: {str(e)}")

    @staticmethod
    def build_invoice_payload(header_data: dict, items_data: list, taxes_data: dict) -> dict:
        """
        Ensambla el diccionario estructurado de la factura.
        :param header_data: Diccionario con la cabecera transaccional y el tercero.
        :param items_data: Lista de diccionarios, representando el detalle (factura items).
        :param taxes_data: Diccionario con los totales exactos de impuestos desde base de datos.
        :return: Diccionario nativo (dict) del payload completo.
        """
        id_documento = header_data.get('ID_DOCUMENTO', 'SIN_ID')

        # 1. Régimen y Responsabilidad
        regimen = header_data.get('REGIMEN')
        if str(regimen) == '2':
            tax_level_code_list_name = '49'
            tax_tribute_code = 'ZZ'
        else:
            tax_level_code_list_name = '48'
            tax_tribute_code = '01'

        if str(regimen) == '3':
            fiscal_responsability = 'O-13'
        else:
            fiscal_responsability = 'R-99-PN'

        # 2. Medios de Pago
        plazo_pago = int(header_data.get('PLAZO_PAGO', 0))
        issue_date_str = header_data.get('FCH_DOCUMENTO', InvoicePayloadBuilder._get_current_timestamp())

        payment_type = 2 if plazo_pago > 0 else 1
        
        # Fecha de vencimiento si es a crédito
        payment_due_date = issue_date_str
        if payment_type == 2:
            try:
                base_dt = datetime.datetime.strptime(issue_date_str, "%Y-%m-%dT%H:%M:%S").replace(tzinfo=BOGOTA_TZ)
                due_dt = base_dt + datetime.timedelta(days=plazo_pago)
                payment_due_date = due_dt.strftime("%Y-%m-%dT%H:%M:%S")
            except ValueError:
                payment_due_date = issue_date_str

        # 3. DocumentItems
        document_items = []
        for idx, item in enumerate(items_data):
            
            cantidad = float(Decimal(str(item.get('CANTIDAD', 0))).quantize(Decimal('0.01'))) # Redondeado a 2 decimales
            vlr_unitario = float(item.get('VLR_UNITARIO', 0))

            # Impuestos por ítem (TaxesInformation)
            taxes_info = []
            
            # IVA
            if item.get('VLR_IVA_LINEA', 0) > 0:
                taxes_info.append({
                    "Id": 0,
                    "TaxEvidenceIndicator": False,
                    "TaxableAmount": round(float(vlr_unitario * cantidad), 2),
                    "TaxAmount": round(float(item.get('VLR_IVA_LINEA', 0)), 2),
                    "Percent": round(float(item.get('PORC_IVA', 0)), 2),
                    "BaseUnitMeasure": 0,
                    "PerUnitAmount": 0,
                    "TaxDetail": ""
                })

            # Impoconsumo
            if item.get('VLR_IMPOCONSUMO_LINEA', 0) > 0:
                taxes_info.append({
                    "Id": 0,
                    "TaxEvidenceIndicator": False,
                    "TaxableAmount": round(float(vlr_unitario * cantidad), 2),
                    "TaxAmount": round(float(item.get('VLR_IMPOCONSUMO_LINEA', 0)), 2),
                    "Percent": round(float(item.get('PORC_IMPOCONSUMO', 0)), 2),
                    "BaseUnitMeasure": 0,
                    "PerUnitAmount": 0,
                    "TaxDetail": ""
                })

            item_dict = {
                "ItemReference": item.get('ID_ARTICULO', 'ND'),
                "Name": item.get('NOM_ARTICULO', 'ND'),
                "Quantity": cantidad,
                "QuantityPackaging": int(cantidad),
                "QuantityPresentation": 0,
                "Price": vlr_unitario,
                "CustomUnitPrice1": 0,
                "CustomUnitPrice2": 0,
                "CustomUnitPrice3": 0,
                "CustomUnitPrice4": 0,
                "LineAllowanceTotal": round(vlr_unitario * cantidad, 2),
                "LineChargeTotal": 0,
                "LineTotalTaxes": float(item.get('VLR_IVA_LINEA', 0) + item.get('VLR_IMPOCONSUMO_LINEA', 0)),
                "LineTotal": round(vlr_unitario * cantidad, 2),
                "LineExtensionAmount": round(vlr_unitario * cantidad, 2),
                "MeasureUnitCode": "94",
                "FreeOFChargeIndicator": False,
                "LineNote": None,
                "AdditionalReference": None,
                "AdditionalProperty": None,
                "TaxesInformation": taxes_info,
                "AllowanceCharge": None,
                "Weight": 0.00,
                "PriceAmount": 0,
                "DiscountPercent": 0,
                "SpecialDiscountPercent": 0,
                "PresentationFactor": 1,
                "Presentation": "UNIDAD",
                "NetUnitPrice": vlr_unitario,
                "LineNumber": idx + 1
            }
            document_items.append(item_dict)

        # 4. TotalInvoiceTaxes (Totales de Impuestos de Cabecera Oficiales desde ERP)
        total_taxes = []
        
        # Agrupar impuestos de IVA desde las líneas para no perder decimales
        iva_taxes_by_percent = {}
        for item in items_data:
            iva_val = float(item.get('VLR_IVA_LINEA', 0))
            if iva_val > 0:
                percent = float(item.get('PORC_IVA', 0))
                base = float(item.get('CANTIDAD', 0)) * float(item.get('VLR_UNITARIO', 0))
                
                if percent not in iva_taxes_by_percent:
                    iva_taxes_by_percent[percent] = {'amount': 0.0, 'base': 0.0}
                
                iva_taxes_by_percent[percent]['amount'] += iva_val
                iva_taxes_by_percent[percent]['base'] += base

        tot_iva = 0.0
        for percent, vals in iva_taxes_by_percent.items():
            tot_iva += vals['amount']
            total_taxes.append({
                "TaxTributeCode": 0,
                "TaxEvidenceIndicator": False,
                "Percent": percent,
                "TaxAmount": float(vals['amount']),
                "TaxableAmount": float(vals['base']),
                "TaxDetail": None
            })

        # Agrupar INC desde ítems para no perder decimales exactos
        inc_taxes_by_percent = {}
        for item in items_data:
            inc_val = float(item.get('VLR_IMPOCONSUMO_LINEA', 0))
            if inc_val > 0:
                percent = float(item.get('PORC_IMPOCONSUMO', 0))
                base = float(item.get('CANTIDAD', 0)) * float(item.get('VLR_UNITARIO', 0))
                
                if percent not in inc_taxes_by_percent:
                    inc_taxes_by_percent[percent] = {'amount': 0.0, 'base': 0.0}
                    
                inc_taxes_by_percent[percent]['amount'] += inc_val
                inc_taxes_by_percent[percent]['base'] += base

        tot_inc = 0.0
        for percent, vals in inc_taxes_by_percent.items():
            tot_inc += vals['amount']
            total_taxes.append({
                "TaxTributeCode": 1,
                "TaxEvidenceIndicator": False,
                "Percent": percent,
                "TaxAmount": float(vals['amount']),
                "TaxableAmount": float(vals['base']),
                "TaxDetail": None
            })

        # RTE_COMPRAS (Desde taxes_data porque es de cabecera en el ERP)
        for key, tax_info in taxes_data.items():
            if key == 'RTE_COMPRAS':
                amount = float(tax_info.get('amount', 0))
                base = float(tax_info.get('base', 0))
                percent = float(tax_info.get('percent', 0))
                total_taxes.append({
                    "TaxTributeCode": 5,
                    "TaxEvidenceIndicator": True,
                    "Percent": percent,
                    "TaxAmount": amount,
                    "TaxableAmount": base,
                    "TaxDetail": None
                })

        # Base sucia
        tax_exclusive_amount = round(float(header_data.get('TOT_MERCANCIA', 0)), 2) # Subtotal Neto (Base)
        tax_inclusive_amount = round(tax_exclusive_amount + tot_iva + tot_inc, 2) # Subtotal + Impuestos
        payable_amount = float(header_data.get('TOT_DOCUMENTO', 0)) # Total facturado final a la cartera

        # Manejo estricto de issueDate
        issue_date_only = issue_date_str.split('T')[0] + "T00:00:00" if 'T' in issue_date_str else issue_date_str + "T00:00:00"

        # Separatar prefijo y consecutivo de NUM_DOCUMENTO (Ej: FES5675 -> FES + 5675)
        import re
        raw_num_doc = header_data.get('NUM_DOCUMENTO', '')
        doc_prefix = "FES"
        doc_number = raw_num_doc
        match = re.match(r'^([A-Za-z]+)(\d+)$', raw_num_doc)
        if match:
            doc_prefix = match.group(1)
            doc_number = match.group(2)
        else:
            doc_number = re.sub(r'\D', '', raw_num_doc)
            if not doc_number:
                doc_number = raw_num_doc

        # ENSAMBLAJE FINAL DEL PAYLOAD NATIVO
        payload = {
            "InvoiceGeneralInformation": {
                "InvoiceNumber": doc_number,
                "PreinvoiceNumber": doc_number,
                "InvoiceAuthorizationPrefix": doc_prefix,
                "InvoiceAuthorizationNumber": header_data.get('NUM_RESOLUCION', ''), # Ajustar si se cruza de maestro
                "DaysOff": 0,
                "Currency": "COP",
                "ExchangeRate": 0,
                "ExchangeRateDate": None,
                "SalesPerson": header_data.get('VENDEDOR_NOMBRE_LARGO', ''),
                "InvoiceDueDate": payment_due_date,
                "InvoicePeriodFrom": None,
                "InvoicePeriodTo": None,
                "CustomizationID": 0,
                "Note": "",
                "ExternalGR": False
            },
            "InvoiceMandateInformation": None,
            "OriginClientApp": 1,
            "CustomerInformation": {
                "IdentificationType": 3 if len(str(header_data.get('NIT_TERCERO', ''))) == 10 else 1,
                "Identification": header_data.get('NIT_TERCERO', ''),
                "DV": int(str(header_data.get('DIGITO_VERIFICACION', '0')).strip()) if str(header_data.get('DIGITO_VERIFICACION', '')).isdigit() else 0,
                "RegistrationName": header_data.get('RAZON_SOCIAL', ''),
                "TradingName": header_data.get('NOMBRE_COMERCIAL', ''),
                "BranchName": None,
                "CountryCode": "CO",
                "CountryName": "COLOMBIA",
                "SubdivisionCode": str(header_data.get('ID_MUNICIPIO_DIAN', ''))[:2] if header_data.get('ID_MUNICIPIO_DIAN', '') else "",
                "SubdivisionName": header_data.get('DEPARTAMENTO_NOMBRE', ''),
                "CityCode": header_data.get('ID_MUNICIPIO_DIAN', ''),
                "CityName": header_data.get('MUNICIPIO_NOMBRE', ''),
                "AddressLine": header_data.get('DIRECCION', ''),
                "CustomerNeighborhoodName": None,
                "Telephone": header_data.get('TELEFONO', ''),
                "Cellphone": "0",
                "Email": header_data.get('EMAIL', ''),
                "CustomerCode": None,
                "AdditionalAccountID": 2, # Standard local definition
                "TaxLevelCodeListName": tax_level_code_list_name,
                "TaxTributeCode": tax_tribute_code,
                "TaxTributeName": "IVA" if tax_tribute_code == '01' else "",
                "FiscalResponsability": fiscal_responsability,
                "PostalZone": header_data.get('ID_MUNICIPIO_DIAN', ''),
                "PartecipationPercent": 0
            },
            "DocumentItems": document_items,
            "TotalInvoiceTaxes": total_taxes,
            "InvoiceTotal": {
                "LineExtensionAmount": tax_exclusive_amount,
                "TaxExclusiveAmount": tax_exclusive_amount,
                "TaxInclusiveAmount": tax_inclusive_amount,
                "AllowanceTotalAmount": 0.00,
                "ChargeTotalAmount": 0.00,
                "PrePaidAmount": 0.00,
                "PayableAmount": round(payable_amount, 2)
            },
            "AdditionalDocuments": None,
            "AdditionalProperty": None,
            "PaymentMeans": [
                {
                    "PaymentType": payment_type,
                    "PaymentMeansCode": "ZZZ",
                    "PaymentDueDate": payment_due_date,
                    "PaymentNote": "** CREDITO **" if payment_type == 2 else "** CONTADO **",
                    "LeadTime": plazo_pago,
                    "ConditionalDiscountPercent": 0.00,
                    "ConditionalDiscountValue": 0.00,
                    "PaymentAmount": round(payable_amount, 2),
                    "PaymentMethod": "Credito" if payment_type == 2 else "10"
                }
            ],
            "InvoiceAllowanceCharges": [],
            "DocumentType": 0,
            "IssueDate": issue_date_only,
            "IssueDateTime": issue_date_str,
            "SalespersonName": header_data.get('VENDEDOR_NOMBRE_LARGO', ''),
            "DocumentFooterText": header_data.get('DOCUMENT_FOOTER_TEXT', ''),
            "SalesOrderPrefixNumber": "",
            "OrderId": "",
            "DocumentHeaderText1": header_data.get('DOCUMENT_HEADER_TEXT_1', ''),
            "IsGiftInvoice": False,
            "ElectronicSignature": None,
            "CreatedBy": header_data.get('CREADO_POR', ''),
            "PurchaseOrderNumber": "",
            "StoreHouse": header_data.get('STORE_HOUSE', ''),
            "TotalBoxes": int(header_data.get('TOTAL_BOXES', 0) or 0),
            "TotalVarious": 0.00,
            "Bag": 0.00,
            "Zone": header_data.get('ZONE', ''),
            "Weight": float(header_data.get('WEIGHT', 0.0) or 0.0),
            "DocumentName": "FACTURA ELECTRONICA DE VENTA",
            "DocumentID": int(id_documento) if str(id_documento).isdigit() else id_documento,
            "Note2": header_data.get('NOTE_2', ''),
            "Note3": header_data.get('NOTE_3', ''),
            "Note4": header_data.get('NOTE_4', ''),
            "Note5": header_data.get('NOTE_5', ''),
            "Note6": header_data.get('NOTE_6', ''),
            "Note7": header_data.get('NOTE_7', ''),
            "Delivery": {
                "AddressLine": header_data.get('DIRECCION', ''),
                "CountryCode": "CO",
                "CountryName": "COLOMBIA",
                "SubdivisionCode": str(header_data.get('ID_MUNICIPIO_DIAN', ''))[:2] if header_data.get('ID_MUNICIPIO_DIAN', '') else "",
                "SubdivisionName": header_data.get('DEPARTAMENTO_NOMBRE', ''),
                "CityCode": header_data.get('ID_MUNICIPIO_DIAN', ''),
                "CityName": header_data.get('MUNICIPIO_NOMBRE', ''),
                "PostalCode": None,
                "ContactPerson": header_data.get('TELEFONO', ''),
                "DeliveryDate": None,
                "DeliveryCompany": None
            },
            "TypeOperation": 0
        }

        # Auditoría Defensiva
        InvoicePayloadBuilder._save_payload_locally(id_documento, payload)

        return payload

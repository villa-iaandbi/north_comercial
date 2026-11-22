from decimal import Decimal, InvalidOperation

from django.contrib import messages
from django.shortcuts import redirect, render
from django.utils.decorators import method_decorator
from django.views import View

from pos.models import PosTurno, TsCaja
from pos.views import _token_empresa_sesion
from pos.views_auth import SESSION_USER_KEY, pos_role_required


@method_decorator(pos_role_required('CAJERO', 'DBA', 'SUPERVISOR'), name='dispatch')
class AperturaTurnoView(View):
    """Selección de caja (TS_CAJAS del tenant) y base de efectivo inicial."""
    template_name = 'pos/turno/apertura.html'

    def _cajas(self):
        # Lista materializada: evita FETCH FIRST (ORA-00933) en Oracle 11g.
        return list(TsCaja.objects.filter(estado='A').order_by('id_caja'))

    def _render(self, request, **extra):
        context = {'cajas': self._cajas(), 'usuario': request.session.get(SESSION_USER_KEY, {})}
        context.update(extra)
        return render(request, self.template_name, context)

    def get(self, request):
        return self._render(request)

    def post(self, request):
        usuario = (request.session.get(SESSION_USER_KEY) or {}).get('email', '')
        id_caja = request.POST.get('caja', '').strip()
        caja = next((c for c in self._cajas() if str(c.id_caja) == id_caja), None)
        if caja is None:
            messages.error(request, 'Seleccione una caja válida.')
            return self._render(request)

        try:
            base = Decimal(request.POST.get('base_efectivo', '0').strip() or '0')
            if base < 0:
                raise InvalidOperation
        except InvalidOperation:
            messages.error(request, 'La base de efectivo debe ser un número mayor o igual a cero.')
            return self._render(request, caja_sel=id_caja)

        # PosTurno.caja_id es texto (convención del POS: código de la caja).
        caja_ref = caja.cod_caja or str(caja.id_caja)
        token = _token_empresa_sesion(request)
        turno = PosTurno.objects.filter(
            caja_id=caja_ref, estado='ABIERTO', token_empresa=token
        ).order_by('-id_turno').first()
        if turno is not None and turno.usuario != usuario:
            messages.error(request, f'La caja {caja_ref} ya tiene un turno abierto por {turno.usuario}.')
            return self._render(request, caja_sel=id_caja)
        if turno is None:
            turno = PosTurno.objects.create(
                token_empresa=token,
                caja_id=caja_ref,
                usuario=usuario,
                base_economica=base,
                estado='ABIERTO',
            )

        request.session['turno_id'] = turno.id_turno
        request.session['caja_id'] = turno.caja_id
        return redirect('pos:pos_home')

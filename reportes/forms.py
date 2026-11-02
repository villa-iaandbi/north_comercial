# -*- coding: utf-8 -*-
import re

from django import forms

from .models import ParametroExogena

# Clases utilitarias Tailwind compartidas por todos los controles del formulario.
_BASE_CLASSES = (
    'block w-full rounded border border-gray-300 bg-white px-2 py-1.5 text-sm text-gray-800 '
    'placeholder-gray-400 shadow-sm focus:border-blue-500 focus:outline-none focus:ring-1 focus:ring-blue-500'
)


def _attrs(nombre, **extra):
    """
    Atributos de un widget: clases Tailwind + enlace Alpine.js (x-model) al estado `form`
    del modal y resaltado en rojo cuando el servidor devuelve error para el campo.
    """
    attrs = {
        'class': _BASE_CLASSES,
        'x-model': f'form.{nombre}',
        ':class': f"errores.{nombre} ? 'border-red-500 ring-1 ring-red-400' : ''",
        'autocomplete': 'off',
    }
    attrs.update(extra)
    return attrs


class ParametroExogenaForm(forms.ModelForm):
    class Meta:
        model = ParametroExogena
        fields = [
            'ano_fiscal',
            'formato_dian',
            'concepto_dian',
            'cuenta_contable',
            'origen_cubo',
            'columna_xml',
            'tope_cuantia_menor',
            'estado',
            'observaciones',
        ]
        widgets = {
            'ano_fiscal': forms.TextInput(attrs=_attrs(
                'ano_fiscal', maxlength='4', inputmode='numeric', placeholder='2025')),
            'formato_dian': forms.TextInput(attrs=_attrs(
                'formato_dian', maxlength='10', placeholder='1001')),
            'concepto_dian': forms.TextInput(attrs=_attrs(
                'concepto_dian', maxlength='10', placeholder='5001')),
            'cuenta_contable': forms.TextInput(attrs=_attrs(
                'cuenta_contable', maxlength='20', placeholder='510506')),
            'origen_cubo': forms.Select(attrs=_attrs('origen_cubo')),
            'columna_xml': forms.TextInput(attrs=_attrs(
                'columna_xml', maxlength='50', placeholder='pagoded')),
            'tope_cuantia_menor': forms.NumberInput(attrs=_attrs(
                'tope_cuantia_menor', min='0', step='0.01', inputmode='decimal', placeholder='0')),
            'estado': forms.Select(attrs=_attrs('estado')),
            'observaciones': forms.Textarea(attrs=_attrs(
                'observaciones', rows='3', maxlength='250', placeholder='Notas opcionales (máx. 250 caracteres)')),
        }
        labels = {
            'ano_fiscal': 'Año fiscal',
            'formato_dian': 'Formato DIAN',
            'concepto_dian': 'Concepto DIAN',
            'cuenta_contable': 'Cuenta contable',
            'origen_cubo': 'Origen del cubo',
            'columna_xml': 'Columna XML',
            'tope_cuantia_menor': 'Tope cuantía menor ($)',
            'estado': 'Estado',
            'observaciones': 'Observaciones',
        }

    # ------------------------------------------------------------------ #
    # Validaciones de campo
    # ------------------------------------------------------------------ #
    def clean_ano_fiscal(self):
        valor = (self.cleaned_data.get('ano_fiscal') or '').strip()
        if not re.fullmatch(r'\d{4}', valor):
            raise forms.ValidationError('Debe ser un año de 4 dígitos (ej. 2025).')
        if not 2000 <= int(valor) <= 2100:
            raise forms.ValidationError('El año fiscal debe estar entre 2000 y 2100.')
        return valor

    def clean_formato_dian(self):
        valor = (self.cleaned_data.get('formato_dian') or '').strip()
        if not re.fullmatch(r'[0-9A-Za-z]+', valor):
            raise forms.ValidationError('Solo se permiten letras y números (ej. 1001).')
        return valor

    def clean_concepto_dian(self):
        valor = (self.cleaned_data.get('concepto_dian') or '').strip()
        if not re.fullmatch(r'[0-9A-Za-z]+', valor):
            raise forms.ValidationError('Solo se permiten letras y números (ej. 5001).')
        return valor

    def clean_cuenta_contable(self):
        valor = (self.cleaned_data.get('cuenta_contable') or '').strip()
        if not re.fullmatch(r'[0-9A-Za-z.\-]+', valor):
            raise forms.ValidationError('Cuenta inválida: use dígitos (admite "." y "-").')
        return valor

    def clean_columna_xml(self):
        valor = (self.cleaned_data.get('columna_xml') or '').strip()
        if not valor:
            raise forms.ValidationError('La columna XML es obligatoria.')
        return valor

    def clean_tope_cuantia_menor(self):
        valor = self.cleaned_data.get('tope_cuantia_menor')
        if valor is None:
            raise forms.ValidationError('El tope de cuantía menor es obligatorio (use 0 si no aplica).')
        if valor < 0:
            raise forms.ValidationError('El tope no puede ser negativo.')
        return valor

    def clean_observaciones(self):
        valor = (self.cleaned_data.get('observaciones') or '').strip()
        return valor or None

    # ------------------------------------------------------------------ #
    # Regla de negocio: evitar parámetros duplicados (doble conteo en Exógena)
    # ------------------------------------------------------------------ #
    def clean(self):
        cleaned = super().clean()
        claves = ('ano_fiscal', 'formato_dian', 'concepto_dian', 'cuenta_contable',
                  'origen_cubo', 'columna_xml')
        if all(cleaned.get(k) for k in claves) and cleaned.get('estado') == ParametroExogena.ESTADO_ACTIVO:
            duplicados = ParametroExogena.objects.filter(
                estado=ParametroExogena.ESTADO_ACTIVO,
                **{k: cleaned[k] for k in claves},
            )
            if self.instance.pk:
                duplicados = duplicados.exclude(pk=self.instance.pk)
            # count() evita el slicing (FETCH FIRST) que .exists()/.first() generan y que Oracle 11g rechaza.
            if duplicados.count() > 0:
                raise forms.ValidationError(
                    'Ya existe un parámetro ACTIVO con el mismo año, formato, concepto, cuenta, '
                    'origen y columna XML.'
                )
        return cleaned

from decimal import Decimal

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('pos', '0004_pospuntoscliente_token_empresa_and_more'),
    ]

    operations = [
        migrations.AddField(
            model_name='posticketdetail',
            name='descuento_valor',
            field=models.DecimalField(decimal_places=2, default=Decimal('0'), max_digits=15),
        ),
    ]

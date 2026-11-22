from django.db import migrations


class Migration(migrations.Migration):

    dependencies = [
        ('core', '0011_passo_rotina_feito'),
    ]

    operations = [
        # o campo guarda o link da imagem de mapeamento facial da YouCam, nao a foto
        migrations.RenameField(
            model_name='perfildermatologico',
            old_name='foto_rosto',
            new_name='url_mapeamento_facial',
        ),
    ]

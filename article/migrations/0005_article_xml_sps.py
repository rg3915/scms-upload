# Generated by Django 3.2.19 on 2023-06-18 18:12

from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):

    dependencies = [
        ('xmlsps', '0001_initial'),
        ('article', '0004_alter_article_article_type'),
    ]

    operations = [
        migrations.AddField(
            model_name='article',
            name='xml_sps',
            field=models.ForeignKey(blank=True, null=True,
                                    on_delete=django.db.models.deletion.SET_NULL, to='xmlsps.xmlsps'),
        ),
    ]

# Generated by Django 3.2.19 on 2023-06-21 02:58

from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):

    dependencies = [
        ('xmlsps', '0001_initial'),
        ('article', '0007_remove_article_xml_sps'),
    ]

    operations = [
        migrations.AddField(
            model_name='article',
            name='xml_sps',
            field=models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, to='xmlsps.xmlsps'),
        ),
    ]
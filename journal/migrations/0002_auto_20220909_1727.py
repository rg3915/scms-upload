# Generated by Django 3.2.12 on 2022-09-09 17:27

from django.db import migrations


class Migration(migrations.Migration):

    dependencies = [
        ('journal', '0001_initial'),
    ]

    operations = [
        migrations.RemoveField(
            model_name='scielojournal',
            name='collections',
        ),
        migrations.RemoveField(
            model_name='scielojournal',
            name='creator',
        ),
        migrations.RemoveField(
            model_name='scielojournal',
            name='official_journal',
        ),
        migrations.RemoveField(
            model_name='scielojournal',
            name='updated_by',
        ),
        migrations.DeleteModel(
            name='JournalInCollection',
        ),
        migrations.DeleteModel(
            name='SciELOJournal',
        ),
    ]
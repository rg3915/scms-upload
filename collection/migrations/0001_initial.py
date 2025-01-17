# Generated by Django 3.2.12 on 2022-12-22 17:54

from django.conf import settings
from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):

    initial = True

    dependencies = [
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.CreateModel(
            name='ClassicWebsiteConfiguration',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('created', models.DateTimeField(auto_now_add=True, verbose_name='Creation date')),
                ('updated', models.DateTimeField(auto_now=True, verbose_name='Last update date')),
                ('title_path', models.CharField(blank=True, help_text='Title path: title.id path or title.mst path without extension', max_length=255, null=True, verbose_name='Title path')),
                ('issue_path', models.CharField(blank=True, help_text='Issue path: issue.id path or issue.mst path without extension', max_length=255, null=True, verbose_name='Issue path')),
                ('serial_path', models.CharField(blank=True, help_text='Serial path', max_length=255, null=True, verbose_name='Serial path')),
                ('cisis_path', models.CharField(blank=True, help_text='Cisis path where there are CISIS utilities such as mx and i2id', max_length=255, null=True, verbose_name='Cisis path')),
                ('bases_work_path', models.CharField(blank=True, help_text='Bases work path', max_length=255, null=True, verbose_name='Bases work path')),
                ('bases_pdf_path', models.CharField(blank=True, help_text='Bases translation path', max_length=255, null=True, verbose_name='Bases pdf path')),
                ('bases_translation_path', models.CharField(blank=True, help_text='Bases translation path', max_length=255, null=True, verbose_name='Bases translation path')),
                ('bases_xml_path', models.CharField(blank=True, help_text='Bases XML path', max_length=255, null=True, verbose_name='Bases XML path')),
                ('htdocs_img_revistas_path', models.CharField(blank=True, help_text='Htdocs img revistas path', max_length=255, null=True, verbose_name='Htdocs img revistas path')),
            ],
        ),
        migrations.CreateModel(
            name='Collection',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('created', models.DateTimeField(auto_now_add=True, verbose_name='Creation date')),
                ('updated', models.DateTimeField(auto_now=True, verbose_name='Last update date')),
                ('acron', models.CharField(blank=True, max_length=255, null=True, verbose_name='Collection Acronym')),
                ('name', models.CharField(blank=True, max_length=255, null=True, verbose_name='Collection Name')),
            ],
            options={
                'abstract': False,
            },
        ),
        migrations.CreateModel(
            name='FilesStorageConfiguration',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('created', models.DateTimeField(auto_now_add=True, verbose_name='Creation date')),
                ('updated', models.DateTimeField(auto_now=True, verbose_name='Last update date')),
                ('host', models.CharField(blank=True, max_length=255, null=True, verbose_name='Host')),
                ('bucket_root', models.CharField(blank=True, max_length=255, null=True, verbose_name='Bucket root')),
                ('bucket_app_subdir', models.CharField(blank=True, max_length=64, null=True, verbose_name='Bucket app subdir')),
                ('bucket_public_subdir', models.CharField(blank=True, max_length=64, null=True, verbose_name='Bucket public subdir')),
                ('bucket_migration_subdir', models.CharField(blank=True, max_length=64, null=True, verbose_name='Bucket migration subdir')),
                ('bucket_temp_subdir', models.CharField(blank=True, max_length=64, null=True, verbose_name='Bucket temp subdir')),
                ('bucket_versions_subdir', models.CharField(blank=True, max_length=64, null=True, verbose_name='Bucket versions subdir')),
                ('access_key', models.CharField(blank=True, max_length=255, null=True, verbose_name='Access key')),
                ('secret_key', models.CharField(blank=True, max_length=255, null=True, verbose_name='Secret key')),
                ('secure', models.BooleanField(default=True, verbose_name='Secure')),
            ],
        ),
        migrations.CreateModel(
            name='NewWebSiteConfiguration',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('created', models.DateTimeField(auto_now_add=True, verbose_name='Creation date')),
                ('updated', models.DateTimeField(auto_now=True, verbose_name='Last update date')),
                ('url', models.CharField(blank=True, max_length=255, null=True, verbose_name='New website url')),
                ('db_uri', models.CharField(blank=True, help_text='mongodb://login:password@host:port/database', max_length=255, null=True, verbose_name='Mongodb Info')),
            ],
        ),
        migrations.CreateModel(
            name='SciELODocument',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('created', models.DateTimeField(auto_now_add=True, verbose_name='Creation date')),
                ('updated', models.DateTimeField(auto_now=True, verbose_name='Last update date')),
                ('pid', models.CharField(blank=True, max_length=23, null=True, verbose_name='PID')),
                ('file_id', models.CharField(blank=True, max_length=50, null=True, verbose_name='File ID')),
            ],
        ),
        migrations.CreateModel(
            name='SciELOFile',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('file_id', models.CharField(blank=True, max_length=255, null=True, verbose_name='ID')),
                ('relative_path', models.CharField(blank=True, max_length=255, null=True, verbose_name='Relative Path')),
                ('name', models.CharField(max_length=255, verbose_name='Filename')),
                ('uri', models.URLField(max_length=255, null=True, verbose_name='URI')),
                ('object_name', models.CharField(max_length=255, null=True, verbose_name='Object name')),
            ],
        ),
        migrations.CreateModel(
            name='SciELOIssue',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('created', models.DateTimeField(auto_now_add=True, verbose_name='Creation date')),
                ('updated', models.DateTimeField(auto_now=True, verbose_name='Last update date')),
                ('issue_pid', models.CharField(max_length=23, verbose_name='Issue PID')),
                ('issue_folder', models.CharField(max_length=23, verbose_name='Issue Folder')),
            ],
        ),
        migrations.CreateModel(
            name='AssetFile',
            fields=[
                ('scielofile_ptr', models.OneToOneField(auto_created=True, on_delete=django.db.models.deletion.CASCADE, parent_link=True, primary_key=True, serialize=False, to='collection.scielofile')),
                ('is_supplementary_material', models.BooleanField(default=False)),
            ],
            bases=('collection.scielofile',),
        ),
        migrations.CreateModel(
            name='FileWithLang',
            fields=[
                ('scielofile_ptr', models.OneToOneField(auto_created=True, on_delete=django.db.models.deletion.CASCADE, parent_link=True, primary_key=True, serialize=False, to='collection.scielofile')),
                ('lang', models.CharField(max_length=4, verbose_name='Language')),
            ],
            bases=('collection.scielofile',),
        ),
        migrations.CreateModel(
            name='SciELOJournal',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('created', models.DateTimeField(auto_now_add=True, verbose_name='Creation date')),
                ('updated', models.DateTimeField(auto_now=True, verbose_name='Last update date')),
                ('scielo_issn', models.CharField(max_length=9, verbose_name='SciELO ISSN')),
                ('acron', models.CharField(blank=True, max_length=25, null=True, verbose_name='Acronym')),
                ('title', models.CharField(blank=True, max_length=255, null=True, verbose_name='Title')),
                ('availability_status', models.CharField(blank=True, choices=[('?', 'Unknown'), ('C', 'Current')], max_length=10, null=True, verbose_name='Availability Status')),
                ('collection', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, to='collection.collection')),
                ('creator', models.ForeignKey(editable=False, on_delete=django.db.models.deletion.CASCADE, related_name='scielojournal_creator', to=settings.AUTH_USER_MODEL, verbose_name='Creator')),
            ],
        ),
    ]

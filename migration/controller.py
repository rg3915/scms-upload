import json
import logging
import os
import sys
import traceback
from copy import deepcopy
from datetime import datetime
from io import StringIO
from random import randint

from django.contrib.auth import get_user_model
from django.db.models import Q
from django.utils.translation import gettext_lazy as _
from lxml import etree
from packtools.sps.models.article_assets import ArticleAssets, SupplementaryMaterials
from packtools.sps.models.article_renditions import ArticleRenditions
from packtools.sps.models.related_articles import RelatedItems
from scielo_classic_website import classic_ws

from collection import controller as collection_controller
from collection.choices import CURRENT
from collection.exceptions import GetSciELOJournalError
from collection.models import AssetFile, FileWithLang, SciELOHTMLFile, XMLFile
from core.controller import parse_months_names, parse_non_standard_date
from django_celery_beat.models import CrontabSchedule, PeriodicTask
from libs.dsm.files_storage.minio import MinioStorage
from libs.dsm.publication.db import mk_connection
from libs.dsm.publication.documents import DocumentToPublish
from libs.dsm.publication.issues import IssueToPublish, get_bundle_id
from libs.dsm.publication.journals import JournalToPublish

from . import exceptions
from .choices import MS_IMPORTED, MS_PUBLISHED, MS_TO_IGNORE
from .models import (
    DocumentMigration,
    IssueMigration,
    JournalMigration,
    MigrationConfiguration,
    MigrationFailure,
)

User = get_user_model()


def read_xml_file(file_path):
    return etree.parse(file_path)


def tostring(xmltree):
    # garante que os diacríticos estarão devidamente representados
    return etree.tostring(xmltree, encoding="utf-8").decode("utf-8")


def _get_classic_website_rel_path(file_path):
    if "htdocs" in file_path:
        return file_path[file_path.find("htdocs") :]
    if "base" in file_path:
        return file_path[file_path.find("base") :]


def start(user_id):
    try:
        logging.info(_("Get or create migration configuration"))
        (
            classic_website,
            files_storage_config,
            new_website_config,
        ) = collection_controller.start()
        try:
            migration_configuration = MigrationConfiguration.objects.get(
                classic_website_config=classic_website
            )
        except MigrationConfiguration.DoesNotExist:
            migration_configuration = MigrationConfiguration()
            migration_configuration.classic_website_config = classic_website
            migration_configuration.new_website_config = new_website_config
            migration_configuration.files_storage_config = files_storage_config
            migration_configuration.creator_id = user_id
            migration_configuration.save()

        schedule_journals_and_issues_migrations(
            classic_website.collection.acron, user_id
        )

    except Exception as e:
        raise exceptions.MigrationStartError("Unable to start system %s" % e)


def schedule_journals_and_issues_migrations(collection_acron, user_id):
    """
    Agenda tarefas para importar e publicar dados de title e issue
    """
    logging.info(_("Schedule journals and issues migrations tasks"))
    items = (
        ("title", _("Migrate journals"), "migration", 0, 2, 0),
        ("issue", _("Migrate issues"), "migration", 0, 7, 2),
    )

    for db_name, task, action, hours_after_now, minutes_after_now, priority in items:
        for mode in ("full", "incremental"):
            name = f"{collection_acron} | {db_name} | {action} | {mode}"
            kwargs = dict(
                collection_acron=collection_acron,
                user_id=user_id,
                force_update=(mode == "full"),
            )
            try:
                periodic_task = PeriodicTask.objects.get(name=name)
            except PeriodicTask.DoesNotExist:
                hours, minutes = sum_hours_and_minutes(
                    hours_after_now, minutes_after_now
                )

                periodic_task = PeriodicTask()
                periodic_task.name = name
                periodic_task.task = task
                periodic_task.kwargs = json.dumps(kwargs)
                if mode == "full":
                    periodic_task.priority = priority
                    periodic_task.enabled = False
                    periodic_task.one_off = True
                    periodic_task.crontab = get_or_create_crontab_schedule(
                        hour=hours,
                        minute=minutes,
                    )
                else:
                    periodic_task.priority = priority
                    periodic_task.enabled = True
                    periodic_task.one_off = False
                    periodic_task.crontab = get_or_create_crontab_schedule(
                        minute=minutes,
                    )
                periodic_task.save()
    logging.info(_("Scheduled journals and issues migrations tasks"))


def schedule_issues_documents_migration(collection_acron, user_id):
    """
    Agenda tarefas para migrar e publicar todos os documentos
    """
    for issue_migration in IssueMigration.objects.filter(
        scielo_issue__scielo_journal__collection__acron=collection_acron
    ):
        journal_acron = issue_migration.scielo_issue.scielo_journal.acron
        scielo_issn = issue_migration.scielo_issue.scielo_journal.scielo_issn
        publication_year = issue_migration.scielo_issue.official_issue.publication_year

        schedule_issue_documents_migration(
            issue_migration, journal_acron, scielo_issn, publication_year, user_id
        )


def schedule_issue_documents_migration(
    collection_acron, journal_acron, scielo_issn, publication_year, user_id
):
    """
    Agenda tarefas para migrar e publicar um conjunto de documentos por:

        - ano
        - periódico
        - periódico e ano
    """
    logging.info(
        _("Schedule issue documents migration {} {} {} {}").format(
            collection_acron,
            journal_acron,
            scielo_issn,
            publication_year,
        )
    )
    action = "migrate"
    task = _("Migrate documents")

    params_list = (
        {"scielo_issn": scielo_issn, "publication_year": publication_year},
        {"scielo_issn": scielo_issn},
        {"publication_year": publication_year},
    )
    documents_group_ids = (
        f"{journal_acron} {publication_year}",
        f"{journal_acron}",
        f"{publication_year}",
    )

    count = 0
    for group_id, params in zip(documents_group_ids, params_list):
        count += 1
        if len(params) == 2:
            modes = ("full", "incremental")
        else:
            modes = ("incremental",)

        for mode in modes:
            name = f"{collection_acron} | {group_id} | {action} | {mode}"

            kwargs = dict(
                collection_acron=collection_acron,
                user_id=user_id,
                force_update=(mode == "full"),
            )
            kwargs.update(params)

            try:
                periodic_task = PeriodicTask.objects.get(name=name, task=task)
            except PeriodicTask.DoesNotExist:
                now = datetime.utcnow()
                periodic_task = PeriodicTask()
                periodic_task.name = name
                periodic_task.task = task
                periodic_task.kwargs = json.dumps(kwargs)
                if mode == "full":
                    # full: force_update = True
                    # modo full está programado para ser executado manualmente
                    # ou seja, a task fica disponível para que o usuário
                    # apenas clique em RUN e rodará na sequência,
                    # não dependente dos atributos: enabled, one_off, crontab

                    # prioridade alta
                    periodic_task.priority = 1
                    # desabilitado para rodar automaticamente
                    periodic_task.enabled = False
                    # este parâmetro não é relevante devido à execução manual
                    periodic_task.one_off = True
                    # este parâmetro não é relevante devido à execução manual
                    hours, minutes = sum_hours_and_minutes(0, 1)
                    periodic_task.crontab = get_or_create_crontab_schedule(
                        hour=hours,
                        minute=minutes,
                    )
                else:
                    # modo incremental está programado para ser executado
                    # automaticamente
                    # incremental: force_update = False

                    # prioridade 3, exceto se houver ano de publicação
                    periodic_task.priority = 3
                    if publication_year:
                        # estabelecer prioridade maior para os mais recentes
                        periodic_task.priority = datetime.now().year - int(
                            publication_year
                        )

                    # deixa habilitado para rodar frequentemente
                    periodic_task.enabled = True

                    # programado para rodar automaticamente 1 vez se o ano de
                    # publicação não é o atual
                    periodic_task.one_off = (
                        publication_year and publication_year != datetime.now().year
                    )

                    # distribui as tarefas para executarem dentro de 1h
                    # e elas executarão a cada 1h
                    hours, minutes = sum_hours_and_minutes(0, count % 100)
                    periodic_task.crontab = get_or_create_crontab_schedule(
                        # hour=hours,
                        minute=minutes,
                    )
                periodic_task.save()
    logging.info(_("Scheduled {} tasks to migrate documents").format(count))


def sum_hours_and_minutes(hours_after_now, minutes_after_now, now=None):
    """
    Retorna a soma dos minutos / horas a partir da hora atual
    """
    now = now or datetime.utcnow()
    hours = now.hour + hours_after_now
    minutes = now.minute + minutes_after_now
    if minutes > 59:
        hours += 1
    hours = hours % 24
    minutes = minutes % 60
    return hours, minutes


def get_or_create_crontab_schedule(day_of_week=None, hour=None, minute=None):
    try:
        crontab_schedule, status = CrontabSchedule.objects.get_or_create(
            day_of_week=day_of_week or "*",
            hour=hour or "*",
            minute=minute or "*",
        )
    except Exception as e:
        raise exceptions.GetOrCreateCrontabScheduleError(
            _("Unable to get_or_create_crontab_schedule {} {} {} {} {}").format(
                day_of_week, hour, minute, type(e), e
            )
        )
    return crontab_schedule


def insert_hyphen_in_YYYYMMMDD(YYYYMMMDD):
    if YYYYMMMDD[4:6] == "00":
        return f"{YYYYMMMDD[:4]}"
    if YYYYMMMDD[6:] == "00":
        return f"{YYYYMMMDD[:4]}-{YYYYMMMDD[4:6]}"
    return f"{YYYYMMMDD[:4]}-{YYYYMMMDD[4:6]}-{YYYYMMMDD[6:]}"


def _register_failure(
    msg,
    collection_acron,
    action_name,
    object_name,
    pid,
    e,
    user_id,
    # exc_type, exc_value, exc_traceback,
):
    exc_type, exc_value, exc_traceback = sys.exc_info()
    logging.error(msg)
    logging.exception(e)
    register_failure(
        collection_acron,
        action_name,
        object_name,
        pid,
        e,
        exc_type,
        exc_value,
        exc_traceback,
        user_id,
    )


def register_failure(
    collection_acron,
    action_name,
    object_name,
    pid,
    e,
    exc_type,
    exc_value,
    exc_traceback,
    user_id,
):
    migration_failure = MigrationFailure()
    migration_failure.collection_acron = collection_acron
    migration_failure.action_name = action_name
    migration_failure.object_name = object_name
    migration_failure.pid = pid[:23]
    migration_failure.exception_msg = str(e)[:555]
    migration_failure.traceback = [
        str(item) for item in traceback.extract_tb(exc_traceback)
    ]
    migration_failure.exception_type = str(type(e))
    migration_failure.creator = User.objects.get(pk=user_id)
    migration_failure.save()


def get_or_create_journal_migration(scielo_journal, creator_id):
    """
    Returns a JournalMigration (registered or new)
    """
    try:
        try:
            item = JournalMigration.objects.get(
                scielo_journal=scielo_journal,
            )
        except JournalMigration.DoesNotExist:
            item = JournalMigration()
            item.creator_id = creator_id
            item.scielo_journal = scielo_journal
            item.save()
    except Exception as e:
        raise exceptions.GetOrCreateJournalMigrationError(
            _("Unable to get_or_create_journal_migration {} {} {}").format(
                scielo_journal, type(e), e
            )
        )
    return item


def get_journal_migration_status(scielo_issn):
    """
    Returns a JournalMigration status
    """
    try:
        return JournalMigration.objects.get(
            scielo_journal__scielo_issn=scielo_issn,
        ).status
    except Exception as e:
        raise exceptions.GetJournalMigratioStatusError(
            _("Unable to get_journal_migration_status {} {} {}").format(
                scielo_issn, type(e), e
            )
        )


class MigrationConfigurationController:
    def __init__(self, collection_acron):
        self._collection_acron = collection_acron

    @property
    def config(self):
        return MigrationConfiguration.objects.get(
            classic_website_config__collection__acron=self._collection_acron
        )

    def connect_db(self):
        try:
            return mk_connection(self.config.new_website_config.db_uri)

        except Exception as e:
            raise exceptions.GetMigrationConfigurationError(
                _("Unable to connect db {} {}").format(type(e), e)
            )

    @property
    def classic_website(self):
        try:
            return self.config.classic_website_config

        except Exception as e:
            raise exceptions.GetMigrationConfigurationError(
                _("Unable to get classic website configuration {} {}").format(
                    type(e), e
                )
            )

    def get_source_file_path(self, db_name):
        try:
            return getattr(self.classic_website, f"{db_name}_path")
        except AttributeError:
            raise exceptions.GetMigrationConfigurationError(
                _("Unable to get path of {} {} {}").format(db_name, type(e), e)
            )

    def get_artigo_source_files_paths(self, journal_acron, issue_folder):
        """
        Apesar de fornecer `issue_folder` o retorno pode ser a base de dados
        inteira do `journal_acron`
        """
        logging.info(
            "Harvest classic website records {} {}".format(journal_acron, issue_folder)
        )
        try:
            artigo_source_files_paths = classic_ws.get_artigo_db_path(
                journal_acron, issue_folder, self.classic_website
            )
        except Exception as e:
            raise exceptions.IssueFilesStoreError(
                _("Unable to get artigo db paths from classic website {} {} {}").format(
                    journal_acron,
                    issue_folder,
                    e,
                )
            )
        logging.info(artigo_source_files_paths)
        return artigo_source_files_paths

    @property
    def classic_website_paths(self):
        try:
            return {
                "BASES_TRANSLATION_PATH": self.classic_website.bases_translation_path,
                "BASES_PDF_PATH": self.classic_website.bases_pdf_path,
                "HTDOCS_IMG_REVISTAS_PATH": self.classic_website.htdocs_img_revistas_path,
                "BASES_XML_PATH": self.classic_website.bases_xml_path,
            }
        except Exception as e:
            raise exceptions.GetMigrationConfigurationError(
                _("Unable to get classic website paths {} {}").format(type(e), e)
            )

    @property
    def bucket_public_subdir(self):
        try:
            return self.config.files_storage_config.bucket_public_subdir
        except Exception as e:
            raise exceptions.GetMigrationConfigurationError(
                _("Unable to get bucket_public_subdir {} {}").format(type(e), e)
            )

    @property
    def bucket_migration_subdir(self):
        try:
            return self.config.files_storage_config.bucket_migration_subdir
        except Exception as e:
            raise exceptions.GetMigrationConfigurationError(
                _("Unable to get bucket_migration_subdir {} {}").format(type(e), e)
            )

    @property
    def files_storage(self):
        try:
            files_storage_config = self.config.files_storage_config
            self._bucket_public_subdir = files_storage_config.bucket_public_subdir
            self._bucket_migration_subdir = files_storage_config.bucket_migration_subdir
            return MinioStorage(
                minio_host=files_storage_config.host,
                minio_access_key=files_storage_config.access_key,
                minio_secret_key=files_storage_config.secret_key,
                bucket_root=files_storage_config.bucket_root,
                bucket_subdir=(
                    files_storage_config.bucket_app_subdir
                    or files_storage_config.bucket_public_subdir
                ),
                minio_secure=files_storage_config.secure,
                minio_http_client=None,
            )
        except Exception as e:
            raise exceptions.GetFilesStorageError(
                _("Unable to get MinioStorage {} {} {}").format(
                    files_storage_config, type(e), e
                )
            )

    def store_issue_files(self, journal_acron, issue_folder):
        try:
            issue_files = classic_ws.get_issue_files(
                journal_acron, issue_folder, self.classic_website_paths
            )
        except Exception as e:
            raise exceptions.IssueFilesStoreError(
                _("Unable to get issue files from classic website {} {} {}").format(
                    journal_acron,
                    issue_folder,
                    e,
                )
            )

        for info in issue_files:
            try:
                mimetype = None
                name, ext = os.path.splitext(info["path"])
                if ext in (".xml", ".html", ".htm"):
                    subdir = self.bucket_migration_subdir
                    mimetype = "text/xml" if ext == ".xml" else "html"
                else:
                    subdir = self.bucket_public_subdir
                subdirs = os.path.join(
                    subdir,
                    journal_acron,
                    issue_folder,
                )
                response = self.files_storage.register(
                    info["path"], subdirs=subdirs, preserve_name=True
                )
                info["relative_path"] = _get_classic_website_rel_path(info["path"])
                info.update(response)
                logging.info("Stored {} in files storage".format(info))
                yield info

            except Exception as e:
                raise exceptions.IssueFilesStoreError(
                    _("Unable to store issue files {} {} {}").format(
                        journal_acron,
                        issue_folder,
                        e,
                    )
                )


def migrate_journals(
    user_id,
    collection_acron,
    force_update=False,
):
    try:
        action = "migrate"
        mcc = MigrationConfigurationController(collection_acron)
        mcc.connect_db()
        source_file_path = mcc.get_source_file_path("title")

        for scielo_issn, journal_data in classic_ws.get_records_by_source_path(
            "title", source_file_path
        ):
            try:
                action = "import"
                journal_migration = import_data_from_title_database(
                    user_id,
                    collection_acron,
                    scielo_issn,
                    journal_data[0],
                    force_update,
                )
                action = "publish"
                publish_imported_journal(journal_migration)
            except Exception as e:
                _register_failure(
                    _("Error migrating journal {} {}").format(
                        collection_acron, scielo_issn
                    ),
                    collection_acron,
                    action,
                    "journal",
                    scielo_issn,
                    e,
                    user_id,
                )
    except Exception as e:
        _register_failure(
            _("Error migrating journal {} {}").format(collection_acron, _("GENERAL")),
            collection_acron,
            action,
            "journal",
            _("GENERAL"),
            e,
            user_id,
        )


def import_data_from_title_database(
    user_id, collection_acron, scielo_issn, journal_data, force_update=False
):
    """
    Create/update JournalMigration
    """
    journal = classic_ws.Journal(journal_data)

    scielo_journal = get_scielo_journal(journal, collection_acron, scielo_issn, user_id)

    # cria ou obtém journal_migration
    journal_migration = get_or_create_journal_migration(
        scielo_journal, creator_id=user_id
    )

    # check if it needs to be update
    if journal_migration.isis_updated_date == journal.isis_updated_date:
        if not force_update:
            # nao precisa atualizar
            return journal_migration
    try:
        journal_migration.isis_created_date = journal.isis_created_date
        journal_migration.isis_updated_date = journal.isis_updated_date
        journal_migration.status = MS_IMPORTED
        if journal.current_status != CURRENT:
            journal_migration.status = MS_TO_IGNORE
        journal_migration.data = journal_data
        journal_migration.save()
        return journal_migration
    except Exception as e:
        raise exceptions.JournalMigrationSaveError(
            _("Unable to save journal migration {} {} {}").format(
                collection_acron, scielo_issn, e
            )
        )


def get_scielo_journal(journal, collection_acron, scielo_issn, user_id):
    try:
        # cria ou obtém official_journal
        official_journal = collection_controller.get_or_create_official_journal(
            title=journal.title,
            issn_l=None,
            e_issn=journal.electronic_issn,
            print_issn=journal.print_issn,
            creator_id=user_id,
        )
        official_journal_data = (
            official_journal.title,
            official_journal.foundation_date,
        )
        journal_data = (
            journal.title,
            journal.first_year,
        )
        if official_journal_data != journal_data:
            official_journal.title = journal.title
            official_journal.foundation_date = journal.first_year
            official_journal.save()
    except Exception as e:
        official_journal = None

    # cria ou obtém scielo_journal
    scielo_journal = collection_controller.get_or_create_scielo_journal(
        collection_acron, scielo_issn, user_id
    )
    try:
        scielo_journal_data = (
            scielo_journal.title,
            scielo_journal.availability_status,
            scielo_journal.official_journal,
            scielo_journal.acron,
        )
        journal_data = (
            journal.title,
            journal.current_status,
            official_journal,
            journal.acronym,
        )
        if scielo_journal_data != journal_data:
            scielo_journal.title = journal.title
            scielo_journal.availability_status = journal.current_status
            scielo_journal.official_journal = official_journal
            scielo_journal.acron = journal.acronym
            scielo_journal.save()
    except Exception as e:
        raise exceptions.JournalMigrationSaveError(
            _("Unable to save scielo_journal {} {}").format(scielo_journal, e)
        )
    return scielo_journal


def publish_imported_journal(journal_migration):
    journal = classic_ws.Journal(journal_migration.data)
    if journal.current_status != CURRENT:
        # journal must not be published
        return

    if journal_migration.status != MS_IMPORTED:
        return

    try:
        journal_to_publish = JournalToPublish(journal.scielo_issn)
        journal_to_publish.add_contact(
            " | ".join(journal.publisher_name),
            journal.publisher_email,
            ", ".join(journal.publisher_address),
            journal.publisher_city,
            journal.publisher_state,
            journal.publisher_country,
        )

        for mission in journal.mission:
            journal_to_publish.add_item_to_mission(mission["language"], mission["text"])

        for item in journal.status_history:
            journal_to_publish.add_item_to_timeline(
                item["status"],
                insert_hyphen_in_YYYYMMMDD(item["date"]),
                item.get("reason"),
            )
        journal_to_publish.add_journal_issns(
            journal.scielo_issn,
            journal.electronic_issn,
            journal.print_issn,
        )
        journal_to_publish.add_journal_titles(
            journal.title,
            journal.abbreviated_iso_title,
            journal.abbreviated_title,
        )

        journal_to_publish.add_online_submission_url(journal.submission_url)

        # TODO links previous e next
        # previous_journal = next_journal_title = None
        # if journal.previous_title:
        #     try:
        #         previous_journal = get_scielo_journal_by_title(
        #             journal.previous_title)
        #     except GetSciELOJournalError:
        #         previous_journal = None
        # if journal.next_title:
        #     try:
        #         next_journal = get_scielo_journal_by_title(journal.next_title)
        #         next_journal_title = journal.next_title
        #     except GetSciELOJournalError:
        #         next_journal_title = None
        # if previous_journal or next_journal_title:
        #     journal_to_publish.add_related_journals(
        #         previous_journal, next_journal_title,
        #     )
        for item in journal.sponsors:
            journal_to_publish.add_sponsor(item)

        # TODO confirmar se subject_categories é subject_descriptors
        journal_to_publish.add_thematic_scopes(
            journal.subject_descriptors,
            journal.subject_areas,
        )

        # journal não tem este dado
        # journal_to_publish.add_issue_count(
        #     journal.issue_count,
        # )

        # journal não tem este dado
        # journal_to_publish.add_item_to_metrics(
        #     journal.total_h5_index,
        #     journal.total_h5_median,
        #     journal.h5_metric_year,
        # )
        # journal não tem este dado
        # journal_to_publish.add_logo_url(journal.logo_url)
        journal_to_publish.add_acron(journal.acronym)
        journal_to_publish.publish_journal()
    except Exception as e:
        raise exceptions.PublishJournalError(
            _("Unable to publish {} {} {}").format(journal_migration, type(e), e)
        )

    try:
        journal_migration.status = MS_PUBLISHED
        journal_migration.save()
    except Exception as e:
        raise exceptions.PublishJournalError(
            _("Unable to publish {} {} {}").format(journal_migration, type(e), e)
        )


def get_or_create_issue_migration(scielo_issue, creator_id):
    """
    Returns a IssueMigration (registered or new)
    """
    try:
        try:
            item = IssueMigration.objects.get(
                scielo_issue=scielo_issue,
            )
        except IssueMigration.DoesNotExist:
            item = IssueMigration()
            item.creator_id = creator_id
            item.scielo_issue = scielo_issue
            item.save()
    except Exception as e:
        raise exceptions.GetOrCreateIssueMigrationError(
            _("Unable to get_or_create_issue_migration {} {} {}").format(
                scielo_issue, type(e), e
            )
        )
    return item


def migrate_issues(
    user_id,
    collection_acron,
    force_update=False,
):
    try:
        mcc = MigrationConfigurationController(collection_acron)
        mcc.connect_db()
        source_file_path = mcc.get_source_file_path("issue")

        for issue_pid, issue_data in classic_ws.get_records_by_source_path(
            "issue", source_file_path
        ):
            try:
                action = "import"
                issue_migration = import_data_from_issue_database(
                    user_id=user_id,
                    collection_acron=collection_acron,
                    scielo_issn=issue_pid[:9],
                    issue_pid=issue_pid,
                    issue_data=issue_data[0],
                    force_update=force_update,
                )
                if issue_migration.status == MS_IMPORTED:
                    schedule_issue_documents_migration(
                        collection_acron=collection_acron,
                        journal_acron=issue_migration.scielo_issue.scielo_journal.acron,
                        scielo_issn=issue_migration.scielo_issue.scielo_journal.scielo_issn,
                        publication_year=issue_migration.scielo_issue.official_issue.publication_year,
                        user_id=user_id,
                    )
                    publish_imported_issue(issue_migration)
            except Exception as e:
                _register_failure(
                    _("Error migrating issue {} {}").format(
                        collection_acron, issue_pid
                    ),
                    collection_acron,
                    action,
                    "issue",
                    issue_pid,
                    e,
                    user_id,
                )
    except Exception as e:
        _register_failure(
            _("Error migrating issue {}").format(collection_acron),
            collection_acron,
            "migrate",
            "issue",
            "GENERAL",
            e,
            user_id,
        )


def import_data_from_issue_database(
    user_id,
    collection_acron,
    scielo_issn,
    issue_pid,
    issue_data,
    force_update=False,
):
    """
    Create/update IssueMigration
    """
    logging.info(
        "Import data from database issue {} {} {}".format(
            collection_acron, scielo_issn, issue_pid
        )
    )
    issue = classic_ws.Issue(issue_data)

    scielo_issue = get_scielo_issue(
        issue, collection_acron, scielo_issn, issue_pid, user_id
    )

    issue_migration = get_or_create_issue_migration(scielo_issue, creator_id=user_id)

    # check if it needs to be update
    if issue_migration.isis_updated_date == issue.isis_updated_date:
        if not force_update:
            # nao precisa atualizar
            logging.info(
                "Skipped: Import data from database issue {} {} {} {} {}".format(
                    collection_acron,
                    scielo_issn,
                    issue_pid,
                    issue_migration.status,
                    force_update,
                )
            )
            return issue_migration
    try:
        issue_migration.isis_created_date = issue.isis_created_date
        issue_migration.isis_updated_date = issue.isis_updated_date
        issue_migration.status = MS_IMPORTED
        if issue.is_press_release:
            issue_migration.status = MS_TO_IGNORE
        issue_migration.data = issue_data

        issue_migration.save()
        return issue_migration
    except Exception as e:
        logging.error(
            _("Error importing issue {} {} {}").format(
                collection_acron, issue_pid, issue_data
            )
        )
        logging.exception(e)
        raise exceptions.IssueMigrationSaveError(
            _("Unable to save {} migration {} {} {}").format(
                "issue", collection_acron, issue_pid, e
            )
        )


def _get_months_from_issue(issue):
    """
    Get months from issue (classic_website.Issue)
    """
    months_names = {}
    for item in issue.bibliographic_strip_months:
        if item.get("text"):
            months_names[item["lang"]] = item.get("text")
    if months_names:
        return months_names.get("en") or months_names.values()[0]


def get_scielo_issue(issue, collection_acron, scielo_issn, issue_pid, user_id):
    logging.info(
        _("Get SciELO Issue {} {} {}").format(collection_acron, scielo_issn, issue_pid)
    )

    try:
        # obtém scielo_journal para criar ou obter scielo_issue
        scielo_journal = collection_controller.get_scielo_journal(
            collection_acron, scielo_issn
        )

        # cria ou obtém scielo_issue
        scielo_issue = collection_controller.get_or_create_scielo_issue(
            scielo_journal,
            issue_pid,
            issue.issue_label,
            user_id,
        )
        logging.info(
            "Check if it is press release = {}".format(bool(issue.is_press_release))
        )
        if not issue.is_press_release:
            # press release não é um documento oficial,
            # sendo assim, não será criado official issue correspondente
            try:
                # obtém ou cria official_issue
                logging.info(
                    _("Create official issue to add to SciELO {}").format(scielo_issue)
                )

                flexible_date = parse_non_standard_date(issue.publication_date)
                months = parse_months_names(_get_months_from_issue(issue))
                official_issue = collection_controller.get_or_create_official_issue(
                    scielo_journal.official_journal,
                    issue.publication_year,
                    issue.volume,
                    issue.number,
                    issue.supplement,
                    user_id,
                    initial_month_number=flexible_date.get("month_number"),
                    initial_month_name=months.get("initial_month_name"),
                    final_month_name=months.get("final_month_name"),
                )

                # atualiza dados de scielo_issue
                scielo_issue.official_issue = official_issue
                scielo_issue.save()
                logging.info(
                    _("Created official issue to add to SciELO {}").format(scielo_issue)
                )
            except Exception as e:
                raise exceptions.SetOfficialIssueToSciELOIssueError(
                    _("Unable to set official issue to SciELO issue {} {} {}").format(
                        scielo_issue, type(e), e
                    )
                )
        return scielo_issue
    except Exception as e:
        raise exceptions.GetSciELOIssueError(
            _("Unable to get SciELO issue {} {} {}").format(scielo_issue, type(e), e)
        )


def publish_imported_issue(issue_migration):
    """
    Raises
    ------
    PublishIssueError
    """
    issue = classic_ws.Issue(issue_migration.data)

    if issue_migration.status != MS_IMPORTED:
        logging.info("Skipped: publish issue {}".format(issue_migration))
        return
    try:
        published_id = get_bundle_id(
            issue.journal,
            issue.publication_year,
            issue.volume,
            issue.number,
            issue.supplement,
        )
        issue_to_publish = IssueToPublish(published_id)

        issue_to_publish.add_identification(
            issue.volume, issue.number, issue.supplement
        )
        issue_to_publish.add_journal(issue.journal)
        issue_to_publish.add_order(int(issue.order[4:]))
        issue_to_publish.add_pid(issue.pid)
        issue_to_publish.add_publication_date(
            issue.publication_year, issue.start_month, issue.end_month
        )
        # FIXME indica se há artigos / documentos
        # para uso de indicação fascículo aop "desativado"
        issue_to_publish.has_docs = []

        issue_to_publish.publish_issue()
    except Exception as e:
        raise exceptions.PublishIssueError(
            _("Unable to publish {} {}").format(
                issue_migration.scielo_issue.issue_pid, e
            )
        )

    try:
        issue_migration.status = MS_PUBLISHED
        issue_migration.save()
    except Exception as e:
        raise exceptions.PublishIssueError(
            _("Unable to upate issue_migration status {} {}").format(
                issue_migration.scielo_issue.issue_pid, e
            )
        )


def get_or_create_document_migration(scielo_document, creator_id):
    """
    Returns a DocumentMigration (registered or new)
    """
    try:
        try:
            dm = DocumentMigration.objects.get(scielo_document=scielo_document)
        except DocumentMigration.DoesNotExist:
            dm = DocumentMigration()
            dm.creator_id = creator_id
            dm.scielo_document = scielo_document
            dm.save()
    except Exception as e:
        raise exceptions.GetOrCreateDocumentMigrationError(
            _("Unable to get_or_create_document_migration {} {} {}").format(
                scielo_document, type(e), e
            )
        )
    return dm


def import_issues_files_and_migrate_documents(
    user_id,
    collection_acron,
    scielo_issn=None,
    publication_year=None,
    force_update=False,
):
    params = {"scielo_issue__scielo_journal__collection__acron": collection_acron}
    if scielo_issn:
        params["scielo_issue__scielo_journal__scielo_issn"] = scielo_issn
    if publication_year:
        params["scielo_issue__official_issue__publication_year"] = publication_year

    logging.info(params)

    items = IssueMigration.objects.filter(
        Q(status=MS_PUBLISHED) | Q(status=MS_IMPORTED),
        **params,
    )

    mcc = MigrationConfigurationController(collection_acron)
    mcc.connect_db()

    for issue_migration in items:
        try:
            import_issue_files(
                user_id=user_id,
                issue_migration=issue_migration,
                store_issue_files=mcc.store_issue_files,
                force_update=force_update,
            )
        except Exception as e:
            _register_failure(
                _("Error import isse files of {}").format(issue_migration),
                collection_acron,
                "import",
                "issue files",
                issue_migration.scielo_issue.issue_pid,
                e,
                user_id,
            )

    for issue_migration in items:
        try:
            for source_file_path in mcc.get_artigo_source_files_paths(
                issue_migration.scielo_issue.scielo_journal.acron,
                issue_migration.scielo_issue.issue_folder,
            ):
                # migra os documentos da base de dados `source_file_path`
                # que não contém necessariamente os dados de só 1 fascículo
                migrate_documents(
                    user_id,
                    collection_acron,
                    source_file_path,
                    mcc.files_storage,
                    mcc.bucket_public_subdir,
                    issue_migration,
                    force_update,
                )

        except Exception as e:
            _register_failure(
                _("Error importing documents of {}").format(issue_migration),
                collection_acron,
                "import",
                "document",
                issue_migration.scielo_issue.issue_pid,
                e,
                user_id,
            )


def import_issue_files(
    user_id,
    issue_migration,
    store_issue_files,
    force_update,
):
    """135
    Migra os arquivos do fascículo (pdf, img, xml ou html)
    """
    logging.info("Import issue files {}".format(issue_migration.scielo_issue))
    if issue_migration.files_status == MS_IMPORTED and not force_update:
        logging.info(
            "Skipped: Import files from classic website {}".format(issue_migration)
        )
        return

    ClassFileModels = {
        "asset": AssetFile,
        "pdf": FileWithLang,
        "xml": XMLFile,
        "html": SciELOHTMLFile,
    }
    try:
        scielo_issue = issue_migration.scielo_issue
        issue = classic_ws.Issue(issue_migration.data)
        for item in store_issue_files(
            scielo_issue.scielo_journal.acron,
            scielo_issue.issue_folder,
        ):
            logging.info("Register stored classic file {}".format(item))

            ClassFile = ClassFileModels[item.pop("type")]
            item["file_id"] = item.get("key")
            params = {k: item[k] for k in item.keys() if hasattr(ClassFile, k)}
            params["scielo_issue"] = scielo_issue

            try:
                # delete file if exists
                del_res = ClassFile.objects.filter(
                    relative_path=item["relative_path"]
                ).delete()
            except Exception as e:
                logging.info(e)
            try:
                file = ClassFile.objects.get(**params)
            except ClassFile.DoesNotExist:
                try:
                    file = ClassFile(**params)
                    file.save()
                except Exception as e:
                    logging.exception(
                        _("Unable to registered imported file {} {}").format(params, e)
                    )
        issue_migration.files_status = MS_IMPORTED
        issue_migration.save()
    except Exception as e:
        raise exceptions.IssueFilesMigrationSaveError(
            _("Unable to save issue files migration {} {}").format(scielo_issue, e)
        )


def migrate_documents(
    user_id,
    collection_acron,
    source_file_path,
    files_storage,
    bucket_public_subdir,
    issue_migration,
    force_update=False,
):
    """
    Importa os registros presentes na base de dados `source_file_path`
    Importa os arquivos dos documentos (xml, pdf, html, imagens)
    Publica os artigos no site
    """
    try:
        # apesar de supostamente estar migrando documentos de um fascículo
        # é possível que source_file_path contenha artigos de mais de 1 issue

        # obtém os registros de title e issue
        journal_migration = JournalMigration.objects.get(
            scielo_journal=issue_migration.scielo_issue.scielo_journal
        )
        journal_issue_and_document_data = {
            "title": journal_migration.data,
            "issue": issue_migration.data,
        }

        # obtém registros da base "artigo" que não necessariamente é só
        # do fascículo de issue_migration
        # possivelmente source_file pode conter registros de outros fascículos
        # se source_file for acrônimo
        logging.info(
            "Importing documents records from source_file_path={}".format(
                source_file_path
            )
        )
        for grp_id, grp_records in classic_ws.get_records_by_source_path(
            "artigo", source_file_path
        ):
            try:
                logging.info(_("Get {} from {}").format(grp_id, source_file_path))
                if len(grp_records) == 1:
                    # é possível que em source_file_path exista registro tipo i
                    journal_issue_and_document_data["issue"] = grp_records[0]
                    continue

                journal_issue_and_document_data["article"] = grp_records

                # instancia Document com registros de title, issue e artigo
                document = classic_ws.Document(journal_issue_and_document_data)
                pid = document.pid
                scielo_issn = document.journal.scielo_issn
                issue_pid = document.issue.pid

                scielo_issue = get_scielo_issue(
                    document.issue, collection_acron, scielo_issn, issue_pid, user_id
                )

                scielo_document = collection_controller.get_or_create_scielo_document(
                    scielo_issue,
                    pid,
                    document.filename_without_extension,
                    user_id,
                )
                document_migration = get_or_create_document_migration(
                    scielo_document=scielo_document,
                    creator_id=user_id,
                )

                document_files_controller = DocumentFilesController(
                    main_language=document.original_language,
                    scielo_document=scielo_document,
                    files_storage=files_storage,
                    bucket_public_subdir=bucket_public_subdir,
                )
                document_files_controller.add_scielo_document_to_files()
                document_files_controller.info()

                import_document(
                    pid,
                    document,
                    document_migration,
                    journal_issue_and_document_data,
                    force_update,
                )

                publish_document(
                    pid,
                    document,
                    document_migration,
                    document_files_controller,
                )

            except Exception as e:
                _register_failure(
                    _("Error migrating document {}").format(pid),
                    collection_acron,
                    "migrate",
                    "document",
                    pid,
                    e,
                    user_id,
                )
    except Exception as e:
        _register_failure(
            _("Error migrating documents"),
            collection_acron,
            "migrate",
            "document",
            "GENERAL",
            e,
            user_id,
        )


def import_document(
    pid, document, document_migration, document_data, force_update=False
):
    """
    Create/update DocumentMigration
    """
    # check if it needs to be update
    if document_migration.isis_updated_date == document.isis_updated_date:
        if not force_update:
            # nao precisa atualizar
            logging.info(
                "Skipped: Import documents from classic website {}. It is already up to date".format(
                    document_migration
                )
            )
            return
    try:
        document_migration.isis_created_date = document.isis_created_date
        document_migration.isis_updated_date = document.isis_updated_date
        document_migration.status = MS_IMPORTED
        document_migration.data = document_data
        document_migration.save()
    except Exception as e:
        raise exceptions.DocumentMigrationSaveError(
            _("Unable to save document migration {} {}").format(pid, e)
        )


def publish_document(pid, document, document_migration, document_files_controller):
    """
    Raises
    ------
    PublishDocumentError
    """
    doc_to_publish = DocumentToPublish(pid)

    if doc_to_publish.doc.created:
        logging.info(
            "Skipped: Publish document {}. It is already published {}".format(
                document_migration, doc_to_publish.doc.created
            )
        )
        return

    if document_migration.status != MS_IMPORTED:
        logging.info(
            "Skipped: Publish document {}. Migration status = {} ".format(
                document_migration, document_migration.status
            )
        )
        return

    try:
        # IDS
        # TODO v3 depende do pid manager
        doc_to_publish.add_identifiers(
            document.scielo_pid_v3,
            document.scielo_pid_v2,
            document.publisher_ahead_id,
        )

        # MAIN METADATA
        doc_to_publish.add_document_type(document.document_type)
        doc_to_publish.add_main_metadata(
            document.original_title,
            document.section,
            document.original_abstract,
            document.original_language,
            document.doi,
        )
        for item in document.authors:
            doc_to_publish.add_author(
                item["surname"],
                item["given_names"],
                item.get("suffix"),
                item.get("affiliation"),
                item.get("orcid"),
            )

        # ISSUE
        try:
            year = document.document_publication_date[:4]
            month = document.document_publication_date[4:6]
            day = document.document_publication_date[6:]
            doc_to_publish.add_publication_date(year, month, day)
        except:
            logging.info("Document has no document publication date %s" % pid)
        doc_to_publish.add_in_issue(
            document.order,
            document.fpage,
            document.fpage_seq,
            document.lpage,
            document.elocation,
        )

        # ISSUE
        bundle_id = get_bundle_id(
            document.journal.scielo_issn,
            document.issue_publication_date[:4],
            document.volume,
            document.issue_number,
            document.supplement,
        )
        doc_to_publish.add_issue(bundle_id)

        # JOURNAL
        doc_to_publish.add_journal(document.journal.scielo_issn)

        # IDIOMAS
        for item in document.doi_with_lang:
            doc_to_publish.add_doi_with_lang(item["language"], item["doi"])

        for item in document.abstracts:
            doc_to_publish.add_abstract(item["language"], item["text"])

        # nao há translated sections
        # TODO necessario resolver
        # for item in document.translated_sections:
        #     doc_to_publish.add_section(item['language'], item['text'])

        for item in document.translated_titles:
            doc_to_publish.add_translated_title(
                item["language"],
                item["text"],
            )
        for lang, keywords in document.keywords_groups.items():
            doc_to_publish.add_keywords(lang, keywords)

        # ARQUIVOS
        # xml
        for xml in document_files_controller.xml_files:
            doc_to_publish.add_xml(xml.public_uri)
            break

        # htmls
        for item in document_files_controller.text_langs:
            doc_to_publish.add_html(item["lang"], uri=None)

        # pdfs
        for item in document_files_controller.rendition_files:
            doc_to_publish.add_pdf(
                lang=item.lang,
                url=item.uri,
                filename=item.name,
                type="pdf",
            )

        # mat supl
        for item in document_files_controller.supplementary_materials:
            doc_to_publish.add_mat_suppl(
                lang=item["lang"],
                url=item["uri"],
                ref_id=item["ref_id"],
                filename=item["name"],
            )

        # RELATED
        # doc_to_publish.add_related_article(doi, ref_id, related_type)
        # <related-article
        #  ext-link-type="doi" id="A01"
        #  related-article-type="commentary-article"
        #  xlink:href="10.1590/0101-3173.2022.v45n1.p139">

        for item in document_files_controller.related_items:
            logging.info(item)
            doc_to_publish.add_related_article(
                doi=item["href"],
                ref_id=item["id"],
                related_type=item["related-article-type"],
            )

        doc_to_publish.publish_document()
        logging.info(_("Published {}").format(document_migration))
    except Exception as e:
        raise exceptions.PublishDocumentError(
            _("Unable to publish {} {}").format(pid, e)
        )

    try:
        document_migration.status = MS_PUBLISHED
        document_migration.save()
    except Exception as e:
        raise exceptions.PublishDocumentError(
            _("Unable to upate document_migration status {} {}").format(pid, e)
        )


class DocumentFilesController:
    def __init__(
        self,
        main_language,
        scielo_document,
        files_storage,
        bucket_public_subdir,
    ):
        self._bucket_public_subdir = os.path.join(
            bucket_public_subdir,
            scielo_document.scielo_issue.scielo_journal.acron,
            scielo_document.scielo_issue.issue_folder,
        )
        self.files_storage = files_storage
        self.scielo_document = scielo_document
        self._main_language = main_language

    def info(self):
        logging.info("DocumentFilesController {}".format(self.scielo_document))
        for item in self.xml_files:
            logging.info("xmlfile: {} {}".format(self.scielo_document, item))
            logging.info(
                "public: {} {}".format(item.public_uri, item.public_object_name)
            )
            logging.info("xml file asset: {} {}".format(self.scielo_document, item))
            for asset in item.assets_files.all():
                logging.info("{} {} {}".format(self.scielo_document, item, asset))
        for item in self.rendition_files:
            logging.info("rendition file: {} {}".format(self.scielo_document, item))
        for item in self.html_files:
            logging.info("html file: {} {}".format(self.scielo_document, item))
            for asset in item.assets_files.all():
                logging.info(
                    "html file asset: {} {} {}".format(
                        self.scielo_document, item, asset
                    )
                )

    def add_scielo_document_to_files(self):
        self.add_rendition_files()
        self.add_xml_files()
        self.add_langs_to_xml_files()
        self.add_html_files()
        self.add_supplementary_material_flag_to_assets()
        self.add_public_xml_files()

    def add_xml_files(self):
        logging.info("Add xml files to {}".format(self.scielo_document))
        self._xml_files = XMLFile.objects.filter(
            scielo_issue=self.scielo_document.scielo_issue,
            file_id=self.scielo_document.file_id,
        )
        self.scielo_document.xml_files.set(self._xml_files)
        self.scielo_document.save()
        logging.info("Added xml files to {}".format(self.scielo_document))

    def add_langs_to_xml_files(self):
        logging.info("Add langs to xml files of {}".format(self.scielo_document))
        self._xmltree = {}
        for item in self.xml_files:
            try:
                xmltree = read_xml_file(self.files_storage.fget(item.object_name))
            except Exception as e:
                raise exceptions.AddLangsToXMLFilesError(
                    _("Unable get xml file {} object_name: {} {} {}").format(
                        self.scielo_document, item.object_name, type(e), e
                    )
                )
            try:
                article = ArticleRenditions(xmltree)
                article_renditions = article.article_renditions
                item.lang = article_renditions[0].language
                item.languages = [
                    {"lang": rendition.language} for rendition in article_renditions
                ]
                item.save()
                logging.info(item)
                self._xmltree[item.lang] = xmltree
            except Exception as e:
                raise exceptions.AddLangsToXMLFilesError(
                    _("Unable to add langs to xml files {} {} {} {}").format(
                        self.scielo_document, item, type(e), e
                    )
                )
        logging.info("Added langs to xml files of {}".format(self.scielo_document))
        return self._xmltree

    def add_rendition_files(self):
        logging.info("Add rendition files to {}".format(self.scielo_document))
        try:
            main_pdf = FileWithLang.objects.get(
                scielo_issue=self.scielo_document.scielo_issue,
                file_id=self.scielo_document.file_id,
                lang="main",
            )
            main_pdf.lang = self._main_language
            main_pdf.save()
        except FileWithLang.DoesNotExist:
            pass
        self._rendition_files = FileWithLang.objects.filter(
            scielo_issue=self.scielo_document.scielo_issue,
            file_id=self.scielo_document.file_id,
        )
        self.scielo_document.renditions_files.set(self._rendition_files)
        self.scielo_document.save()
        logging.info("Added rendition files to {}".format(self.scielo_document))

    def add_html_files(self):
        logging.info("Add html files to {}".format(self.scielo_document))
        self._html_files = SciELOHTMLFile.objects.filter(
            scielo_issue=self.scielo_document.scielo_issue,
            file_id=self.scielo_document.file_id,
        )
        self.scielo_document.html_files.set(self._html_files)
        self.scielo_document.save()
        logging.info("Added html files to {}".format(self.scielo_document))

    @property
    def rendition_files(self):
        if not hasattr(self, "_rendition_files") or not self._rendition_files:
            self.add_rendition_files()
        return self._rendition_files

    @property
    def html_files(self):
        if not hasattr(self, "_html_files") or not self._html_files:
            self.add_html_files()
        return self._html_files

    @property
    def xml_files(self):
        if not hasattr(self, "_xml_files") or not self._xml_files:
            self.add_xml_files()
        return self._xml_files

    @property
    def xmltree(self):
        if not hasattr(self, "_xmltree") or not self._xmltree:
            self.add_langs_to_xml_files()
        return self._xmltree

    @property
    def issue_assets_uri(self):
        if not hasattr(self, "_issue_assets_uri") or not self._issue_assets_uri:
            self._issue_assets_uri = {
                name: asset.uri for name, asset in self.issue_assets_dict.items()
            }
        return self._issue_assets_uri

    @property
    def issue_assets_dict(self):
        if not hasattr(self, "_issue_assets_as_dict") or not self._issue_assets_as_dict:
            self._issue_assets_as_dict = {
                asset.name: asset
                for asset in AssetFile.objects.filter(
                    scielo_issue=self.scielo_document.scielo_issue,
                )
            }
        return self._issue_assets_as_dict

    @property
    def text_langs(self):
        if not hasattr(self, "_text_langs") or not self._text_langs:
            if self.xmltree:
                self._text_langs = []
                for lang, xmltree in self.xmltree.items():
                    for rendition in ArticleRenditions(xmltree).article_renditions:
                        self._text_langs.append({"lang": rendition.language})
            else:
                self._text_langs = [{"lang": self._main_language}] + [
                    {"lang": html.lang}
                    for html in self.html_files
                    if html.part == "before"
                ]
        return self._text_langs

    @property
    def related_items(self):
        if not hasattr(self, "_related_items") or not self._related_items:
            items = []
            for lang, xmltree in self.xmltree.items():
                related = RelatedItems(xmltree)
                items.extend(related.related_articles)
            self._related_items = items
        return self._related_items

    def get_object_name(self, file_path):
        return self.files_storage.build_object_name(
            file_path, self._bucket_public_subdir, preserve_name=True
        )

    def add_supplementary_material_flag_to_assets(self):
        logging.info(
            _("Add supplementary material flag to assets {}").format(
                self.scielo_document
            )
        )
        self._supplementary_materials = []
        for lang, xmltree in self.xmltree.items():
            try:
                suppl_mats = SupplementaryMaterials(xmltree)
                for sm in suppl_mats.items:
                    try:
                        asset_file = self.issue_assets_dict.get(sm.name)
                        if asset_file:
                            # FIXME tratar asset_file nao encontrado
                            asset_file.is_supplementary_material = True
                            asset_file.save()
                            self._supplementary_materials.append(
                                {
                                    "uri": asset_file.uri,
                                    "lang": lang,
                                    "ref_id": None,
                                    "filename": sm.name,
                                }
                            )
                    except Exception as e:
                        raise exceptions.AddSupplementaryMaterialFlagToAssetError(
                            _(
                                "Unable to add supplentary material flag to asset {} {} {} {}"
                            ).format(self.scielo_document, lang, type(e), e)
                        )

            except Exception as e:
                raise exceptions.AddSupplementaryMaterialFlagToAssetError(
                    _(
                        "Unable to add supplentary material flag to asset {} {} {} {}"
                    ).format(self.scielo_document, lang, type(e), e)
                )
        logging.info(
            _("Added supplementary material flag to assets {}").format(
                self.scielo_document
            )
        )

    @property
    def supplementary_materials(self):
        if (
            not hasattr(self, "_supplementary_materials")
            or not self._supplementary_materials
        ):
            self.add_supplementary_material_flag_to_assets()
        return self._supplementary_materials

    def add_public_xml_files(self):
        if not self.xmltree:
            return

        logging.info(_("Add public xml files to {}").format(self.scielo_document))
        from_to = self.issue_assets_uri

        for xml_file in self.xml_files:
            try:
                # copia de xml
                xmltree = deepcopy(self.xmltree[xml_file.lang])

                # obtém os assets do XML
                article_assets = ArticleAssets(xmltree)
                for asset_in_xml in article_assets.article_assets:
                    asset = self.issue_assets_dict.get(asset_in_xml.name)
                    if asset:
                        # FIXME tratar asset_file nao encontrado
                        xml_file.assets_files.add(asset)
                xml_file.save()

                # substitui name por uri
                article_assets.replace_names(from_to)

                # registra XML modificado no Files Storage
                object_name = self.get_object_name(xml_file.name)

                #
                uri = self.files_storage.fput_content(
                    tostring(article_assets.xmltree),
                    mimetype="text/xml",
                    object_name=object_name,
                )
                xml_file.public_uri = uri
                xml_file.public_object_name = object_name
                xml_file.save()
                logging.info(_("Registered {} {}").format(xml_file, uri))
            except Exception as e:
                raise exceptions.AddPublicXMLError(
                    _("Unable to add public XML to {} {} {})").format(
                        xml_file, type(e), e
                    )
                )

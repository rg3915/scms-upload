import hashlib
import logging
import os
from datetime import datetime
from http import HTTPStatus
from shutil import copyfile

from django.core.files.base import ContentFile
from django.db import models
from django.utils.translation import gettext as _
from wagtail.admin.panels import FieldPanel

from core.forms import CoreAdminModelForm
from core.models import CommonControlField
from pid_requester import exceptions, v3_gen, xml_sps_adapter
from xmlsps.xml_sps_lib import get_xml_with_pre

LOGGER = logging.getLogger(__name__)
LOGGER_FMT = "%(asctime)s [%(levelname)s] %(name)s: %(message)s"


def utcnow():
    return datetime.utcnow()
    # return datetime.utcnow().isoformat().replace("T", " ") + "Z"


class SyncFailure(CommonControlField):
    error_type = models.CharField(
        _("Exception Type"), max_length=255, null=True, blank=True
    )
    error_msg = models.TextField(_("Exception Msg"), null=True, blank=True)
    traceback = models.JSONField(null=True, blank=True)

    @property
    def data(self):
        return {
            "error_type": self.error_type,
            "error_msg": self.error_msg,
            "traceback": self.traceback,
        }

    @classmethod
    def create(cls, error_msg, error_type, traceback, creator):
        logging.info("SyncFailure.create")
        obj = cls()
        obj.error_msg = error_msg
        obj.error_type = error_type
        obj.traceback = traceback
        obj.creator = creator
        obj.created = utcnow()
        obj.save()
        return obj


class PidProviderConfig(CommonControlField):
    """
    Tem função de guardar XML que falhou no registro
    """

    pid_provider_api_post_xml = models.TextField(
        _("XML Post URI"), null=True, blank=True
    )
    pid_provider_api_get_token = models.TextField(
        _("Get Token URI"), null=True, blank=True
    )
    timeout = models.IntegerField(_("Timeout"), null=True, blank=True)
    api_username = models.TextField(_("API Username"), null=True, blank=True)
    api_password = models.TextField(_("API Password"), null=True, blank=True)

    def __unicode__(self):
        return f"{self.pid_provider_api_post_xml}"

    def __str__(self):
        return f"{self.pid_provider_api_post_xml}"

    @classmethod
    def get_or_create(
        cls,
        creator=None,
        pid_provider_api_post_xml=None,
        pid_provider_api_get_token=None,
        api_username=None,
        api_password=None,
        timeout=None,
    ):
        obj = cls.objects.first()
        if obj is None:
            obj = cls()
            obj.pid_provider_api_post_xml = pid_provider_api_post_xml
            obj.pid_provider_api_get_token = pid_provider_api_get_token
            obj.api_username = api_username
            obj.api_password = api_password
            obj.timeout = timeout
            obj.creator = creator
            obj.save()
        return obj

    panels = [
        FieldPanel("pid_provider_api_post_xml"),
        FieldPanel("pid_provider_api_get_token"),
        FieldPanel("api_username"),
        FieldPanel("api_password"),
        FieldPanel("timeout"),
    ]

    base_form_class = CoreAdminModelForm


class PidRequesterBadRequest(CommonControlField):
    """
    Tem função de guardar XML que falhou no registro
    """

    basename = models.TextField(_("Basename"), null=True, blank=True)
    finger_print = models.CharField(max_length=65, null=True, blank=True)
    error_type = models.TextField(null=True, blank=True)
    error_message = models.TextField(null=True, blank=True)
    xml = models.FileField(upload_to="bad_request")

    class Meta:

        indexes = [
            models.Index(fields=["basename"]),
            models.Index(fields=["finger_print"]),
            models.Index(fields=["error_type"]),
            models.Index(fields=["error_message"]),
        ]

    def __unicode__(self):
        return f"{self.basename} {self.error_type}"

    def __str__(self):
        return f"{self.basename} {self.error_type}"

    @property
    def data(self):
        return {
            "error_type": self.error_type,
            "error_message": self.error_message,
            "id": self.finger_print,
            "filename": self.basename,
        }

    @classmethod
    def get_or_create(cls, creator, basename, exception, xml_adapter):
        finger_print = xml_adapter.finger_print

        try:
            obj = cls.objects.get(finger_print=finger_print)
        except cls.DoesNotExist:
            obj = cls()
            obj.finger_print = finger_print

        obj.xml = ContentFile(xml_adapter.tostring(), name=finger_print + ".xml")
        obj.basename = basename
        obj.error_type = str(type(exception))
        obj.error_message = str(exception)
        obj.creator = creator
        obj.save()
        return obj

    panels = [
        FieldPanel("basename"),
        FieldPanel("xml"),
        FieldPanel("error_type"),
        FieldPanel("error_message"),
    ]

    base_form_class = CoreAdminModelForm


class XMLJournal(models.Model):
    """
    Tem função de guardar os dados de Journal encontrados no XML
    Tem objetivo de identificar o Documento (Artigo)
    """

    issn_electronic = models.CharField(
        _("issn_epub"), max_length=9, null=True, blank=True
    )
    issn_print = models.CharField(_("issn_ppub"), max_length=9, null=True, blank=True)

    class Meta:
        indexes = [
            models.Index(fields=["issn_electronic"]),
            models.Index(fields=["issn_print"]),
        ]

    def __str__(self):
        return f"{self.issn_electronic} {self.issn_print}"

    @classmethod
    def get_or_create(cls, issn_electronic, issn_print):
        try:
            return cls.objects.get(
                issn_electronic=issn_electronic,
                issn_print=issn_print,
            )
        except cls.DoesNotExist:
            journal = cls()
            journal.issn_electronic = issn_electronic
            journal.issn_print = issn_print
            journal.save()
            return journal


class XMLIssue(models.Model):
    """
    Tem função de guardar os dados de Issue encontrados no XML
    Tem objetivo de identificar o Documento (Artigo)
    """

    journal = models.ForeignKey(
        XMLJournal, on_delete=models.SET_NULL, null=True, blank=True
    )
    pub_year = models.CharField(_("pub_year"), max_length=4, null=True, blank=True)
    volume = models.CharField(_("volume"), max_length=10, null=True, blank=True)
    number = models.CharField(_("number"), max_length=10, null=True, blank=True)
    suppl = models.CharField(_("suppl"), max_length=10, null=True, blank=True)

    class Meta:
        unique_together = [
            ["journal", "pub_year", "volume", "number", "suppl"],
        ]
        indexes = [
            models.Index(fields=["journal"]),
            models.Index(fields=["volume"]),
            models.Index(fields=["number"]),
            models.Index(fields=["suppl"]),
            models.Index(fields=["pub_year"]),
        ]

    def __str__(self):
        return (
            f'{self.journal} {self.volume or ""} {self.number or ""} {self.suppl or ""}'
        )

    @classmethod
    def get_or_create(cls, journal, volume, number, suppl, pub_year):
        try:
            return cls.objects.get(
                journal=journal,
                volume=volume,
                number=number,
                suppl=suppl,
                pub_year=pub_year,
            )
        except cls.DoesNotExist:
            issue = cls()
            issue.journal = journal
            issue.volume = volume
            issue.number = number
            issue.suppl = suppl
            issue.pub_year = pub_year
            issue.save()
            return issue


def xml_directory_path(instance, filename):
    # file will be uploaded to MEDIA_ROOT/user_<id>/<filename>
    return f"xml_pid_requester/{instance.finger_print[-1]}/{instance.finger_print[-2]}/{instance.finger_print}/{filename}"


class XMLVersion(CommonControlField):
    """
    Tem função de guardar a versão do XML
    """

    pid_requester_xml = models.ForeignKey(
        "PidRequesterXML", on_delete=models.SET_NULL, null=True, blank=True
    )
    file = models.FileField(upload_to=xml_directory_path, null=True, blank=True)
    finger_print = models.CharField(max_length=64, null=True, blank=True)
    pkg_name = models.TextField(_("Name"), null=True, blank=True)

    class Meta:
        indexes = [
            models.Index(fields=["finger_print"]),
            models.Index(fields=["pid_requester_xml"]),
        ]

    def __str__(self):
        return self.finger_print

    @classmethod
    def create(
        cls,
        creator,
        pid_requester_xml,
        pkg_name=None,
        finger_print=None,
        xml_content=None,
    ):
        obj = cls()
        obj.finger_print = finger_print
        obj.pid_requester_xml = pid_requester_xml
        obj.pkg_name = pkg_name
        obj.save_file(pkg_name + ".xml", xml_content)
        obj.creator = creator
        obj.created = utcnow()
        obj.save()
        return obj

    def save_file(self, name, content):
        self.file.save(name, ContentFile(content))

    @property
    def xml_with_pre(self):
        try:
            return get_xml_with_pre(self.xml_content)
        except Exception as e:
            raise exceptions.PidRequesterXMLWithPreError(
                _("Unable to get xml with pre (PidRequesterXML) {}: {} {}").format(
                    self.pkg_name, type(e), e
                )
            )

    @property
    def xml_content(self):
        try:
            if self.file:
                with self.file.open("r") as fp:
                    return fp.read()
            return None
        except Exception as e:
            raise exceptions.PidRequesterXMLContentError(
                _("Unable to get xml content (PidRequesterXML) {}: {} {}").format(
                    self.name, type(e), e
                )
            )


class XMLRelatedItem(CommonControlField):
    """
    Tem função de guardar os relacionamentos entre outro Documento (Artigo)
    Tem objetivo de identificar o Documento (Artigo)
    """

    main_doi = models.TextField(_("DOI"), null=True, blank=True)

    class Meta:
        indexes = [
            models.Index(fields=["main_doi"]),
        ]

    def __str__(self):
        return self.main_doi

    @classmethod
    def get_or_create(cls, main_doi, creator=None):
        try:
            return cls.objects.get(main_doi=main_doi)
        except cls.DoesNotExist:
            obj = cls()
            obj.main_doi = main_doi
            obj.creator = creator
            obj.created = utcnow()
            obj.save()
            return obj


class PidRequesterXML(CommonControlField):
    """
    Tem responsabilidade de garantir a atribuição do PID da versão 3,
    armazenando dados chaves que garantem a identificação do XML
    """

    journal = models.ForeignKey(
        XMLJournal, on_delete=models.SET_NULL, null=True, blank=True
    )
    issue = models.ForeignKey(
        XMLIssue, on_delete=models.SET_NULL, null=True, blank=True
    )
    related_items = models.ManyToManyField(XMLRelatedItem)
    current_version = models.ForeignKey(
        XMLVersion, on_delete=models.SET_NULL, null=True, blank=True
    )

    pkg_name = models.TextField(_("Package name"), null=True, blank=True)
    v3 = models.CharField(_("v3"), max_length=23, null=True, blank=True)
    v2 = models.CharField(_("v2"), max_length=23, null=True, blank=True)
    aop_pid = models.CharField(_("AOP PID"), max_length=23, null=True, blank=True)

    elocation_id = models.TextField(_("elocation id"), null=True, blank=True)
    fpage = models.CharField(_("fpage"), max_length=10, null=True, blank=True)
    fpage_seq = models.CharField(_("fpage_seq"), max_length=10, null=True, blank=True)
    lpage = models.CharField(_("lpage"), max_length=10, null=True, blank=True)
    article_pub_year = models.CharField(
        _("Document Publication Year"), max_length=4, null=True, blank=True
    )
    main_toc_section = models.TextField(_("main_toc_section"), null=True, blank=True)
    main_doi = models.TextField(_("DOI"), null=True, blank=True)

    z_article_titles_texts = models.CharField(
        _("article_titles_texts"), max_length=64, null=True, blank=True
    )
    z_surnames = models.CharField(_("surnames"), max_length=64, null=True, blank=True)
    z_collab = models.CharField(_("collab"), max_length=64, null=True, blank=True)
    z_links = models.CharField(_("links"), max_length=64, null=True, blank=True)
    z_partial_body = models.CharField(
        _("partial_body"), max_length=64, null=True, blank=True
    )
    synchronized = models.BooleanField(null=True, blank=True, default=False)
    sync_failure = models.ForeignKey(
        SyncFailure, null=True, blank=True, on_delete=models.SET_NULL
    )

    class Meta:
        indexes = [
            models.Index(fields=["pkg_name"]),
            models.Index(fields=["v3"]),
            models.Index(fields=["journal"]),
            models.Index(fields=["issue"]),
            models.Index(fields=["elocation_id"]),
            models.Index(fields=["fpage"]),
            models.Index(fields=["fpage_seq"]),
            models.Index(fields=["lpage"]),
            models.Index(fields=["article_pub_year"]),
            models.Index(fields=["main_doi"]),
            models.Index(fields=["z_article_titles_texts"]),
            models.Index(fields=["z_surnames"]),
            models.Index(fields=["z_collab"]),
            models.Index(fields=["z_links"]),
            models.Index(fields=["z_partial_body"]),
            models.Index(fields=["synchronized"]),
        ]

    def __str__(self):
        return self.pkg_name or self.v3 or "PidRequesterXML sem ID"

    @property
    def data(self):
        _data = {
            "v3": self.v3,
            "v2": self.v2,
            "aop_pid": self.aop_pid,
            "pkg_name": self.pkg_name,
            "created": self.created and self.created.isoformat(),
            "updated": self.updated and self.updated.isoformat(),
            "record_status": "updated" if self.updated else "created",
            "synchronized": self.synchronized,
        }
        if self.sync_failure:
            _data.update(self.sync_failure.data)
        return _data

    @classmethod
    def unsynchronized(cls):
        """
        Identifica no pid requester os registros que não
        estão sincronizados com o pid provider (central) e
        faz a sincronização, registrando o XML local no pid provider
        """
        return cls.objects.filter(synchronized=False).iterator()

    @classmethod
    def get_xml_with_pre(cls, v3):
        try:
            return cls.objects.get(v3=v3).xml_with_pre
        except:
            return None

    @property
    def xml_with_pre(self):
        return self.current_version and self.current_version.xml_with_pre

    @property
    def is_aop(self):
        return self.issue is None

    def set_current_version(self, creator, pkg_name, finger_print, xml_content):
        if (
            not self.current_version
            or self.current_version.finger_print != finger_print
        ):
            logging.info("PidRequesterXML.set_current_version")
            self.current_version = XMLVersion.create(
                creator=creator,
                pid_requester_xml=self,
                pkg_name=pkg_name,
                finger_print=finger_print,
                xml_content=xml_content,
            )

    @classmethod
    def register(
        cls,
        xml_with_pre,
        filename,
        user,
        synchronized=None,
        error_msg=None,
        error_type=None,
        traceback=None,
    ):
        """
        Evaluate the XML data and returns corresponding PID v3, v2, aop_pid

        Parameters
        ----------
        xml : XMLWithPre
        filename : str
        user : User

        Returns
        -------
            {
                "v3": self.v3,
                "v2": self.v2,
                "aop_pid": self.aop_pid,
                "xml_uri": self.xml_uri,
                "article": self.article,
                "created": self.created.isoformat(),
                "updated": self.updated.isoformat(),
                "xml_changed": boolean,
                "record_status": created | updated | retrieved
            }
            or
            {
                "error_type": self.error_type,
                "error_message": self.error_message,
                "id": self.finger_print,
                "filename": self.name,
            }

        """
        try:
            logging.info(f"PidRequesterXML.register ....  {filename}")
            pkg_name, ext = os.path.splitext(os.path.basename(filename))

            # adaptador do xml with pre
            xml_adapter = xml_sps_adapter.PidRequesterXMLAdapter(xml_with_pre)

            # consulta se documento já está registrado
            registered = cls._query_document(xml_adapter)

            # analisa se aceita ou rejeita registro
            cls.evaluate_registration(xml_adapter, registered)

            # verfica os PIDs encontrados no XML / atualiza-os se necessário
            xml_changed = cls._complete_pids(xml_adapter, registered)

            xml_content = xml_adapter.tostring()
            finger_print = xml_adapter.finger_print

            registered = cls._save(
                registered,
                xml_adapter,
                user,
                pkg_name,
                xml_content,
                finger_print,
                synchronized,
                error_type,
                error_msg,
                traceback,
            )
            data = registered.data.copy()
            data["xml_changed"] = xml_changed
            return data

        except (
            exceptions.ForbiddenPidRequesterXMLRegistrationError,
            exceptions.NotEnoughParametersToGetDocumentRecordError,
            exceptions.QueryDocumentMultipleObjectsReturnedError,
        ) as e:
            bad_request = PidRequesterBadRequest.get_or_create(
                user,
                filename,
                e,
                xml_adapter,
            )
            return bad_request.data

    @classmethod
    def _save(
        cls,
        registered,
        xml_adapter,
        user,
        pkg_name,
        xml_content,
        finger_print,
        synchronized=None,
        error_type=None,
        error_msg=None,
        traceback=None,
    ):
        if registered:
            registered.updated_by = user
            registered.updated = utcnow()
        else:
            registered = cls()
            registered.creator = user
            registered.created = utcnow()
            registered.save()

        registered._add_data(xml_adapter, user, pkg_name)

        registered.synchronized = synchronized
        if error_msg or error_type or traceback:
            registered.synchronized = False
            registered.sync_failure = SyncFailure.create(
                error_msg,
                error_type,
                traceback,
                user,
            )
        registered.set_current_version(
            creator=user,
            pkg_name=pkg_name,
            finger_print=finger_print,
            xml_content=xml_content,
        )
        registered.save()
        return registered

    @classmethod
    def evaluate_registration(cls, xml_adapter, registered):
        """
        XML é versão AOP, mas
        documento está registrado com versão VoR (fascículo),
        então, recusar o registro,
        pois está tentando registrar uma versão desatualizada
        """
        logging.info("PidRequesterXML.evaluate_registration")
        if xml_adapter.is_aop and registered and not registered.is_aop:
            raise exceptions.ForbiddenPidRequesterXMLRegistrationError(
                _(
                    "The XML content is an ahead of print version "
                    "but the document {} is already published in an issue"
                ).format(registered)
            )
        return True

    def set_synchronized(
        self, user, xml_uri=None, error_type=None, error_msg=None, traceback=None
    ):
        logging.info("PidRequesterXML.set_synchronized")
        self.synchronized = bool(xml_uri)
        if error_type or error_msg or traceback:
            self.sync_failure = SyncFailure.create(
                error_msg,
                error_type,
                traceback,
                user,
            )
        self.updated_by = user
        self.updated = utcnow()
        self.save()

    def is_equal_to(self, xml_adapter):
        return bool(
            self.current_version
            and self.current_version.finger_print == xml_adapter.finger_print
        )

    @classmethod
    def check_registration_demand(cls, xml_with_pre):
        """
        Verifica se há necessidade de registrar local (upload) e/ou
        remotamente (core)

        Parameters
        ----------
        xml_with_pre : XMLWithPre

        Returns
        -------
        exceptions.QueryDocumentMultipleObjectsReturnedError
        """
        logging.info("PidRequesterXML.check_registration_demand")
        xml_adapter = xml_sps_adapter.PidRequesterXMLAdapter(xml_with_pre)

        try:
            registered = cls._query_document(xml_adapter)
        except (
            exceptions.NotEnoughParametersToGetDocumentRecordError,
            exceptions.QueryDocumentMultipleObjectsReturnedError,
        ) as e:
            logging.exception(e)
            return {"error_msg": str(e), "error_type": str(type(e))}

        required_remote = (
            not registered
            or not registered.is_equal_to(xml_adapter)
            or not registered.synchronized
        )
        required_local = not registered or required_remote

        return dict(
            registered=registered and registered.data or {},
            required_local_registration=required_local,
            required_remote_registration=required_remote,
        )

    @classmethod
    def get_registered(cls, xml_with_pre):
        """
        Get registered

        Parameters
        ----------
        xml_with_pre : XMLWithPre

        Returns
        -------
            None
            or
            {
                "v3": self.v3,
                "v2": self.v2,
                "aop_pid": self.aop_pid,
                "xml_uri": self.xml_uri,
                "article": self.article,
                "created": self.created.isoformat(),
                "updated": self.updated.isoformat(),
            }
            or
            {"error_msg": str(e), "error_type": str(type(e))}
        """
        xml_adapter = xml_sps_adapter.PidRequesterXMLAdapter(xml_with_pre)
        try:
            registered = cls._query_document(xml_adapter)
        except (
            exceptions.NotEnoughParametersToGetDocumentRecordError,
            exceptions.QueryDocumentMultipleObjectsReturnedError,
        ) as e:
            logging.exception(e)
            return {"error_msg": str(e), "error_type": str(type(e))}
        if registered:
            return registered.data

    @classmethod
    def _query_document(cls, xml_adapter):
        """
        Query document

        Arguments
        ---------
        xml_adapter : PidRequesterXMLAdapter

        Returns
        -------
        None or PidRequesterXML

        Raises
        ------
        exceptions.QueryDocumentMultipleObjectsReturnedError
        exceptions.NotEnoughParametersToGetDocumentRecordError
        """
        LOGGER.info("_query_document")
        items = xml_adapter.query_list
        for params in items:
            cls.validate_query_params(params)

            try:
                return cls.objects.get(**params)
            except cls.DoesNotExist:
                continue
            except cls.MultipleObjectsReturned as e:
                # seria inesperado já que os dados informados devem encontrar
                # ocorrência única ou None
                logging.info(f"params={params} | e={e}")
                raise exceptions.QueryDocumentMultipleObjectsReturnedError(
                    _("Found more than one document matching to {}").format(params)
                )

    def _add_data(self, xml_adapter, user, pkg_name):
        logging.info(f"PidRequesterXML._add_data {pkg_name}")
        self.pkg_name = pkg_name
        self.article_pub_year = xml_adapter.article_pub_year
        self.v3 = xml_adapter.v3
        self.v2 = xml_adapter.v2
        self.aop_pid = xml_adapter.aop_pid

        self.fpage = xml_adapter.fpage
        self.fpage_seq = xml_adapter.fpage_seq
        self.lpage = xml_adapter.lpage

        self.main_doi = xml_adapter.main_doi
        self.main_toc_section = xml_adapter.main_toc_section
        self.elocation_id = xml_adapter.elocation_id

        self.z_article_titles_texts = xml_adapter.z_article_titles_texts
        self.z_surnames = xml_adapter.z_surnames
        self.z_collab = xml_adapter.z_collab
        self.z_links = xml_adapter.z_links
        self.z_partial_body = xml_adapter.z_partial_body

        self.journal = XMLJournal.get_or_create(
            xml_adapter.journal_issn_electronic,
            xml_adapter.journal_issn_print,
        )
        self.issue = None
        if xml_adapter.volume or xml_adapter.number or xml_adapter.suppl:
            self.issue = XMLIssue.get_or_create(
                self.journal,
                xml_adapter.volume,
                xml_adapter.number,
                xml_adapter.suppl,
                xml_adapter.pub_year,
            )

        for related in xml_adapter.related_items:
            self._add_related_item(related["href"], user)

    def _add_related_item(self, main_doi, creator):
        self.related_items.add(XMLRelatedItem.get_or_create(main_doi, creator))

    @classmethod
    def _get_unique_v3(cls):
        """
        Generate v3 and return it only if it is new

        Returns
        -------
            str
        """
        while True:
            generated = v3_gen.generates()
            if not cls._is_registered_pid(v3=generated):
                return generated

    @classmethod
    def _is_registered_pid(cls, v2=None, v3=None, aop_pid=None):
        if v3:
            kwargs = {"v3": v3}
        elif v2:
            kwargs = {"v2": v2}
        elif aop_pid:
            kwargs = {"aop_pid": aop_pid}

        if kwargs:
            try:
                found = cls.objects.filter(**kwargs)[0]
            except IndexError:
                return False
            else:
                return True

    @classmethod
    def _v2_generates(cls, xml_adapter):
        # '2022-10-19T13:51:33.830085'
        h = utcnow()
        mm = str(h.month).zfill(2)
        dd = str(h.day).zfill(2)
        nnnnn = str(h.timestamp()).split(".")[0][-5:]
        return f"{xml_adapter.v2_prefix}{mm}{dd}{nnnnn}"

    @classmethod
    def _get_unique_v2(cls, xml_adapter):
        """
        Generate v2 and return it only if it is new

        Returns
        -------
            str
        """
        while True:
            generated = cls._v2_generates(xml_adapter)
            if not cls._is_registered_pid(v2=generated):
                return generated

    @classmethod
    def _complete_pids(cls, xml_adapter, registered):
        """
        Update `xml_adapter` pids with `registered` pids or
        create `xml_adapter` pids

        Parameters
        ----------
        xml_adapter: PidRequesterXMLAdapter
        registered: XMLArticle

        Returns
        -------
        bool

        """
        before = (xml_adapter.v2, xml_adapter.v3, xml_adapter.aop_pid)

        # adiciona os pids faltantes aos dados de entrada
        cls._add_pid_v3(xml_adapter, registered)
        cls._add_pid_v2(xml_adapter, registered)
        cls._add_aop_pid(xml_adapter, registered)

        after = (xml_adapter.v2, xml_adapter.v3, xml_adapter.aop_pid)

        LOGGER.info("%s %s" % (before, after))
        return before != after

    @classmethod
    def _is_valid_pid(cls, value):
        return bool(value and len(value) == 23)

    @classmethod
    def _add_pid_v3(cls, xml_adapter, registered):
        """
        Atribui v3 ao xml_adapter,
        recuperando do registered ou obtendo um v3 inédito

        Arguments
        ---------
        xml_adapter: PidRequesterXMLAdapter
        registered: XMLArticle
        """
        if registered:
            # recupera do registrado
            xml_adapter.v3 = registered.v3
        else:
            # se v3 de xml está ausente ou já está registrado para outro xml
            if not cls._is_valid_pid(xml_adapter.v3) or cls._is_registered_pid(
                v3=xml_adapter.v3
            ):
                # obtém um v3 inédito
                xml_adapter.v3 = cls._get_unique_v3()

    @classmethod
    def _add_aop_pid(cls, xml_adapter, registered):
        """
        Atribui aop_pid ao xml_adapter, recuperando do registered, se existir

        Arguments
        ---------
        xml_adapter: PidRequesterXMLAdapter
        registered: XMLArticle
        """
        if registered and registered.aop_pid:
            xml_adapter.aop_pid = registered.aop_pid

    @classmethod
    def _add_pid_v2(cls, xml_adapter, registered):
        """
        Adiciona ou atualiza a xml_adapter, v2 recuperado de registered ou gerado

        Arguments
        ---------
        xml_adapter: PidRequesterXMLAdapter
        registered: XMLArticle

        """
        if registered and registered.v2 and xml_adapter.v2 != registered.v2:
            xml_adapter.v2 = registered.v2
        if not cls._is_valid_pid(xml_adapter.v2):
            xml_adapter.v2 = cls._get_unique_v2(xml_adapter)

    @classmethod
    def validate_query_params(cls, query_params):
        """
        Validate query parameters

        Arguments
        ---------
        filter_by_issue: bool
        aop_version: bool

        Returns
        -------
        bool
        """
        _params = query_params
        if not any(
            [
                _params.get("journal__issn_print"),
                _params.get("journal__issn_electronic"),
            ]
        ):
            raise exceptions.NotEnoughParametersToGetDocumentRecordError(
                _("No attribute enough for disambiguations {}").format(
                    _params,
                )
            )

        if not any(
            [
                _params.get("article_pub_year"),
                _params.get("issue__pub_year"),
            ]
        ):
            raise exceptions.NotEnoughParametersToGetDocumentRecordError(
                _("No attribute enough for disambiguations {}").format(
                    _params,
                )
            )

        if any(
            [
                _params.get("main_doi"),
                _params.get("fpage"),
                _params.get("elocation_id"),
            ]
        ):
            return True

        if not any(
            [
                _params.get("z_surnames"),
                _params.get("z_collab"),
                _params.get("z_links"),
                _params.get("pkg_name"),
            ]
        ):
            raise exceptions.NotEnoughParametersToGetDocumentRecordError(
                _("No attribute enough for disambiguations {}").format(
                    _params,
                )
            )
        return True

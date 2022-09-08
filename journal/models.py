from django.db import models
from django.utils.translation import gettext_lazy as _

from core.models import CommonControlField
from collection.models import Collection
from .forms import OfficialJournalForm

from wagtail.core.models import Orderable
from wagtail.admin.edit_handlers import FieldPanel, MultiFieldPanel, InlinePanel

from modelcluster.fields import ParentalKey
from modelcluster.models import ClusterableModel


class OfficialJournal(CommonControlField):
    """
    Class that represent the Official Journal
    """

    def __unicode__(self):
        return u'%s' % (self.title)

    def __str__(self):
        return u'%s' % (self.title)

    title = models.CharField(_('Official Title'), max_length=256, null=True, blank=True)
    foundation_year = models.CharField(_('Foundation Year'), max_length=4, null=True, blank=True)
    ISSN_print = models.CharField(_('ISSN Print'), max_length=9, null=True, blank=True)
    ISSN_electronic = models.CharField(_('ISSN Eletronic'), max_length=9, null=True, blank=True)
    ISSNL = models.CharField(_('ISSNL'), max_length=9, null=True, blank=True)

    base_form_class = OfficialJournalForm


class JournalInCollection(CommonControlField):
    """
    Class that represent the Official Journal
    """

    # TODO futuramente ter um formulário para gerir os dados

    def __unicode__(self):
        return u'%s' % self.scielo_issn

    def __str__(self):
        return u'%s' % self.scielo_issn

    scielo_issn = models.CharField(_('SciELO ISSN'), max_length=9, null=False, blank=False)
    collection = models.ForeignKey(Collection, on_delete=models.CASCADE)

    # TODO acrescentar
    # data de entrada
    # data de saída
    # motivo da saída


class SciELOJournal(CommonControlField):
    """
    Class that represent the Official Journal
    """

    def __unicode__(self):
        return u'%s %s' % (self.official_journal.title, [c.scielo_issn for c in self.collections])

    def __str__(self):
        return u'%s %s' % (self.official_journal.title, [c.scielo_issn for c in self.collections])

    official_journal = models.ForeignKey(OfficialJournal, on_delete=models.CASCADE)
    collections = models.ManyToManyField(JournalInCollection)


class NonOfficialJournalTitle(ClusterableModel, CommonControlField):

    def __unicode__(self):
        return u'%s' % (self.official_journal.title)

    def __str__(self):
        return u'%s' % (self.official_journal.title)

    official_journal = models.ForeignKey('OfficialJournal', null=True, blank=False, related_name='OfficialJournal', on_delete=models.CASCADE)


    panels=[
        FieldPanel('official_journal'),
        InlinePanel('page_non_official_title', label=_('Non Official Journal Title'))
    ]

    base_form_class = OfficialJournalForm


class NonOfficialTitle(Orderable):
   page = ParentalKey(NonOfficialJournalTitle, related_name='page_non_official_title')
   non_official_journal_title = models.CharField(_('Non Official Journal Title'),max_length=255, null=False, blank=False)


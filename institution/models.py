from django.db import models
from django.utils.translation import gettext as _
from modelcluster.models import ClusterableModel
from wagtail.admin.edit_handlers import FieldPanel, InlinePanel

from core.models import CommonControlField
from location.models import Location

from . import choices
from .forms import InstitutionForm


class Institution(CommonControlField, ClusterableModel):
    name = models.TextField(_("Name"), null=True, blank=True)
    institution_type = models.CharField(
        _("Institution Type"),
        choices=choices.inst_type,
        max_length=255,
        null=True,
        blank=True,
    )

    location = models.ForeignKey(
        Location, null=True, blank=True, on_delete=models.SET_NULL
    )

    acronym = models.TextField(_("Institution Acronym"), blank=True, null=True)

    level_1 = models.TextField(_("Organization Level 1"), blank=True, null=True)

    level_2 = models.TextField(_("Organization Level 2"), blank=True, null=True)

    level_3 = models.TextField(_("Organization Level 3"), blank=True, null=True)

    url = models.URLField("url", blank=True, null=True)

    logo = models.ImageField(_("Logo"), blank=True, null=True)

    panels = [
        FieldPanel("name"),
        FieldPanel("acronym"),
        FieldPanel("institution_type"),
        FieldPanel("location"),
        FieldPanel("level_1"),
        FieldPanel("level_2"),
        FieldPanel("level_3"),
        FieldPanel("url"),
        FieldPanel("logo"),
    ]

    def __unicode__(self):
        return "%s | %s | %s | %s | %s" % (
            self.name,
            self.acronym,
            self.level_1,
            self.level_2,
            self.level_3,
            self.location,
        )

    def __str__(self):
        return "%s | %s | %s | %s | %s" % (
            self.name,
            self.acronym,
            self.level_1,
            self.level_2,
            self.level_3,
            self.location,
        )

    @classmethod
    def get_or_create(
        cls, inst_name, inst_acronym, level_1, level_2, level_3, location
    ):
        # Institution
        # check if exists the institution
        parms = {}
        if inst_name:
            parms["name"] = inst_name
        if inst_acronym:
            parms["acronym"] = inst_acronym
        if location:
            parms["location"] = location
        if level_1:
            parms["level_1"] = level_1
        if level_2:
            parms["level_2"] = level_2
        if level_3:
            parms["level_3"] = level_3

        try:
            return cls.objects.get(**parms)
        except:
            institution = cls()
            institution.name = inst_name
            institution.acronym = inst_acronym
            institution.level_1 = level_1
            institution.level_2 = level_2
            institution.level_3 = level_3
            institution.location = location
            institution.save()
        return institution

    base_form_class = InstitutionForm


class InstitutionHistory(models.Model):
    institution = models.ForeignKey(
        "Institution", null=True, blank=True, related_name="+", on_delete=models.CASCADE
    )
    initial_date = models.DateField(_("Initial Date"), null=True, blank=True)
    final_date = models.DateField(_("Final Date"), null=True, blank=True)

    panels = [
        FieldPanel("institution", heading=_("Institution")),
        FieldPanel("initial_date"),
        FieldPanel("final_date"),
    ]

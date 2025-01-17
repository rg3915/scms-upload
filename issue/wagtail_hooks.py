from django.http import HttpResponseRedirect
from django.utils.translation import gettext as _
from wagtail.contrib.modeladmin.options import ModelAdmin, modeladmin_register
from wagtail.contrib.modeladmin.views import CreateView

from config.menu import get_menu_order

from .models import Issue


class IssueCreateView(CreateView):
    def form_valid(self, form):
        self.object = form.save_all(self.request.user)
        return HttpResponseRedirect(self.get_success_url())


class IssueAdmin(ModelAdmin):
    model = Issue
    inspect_view_enabled = True
    menu_label = _("Issues")
    create_view_class = IssueCreateView
    menu_icon = "folder"
    menu_order = get_menu_order("issue")
    add_to_settings_menu = False
    exclude_from_explorer = False

    list_display = (
        "official_journal",
        "publication_year",
        "volume",
        "number",
        "supplement",
    )
    list_filter = (
        "official_journal",
        "publication_year",
    )
    search_fields = (
        "official_journal__title",
        "publication_year",
        "volume",
        "number",
        "supplement",
    )


modeladmin_register(IssueAdmin)

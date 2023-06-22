from article.models import Article
from xmlsps.controller import my_pipeline_crossref


def run(*args):
    # Article.objects.filter(
    #     xml_sps__isnull=False,
    #     issue__publication_year=2023
    # )
    my_pipeline_crossref()

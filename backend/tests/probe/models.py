from django.db import models

from foundation.models import CellScopedModel


class Probe(CellScopedModel):
    label = models.CharField(max_length=40)

    class Meta(CellScopedModel.Meta):
        app_label = "probe"

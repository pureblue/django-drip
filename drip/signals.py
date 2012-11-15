from django.db.models.signals import post_save
from django.contrib.contenttypes.models import ContentType

from drip.models import Drip

def post_save_handler(sender, instance, created, **kwargs):
    if created:
        content_type = ContentType.objects.get_for_model(instance)
        drips = Drip.objects.filter(enabled=True, trigger_model=content_type)
        for drip in drips:
            drip.drip.run()

def connect_signals(content_type=None):
    if not content_type:
        ct_pks = Drip.objects.filter(
            enabled=True, trigger_model__isnull=False).values_list('trigger_model', flat=True).distinct()
        content_types = ContentType.objects.filter(pk__in=ct_pks)
    else:
        content_types = [content_type]
    for content_type in content_types:
        post_save.connect(post_save_handler, sender=content_type.model_class(), dispatch_uid="drip-post-save-signal")


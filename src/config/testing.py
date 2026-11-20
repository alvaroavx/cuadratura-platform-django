from django.conf import settings
from django.test.runner import DiscoverRunner


class SourceDiscoverRunner(DiscoverRunner):
    """Discover the application tests when manage.py test has no labels."""

    def build_suite(self, test_labels=None, **kwargs):
        return super().build_suite(test_labels or [str(settings.SOURCE_DIR)], **kwargs)

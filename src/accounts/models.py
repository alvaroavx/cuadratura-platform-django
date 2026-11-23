import uuid
from django.contrib.auth.models import AbstractUser
from django.db import models

class User(AbstractUser):
    class PlatformRole(models.TextChoices):
        PLATFORM_ADMIN = "PLATFORM_ADMIN", "Administrador de plataforma"
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    platform_role = models.CharField(max_length=32, choices=PlatformRole.choices, blank=True)

    @property
    def is_platform_admin(self):
        return self.is_superuser or self.platform_role == self.PlatformRole.PLATFORM_ADMIN



class FederatedIdentity(models.Model):
    user = models.ForeignKey(User, on_delete=models.CASCADE, related_name="federated_identities")
    provider = models.SlugField(max_length=64)
    subject = models.CharField(max_length=191)

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=["provider", "subject"], name="federated_identity_subject_unique"),
            models.UniqueConstraint(fields=["user", "provider"], name="federated_identity_user_unique"),
        ]

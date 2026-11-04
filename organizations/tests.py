from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse

from audit.models import AuditEvent
from .models import OrganizationMembership, TaxpayerOrganization


class OrganizationArchiveTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        User = get_user_model()
        cls.admin = User.objects.create_user(username="admin-plataforma", password="x", is_superuser=True)
        cls.member = User.objects.create_user(username="admin-org", password="x")
        cls.org = TaxpayerOrganization.objects.create(tax_id="77.111.222-3", legal_name="Taller Ficticio SpA")
        OrganizationMembership.objects.create(
            organization=cls.org, user=cls.member, role=OrganizationMembership.Role.ORGANIZATION_ADMIN
        )
        cls.url = reverse("organization-archive", args=[cls.org.id])

    def test_edit_page_shows_delete_button_to_platform_admin(self):
        self.client.force_login(self.admin)
        response = self.client.get(reverse("organization-edit", args=[self.org.id]))
        self.assertContains(response, self.url)

    def test_organization_admin_cannot_archive(self):
        self.client.force_login(self.member)
        self.assertEqual(self.client.get(self.url).status_code, 403)
        response = self.client.post(self.url, {"reason": "x", "confirm_tax_id": self.org.tax_id})
        self.assertEqual(response.status_code, 403)
        self.org.refresh_from_db()
        self.assertIsNone(self.org.archived_at)

    def test_wrong_tax_id_does_not_archive(self):
        self.client.force_login(self.admin)
        response = self.client.post(self.url, {"reason": "Duplicado", "confirm_tax_id": "11.111.111-1"})
        self.assertContains(response, "El RUT no coincide")
        self.org.refresh_from_db()
        self.assertIsNone(self.org.archived_at)

    def test_archive_hides_blocks_and_audits(self):
        self.client.force_login(self.admin)
        response = self.client.post(self.url, {"reason": "Creado por error", "confirm_tax_id": "77111222-3"})
        self.assertRedirects(response, reverse("organization-list"), fetch_redirect_response=False)
        self.org.refresh_from_db()
        self.assertIsNotNone(self.org.archived_at)
        self.assertFalse(self.org.is_active)
        self.assertEqual(self.org.archived_by, self.admin)
        self.assertTrue(
            AuditEvent.objects.filter(
                organization=self.org, action="organization.archived", metadata__reason="Creado por error"
            ).exists()
        )
        # Desaparece de la lista y queda inaccesible, incluso para el admin.
        # La lista muestra el mensaje de éxito con el nombre; se verifica la ausencia de la tarjeta.
        self.assertNotContains(self.client.get(reverse("organization-list")), reverse("organization-edit", args=[self.org.id]))
        self.assertEqual(self.client.get(reverse("organization-edit", args=[self.org.id])).status_code, 404)
        self.assertEqual(self.client.get(self.url).status_code, 404)
        self.client.force_login(self.member)
        self.assertNotContains(self.client.get(reverse("organization-list")), reverse("organization-period-current", args=[self.org.id]))
        self.assertEqual(
            self.client.get(reverse("organization-period", args=[self.org.id, "2026-11"])).status_code, 404
        )

    def test_archive_list_is_read_only_and_admin_only(self):
        self.client.force_login(self.admin)
        self.client.post(self.url, {"reason": "Creado por error", "confirm_tax_id": self.org.tax_id})
        listing = reverse("organization-archive-list")
        self.assertContains(self.client.get(reverse("organization-list")), "Ver archivo (1)")
        response = self.client.get(listing)
        self.assertContains(response, "Taller Ficticio SpA")
        self.assertContains(response, "Creado por error")
        self.assertNotContains(response, reverse("organization-edit", args=[self.org.id]))
        self.client.force_login(self.member)
        self.assertEqual(self.client.get(listing).status_code, 403)
        self.assertNotContains(self.client.get(reverse("organization-list")), "Ver archivo")

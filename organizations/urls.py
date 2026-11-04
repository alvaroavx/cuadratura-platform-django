from django.urls import path
from . import views
urlpatterns=[
 path("",views.organization_list,name="organization-list"),
 path("organizaciones/archivo/",views.organization_archive_list,name="organization-archive-list"),
 path("organizaciones/nueva/",views.organization_create,name="organization-create"),
 path("organizaciones/consultar-rut/",views.organization_tax_id_lookup,name="organization-tax-id-lookup"),
 path("organizaciones/<uuid:organization_id>/editar/",views.organization_edit,name="organization-edit"),
 path("organizaciones/<uuid:organization_id>/consultar-rut/",views.organization_update_lookup,name="organization-update-lookup"),
 path("organizaciones/<uuid:organization_id>/actualizar-desde-rut/",views.organization_update_from_lookup,name="organization-update-from-lookup"),
 path("organizaciones/<uuid:organization_id>/eliminar/",views.organization_archive,name="organization-archive"),
 path("organizaciones/<uuid:organization_id>/invitar/",views.invite_user,name="organization-invite"),
 path("organizaciones/<uuid:organization_id>/membresias/nueva/",views.membership_create,name="membership-create"),
 path("organizaciones/<uuid:organization_id>/usuarios/alta-controlada/",views.controlled_user_create,name="controlled-user-create"),
 path("organizaciones/<uuid:organization_id>/periodos/<str:period>/administracion/",views.organization_administration,name="organization-administration"),
]

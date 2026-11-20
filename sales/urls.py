from django.urls import path
from documents.views import upload_document_pdf

from . import views
urlpatterns=[
 path("organizaciones/<uuid:organization_id>/periodos/<str:period>/honorarios/cargar/",upload_document_pdf,{"honorarium": True},name="honorarium-upload"),
 path("organizaciones/<uuid:organization_id>/periodos/<str:period>/facturas/cargar/",upload_document_pdf,name="invoice-upload"),
 path("organizaciones/<uuid:organization_id>/periodos/actual/",views.current_period,name="organization-period-current"),
 path("organizaciones/<uuid:organization_id>/periodos/seleccionar/",views.select_period,name="organization-period-select"),
 path("organizaciones/<uuid:organization_id>/periodos/<str:period>/",views.period_dashboard,name="organization-period"),
 path("organizaciones/<uuid:organization_id>/periodos/<str:period>/operaciones/nueva/",views.manual_operation,name="manual-operation"),
 path("organizaciones/<uuid:organization_id>/periodos/<str:period>/ventas/<uuid:sale_id>/boleta/",views.manual_receipt,name="manual-receipt"),
]
